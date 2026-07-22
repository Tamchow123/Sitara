"""Bridge between the storage-agnostic Redis budget ledger (``cost_control``)
and the durable per-attempt audit columns on ``GenerationAttempt`` (Phase 16).

The atomic ceiling lives entirely in Redis (``cost_control``). These columns are
PRIVATE audit data for ad-hoc totals, admin inspection and incident
reconciliation — never exposed through any API. They are folded in ONLY on a
genuine first-time ledger transition (``newly_reserved`` / ``transitioned``), so
a Celery redelivery that merely replays a reservation does not double-count. A
reconcile that cannot reach the ledger fails CONSERVATIVELY: the reservation
stays counted against the ceiling (over-counting spend, never under-counting)
and the pipeline is never crashed into resubmitting a paid call.

Reserve failures, by contrast, propagate — a live provider call must never
proceed without a successful atomic reservation.
"""

from __future__ import annotations

import logging

from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import cost_control

logger = logging.getLogger(__name__)


def cost_enabled(attempt) -> bool:
    """Cost accounting applies ONLY to live attempts. A demo attempt never
    reaches the ledger (it cannot spend money by construction)."""
    return attempt is not None and not attempt.is_demo


# A benign no-op reservation returned for a demo attempt: ``newly_reserved`` is
# False so no audit fold occurs and no ledger call was ever made.
_DISABLED_RESERVE = cost_control.ReserveOutcome(status="disabled", amount_micro_usd=0)


def reserve(attempt, stage: str, amount_micro_usd: int, profile) -> cost_control.ReserveOutcome:
    """Obtain/replay the deterministic reservation for this billable call and
    fold a genuinely new reservation into the attempt's audit totals. Propagates
    ``BudgetExhausted`` / ``BudgetLedgerUnavailable`` — the caller must NOT invoke
    the provider on either (fail closed).

    The demo bypass is enforced HERE (self-defending bridge), not only at the
    call sites: a demo attempt never reaches the ledger regardless of caller
    discipline. Callers may still short-circuit for efficiency, but this guard is
    the single source of truth for the zero-cost-demo invariant."""
    if not cost_enabled(attempt):
        return _DISABLED_RESERVE
    reservation_id = cost_control.reservation_id_for(attempt.id, stage, profile)
    outcome = cost_control.reserve(reservation_id, amount_micro_usd, profile)
    if outcome.newly_reserved:
        _update(
            attempt,
            cost_reserved_micro_usd=F("cost_reserved_micro_usd") + outcome.amount_micro_usd,
            cost_pricing_profile_version=profile.version,
        )
    return outcome


def reconcile_actual(
    attempt,
    stage: str,
    profile,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """Reconcile a reservation down to its measured estimated-actual cost and
    record the safe token counts. Ledger failure is swallowed conservatively."""
    if not cost_enabled(attempt):
        return
    actual = cost_control.anthropic_actual_micro_usd(profile, input_tokens, output_tokens)

    def op(rid):
        return cost_control.reconcile_actual(rid, actual, profile)

    outcome = _reconcile_safe(attempt, stage, profile, op)
    if outcome is None or not outcome.transitioned:
        return
    fields = _fold_fields(outcome)
    if input_tokens:
        fields["accounted_input_tokens"] = Coalesce(F("accounted_input_tokens"), 0) + int(
            input_tokens
        )
    if output_tokens:
        fields["accounted_output_tokens"] = Coalesce(F("accounted_output_tokens"), 0) + int(
            output_tokens
        )
    _update(attempt, **fields)


def reconcile_fixed(attempt, stage: str, profile, actual_micro_usd: int) -> None:
    """Reconcile to a fixed measured actual (used for a provider with no
    trustworthy per-call billing, e.g. Replicate retains its configured maximum
    as the estimated actual). Ledger failure is swallowed conservatively."""
    if not cost_enabled(attempt):
        return

    def op(rid):
        return cost_control.reconcile_actual(rid, actual_micro_usd, profile)

    outcome = _reconcile_safe(attempt, stage, profile, op)
    if outcome is not None and outcome.transitioned:
        _update(attempt, **_fold_fields(outcome))


def retain(attempt, stage: str, profile) -> bool:
    """Retain the full conservative reservation for an ambiguous acceptance /
    billing outcome (unresolved spend). Ledger failure is swallowed
    conservatively — the reservation was already counted.

    Returns whether the reservation reached a terminal ledger state: True when
    the ledger answered (reconciled/retained/released/already/missing), False
    when the ledger call was swallowed (unavailable/inconsistent) and the
    reservation may still be ``reserved``. A demo attempt has nothing to settle
    and returns True."""
    if not cost_enabled(attempt):
        return True

    def op(rid):
        return cost_control.retain(rid, profile)

    outcome = _reconcile_safe(attempt, stage, profile, op)
    if outcome is not None and outcome.transitioned:
        _update(attempt, **_fold_fields(outcome))
    return outcome is not None


def _fold_fields(outcome) -> dict:
    return {
        "cost_estimated_micro_usd": F("cost_estimated_micro_usd") + outcome.estimated_micro_usd,
        "cost_unresolved_micro_usd": F("cost_unresolved_micro_usd") + outcome.unresolved_micro_usd,
    }


def release(attempt, stage: str, profile) -> None:
    """Release a reservation on a DEFINITELY pre-spend failure (no provider work
    could have occurred). Ledger failure is swallowed conservatively — an
    un-released reservation only over-counts the ceiling, never under-counts."""
    if not cost_enabled(attempt):
        return

    def op(rid):
        return cost_control.release(rid, profile)

    _reconcile_safe(attempt, stage, profile, op)
    # No audit fold on release: the reserved audit total stays as the cumulative
    # maximum reserved; estimated/unresolved simply remain zero for this call.


def mark_complete(attempt, *, settled: bool = True) -> None:
    """Record that this attempt's accounting is fully settled — every reservation
    it touched reached a terminal ledger state. Unresolved spend may still be
    positive (a conservative ambiguous/retained outcome). ``settled`` is False
    when a terminal reconcile/retain was swallowed (a Redis outage or identity
    mismatch): completion then stays False so the audit row does not falsely claim
    a settled ledger while a reservation may still be ``reserved``."""
    if cost_enabled(attempt) and settled:
        _update(attempt, cost_accounting_complete=True)


def _reconcile_safe(attempt, stage, profile, op):
    reservation_id = cost_control.reservation_id_for(attempt.id, stage, profile)
    try:
        return op(reservation_id)
    except cost_control.BudgetLedgerInconsistent:
        # A genuine reservation-identity mismatch is an application bug, not a
        # transient outage — log it distinctly and loudly so it is not mistaken
        # for routine Redis flakiness. Still conservative: the reconcile is a
        # no-op and the reservation stays counted against the ceiling.
        logger.error(
            "budget reservation identity mismatch attempt=%s stage=%s",
            attempt.id,
            stage,
        )
        return None
    except cost_control.BudgetLedgerUnavailable:
        # Conservative: leave the reservation counted against the ceiling. Log
        # only the operation name, attempt UUID and stage — never amounts or
        # ledger internals.
        logger.warning(
            "budget reconcile unavailable attempt=%s stage=%s",
            attempt.id,
            stage,
        )
        return None


def _update(attempt, **fields) -> None:
    """Best-effort private-audit write. These columns are non-authoritative, so a
    database error here (a transient outage, or a CHECK-constraint violation from
    a crash-window audit skew) must NEVER propagate and break the pipeline — it is
    caught, logged with only the safe attempt UUID, and swallowed. The write runs
    in its own savepoint so a failure cannot poison an enclosing transaction."""
    from django.db import DatabaseError, transaction

    from sitara.designs.models import GenerationAttempt

    fields["updated_at"] = timezone.now()
    try:
        with transaction.atomic():
            GenerationAttempt.objects.filter(pk=attempt.pk).update(**fields)
    except DatabaseError:
        logger.warning("budget audit write failed attempt=%s", attempt.pk)
