"""DesignSpec generation orchestration (Phase 8).

The single service that turns a complete Design into one persisted, validated
DesignVersion. It performs EVERY pre-spend validation first, acquires a
non-blocking PostgreSQL advisory lock keyed by the Design UUID before any
provider call, makes at most two controlled provider requests, re-validates
the output through Pydantic and business checks, and persists exactly one
DesignVersion only after a valid result exists.

On any failure nothing is persisted, the Design and answers are unchanged, and
logs carry only the operation, Design UUID, attempt number and exception type
— never a prompt, answer, output, key or provider error body.
"""

import contextlib
import json
import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from pydantic import ValidationError

from sitara.ai_gateway.policy import get_structured_design_generation_provider
from sitara.ai_gateway.structured_design import (
    StructuredDesignProviderError,
    StructuredDesignRequest,
)
from sitara.catalogue.models import InspirationAsset, UsageRights
from sitara.designs.models import Design, GenerationAttempt
from sitara.designs.services import (
    create_next_design_version_locked,
    design_completion_errors,
)

from . import cost_accounting, cost_control
from .context import DesignNotReady, GenerationContext, build_generation_context
from .design_spec import DESIGN_SPEC_SCHEMA_VERSION, SPEC_TEMPLATE_VERSION, DesignSpec
from .input_safety import GeneratedContentRejected, RejectionCategory, contains_phrase, iter_strings
from .inspiration_context import (
    InspirationAssetIneligible,
    InspirationContextSnapshot,
    InspirationMetadataUnavailable,
    build_inspiration_context_snapshot,
    inspiration_context_sha256,
)
from .prompting import SYSTEM_PROMPT, build_user_message

logger = logging.getLogger(__name__)

MAX_PROVIDER_REQUESTS = 2

# Deterministic cost-reservation stage per Anthropic request number for INITIAL
# generation. The controlled validation retry is a DISTINCT logical billable
# call and therefore has its own reservation stage.
_INITIAL_STRUCTURED_STAGES = {
    1: cost_control.STAGE_STRUCTURED_INITIAL,
    2: cost_control.STAGE_STRUCTURED_RETRY,
}


class GenerationLocked(Exception):
    """Another spec generation holds the Design's advisory lock. Safe message."""


class GenerationRefused(Exception):
    """The provider refused. No retry, nothing persisted. Safe message."""


class GenerationFailed(Exception):
    """The output was invalid after the allowed attempts. Safe message; carries
    the number of provider requests actually made."""

    def __init__(self, attempts: int):
        self.attempts = attempts
        super().__init__("structured design generation produced no valid output")


class SourceSelectionMismatch(Exception):
    """The generated source_selections did not match the trusted input."""


class DesignChangedDuringGeneration(Exception):
    """The Design's inputs changed between the pre-spend snapshot and
    persistence (a concurrent draft edit or an inspiration becoming
    ineligible). Nothing is persisted, the newer draft is left untouched, and
    the paid provider is NOT retried. Safe message."""


class ProviderIdentityChanged(Exception):
    """The provider or model identity differed across the two attempts, so the
    aggregated provenance would be incoherent. Nothing is persisted. Safe
    message (never carries a model value)."""


@dataclass(frozen=True)
class AggregatedUsage:
    """Provider/model identity and TOTAL token usage across every returned
    response of one generation operation. A token total is None when ANY
    response lacked that dimension (never a misleading partial)."""

    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


def aggregate_usage(responses: list) -> AggregatedUsage:
    """Sum usage across all returned responses. Provider/model identity must be
    consistent; a missing dimension on any response yields None for that
    total."""
    first = responses[0]
    input_total = 0
    output_total = 0
    input_known = True
    output_known = True
    for response in responses:
        if response.provider != first.provider or response.model != first.model:
            raise ProviderIdentityChanged("provider identity changed across attempts")
        if response.input_tokens is None:
            input_known = False
        else:
            input_total += response.input_tokens
        if response.output_tokens is None:
            output_known = False
        else:
            output_total += response.output_tokens
    return AggregatedUsage(
        provider=first.provider,
        model=first.model,
        # A known-but-zero total would violate the positive-token DB constraint;
        # treat it as absent rather than persist a misleading 0.
        input_tokens=input_total if (input_known and input_total > 0) else None,
        output_tokens=output_total if (output_known and output_total > 0) else None,
    )


def _input_snapshot(design: Design) -> tuple:
    """A deterministic fingerprint of the generation inputs: questionnaire
    version id, normalised persisted answers and ordered selected inspiration
    ids. Compared before persistence to reject a spec built from stale
    inputs."""
    answers = design.answers or {}
    inspiration_ids = list(
        design.inspiration_selections.order_by("position").values_list(
            "inspiration_asset_id", flat=True
        )
    )
    return (
        str(design.questionnaire_version_id),
        json.dumps(answers, sort_keys=True, ensure_ascii=False),
        [str(asset_id) for asset_id in inspiration_ids],
    )


@dataclass(frozen=True)
class _InputSnapshot:
    """The full pre-persistence freshness fingerprint: the base
    questionnaire/answers/inspiration-id tuple, the exact canonical
    inspiration-context snapshot and its hash. Equality compares both the
    hash AND the exact content — a provider response built from stale
    metadata (a selection change, asset retirement, rights revocation/
    expiry, metadata mutation or attribution mutation) is never persisted."""

    base: tuple
    inspiration_context: InspirationContextSnapshot
    inspiration_context_sha256: str


def _build_input_snapshot(
    design: Design, inspiration_context: InspirationContextSnapshot
) -> _InputSnapshot:
    return _InputSnapshot(
        base=_input_snapshot(design),
        inspiration_context=inspiration_context,
        inspiration_context_sha256=inspiration_context_sha256(inspiration_context),
    )


def _lock_key(design_id) -> int:
    # A stable signed 64-bit advisory-lock key from the Design UUID.
    return int.from_bytes(design_id.bytes[:8], "big", signed=True)


@contextlib.contextmanager
def advisory_lock(design_id):
    """Non-blocking session advisory lock keyed by the Design UUID. Raises
    GenerationLocked if another generation already holds it; always released."""
    key = _lock_key(design_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]
        if not acquired:
            raise GenerationLocked("another spec generation is in progress for this design")
        try:
            yield
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def _assert_source_selections_match(spec: DesignSpec, canonical: dict) -> None:
    if spec.source_selections.model_dump() != canonical:
        raise SourceSelectionMismatch("generated source_selections did not match the input")


def _assert_no_inspiration_leakage(
    spec: DesignSpec, inspiration_context: InspirationContextSnapshot
) -> None:
    """The audit-only title/attribution of a selected inspiration (never sent
    to the provider) must never appear in generated output — its presence
    would mean the model guessed or fabricated it. Provider cues themselves
    are allowed to influence the narrative; this checks only the fields the
    model was never given.

    Matched the same way as every other safety denylist in this codebase
    (token-boundary ``contains_phrase``, never a raw substring test), and
    only for titles/attributions of at least two words — a short, ordinary
    bridalwear/colour/fabric word used as a catalogue title (nothing
    requires titles to be distinctive) must never spuriously match
    unrelated generated prose."""
    needles = [
        text.strip()
        for item in inspiration_context.items
        for text in (item.acknowledgement.title, item.acknowledgement.attribution)
        if len(text.split()) >= 2
    ]
    if not needles:
        return
    haystack = " ".join(iter_strings(spec.model_dump(mode="python")))
    if any(contains_phrase(haystack, needle) for needle in needles):
        raise GeneratedContentRejected(RejectionCategory.INSPIRATION_LEAKAGE)


def _validate_output(payload: dict, context: GenerationContext) -> DesignSpec:
    """Fresh Django-side revalidation + business checks. Raises on any failure
    (all treated as retryable by the caller)."""
    spec = DesignSpec.model_validate(payload)
    scan_design_spec_or_raise(spec)
    _assert_source_selections_match(spec, context.source_selections)
    _assert_no_inspiration_leakage(spec, context.inspiration_context)
    return spec


def scan_design_spec_or_raise(spec: DesignSpec) -> None:
    # Imported here to keep the safety module free of Django/domain imports.
    from .input_safety import scan_design_spec

    scan_design_spec(spec)


def _generate_valid_spec(provider, context: GenerationContext, design_id, generation_attempt=None):
    """Make at most MAX_PROVIDER_REQUESTS controlled requests. Returns
    (spec, usage, attempts) where ``usage`` aggregates token counts across
    EVERY returned response (both attempts). A provider transport error or
    refusal aborts immediately (no retry).

    When a Phase 10 ``generation_attempt`` is supplied, its durable
    ``text_submission_in_flight`` marker is persisted immediately BEFORE each
    paid request — so ONLY a genuinely in-flight submission can ever leave it
    set (pre-call validation failures never touch it), and a worker loss
    inside the request window is visible to the redelivery. The pipeline
    clears it on definitive outcomes."""
    responses: list = []  # every StructuredDesignResult actually returned
    attempts = 0
    # Live cost accounting applies only to a live attempt; a demo attempt never
    # reaches the ledger (BudgetExhausted / BudgetLedgerUnavailable propagate to
    # the pipeline text stage, which fails the attempt closed — no provider call).
    cost_on = cost_accounting.cost_enabled(generation_attempt)
    profile = cost_control.active_pricing_profile()
    for attempt in range(1, MAX_PROVIDER_REQUESTS + 1):
        attempts += 1
        request = StructuredDesignRequest(
            system_prompt=SYSTEM_PROMPT,
            user_message=build_user_message(context, retry=attempt > 1),
            source_selections=context.source_selections,
            max_output_tokens=settings.DESIGN_SPEC_MAX_OUTPUT_TOKENS,
            attempt=attempt,
        )
        stage = _INITIAL_STRUCTURED_STAGES[attempt]
        # Reserve BEFORE the submission marker (spec Part A §6 ordering); a
        # rejected or unavailable reservation raises and no provider call runs.
        if cost_on:
            cost_accounting.reserve(
                generation_attempt,
                stage,
                cost_control.anthropic_call_max_micro_usd(profile, request.max_output_tokens),
                profile,
            )
        if generation_attempt is not None:
            GenerationAttempt.objects.filter(pk=generation_attempt.pk).update(
                text_submission_in_flight=True, updated_at=timezone.now()
            )
            generation_attempt.text_submission_in_flight = True
        try:
            result = provider.generate(request)  # StructuredDesignProviderError propagates
        except StructuredDesignProviderError as exc:
            # An ambiguous acceptance may already have billed — retain the full
            # reservation; a definitive pre-request answer never billed — release.
            if cost_on:
                if getattr(exc, "ambiguous_acceptance", False):
                    cost_accounting.retain(generation_attempt, stage, profile)
                else:
                    cost_accounting.release(generation_attempt, stage, profile)
            raise
        # The provider answered — the call is billable. Reconcile to reported
        # usage when present, otherwise retain the conservative reservation.
        if cost_on:
            if result.input_tokens is not None or result.output_tokens is not None:
                cost_accounting.reconcile_actual(
                    generation_attempt,
                    stage,
                    profile,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            else:
                cost_accounting.retain(generation_attempt, stage, profile)
        responses.append(result)
        if result.refused:
            logger.warning(
                "design spec generation refused design=%s attempt=%s", design_id, attempt
            )
            raise GenerationRefused("the provider refused to generate a specification")
        if result.payload is not None:
            try:
                spec = _validate_output(result.payload, context)
            except (
                ValidationError,
                GeneratedContentRejected,
                SourceSelectionMismatch,
            ) as exc:
                logger.warning(
                    "design spec output rejected design=%s attempt=%s exception_type=%s",
                    design_id,
                    attempt,
                    type(exc).__name__,
                )
            else:
                # Aggregate usage over every response consumed so far (an
                # invalid first attempt still spent tokens).
                return spec, aggregate_usage(responses), attempts
    raise GenerationFailed(attempts)


def _lock_inspiration_rows_in_order(locked_design: Design) -> None:
    """Lock every row a selected inspiration's provider-safe snapshot could
    depend on, in one documented order (Design already locked by the caller;
    DesignInspiration by position; InspirationAsset by uuid; UsageRights by
    uuid) — never across the provider request, only for the remainder of
    this short finalisation transaction. Blocks a concurrent selection
    change, asset retirement, rights verification/revocation or (should a
    future write path allow it) an attribution edit until this transaction
    commits, closing the window between the snapshot rebuild below and the
    version write."""
    inspirations = list(
        locked_design.inspiration_selections.select_for_update().order_by("position")
    )
    asset_ids = sorted({selection.inspiration_asset_id for selection in inspirations})
    if not asset_ids:
        return
    list(InspirationAsset.objects.select_for_update().filter(pk__in=asset_ids).order_by("pk"))
    rights_ids = sorted(
        InspirationAsset.objects.filter(pk__in=asset_ids, usage_rights__isnull=False).values_list(
            "usage_rights_id", flat=True
        )
    )
    if rights_ids:
        list(UsageRights.objects.select_for_update().filter(pk__in=rights_ids).order_by("pk"))


def _rebuild_input_snapshot_locked(locked_design: Design) -> _InputSnapshot | None:
    """Rebuild the full freshness fingerprint under the locks acquired by
    :func:`_lock_inspiration_rows_in_order`. Returns ``None`` (never a raw
    exception) when a selected inspiration is no longer eligible or its
    metadata no longer passes the safety scan — both are a "changed"
    condition, not a crash."""
    try:
        fresh_inspiration_context = build_inspiration_context_snapshot(locked_design)
    except (InspirationAssetIneligible, InspirationMetadataUnavailable):
        return None
    return _build_input_snapshot(locked_design, fresh_inspiration_context)


def _finalise_atomic(
    design, spec: DesignSpec, usage: AggregatedUsage, input_snapshot: _InputSnapshot, attempt=None
):
    """Re-check freshness and persist the DesignVersion in ONE transaction under
    the Design row lock.

    The provider call has already completed (no transaction/lock is held across
    it). Here the Design row is locked with ``select_for_update()``, every row
    a selected inspiration's snapshot could depend on is locked in one
    documented order, and — under those SAME locks — completion +
    inspiration-eligibility validation is re-run, the full input snapshot
    (base fingerprint, exact inspiration-context content and its hash) is
    rebuilt and compared, and the version is created and populated. Because
    the locks span the whole block, a concurrent draft mutation, asset
    retirement or rights change can only commit BEFORE this block — causing
    :class:`DesignChangedDuringGeneration` — or AFTER it commits; it can
    never slip between the freshness check and version creation.

    When ``attempt`` is supplied (Phase 10 async pipeline) the attempt's
    ``design_version`` link is written in this SAME transaction, so there is no
    crash window in which a DesignVersion exists but is not yet linked to the
    attempt that created it. The attempt must belong to the locked Design."""
    with transaction.atomic():
        locked = Design.objects.select_for_update().get(pk=design.pk)
        _lock_inspiration_rows_in_order(locked)
        fresh = _rebuild_input_snapshot_locked(locked)
        if design_completion_errors(locked) or fresh is None or fresh != input_snapshot:
            logger.warning("design spec discarded (inputs changed) design=%s", design.id)
            raise DesignChangedDuringGeneration(
                "the design changed during generation; no version was created"
            )
        version = create_next_design_version_locked(locked)
        version.design_spec = spec.model_dump(mode="json")
        version.design_spec_schema_version = DESIGN_SPEC_SCHEMA_VERSION
        version.design_spec_template_version = SPEC_TEMPLATE_VERSION
        version.design_spec_provider = usage.provider
        version.design_spec_model = usage.model
        version.design_spec_input_tokens = usage.input_tokens
        version.design_spec_output_tokens = usage.output_tokens
        version.design_spec_generated_at = timezone.now()
        # Frozen from the creating attempt (Phase 15) — never re-derived from
        # current settings, so a later settings change can never relabel an
        # already-generated version's historical demo/live identity.
        version.is_demo = attempt.is_demo if attempt is not None else False
        version.inspiration_context = fresh.inspiration_context.model_dump(mode="json")
        version.inspiration_context_schema_version = fresh.inspiration_context.schema_version
        version.inspiration_context_sha256 = fresh.inspiration_context_sha256
        version.save()
        if attempt is not None:
            if attempt.design_id != locked.pk:
                # Defensive: an attempt from a different design must never be
                # linked to this version. Nothing is persisted.
                raise DesignChangedDuringGeneration(
                    "the attempt does not belong to this design; no version was linked"
                )
            attempt.design_version = version
            # Clear the text-submission marker in the SAME transaction as the
            # version linkage: the submission window closed with a durable
            # outcome, and there is no crash window between the two writes.
            attempt.text_submission_in_flight = False
            attempt.save(
                update_fields=["design_version", "text_submission_in_flight", "updated_at"]
            )
    return version


def generate_design_spec_for_design(design, *, provider=None, attempt=None):
    """Generate, validate and persist one DesignVersion for ``design``.

    ``provider`` may be injected (fixtures/fakes in tests and the offline
    command); when omitted the gated live Anthropic provider is selected —
    only after every gate passes. ``attempt`` (Phase 10) links the created
    DesignVersion to a GenerationAttempt atomically. Raises DesignNotReady /
    GenerationLocked / GenerationRefused / GenerationFailed /
    StructuredDesignProviderError on failure, persisting nothing."""
    # Every pre-spend validation FIRST (before any provider selection/call).
    context = build_generation_context(design)
    # Snapshot the exact inputs the context was built from — base fingerprint
    # plus the exact inspiration-context content and hash already built by
    # build_generation_context above — to detect a concurrent draft edit or
    # inspiration/rights change while the (un-transacted) provider call runs.
    input_snapshot = _build_input_snapshot(design, context.inspiration_context)

    # The advisory lock is a SESSION-level lock, deliberately NOT a row lock or
    # an open transaction — no database transaction is held across the network
    # request.
    with advisory_lock(design.id):
        # Close the race: another holder may have generated between the
        # pre-check and acquiring the lock.
        if design.versions.exists():
            raise DesignNotReady(
                "already_generated", "This design already has a generated version."
            )
        selected = provider if provider is not None else get_structured_design_generation_provider()
        spec, usage, spec_attempts = _generate_valid_spec(
            selected, context, design.id, generation_attempt=attempt
        )

        # Freshness re-check AND persistence in ONE short transaction under the
        # Design row lock: re-run completion + inspiration-eligibility
        # validation, recompute and compare the input snapshot, and create the
        # version — all while the row is locked so no concurrent draft edit can
        # commit between the check and the write. Any change means the draft
        # moved on during the (un-transacted) call — persist nothing, never
        # touch the newer draft, and never retry the provider.
        version = _finalise_atomic(design, spec, usage, input_snapshot, attempt=attempt)
    logger.info(
        "design spec generated design=%s version=%s attempts=%s provider=%s",
        design.id,
        version.version_number,
        spec_attempts,
        usage.provider,
    )
    # Transient (not persisted) — the management command reports it. Deliberately
    # NOT "generation_attempts" (that is the GenerationAttempt reverse relation).
    version.spec_generation_attempts = spec_attempts
    return version
