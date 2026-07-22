# 0017 — Live-generation security and cost controls

- **Status:** accepted
- **Date:** 2026-07-22
- **Deciders:** Sitara maintainers
- **Phase:** Phase 16 (see ../phases/PHASES.md)
- **Related:** ADR 0003 (session authentication), ADR 0004 (private design
  ownership), ADR 0009 (structured DesignSpec generation), ADR 0011
  (asynchronous generation pipeline), ADR 0012 (private design-image storage),
  ADR 0013 (generation progress and results), ADR 0015 (single-round
  constrained refinement), ADR 0016 (deterministic demo mode)

## Context

Before Phase 16, `LIVE_GENERATION_ENABLED` defaulted false and public live
generation was intentionally impossible: the roadmap forbade enabling it until
rate-limit and cost-ceiling safeguards existed. The paid pipeline could submit
Anthropic and Replicate calls with no per-worker spend ceiling, no abuse
throttle, no durable per-attempt cost accounting, and no reconciliation of
generations that die mid-flight. Django admin, security headers, and error
monitoring were also not yet production-hardened.

This phase builds the controls that must exist *before* public live generation
can ever be switched on — without switching it on. Demo mode (ADR 0016) is
zero-cost by construction and is deliberately untouched by every control here:
the demo branch never reaches the budget ledger, admission control, or paid
provider wrappers, and no setting in this ADR can make demo mode spend money.

The hard cost control is **not** a decorative HTTP-boundary counter. It executes
inside the asynchronous pipeline immediately before every potentially billable
provider submission, so a redelivered Celery task, a resumed attempt, or a
direct pipeline entry are all equally gated.

## Decision

### Integer micro-USD money, never binary float

All money is integer **micro-USD** (1 USD = 1,000,000 µUSD). No cost is ever
represented as a binary float, so reservations, reconciliation, and the daily
ceiling are exact and race-free. Cost estimates round **up** (`_ceil_div`) so a
reservation can never under-reserve.

### Pricing is operator-configured and unverified by default; the ledger fails closed

Provider prices are **not** verified against official sources in this phase. The
pricing inputs — `ANTHROPIC_INPUT_MICRO_USD_PER_MTOK`,
`ANTHROPIC_OUTPUT_MICRO_USD_PER_MTOK`, `REPLICATE_MAX_IMAGE_MICRO_USD` — default
to `0`, and `LIVE_GENERATION_DAILY_BUDGET_MICRO_USD` defaults to `0`. A named
`LIVE_GENERATION_PRICING_PROFILE` must be set for a config to be considered
valid (`live_cost_config_is_valid()`), which the public `/config` endpoint
requires before it will report live generation available. Any operator enabling
live mode must first set real, dated prices and a positive ceiling; until then
the system fails closed. Documenting a price here would imply verification that
did not happen — so no price is asserted.

### Atomic reserve-before-spend daily cost ceiling (Redis + Lua)

`sitara.generation.cost_control.RedisBudgetLedger` enforces the ceiling with
server-side Lua scripts so there is **no check-then-increment race** between
concurrent workers:

- **reserve** — computes a conservative maximum cost for the imminent provider
  stage, then atomically: reads the UTC-day running total, rejects (returns
  exhausted) if `total + amount` would exceed the ceiling, otherwise records the
  reservation under a deterministic per-attempt/stage key and adds `amount` to
  the day total. Reservation ids are deterministic
  (`reservation_id_for(attempt, stage, profile)`), so a redelivered task
  re-reserving the same stage is idempotent rather than double-charging.
- **reconcile** — after the call returns, atomically lowers the reservation from
  the conservative maximum to the estimated *actual* cost (from real token
  usage / a single image), returning the difference to the day total.
- **retain** — for an **ambiguous** outcome (the call may or may not have
  billed), the conservative reservation is **kept** (converted to a durable
  spend), never released. Ambiguity fails toward assuming spend.
- **release** — only on a proven **pre-spend** failure (no provider submission
  occurred) is the reservation removed and the day total decremented; release
  DELETEs the reservation key so a bounded retry can cleanly re-reserve.

The day key carries UTC-midnight boundaries and a TTL so stale days expire.
The exact production Redis key names are internal and are not reproduced here.

### Reservation / reconciliation state machine

```
reserve(max)  ── success ──▶ reconcile(actual)      (spend = actual)
      │
      ├─ ambiguous outcome ─▶ retain(max)            (spend = max, fail-safe)
      │
      └─ proven no submission ▶ release              (spend = 0)
```

`sitara.generation.cost_accounting` bridges ledger outcomes into durable
per-attempt `GenerationAttempt` audit columns (reserved/actual micro-USD and
token counts) inside a savepoint, so accounting failure can never corrupt or
roll back the generation itself. Every accounting entry self-guards on demo mode
(a demo attempt is a no-op at the ledger boundary), so demo can never touch the
budget.

### Global UTC daily count limit, ownership-first throttles

`sitara.generation.admission.enforce_live_admission` runs before any live
submission and applies, in order:

1. `LIVE_GENERATION_ENABLED` gate → `503 live_generation_disabled` when off.
2. Per-session and per-IP Redis throttles (`LIVE_GENERATION_SESSION_LIMIT` /
   `_IP_LIMIT` over their `_WINDOW_SECONDS`) → `429 generation_limit_reached`.
   IPs are HMAC-hashed (never stored/logged raw); ownership resolution happens
   first so throttling can never leak whether a foreign design exists.
3. A global UTC-day count limit (`LIVE_GENERATION_DAILY_COUNT_LIMIT`), reserved
   atomically alongside the count and compensated (released) if the enqueue
   transaction rolls back.
4. A budget preflight against the ceiling → `503
   live_generation_budget_exhausted` before any paid call is attempted.

A `0` limit means "unset / not admitting", consistent with the fail-closed
default posture.

### Retention purge and stuck-job reconciliation (Celery Beat)

Two idempotent maintenance tasks run on a Beat schedule
(`sitara.generation.maintenance`, wired in `config/celery.py`):

- **purge_expired_designs** — deletes designs older than
  `DESIGN_RETENTION_DAYS` (default 30). Candidate ids are collected, then each is
  purged under `transaction.atomic()` + `select_for_update()`; the design's
  permanent and Phase 10 staging storage objects are deleted first, and a
  storage-deletion failure aborts that one design (counted, retried next run)
  without deleting its row — objects are never orphaned by a half-purge. This
  also completes the Phase 10 staging-object retention that ADR 0011/0012
  deferred.
- **reconcile_stuck_generations** — moves attempts stuck in a running state past
  `GENERATION_STUCK_AFTER_SECONDS` (default 600) to `failed` with a controlled
  `generation_stuck` result, so a dead worker never leaves a design polling
  forever.

Both use bounded batch sizes (`DESIGN_PURGE_BATCH_SIZE` /
`GENERATION_STUCK_BATCH_SIZE`) and log only safe counters and row UUIDs.

### Redis persistence / no-eviction operational requirement

The budget ledger uses a dedicated Redis logical database
(`LIVE_GENERATION_BUDGET_REDIS_URL`, default DB 2 — separate from Celery's DB 0
and the auth cache's DB 1). Because reservation/day-total keys are the
authoritative spend record between reconciliations, that Redis instance **must**
be configured with persistence and a **non-eviction** policy
(`maxmemory-policy noeviction`) and must be a standalone (non-cluster) instance
so the multi-key Lua scripts are atomic. A Redis outage fails **closed**
(`BudgetLedgerUnavailable` → `503`), never open.

### Production security hardening

`config/middleware.ContentSecurityPolicyMiddleware` sets a strict CSP —
`API_CONTENT_SECURITY_POLICY` (`default-src 'none'; frame-ancestors 'none';
base-uri 'none'`) for API responses and a narrower admin-compatible
`ADMIN_CONTENT_SECURITY_POLICY` for `/admin/` — and never overwrites an existing
CSP. Standing headers: `SECURE_CONTENT_TYPE_NOSNIFF`,
`SECURE_REFERRER_POLICY="same-origin"`,
`SECURE_CROSS_ORIGIN_OPENER_POLICY="same-origin"`. Outside debug: HSTS
(`SECURE_HSTS_SECONDS` one year in production, subdomains included), SSL redirect,
and `SECURE_PROXY_SSL_HEADER` for the documented single trusted proxy. HSTS
**preload** stays an explicit operator opt-in (`SECURE_HSTS_PRELOAD`, default
false); Django's `security.W021` preload advisory is the only silenced deploy
check (`SILENCED_SYSTEM_CHECKS=["security.W021"]`) because preload is a
hard-to-reverse domain-wide commitment, never a casual default. The frontend
sets matching headers via `apps/web/src/lib/security-headers.ts` (no wildcard,
no `unsafe-eval` in production). The same-origin `/api/` transport keeps the
CORS surface minimal — no cross-origin allowance is added.

### Admin enablement policy

`ADMIN_ENABLED` (`DJANGO_ADMIN_ENABLED`, default **off in production**, on
elsewhere) conditionally mounts the Django admin URLconf. When disabled the
admin routes do not exist at all (a genuine 404), rather than being present but
protected.

### Correlation-aware structured logging and log redaction

`config/correlation.py` holds request/attempt correlation ids in `contextvars`.
`RequestCorrelationMiddleware` (outermost) accepts a client `X-Request-ID` only
when it is a canonical UUID (otherwise generates one), echoes it, and clears the
context after every request — success or exception — so ids never leak between
requests on a reused thread. Celery `task_prerun`/`task_postrun` bind and clear
the same context (the generation task id **is** the attempt UUID). `config/logging.py`'s
`JsonFormatter` emits only safe fields — timestamp, level, logger, message, and
the correlation ids — plus the exception **type** name, and **never** the
exception message or traceback body (which can carry secrets, user input,
storage keys, or provider data). `CELERY_WORKER_HIJACK_ROOT_LOGGER` and
`CELERY_WORKER_REDIRECT_STDOUTS` are set false so the worker keeps this logging
configuration instead of Celery's default traceback-printing root logger.

### Privacy-safe Sentry for Django and Next.js

Sentry (`config/sentry.py` backend `sentry-sdk`; `apps/web` `@sentry/nextjs`) is
**disabled entirely without a DSN** — the default — so tests and CI construct no
client and make no Sentry network call. When a DSN is set it is held to the same
secret-never-leaves-the-boundary rule as the log formatter: `send_default_pii`
off; request bodies, cookies, `Authorization`/`X-CSRFToken` headers, and
signed-URL query strings stripped; user identity dropped; tracing off; no session
replay. `include_local_variables=False` and an explicit
`LoggingIntegration(level=None, event_level=None)` ensure no log record becomes a
Sentry breadcrumb or event and no stack-frame locals are captured; `before_send`
(`scrub_event`) additionally reduces every exception to its type — blanking the
message and stripping frame locals — as defense in depth. The frontend
`scrubSentryEvent` mirrors this. Correlation ids are attached as tags.

### Controlled frontend error states

The frontend renders each new controlled error code
(`live_generation_disabled`, `generation_limit_reached`,
`live_generation_budget_exhausted`, `generation_stuck`) as a specific, honest
user-facing state — never an unhandled 5xx — and never surfaces the raw
refinement note or any internal reservation/key detail.

## Consequences

- Public live generation is now *technically gated but still off*: enabling it
  requires an operator to set `LIVE_GENERATION_ENABLED=true`, a named pricing
  profile with real dated prices, a positive daily ceiling, complete provider
  config, **and** a persistent no-eviction standalone Redis — several
  independent deliberate acts, none of which a stray credential satisfies.
- Demo mode is unchanged and remains zero-cost by construction; none of these
  controls apply to it.
- The manual budgeted live-generation checkpoint (one real, price-verified,
  ceiling-bounded provider call) remains **pending** and is out of scope here.
- Provider prices are unverified; the operator owns verifying and dating them
  before enabling live mode.
- A `BudgetWindow` table and a spend dashboard were deliberately **not** added
  (see Alternatives); ad-hoc `GenerationAttempt` queries cover audit needs.

## Alternatives considered

- **A `BudgetWindow`/spend database table instead of Redis + Lua** — rejected
  for this phase: the atomic reserve-before-spend ceiling needs a fast,
  race-free counter on the hot path, which Redis Lua provides without a
  write-contended row; durable per-attempt cost already lands in
  `GenerationAttempt`. Revisit only if the Redis mechanism proves insufficient.
- **A spend dashboard** — rejected as out of scope; ad-hoc GenerationAttempt
  queries suffice until a real operational need appears.
- **Releasing the reservation on ambiguous provider outcomes** — rejected:
  ambiguity must fail toward assuming spend (retain), never toward under-counting
  a possibly-billed call.
- **HTTP-boundary cost counting** — rejected: it would miss redelivered tasks,
  resumes, and direct pipeline entries; the ceiling must sit immediately before
  each billable submission inside the pipeline.
- **Enabling live mode in this phase** — rejected: the phase ships the controls
  with live mode still disabled and pricing left for operator configuration; a
  budgeted live checkpoint is a separate, deliberate act.
- **Present-but-authenticated admin in production** — rejected in favour of not
  mounting admin at all when disabled, reducing the production attack surface to
  a genuine 404.
- **A public "showcase" fallback when live generation is disabled/exhausted** —
  rejected: when live is off or the ceiling is hit the correct behaviour is an
  honest controlled error state, not a fabricated gallery. Demo mode (ADR 0016)
  already provides the honest zero-cost end-to-end journey, and its
  private-journey-only boundary (no public showcase/gallery) still holds. A
  budget-exhausted or disabled live request must never silently degrade into
  presenting stock imagery as if it were a fresh render.
