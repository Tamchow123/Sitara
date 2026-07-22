"""Atomic live-generation budget accounting (Phase 16, Part A, ADR 0017).

Integer micro-US-dollars only. A dedicated Redis logical database holds a
UTC-day budget ledger with an atomic Lua reserve / reconcile protocol so no two
workers can check-then-increment their way past the hard daily ceiling. The hard
control runs immediately BEFORE each potentially billable provider submission
inside the asynchronous pipeline — never a decorative counter at the HTTP edge.

Non-negotiables enforced here:

* Binary floating point is never used for pricing, reservations, reconciliation,
  totals or ceiling comparisons — integer arithmetic with conservative ceiling
  division throughout. Every amount crosses the Python boundary as a strict
  integer string; Redis's embedded Lua represents numbers as IEEE-754 doubles,
  but doubles are EXACT for integers up to 2^53 (~9 quadrillion micro-USD, i.e.
  billions of USD) — far above any configurable daily ceiling — so the in-script
  ceiling comparison is exact for every value this module can ever hold.
* Failure fails closed: any Redis error, timeout, malformed script output, or an
  inconsistent reservation identity means a live provider call must NOT proceed.
* Demo attempts never reach this module — the demo adapters neither import nor
  invoke any function here, and a demo attempt records zero live-provider cost.
* A reservation identity is deterministic (attempt UUID + stage + pricing
  profile) so a Celery redelivery or service retry reuses it and never creates a
  second reservation for the same logical provider call. The controlled Anthropic
  validation retry is a distinct logical billable call with its own reservation.
* No raw session keys, IP addresses, emails, prompts, questionnaire answers,
  refinement notes, provider credentials, image URLs or storage keys are stored
  in Redis — only opaque reservation ids and integer micro-USD amounts.

Redis operational requirement (see ADR 0017): the ledger runs against a
STANDALONE (non-cluster) Redis with persistence appropriate to the deployment,
no arbitrary eviction of budget keys, and exclusive control of its logical key
namespace / database. The reconcile script addresses the per-reservation day
total by name computed inside the script, which requires standalone Redis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost unit and key namespace
# ---------------------------------------------------------------------------
MICRO_USD_PER_USD = 1_000_000

# Every integer this module reserves, accumulates and reconciles is handled by a
# Redis Lua script, whose numbers are IEEE-754 doubles: only integers with
# magnitude below 2**53 stay exact. Prices and the daily ceiling are bounded
# below this so no reserved or day-total value can ever silently lose precision.
_MAX_ACCOUNTED_MICRO_USD = 2**53

# Exclusive namespace for every key this module writes. Nothing else in Sitara
# writes under this prefix, and it lives in its own logical Redis database.
_NAMESPACE = "sitara:livebudget"
_TOTAL_PREFIX = f"{_NAMESPACE}:total:"  # + YYYYMMDD  (per-UTC-day accounted total)
_RES_PREFIX = f"{_NAMESPACE}:res:"  # + reservation-id  (one reservation hash)

# Global daily accepted-attempt count (Phase 16 Part B) — same dedicated Redis
# database and atomic-scripting discipline as the cost ledger, deliberately NOT
# a generic quota framework.
_COUNT_TOTAL_PREFIX = f"{_NAMESPACE}:count:total:"  # + YYYYMMDD
_COUNT_RES_PREFIX = f"{_NAMESPACE}:count:res:"  # + reservation-id

# Bounded grace beyond the end of the UTC day so a reservation/total key always
# outlives the day it belongs to (allowing a late reconcile) but is never
# unbounded. Two days is generous for a minutes-long generation job.
_EXPIRY_GRACE_SECONDS = 2 * 24 * 60 * 60

# Deterministic reservation stages. Each names ONE logical billable provider
# call. Replicate polling, cancellation, downloading and local processing are
# NOT new submissions and never appear here.
STAGE_STRUCTURED_INITIAL = "structured_initial"
STAGE_STRUCTURED_RETRY = "structured_retry"
STAGE_STRUCTURED_REFINEMENT_INITIAL = "structured_refinement_initial"
STAGE_STRUCTURED_REFINEMENT_RETRY = "structured_refinement_retry"
STAGE_IMAGE_SUBMISSION = "image_submission"

_ALL_STAGES = frozenset(
    {
        STAGE_STRUCTURED_INITIAL,
        STAGE_STRUCTURED_RETRY,
        STAGE_STRUCTURED_REFINEMENT_INITIAL,
        STAGE_STRUCTURED_REFINEMENT_RETRY,
        STAGE_IMAGE_SUBMISSION,
    }
)


# ---------------------------------------------------------------------------
# Exceptions — all fail closed at the provider boundary
# ---------------------------------------------------------------------------
class BudgetLedgerUnavailable(Exception):
    """The ledger could not be reached or returned an unusable result. The
    caller MUST NOT invoke the provider (fail closed)."""


class BudgetExhausted(Exception):
    """Reserving the requested amount would exceed the hard daily ceiling. No
    mutation occurred; the provider MUST NOT be invoked."""


class CountLimitReached(Exception):
    """Admitting one more live attempt would exceed the global daily count. No
    mutation occurred."""


class BudgetLedgerInconsistent(BudgetLedgerUnavailable):
    """A replayed reservation carried a different amount or pricing profile than
    the stored one — treated as fail-closed, never silently reconciled."""


# ---------------------------------------------------------------------------
# Pricing profile
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PricingProfile:
    """Immutable snapshot of the operator-configured, versioned pricing profile.

    ``version`` stamps every reservation so a provider-model or price change
    (which MUST bump the version) can never silently continue accounting under
    stale, unreviewed prices. All prices are integer micro-USD.
    """

    version: str
    anthropic_input_micro_usd_per_mtok: int
    anthropic_output_micro_usd_per_mtok: int
    replicate_max_image_micro_usd: int

    @property
    def is_valid(self) -> bool:
        # A live-usable profile needs a non-empty version and a POSITIVE, bounded
        # price for every provider stage Sitara actually bills: Anthropic input
        # and output tokens, and the Replicate image call. A price of 0 is treated
        # as UNCONFIGURED, never "free" — Sitara uses no free provider, and a
        # zero price would let live generation run with zero-value reservations
        # that never consume the daily ceiling (a silent fail-open). So a missing
        # or non-positive price fails closed. Prices stay below 2**53 so every
        # reserved/accumulated Lua integer remains exact.
        return bool(self.version) and all(
            0 < value < _MAX_ACCOUNTED_MICRO_USD
            for value in (
                self.anthropic_input_micro_usd_per_mtok,
                self.anthropic_output_micro_usd_per_mtok,
                self.replicate_max_image_micro_usd,
            )
        )


def active_pricing_profile() -> PricingProfile:
    return PricingProfile(
        version=settings.LIVE_GENERATION_PRICING_PROFILE,
        anthropic_input_micro_usd_per_mtok=settings.ANTHROPIC_INPUT_MICRO_USD_PER_MTOK,
        anthropic_output_micro_usd_per_mtok=settings.ANTHROPIC_OUTPUT_MICRO_USD_PER_MTOK,
        replicate_max_image_micro_usd=settings.REPLICATE_MAX_IMAGE_MICRO_USD,
    )


def daily_budget_micro_usd() -> int:
    return int(settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD)


def live_cost_config_is_valid() -> bool:
    """Public live generation may only be considered available when a positive,
    bounded daily budget AND a valid pricing profile (positive bounded prices for
    every billable stage) are configured. Fails closed."""
    budget = daily_budget_micro_usd()
    return 0 < budget < _MAX_ACCOUNTED_MICRO_USD and active_pricing_profile().is_valid


# ---------------------------------------------------------------------------
# Conservative integer estimates (never under-reserve)
# ---------------------------------------------------------------------------
def _ceil_div(numerator: int, denominator: int) -> int:
    """Integer ceiling division. ``denominator`` is always positive here."""
    return -(-numerator // denominator)


def _token_cost_micro_usd(tokens: int, micro_usd_per_mtok: int) -> int:
    """Conservative (rounded-up) micro-USD cost for ``tokens`` at the given
    per-million-token rate. Pure integer arithmetic; rounds up so the estimate
    can never come out below the true fractional cost."""
    if tokens <= 0 or micro_usd_per_mtok <= 0:
        return 0
    return _ceil_div(tokens * micro_usd_per_mtok, MICRO_USD_PER_USD)


def anthropic_call_max_micro_usd(profile: PricingProfile, max_output_tokens: int) -> int:
    """A conservative maximum for one Anthropic request: the configured
    upper-bound input-token count at the input rate plus the request's maximum
    output tokens at the output rate, each rounded up. Flat per-call maximum,
    matching the flat max-output-tokens contract, so it never under-reserves as
    long as the operator-verified ANTHROPIC_MAX_INPUT_TOKENS covers the assembled
    request's real token count."""
    max_input_tokens = int(settings.ANTHROPIC_MAX_INPUT_TOKENS)
    input_cost = _token_cost_micro_usd(max_input_tokens, profile.anthropic_input_micro_usd_per_mtok)
    output_cost = _token_cost_micro_usd(
        max(int(max_output_tokens), 0), profile.anthropic_output_micro_usd_per_mtok
    )
    return input_cost + output_cost


def anthropic_actual_micro_usd(
    profile: PricingProfile, input_tokens: int | None, output_tokens: int | None
) -> int:
    """The measured estimated-actual cost from reported usage. Rounded up so a
    fractional actual can never be under-counted."""
    return _token_cost_micro_usd(
        max(int(input_tokens or 0), 0), profile.anthropic_input_micro_usd_per_mtok
    ) + _token_cost_micro_usd(
        max(int(output_tokens or 0), 0), profile.anthropic_output_micro_usd_per_mtok
    )


def replicate_call_max_micro_usd(profile: PricingProfile) -> int:
    """The configured conservative maximum for one Replicate image call. Replicate
    exposes no trustworthy per-call billing through the safe provider boundary, so
    this fixed maximum is retained as the estimated actual on success."""
    return max(int(profile.replicate_max_image_micro_usd), 0)


# ---------------------------------------------------------------------------
# Reservation identity
# ---------------------------------------------------------------------------
def reservation_id_for(attempt_id, stage: str, profile: PricingProfile) -> str:
    """Deterministic, durable, server-owned reservation identity. Derived ONLY
    from the attempt UUID, the stage and the pricing-profile version — never from
    prompt content or user text — so a redelivery reuses the exact same id."""
    if stage not in _ALL_STAGES:
        raise BudgetLedgerUnavailable(f"unknown reservation stage: {stage!r}")
    return f"{attempt_id}:{stage}:{profile.version}"


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReserveOutcome:
    status: str  # "reserved" (new) | "replayed" (idempotent)
    amount_micro_usd: int

    @property
    def newly_reserved(self) -> bool:
        return self.status == "reserved"


@dataclass(frozen=True)
class ReconcileOutcome:
    status: str  # "reconciled" | "retained" | "released" | "already" | "missing"
    estimated_micro_usd: int
    unresolved_micro_usd: int
    released_micro_usd: int

    @property
    def transitioned(self) -> bool:
        # A genuine first-time transition (safe to fold into durable DB totals).
        return self.status in {"reconciled", "retained", "released"}


# ---------------------------------------------------------------------------
# Redis Lua ledger
# ---------------------------------------------------------------------------
# All amounts pass as strings and are stored as strings so a replay comparison is
# exact. Totals are integer-incremented. Neither the total nor a reservation can
# go negative, and a rejected reservation performs NO mutation.
_RESERVE_LUA = """
local res = KEYS[1]
local total = KEYS[2]
local amount = tonumber(ARGV[1])
local profile = ARGV[2]
local day = ARGV[3]
local ceiling = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
if redis.call('EXISTS', res) == 1 then
  local stored_amount = redis.call('HGET', res, 'amount')
  local stored_profile = redis.call('HGET', res, 'profile')
  if stored_amount ~= ARGV[1] or stored_profile ~= profile then
    return {'inconsistent'}
  end
  return {'replayed', stored_amount}
end
local current = tonumber(redis.call('GET', total) or '0')
if current + amount > ceiling then
  return {'rejected', tostring(current)}
end
redis.call('HSET', res,
  'amount', ARGV[1], 'profile', profile, 'day', day,
  'state', 'reserved', 'estimated', '0', 'unresolved', '0')
redis.call('EXPIRE', res, ttl)
redis.call('INCRBY', total, amount)
redis.call('EXPIRE', total, ttl)
return {'reserved', ARGV[1]}
"""

# Reconcile computes the reservation's day-total key by name from the prefix and
# the stored day, so a reconcile after the day rolls over still adjusts the right
# total. Requires standalone (non-cluster) Redis (documented in ADR 0017).
_RECONCILE_LUA = """
local res = KEYS[1]
local mode = ARGV[1]
local profile = ARGV[2]
local actual = tonumber(ARGV[3])
local prefix = ARGV[4]
if redis.call('EXISTS', res) == 0 then
  return {'missing'}
end
if redis.call('HGET', res, 'profile') ~= profile then
  return {'inconsistent'}
end
local state = redis.call('HGET', res, 'state')
if state ~= 'reserved' then
  return {'already', state}
end
local reserved = tonumber(redis.call('HGET', res, 'amount'))
local day = redis.call('HGET', res, 'day')
local total = prefix .. day
if mode == 'release' then
  -- A release is ONLY invoked on a provably pre-spend failure, so it refunds the
  -- total AND deletes the reservation entirely. Deleting (rather than marking a
  -- terminal state) lets a bounded retry of the SAME deterministic stage cleanly
  -- re-reserve fresh budget instead of getting an idempotent no-op replay.
  local cur = tonumber(redis.call('GET', total) or '0')
  local dec = math.min(reserved, cur)
  if dec > 0 then redis.call('DECRBY', total, dec) end
  redis.call('DEL', res)
  return {'released', '0', '0', tostring(dec)}
elseif mode == 'retain' then
  redis.call('HSET', res, 'state', 'reconciled',
    'estimated', tostring(reserved), 'unresolved', tostring(reserved))
  return {'retained', tostring(reserved), tostring(reserved), '0'}
else
  if actual < 0 then actual = 0 end
  if actual > reserved then actual = reserved end
  local refund = reserved - actual
  local dec = 0
  if refund > 0 then
    local cur = tonumber(redis.call('GET', total) or '0')
    dec = math.min(refund, cur)
    if dec > 0 then redis.call('DECRBY', total, dec) end
  end
  redis.call('HSET', res, 'state', 'reconciled', 'estimated', tostring(actual), 'unresolved', '0')
  return {'reconciled', tostring(actual), '0', tostring(dec)}
end
"""


# Global daily accepted-attempt count. Reserve admits one more attempt only if
# the day total is below the limit; an idempotent replay (same reservation id)
# never increments; release decrements and deletes so a definite pre-provider
# failure returns the slot. Never negative.
_COUNT_RESERVE_LUA = """
local res = KEYS[1]
local total = KEYS[2]
local day = ARGV[1]
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
if redis.call('EXISTS', res) == 1 then
  return {'replayed'}
end
local current = tonumber(redis.call('GET', total) or '0')
if current + 1 > limit then
  return {'rejected', tostring(current)}
end
redis.call('SET', res, day)
redis.call('EXPIRE', res, ttl)
redis.call('INCRBY', total, 1)
redis.call('EXPIRE', total, ttl)
return {'reserved'}
"""

_COUNT_RELEASE_LUA = """
local res = KEYS[1]
local prefix = ARGV[1]
if redis.call('EXISTS', res) == 0 then
  return {'missing'}
end
local day = redis.call('GET', res)
local total = prefix .. day
local cur = tonumber(redis.call('GET', total) or '0')
if cur > 0 then redis.call('DECRBY', total, 1) end
redis.call('DEL', res)
return {'released'}
"""


def _utc_day() -> str:
    # Settings pin USE_TZ + UTC, so now() is UTC — the count and cost windows use
    # UTC calendar days by construction.
    return timezone.now().strftime("%Y%m%d")


def _expiry_seconds() -> int:
    now = timezone.now()
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    remaining = int((end_of_day - now).total_seconds())
    return max(remaining, 0) + _EXPIRY_GRACE_SECONDS


class RedisBudgetLedger:
    """The real ledger. Constructs its Redis client lazily and caches the two
    registered Lua scripts. Every operation is a single atomic script call."""

    def __init__(self, url: str, timeout_seconds: int):
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._client = None
        self._reserve_script = None
        self._reconcile_script = None
        self._count_reserve_script = None
        self._count_release_script = None

    def _connect(self):
        if self._client is None:
            import redis  # pinned redis==5.2.1

            self._client = redis.Redis.from_url(
                self._url,
                socket_timeout=self._timeout_seconds,
                socket_connect_timeout=self._timeout_seconds,
                decode_responses=True,
            )
            self._reserve_script = self._client.register_script(_RESERVE_LUA)
            self._reconcile_script = self._client.register_script(_RECONCILE_LUA)
            self._count_reserve_script = self._client.register_script(_COUNT_RESERVE_LUA)
            self._count_release_script = self._client.register_script(_COUNT_RELEASE_LUA)
        return self._client

    def reserve_count(self, reservation_id: str, limit: int) -> str:
        """Atomically admit one more live attempt for the current UTC day.
        Returns "reserved" (new), "replayed" (idempotent), and raises
        ``CountLimitReached`` when the limit would be exceeded (no mutation) or
        ``BudgetLedgerUnavailable`` on any ledger failure (fail closed)."""
        day = _utc_day()
        total_key = f"{_COUNT_TOTAL_PREFIX}{day}"
        res_key = f"{_COUNT_RES_PREFIX}{reservation_id}"
        try:
            self._connect()
            raw = self._count_reserve_script(
                keys=[res_key, total_key],
                args=[day, str(int(limit)), str(_expiry_seconds())],
            )
        except Exception as exc:
            raise BudgetLedgerUnavailable("count ledger unavailable") from exc
        status = _to_str(raw[0]) if isinstance(raw, list | tuple) and raw else ""
        if status == "rejected":
            raise CountLimitReached("daily live-generation count reached")
        if status in {"reserved", "replayed"}:
            return status
        raise BudgetLedgerUnavailable(f"unexpected count status: {status!r}")

    def release_count(self, reservation_id: str) -> str:
        """Return a previously reserved count slot (definite pre-provider
        failure). Idempotent; never drives the total negative. Ledger failure is
        raised so the caller can log it — the slot simply stays counted."""
        res_key = f"{_COUNT_RES_PREFIX}{reservation_id}"
        try:
            self._connect()
            raw = self._count_release_script(keys=[res_key], args=[_COUNT_TOTAL_PREFIX])
        except Exception as exc:
            raise BudgetLedgerUnavailable("count ledger unavailable") from exc
        return _to_str(raw[0]) if isinstance(raw, list | tuple) and raw else ""

    def read_day_budget_total(self) -> int:
        """Read the current UTC-day accounted budget total (a cheap, non-mutating
        read for the enqueue-time preflight only — never the hard boundary)."""
        total_key = f"{_TOTAL_PREFIX}{_utc_day()}"
        try:
            raw = self._connect().get(total_key)
        except Exception as exc:
            raise BudgetLedgerUnavailable("budget ledger unavailable") from exc
        return int(raw) if raw else 0

    def reserve(
        self, reservation_id: str, amount_micro_usd: int, profile: PricingProfile
    ) -> ReserveOutcome:
        amount = int(amount_micro_usd)
        if amount < 0:
            raise BudgetLedgerUnavailable("reservation amount must be non-negative")
        day = _utc_day()
        total_key = f"{_TOTAL_PREFIX}{day}"
        res_key = f"{_RES_PREFIX}{reservation_id}"
        try:
            self._connect()
            raw = self._reserve_script(
                keys=[res_key, total_key],
                args=[
                    str(amount),
                    profile.version,
                    day,
                    str(daily_budget_micro_usd()),
                    str(_expiry_seconds()),
                ],
            )
        except Exception as exc:  # redis errors, connection failures, script errors
            raise BudgetLedgerUnavailable("budget ledger unavailable") from exc
        return _parse_reserve(raw)

    def _reconcile(
        self, reservation_id: str, mode: str, actual_micro_usd: int, profile: PricingProfile
    ) -> ReconcileOutcome:
        res_key = f"{_RES_PREFIX}{reservation_id}"
        try:
            self._connect()
            raw = self._reconcile_script(
                keys=[res_key],
                args=[mode, profile.version, str(int(actual_micro_usd)), _TOTAL_PREFIX],
            )
        except Exception as exc:
            raise BudgetLedgerUnavailable("budget ledger unavailable") from exc
        return _parse_reconcile(raw)

    def reconcile_actual(self, reservation_id, actual_micro_usd, profile):
        return self._reconcile(reservation_id, "reconcile", actual_micro_usd, profile)

    def retain(self, reservation_id, profile):
        return self._reconcile(reservation_id, "retain", 0, profile)

    def release(self, reservation_id, profile):
        return self._reconcile(reservation_id, "release", 0, profile)


def _to_str(value) -> str:
    # decode_responses=True yields str, but be defensive about bytes too.
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _parse_reserve(raw) -> ReserveOutcome:
    if not isinstance(raw, list | tuple) or not raw:
        raise BudgetLedgerUnavailable("malformed reserve result")
    status = _to_str(raw[0])
    if status == "inconsistent":
        raise BudgetLedgerInconsistent("reservation identity mismatch")
    if status == "rejected":
        raise BudgetExhausted("daily live-generation budget exhausted")
    if status in {"reserved", "replayed"}:
        try:
            amount = int(_to_str(raw[1]))
        except (IndexError, ValueError) as exc:
            raise BudgetLedgerUnavailable("malformed reserve amount") from exc
        return ReserveOutcome(status=status, amount_micro_usd=amount)
    raise BudgetLedgerUnavailable(f"unexpected reserve status: {status!r}")


def _parse_reconcile(raw) -> ReconcileOutcome:
    if not isinstance(raw, list | tuple) or not raw:
        raise BudgetLedgerUnavailable("malformed reconcile result")
    status = _to_str(raw[0])
    if status == "inconsistent":
        raise BudgetLedgerInconsistent("reservation identity mismatch")
    if status == "missing":
        return ReconcileOutcome("missing", 0, 0, 0)
    if status == "already":
        return ReconcileOutcome("already", 0, 0, 0)
    if status in {"reconciled", "retained", "released"}:
        try:
            estimated = int(_to_str(raw[1]))
            unresolved = int(_to_str(raw[2]))
            released = int(_to_str(raw[3]))
        except (IndexError, ValueError) as exc:
            raise BudgetLedgerUnavailable("malformed reconcile amounts") from exc
        return ReconcileOutcome(status, estimated, unresolved, released)
    raise BudgetLedgerUnavailable(f"unexpected reconcile status: {status!r}")


# ---------------------------------------------------------------------------
# Ledger resolution / test injection seam
# ---------------------------------------------------------------------------
_ledger = None


def get_ledger():
    """Return the process-wide ledger, constructing the real Redis-backed one
    lazily. Tests install an in-memory ledger via ``set_ledger``."""
    global _ledger
    if _ledger is None:
        _ledger = RedisBudgetLedger(
            settings.LIVE_GENERATION_BUDGET_REDIS_URL,
            settings.LIVE_GENERATION_BUDGET_REDIS_TIMEOUT_SECONDS,
        )
    return _ledger


def set_ledger(ledger) -> None:
    """Install a ledger (real or in-memory fake). Used by tests only."""
    global _ledger
    _ledger = ledger


def reset_ledger() -> None:
    global _ledger
    _ledger = None


# ---------------------------------------------------------------------------
# Public boundary operations (thin wrappers over the resolved ledger)
# ---------------------------------------------------------------------------
def reserve(reservation_id: str, amount_micro_usd: int, profile: PricingProfile) -> ReserveOutcome:
    """Obtain or replay a deterministic reservation. Raises ``BudgetExhausted``
    if the ceiling would be exceeded (no mutation) and ``BudgetLedgerUnavailable``
    on any ledger failure — in both cases the provider MUST NOT be invoked.

    This is the AUTHORITATIVE provider-boundary guard: public admission validity
    (``generation_is_available()``) is only checked at enqueue time and a worker
    deliberately uses internal provider gates, so a job queued under a valid cost
    config could otherwise be processed after a deployment/config change left a
    blank/rotated profile, a zero price or an invalid ceiling — reserving 0 and
    then making the paid call. Re-validate the ceiling, the profile and a strictly
    positive amount HERE, immediately before the reservation, and fail closed
    (``BudgetLedgerUnavailable``) so no paid provider call can proceed under an
    invalid or zero-value cost configuration."""
    if not live_cost_config_is_valid() or not profile.is_valid or int(amount_micro_usd) <= 0:
        raise BudgetLedgerUnavailable("live cost configuration is not valid at reservation time")
    return get_ledger().reserve(reservation_id, amount_micro_usd, profile)


def reconcile_actual(
    reservation_id: str, actual_micro_usd: int, profile: PricingProfile
) -> ReconcileOutcome:
    return get_ledger().reconcile_actual(reservation_id, actual_micro_usd, profile)


def retain(reservation_id: str, profile: PricingProfile) -> ReconcileOutcome:
    return get_ledger().retain(reservation_id, profile)


def release(reservation_id: str, profile: PricingProfile) -> ReconcileOutcome:
    return get_ledger().release(reservation_id, profile)


def reserve_count(reservation_id: str) -> str:
    """Admit one more live attempt for today against the configured daily count
    limit. Raises ``CountLimitReached`` / ``BudgetLedgerUnavailable``."""
    return get_ledger().reserve_count(reservation_id, settings.LIVE_GENERATION_DAILY_COUNT_LIMIT)


def release_count(reservation_id: str) -> str:
    return get_ledger().release_count(reservation_id)


def count_reservation_id(design_id, idempotency_key) -> str:
    """Deterministic per-(design, idempotency-key) count reservation identity, so
    a replay or concurrent same-key request never reserves a second slot."""
    return f"{design_id}:{idempotency_key}"


def day_budget_total_micro_usd() -> int:
    return get_ledger().read_day_budget_total()
