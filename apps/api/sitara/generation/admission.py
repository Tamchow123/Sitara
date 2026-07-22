"""Live-generation admission controls (Phase 16, Part B, ADR 0017).

Applied by the generate/refine views AFTER ownership filtering (never before —
an inaccessible or foreign design must stay an indistinguishable 404, and a
throttle must never create a session/workspace for one). Demo generation
bypasses every live quota by construction. The authoritative cost reservation
still happens later, immediately before each provider call (Part A); this layer
adds request-rate throttling, a cheap budget preflight, and the exact
three-mode error semantics.

Nothing here stores a raw IP, session key, workspace UUID, user id or email —
throttle identifiers are HMAC-SHA256 hashed with SECRET_KEY (via
``accounts.rate_limits``), and the global count uses opaque per-(design, key)
reservation ids in the dedicated budget Redis database.
"""

from __future__ import annotations

import logging

from django.conf import settings

from sitara.accounts.rate_limits import client_ip, hash_identifier
from sitara.designs.models import DesignVersion

from . import cost_control

logger = logging.getLogger(__name__)

_THROTTLE_PREFIX = "genrl"  # distinct from the auth rate-limit namespace


class LiveGenerationDisabled(Exception):
    """Live mode is the resolved mode but LIVE_GENERATION_ENABLED is off. -> 503
    live_generation_disabled (deliberately NOT the generic generation_unavailable).

    A live-but-incomplete configuration (flag on, some secret/model/cost setting
    missing) is deliberately NOT handled here — it falls through to the enqueue
    gate's existing ``GenerationUnavailable`` -> 503 generation_unavailable, which
    never discloses which piece is absent."""


class GenerationLimitReached(Exception):
    """A per-session or per-IP request throttle, or the global daily count, was
    exceeded. -> 429 generation_limit_reached with a conservative Retry-After."""

    def __init__(self, retry_after: int):
        self.retry_after = int(retry_after)
        super().__init__("generation limit reached")


class LiveGenerationBudgetExhausted(Exception):
    """The cheap enqueue-time budget preflight found today's ceiling exhausted.
    -> 503 live_generation_budget_exhausted (UX optimisation; the authoritative
    reservation remains at the provider boundary)."""


class AdmissionControlUnavailable(Exception):
    """A throttle backend outage. -> controlled 503, never unthrottled live
    generation and never an unhandled exception."""


def attempt_is_demo(design, source_version_id=None) -> bool:
    """Resolve the SAME demo/live decision the enqueue transaction freezes, from
    the SAME input: a refinement inherits the mode of the specific
    ``source_version_id`` the client named; an initial generation inherits the
    resumable (first) version's mode, or ``DEMO_MODE`` for a brand-new design.
    Resolving from the named source version — rather than always the first
    version — keeps admission's mode gate aligned with enqueue even if a future
    change ever allows more than one existing version at refine time."""
    if source_version_id is not None:
        version = DesignVersion.objects.filter(design=design, pk=source_version_id).first()
    else:
        version = DesignVersion.objects.filter(design=design).order_by("version_number").first()
    return version.is_demo if version is not None else bool(settings.DEMO_MODE)


def enforce_live_admission(request, design, source_version_id=None) -> str:
    """Enforce live-generation admission for one owned design. Returns "demo"
    (bypassed) or "live" (admitted). Raises a controlled admission exception the
    view maps to the exact status/code. Ownership has ALREADY been checked.
    ``source_version_id`` is supplied for a refinement so the mode gate resolves
    from the same version the refinement enqueue will."""
    if attempt_is_demo(design, source_version_id):
        # Demo bypasses every live quota, throttle and budget check by
        # construction — it cannot spend money. Enqueue still checks demo pack
        # readiness separately.
        return "demo"

    if not settings.LIVE_GENERATION_ENABLED:
        raise LiveGenerationDisabled
    # A flag-on-but-config-incomplete state is left to the enqueue gate's
    # GenerationUnavailable (503 generation_unavailable), which never names the
    # missing piece — admission does not re-check provider readiness.

    _throttle(
        "session",
        _session_identifier(request),
        settings.LIVE_GENERATION_SESSION_LIMIT,
        settings.LIVE_GENERATION_SESSION_WINDOW_SECONDS,
    )
    _throttle(
        "ip",
        client_ip(request),
        settings.LIVE_GENERATION_IP_LIMIT,
        settings.LIVE_GENERATION_IP_WINDOW_SECONDS,
    )
    _budget_preflight()
    return "live"


def _session_identifier(request) -> str:
    # Ownership has passed, so the workspace-owning Django session exists. Fall
    # back to the client IP only if a session key is somehow unavailable — never
    # skip the throttle entirely.
    return request.session.session_key or client_ip(request)


def _throttle(scope: str, identifier: str, limit: int, window_seconds: int) -> None:
    from django.core.cache import cache

    key = f"{_THROTTLE_PREFIX}:{scope}:{hash_identifier(identifier)}"
    try:
        # Fixed window; both scopes are enforced. A zero limit rejects every
        # request (fail closed).
        cache.add(key, 0, timeout=window_seconds)
        current = cache.incr(key)
    except Exception:
        # A cache backend outage must fail closed, never allow unthrottled live
        # generation. Never echo the backend exception content.
        raise AdmissionControlUnavailable from None
    if current > int(limit):
        raise GenerationLimitReached(window_seconds)


def daily_count_retry_after() -> int:
    """A conservative Retry-After (seconds) for a global daily-count rejection:
    the time until the UTC day rolls over and the count resets. Bounded to at
    least one second."""
    from django.utils import timezone

    now = timezone.now()
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return max(int((end - now).total_seconds()), 1)


def _budget_preflight() -> None:
    if not cost_control.daily_budget_micro_usd():
        # No budget configured — the enqueue gate rejects this as
        # generation_unavailable; do not mislabel it as budget-exhausted here.
        return
    profile = cost_control.active_pricing_profile()
    # The cheapest possible first billable call sets the "obviously exhausted"
    # threshold: if not even that would fit, reject early (UX only).
    min_call = cost_control.anthropic_call_max_micro_usd(
        profile, settings.DESIGN_SPEC_MAX_OUTPUT_TOKENS
    )
    try:
        total = cost_control.day_budget_total_micro_usd()
    except cost_control.BudgetLedgerUnavailable:
        # Preflight is a UX optimisation, not the hard boundary — if the budget
        # ledger cannot be read here, let enqueue proceed; the authoritative
        # provider-time reservation still fails closed.
        logger.info("budget preflight skipped: ledger read unavailable")
        return
    if total + min_call > cost_control.daily_budget_micro_usd():
        raise LiveGenerationBudgetExhausted
