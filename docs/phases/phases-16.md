/run-phase phase-16 Use this message as the binding Phase 16 requirements description.

# Sitara Phase 16 — Security and live-generation cost controls

Known `main` commit when this specification was written:

```
c2c53d55613553ad24a0486158a6cfe91c47edf5
```

The latest `main` must be a clean descendant of that commit and must contain the fully merged Phase 15 deterministic demo pipeline.

Before changing anything:

1. Run `git status --short`, `git log -15 --oneline`, and `git rev-parse HEAD`.
2. Confirm the working tree is clean.
3. Confirm Phases 1–15 are delivered and Phase 16 is still the next roadmap phase.
4. Report unexpected application-code commits or documentation conflicts before proceeding.
5. Do not work directly on `main`; follow the `/run-phase` branch, council-review, commit, push and draft-PR workflow.

## Main objective

Implement the controls required before public live generation can be enabled:

* precise `live_generation_disabled` behaviour;
* per-session and per-IP Redis-backed generation throttling;
* a global UTC daily live-generation count limit;
* an atomic hard daily live-generation cost ceiling;
* durable per-attempt token and estimated-cost accounting;
* safe handling of ambiguous provider-spend outcomes;
* design and staging-data retention;
* stuck-generation reconciliation;
* Celery Beat scheduling;
* production security hardening;
* locked-down Django admin behaviour;
* structured correlation-aware logging;
* privacy-safe Sentry integration for Django and Next.js;
* controlled frontend handling of all new error states.

Do not implement a decorative counter that only runs at the HTTP boundary. The hard cost control must execute immediately before every potentially billable provider submission inside the asynchronous live pipeline.

The complete live flow must become:

```
owned design or refinement request
          |
          v
idempotency and pre-spend validation
          |
          v
live-mode admission controls
  - session throttle
  - IP throttle
  - global daily count reservation
          |
          v
queued durable GenerationAttempt
          |
          v
before each billable provider call:
  - calculate conservative maximum cost
  - atomically reserve daily budget in Redis
  - persist/retain existing submission marker semantics
  - invoke provider only after reservation succeeds
          |
          v
reconcile reservation:
  - actual estimated cost when safely measurable
  - release only when definitely no spend occurred
  - retain conservative reservation when acceptance or billing is ambiguous
          |
          v
normal prompt, image, staging, ingest, result and refinement flow
```

Demo generation must bypass all live quotas and budget accounting and continue to make zero paid calls by construction.

## Safety mode throughout this phase

During implementation, testing, council review, CI and all manual checkpoints keep:

```
DEMO_MODE=true
ALLOW_PAID_AI_CALLS=false
LIVE_GENERATION_ENABLED=false
```

Use no real Anthropic or Replicate credentials.

Do not perform a paid live-generation checkpoint as part of this phase. A real checkpoint requires separate explicit user authorisation, a stated maximum budget and freshly verified provider pricing.

Tests must prove that opening flags and providing non-empty test credentials still produces no provider network request unless the test has deliberately injected a fake provider behind the controlled test boundary.

Never run:

```
docker compose down --volumes
```

Do not delete or reset development volumes.

## Read first

Read the current versions of:

* `CLAUDE.md`
* `.claude/phase-council.json`
* `.claude/review/README.md`
* `README.md`
* `.env.example`
* `compose.yaml`
* `.github/workflows/ci.yml`
* `docs/PROPOSAL.md`
* `docs/phases/PHASES.md`
* `docs/phases/phases-10.md`
* `docs/phases/phases-11.md`
* `docs/phases/phases-12.md`
* `docs/phases/phases-13.md`
* `docs/phases/phases-14.md`
* `docs/phases/phases-15.md`
* `docs/decisions/0002-application-foundation.md`
* `docs/decisions/0003-session-authentication.md`
* `docs/decisions/0004-private-design-ownership.md`
* `docs/decisions/0011-asynchronous-generation-pipeline.md`
* `docs/decisions/0012-private-design-image-storage.md`
* `docs/decisions/0013-generation-progress-and-results.md`
* `docs/decisions/0014-inspiration-metadata-influence.md`
* `docs/decisions/0015-single-round-refinement.md`
* `docs/decisions/0016-deterministic-demo-mode.md`
* `apps/api/config/settings.py`
* `apps/api/config/urls.py`
* `apps/api/sitara/accounts/rate_limits.py`
* `apps/api/sitara/accounts/views.py`
* `apps/api/sitara/ai_gateway/policy.py`
* `apps/api/sitara/ai_gateway/structured_design.py`
* `apps/api/sitara/ai_gateway/image_generation.py`
* concrete Anthropic and Replicate provider wrappers
* `apps/api/sitara/designs/models.py`
* `apps/api/sitara/designs/views.py`
* `apps/api/sitara/designs/jobs.py`
* `apps/api/sitara/designs/openapi.py`
* `apps/api/sitara/designs/admin.py`
* `apps/api/sitara/designs/ownership.py`
* `apps/api/sitara/generation/pipeline.py`
* `apps/api/sitara/generation/services.py`
* `apps/api/sitara/generation/refinement_service.py`
* `apps/api/sitara/generation/tasks.py`
* `apps/api/sitara/generation/errors.py`
* `apps/api/sitara/media/ingest.py`
* `apps/api/sitara/media/delivery.py`
* `apps/web/next.config.*`
* `apps/web/src/middleware.*` or the current middleware location
* `apps/web/src/lib/api.ts`
* the questionnaire review and generation-submission components
* the refinement submission components
* generation progress/error mapping
* the public configuration endpoint and generated OpenAPI types.

Use the current repository layout. Do not reintroduce `backend/`, `frontend/`, `docker-compose.yml`, old provider names, local phase-agent copies or duplicated application pipelines.

## Required commit boundaries

Implement as six independently reviewed commits:

1. `feat(cost-control): add atomic live-generation budget accounting`
2. `feat(generation): add live admission limits and controlled error responses`
3. `feat(maintenance): add retention purge and stuck-job reconciliation`
4. `feat(security): harden production headers, cookies, CSP, and admin`
5. `feat(observability): add structured correlation logging and Sentry`
6. `docs(phase-16): record live-generation security and cost controls`

Frontend changes required for controlled generation-limit UX may be included in commit 2. Do not create an extra frontend-only commit unless the implementation is too large to review coherently.

Do not combine these commits. Each commit must pass focused tests and the per-commit council before moving to the next.

# Part A — Atomic live-generation cost accounting

## 1. Cost unit and arithmetic

Use integer micro-US dollars throughout accounting:

```
1 USD = 1_000_000 micro-USD
```

Never use binary floating-point arithmetic for:

* provider pricing;
* budget reservations;
* reconciliation;
* totals;
* comparisons with the ceiling.

Use integer arithmetic and conservative ceiling division. `Decimal` may be used only at tightly controlled parsing or presentation boundaries; values stored in Redis and the database must be integers.

No costs or remaining-budget values are exposed through public APIs.

## 2. Configuration

Add strict environment-driven settings equivalent to:

* `LIVE_GENERATION_DAILY_BUDGET_MICRO_USD`
* `LIVE_GENERATION_DAILY_COUNT_LIMIT`
* per-session generation limit and window;
* per-IP generation limit and window;
* Anthropic input price per million tokens in micro-USD;
* Anthropic output price per million tokens in micro-USD;
* a conservative Replicate maximum image-call cost in micro-USD;
* a versioned pricing-profile identifier;
* a dedicated Redis URL or logical database for generation limits and budget accounting;
* retention and maintenance settings specified later.

Names may be refined for consistency, but keep them explicit and narrowly scoped.

Requirements:

* invalid integers refuse startup without echoing the supplied value;
* negative values are never accepted;
* the daily live budget defaults to zero or otherwise fails closed;
* public live generation cannot be considered available unless the budget ceiling and pricing profile are valid;
* demo availability must not depend on live cost configuration;
* production configuration must not silently inherit unsafe development values;
* pricing remains operator configuration, not a hard-coded consequence of a provider model identifier;
* changing a provider model must not silently continue under an unreviewed pricing profile;
* `.env.example` contains placeholders and explanations only;
* no pricing values are exposed through `/api/v1/config/public/`.

Use UTC calendar days for the count and cost windows because the backend timezone is UTC.

## 3. Redis budget ledger

Create a focused module such as:

```
apps/api/sitara/generation/cost_control.py
```

Do not create a new service, microservice, generic ledger framework or `BudgetWindow` database table.

Use the direct pinned Redis client already present in the repository. Do not reach through undocumented Django cache internals.

Implement an atomic Lua script, or an equivalently strong single Redis transaction, that:

1. accepts a deterministic reservation identifier;
2. returns the existing reservation unchanged when replayed;
3. reads the current UTC-day accounted total;
4. rejects without mutation when adding the requested reservation would exceed the ceiling;
5. otherwise records the reservation and increments the day total atomically;
6. sets bounded expiries extending beyond the end of the UTC day;
7. never performs a check-then-increment sequence across separate client calls.

Implement atomic reconciliation operations that:

* lower a reservation to a measured estimated actual cost;
* return the unused amount to the available daily ceiling;
* release the entire reservation only for a definitely pre-spend failure;
* leave the full conservative reservation accounted when acceptance or billing is ambiguous;
* are idempotent under Celery redelivery;
* never allow a reservation or the daily total to become negative;
* never increase a reconciled reservation accidentally;
* refuse inconsistent reservation identifiers, amounts or pricing profiles.

The budget Redis connection failing, timing out, evicting required state or returning malformed script output must fail closed. A live provider call must not proceed.

Use a dedicated key namespace and store no:

* raw session keys;
* raw IP addresses;
* emails;
* prompts;
* questionnaire answers;
* refinement notes;
* provider credentials;
* image URLs;
* storage keys.

Document the production Redis requirements for this ledger: persistence appropriate to the deployment, no arbitrary eviction of budget keys and exclusive control of its logical key namespace. Do not pretend Redis durability properties are stronger than the configured deployment.

If the required hard semantics genuinely cannot be met using the roadmap-mandated Redis mechanism, stop in `BLOCKED` with evidence rather than silently weakening the ceiling or introducing a database budget table without approval.

## 4. Deterministic provider-call reservation identifiers

A billable call must have a stable reservation identity derived from durable server-owned state, for example:

* attempt UUID;
* stage (`structured_initial`, `structured_retry`, `structured_refinement_initial`, `structured_refinement_retry`, `image_submission`);
* pricing-profile version.

Do not derive reservation identity from raw prompt content or user text.

A Celery redelivery or service retry must discover and reuse the same reservation. It must never create a second reservation for the same logical provider call.

Anthropic’s controlled validation retry is a distinct logical billable call and therefore needs its own reservation.

Replicate polling, cancellation checks, downloading and local image processing are not new provider submissions and must not create extra reservations.

## 5. Conservative maximum estimates

Before each Anthropic request, calculate a conservative maximum using:

* the configured input-token rate;
* a defensible upper bound for that exact assembled request;
* the configured maximum output tokens;
* the output-token rate;
* conservative upward rounding.

Do not assume the number of UTF-8 characters equals the exact token count for actual billing. It may be used only as a deliberately conservative upper bound if documented and proven never to under-reserve within Sitara’s bounded request contract.

Before each Replicate prediction creation, reserve the configured conservative maximum image-call amount.

When a provider does not expose trustworthy actual billing information through the existing safe provider boundary, retain the configured conservative amount as the estimated actual. Do not scrape provider dashboards or parse undocumented response fields.

## 6. Provider-boundary integration

The required ordering for a new live provider submission is:

1. finish deterministic input validation and all other pre-spend checks;
2. obtain or replay the deterministic cost reservation;
3. persist the existing durable submission-in-flight marker;
4. invoke the provider;
5. reconcile only once the outcome is safely classified.

Preserve the existing rules:

* no database transaction or row lock remains held during provider network I/O;
* `text_submission_in_flight` is persisted before an Anthropic request;
* `image_submission_in_flight` is persisted before Replicate prediction creation;
* an accepted image prediction ID is never resubmitted;
* an ambiguous text or image submission is never automatically repeated;
* demo attempts never enter this module.

Outcome handling:

* **Successful Anthropic response with valid usage:** reconcile using reported input/output tokens and persist the safe token totals.
* **Successful response with missing or invalid usage metadata:** retain the conservative reservation.
* **Schema-invalid but provider-completed Anthropic output:** the call may still be billable; reconcile using valid usage when available, otherwise retain the reservation. Its controlled second attempt gets a separate reservation.
* **Definitively pre-request failure:** release the reservation and clear the matching submission marker atomically with the existing state transition where applicable.
* **Definitive provider response after submission:** treat the request as potentially billable and retain/reconcile appropriately.
* **Ambiguous transport failure or crash window:** retain the complete reservation and preserve unresolved-spend evidence.
* **Accepted Replicate prediction:** never release its reservation merely because later polling, downloading, staging or ingest fails.
* **Demo provider call:** no reservation function may be imported or invoked by the demo adapters.

Do not weaken the existing conservative unresolved-spend rules.

## 7. Persistent accounting

Persist enough private accounting on `GenerationAttempt` for:

* ad-hoc totals;
* admin inspection;
* incident reconciliation;
* proving whether cost is reconciled or unresolved;
* recording the pricing-profile version;
* recording safe token counts where available.

Use the smallest clear durable schema. Prefer explicit integer columns and a tightly bounded status over a loose arbitrary JSON event log. A small structured breakdown is acceptable only when strict validation, immutability rules and queryability are preserved.

At minimum, make it possible to determine:

* total maximum amount reserved for the attempt;
* total estimated amount accounted as spent;
* amount still unresolved, if any;
* whether accounting is complete;
* pricing-profile version;
* accumulated safe input/output token counts where available.

Requirements:

* non-negative database constraints;
* reconciled/estimated values cannot exceed reserved values unless an explicit, tested corrective path exists;
* demo attempts have zero live-provider cost;
* accounting fields are private and never included in job, design, result, image, public-config or OpenAPI response schemas;
* admin fields are read-only where mutation would corrupt audit history;
* no provider keys, prompts, outputs or URLs are stored.

# Part B — Live admission controls

## 8. Exact generation-mode errors

Preserve the existing three-mode generation policy:

* demo;
* live;
* unavailable.

When `DEMO_MODE=true`, demo retains absolute precedence. An unavailable demo pack must never fall back to live generation.

When `DEMO_MODE=false` and `LIVE_GENERATION_ENABLED=false`, both initial generation and refinement must return:

```
HTTP 503
error.code = "live_generation_disabled"
```

Do not collapse this into the existing generic `generation_unavailable`.

When `LIVE_GENERATION_ENABLED=true` but another required live configuration is incomplete, return the existing safe unavailable response or a narrowly documented replacement. Do not expose which secret, model or credential is absent.

Update OpenAPI and generated frontend types normally; never hand-edit generated files.

## 9. Session and IP throttling

Apply generation throttles to:

* `POST /api/v1/designs/{id}/generate/`
* `POST /api/v1/designs/{id}/refine/`

Use Redis and HMAC-SHA256 identifiers based on `SECRET_KEY`.

Never store raw:

* IP addresses;
* Django session keys;
* workspace UUIDs;
* user IDs;
* emails.

Continue trusting only `REMOTE_ADDR`. Do not begin trusting `X-Forwarded-For` or arbitrary proxy headers in this phase.

Both session and IP limits must be enforced. Exceeding either returns:

```
HTTP 429
error.code = "generation_limit_reached"
```

Include a conservative `Retry-After` header.

A Redis outage must not allow unthrottled live generation. It must produce a controlled 503 response, not an unhandled exception.

Important ownership ordering:

* inaccessible and nonexistent designs must remain indistinguishable 404s;
* default DRF throttle execution happens before the view handler, so do not attach a throttle in a way that lets a foreign design return 429 before ownership is checked;
* perform ownership filtering first, then apply the generation-specific throttle through a focused DRF-compatible mechanism or explicitly invoked admission service;
* do not create a session or workspace merely to throttle an inaccessible GET or foreign UUID.

Idempotency:

* an exact replay of an already-created attempt must return that attempt and must not consume another global daily generation slot;
* request-rate throttles may count HTTP attempts, but the global accepted-generation count must count only one logical attempt;
* concurrent requests using the same design and idempotency key must not reserve two count slots.

## 10. Global daily live-generation count

Implement an atomic global count for newly accepted live attempts per UTC day.

Requirements:

* demo attempts never count;
* idempotent replays do not increment;
* concurrent admission cannot exceed the configured daily count;
* count reservation must be reversible when attempt creation or queue submission definitely fails before any provider work can occur;
* it must not be released for an attempt that may already have entered provider processing;
* Redis failure fails closed;
* no count or remaining allowance is exposed publicly.

Use the same disciplined Redis namespace and atomic scripting approach as the cost ledger where practical, without turning the module into a generic quota framework.

## 11. API and frontend behaviour

Both initial generation and refinement must handle:

* `live_generation_disabled`;
* `generation_limit_reached`;
* `live_generation_budget_exhausted`;
* temporary admission-control unavailability;
* the existing generation/refinement conflict errors.

Do not add a public showcase gallery or gallery fallback. Phase 15 deliberately removed that scope.

The frontend must:

* show clear non-technical copy;
* avoid automatic retry loops for quota or budget exhaustion;
* preserve the user’s private draft/result;
* continue allowing normal navigation;
* respect `Retry-After` where supplied;
* use the existing same-origin, CSRF-aware typed API client;
* add no Axios, Redux, browser storage or alternate API client;
* handle the same controls for refinement;
* never display internal budget amounts, pricing rates, Redis state or provider configuration.

## 12. Budget exhaustion at enqueue versus provider time

An inexpensive budget-availability preflight may reject an obviously exhausted day at the enqueue boundary and return:

```
HTTP 503
error.code = "live_generation_budget_exhausted"
```

However, this preflight is only user-experience optimisation. It is not the hard security boundary.

The authoritative reservation remains immediately before each live provider submission. A queued job must still fail safely if another worker consumes the remaining budget before it reaches its provider stage.

Add a stable, user-friendly asynchronous job failure mapping for provider-time budget exhaustion. Do not surface a raw Redis or cost-control exception.

# Part C — Retention and stuck-job maintenance

## 13. Celery Beat

Add Celery Beat using the existing Celery application.

Add explicit periodic schedules for:

* stuck-generation reconciliation;
* expired-design purge.

Add a `celery-beat` service to local Compose if the current structure requires one.

Do not add `django-celery-beat` or database-managed schedules unless current code proves static settings are insufficient.

Tasks must be idempotent, bounded and safe under duplicate Beat delivery.

## 14. Design retention purge

Add a strict positive setting equivalent to:

```
DESIGN_RETENTION_DAYS=30
```

Implement a bounded batch purge for designs older than the configured retention period.

For each purged design, remove:

* permanent original and thumbnail objects through the `design_images` storage alias;
* generation-staging objects through the correct staging storage;
* corresponding database rows through normal cascades.

Never delete:

* catalogue assets;
* rights evidence;
* shared demo source-pack objects;
* unrelated object prefixes;
* Phase 2 evaluation evidence.

Storage/database deletion cannot be one atomic transaction. Use an idempotent order that fails safely:

* if object deletion fails, retain the database row for retry;
* if object deletion succeeds and database deletion later fails, a retry must tolerate missing objects;
* never log object keys;
* log only safe design/attempt UUIDs, counts and exception types.

Skip genuinely in-progress attempts. The stuck-job reconciler should resolve stale work before retention removes it.

Use bounded batches so one run cannot monopolise the worker.

## 15. Staging cleanup

Phase 10/11 staging objects were intentionally retained for crash recovery.

After an attempt is terminal and the recovery/grace conditions are satisfied, Phase 16 must eventually remove its raw staging object and clear or remove the corresponding retained metadata safely.

Do not remove staging data for:

* an in-progress job;
* a job whose output may still be required for resume;
* unresolved ambiguous spend/output cases unless the maintenance policy explicitly preserves all evidence required for operator reconciliation.

If design-level 30-day purge alone is chosen as the cleanup boundary, document that clearly and prove it deletes the staging object. Do not leave staging objects permanently orphaned.

## 16. Stuck-generation reconciler

The roadmap threshold is ten minutes. Add a configurable setting with a default equivalent to:

```
GENERATION_STUCK_AFTER_SECONDS=600
```

Inspect attempts in:

* `queued`;
* `running_text`;
* `running_image`.

Use `updated_at` and timezone-aware UTC comparisons.

Avoid racing a genuinely active worker:

* use the same attempt-level PostgreSQL advisory lock as the generation pipeline, or extract a focused shared helper;
* if the lock is held by an active task, skip that attempt;
* use short transactions and row locks for the terminal state change;
* process bounded batches.

When an attempt is genuinely stale:

* mark it failed with a stable safe error code;
* set `completed_at`;
* move the Design to the correct failed state;
* preserve any accepted prediction ID, submission marker and unresolved-spend evidence;
* retain the conservative cost reservation when spend may have occurred;
* release a cost reservation only when durable state proves no provider call could have occurred;
* never automatically enqueue replacement paid work.

Do not turn all old jobs into failed records without coordinating with the pipeline lock.

# Part D — Security hardening

## 17. Django production settings

Complete a focused Django hardening pass.

Preserve and test:

* `DEBUG=false` in production;
* `SESSION_COOKIE_HTTPONLY=true`;
* secure session and CSRF cookies outside debug;
* `SameSite=Lax`;
* JSON CSRF failures;
* `X_FRAME_OPTIONS=DENY`;
* content-type nosniff;
* minimal explicit CORS/CSRF origin lists;
* no wildcard origins;
* no trusted proxy headers unless explicitly configured.

Add or validate appropriate production settings for:

* HTTPS redirect;
* HSTS;
* HSTS subdomains where justified;
* secure referrer policy;
* cross-origin opener policy;
* cookie paths and domains;
* request-size limits where relevant;
* safe host validation.

Do not enable HSTS preload casually. Do not trust arbitrary proxy headers. Production-like `manage.py check --deploy` must pass without weakening checks.

## 18. Content Security Policy

Add a deliberate CSP for both surfaces:

### Django API

JSON API responses can use a restrictive policy such as:

```
default-src 'none';
frame-ancestors 'none';
base-uri 'none';
```

Ensure admin receives a policy compatible with the Django admin only when the admin is enabled.

### Next.js frontend

Add a production CSP compatible with the current App Router implementation and private signed image delivery.

Requirements:

* no wildcard `*`;
* no `unsafe-eval` in production;
* exact `connect-src 'self'` for application requests;
* exact frame restrictions;
* restricted `base-uri`, `form-action` and `object-src`;
* image sources must use a validated, server-side configured allowlist suitable for the signed storage origin;
* development-only allowances must not leak into production;
* never expose storage credentials or internal Django hosts;
* add tests for the production header.

Use the smallest design that works with the current Next.js rendering architecture. Do not introduce a second proxy merely to avoid configuring CSP.

## 19. Admin lockdown

Django admin must remain staff-only and must not become a public application surface.

Add an explicit environment-controlled admin policy:

* production admin disabled by default unless deliberately enabled;
* when disabled, the admin route is not mounted;
* enabling it never bypasses Django staff/superuser checks;
* no secret values, raw provider payloads, prompts, storage keys, raw IPs or session keys appear in admin list displays;
* cost fields are private and read-only;
* existing catalogue approval and rights workflows continue to function when admin is enabled.

Changing the URL alone is not considered the security control. Do not build a custom admin authentication system.

# Part E — Observability

## 20. Request and job correlation

Implement bounded correlation identifiers with `contextvars` or an equivalently safe request-local mechanism.

For HTTP:

* generate a server-owned request UUID;
* optionally accept a client `X-Request-ID` only when it is a valid canonical UUID;
* replace malformed or oversized values;
* return the effective ID as `X-Request-ID`;
* clear context after every response and exception.

For Celery:

* set the attempt/job UUID as correlation context at task entry;
* generate or propagate a task request ID safely;
* clear context in `finally`.

Do not log raw Django session keys. A session correlation value, if required, must be HMAC-hashed and must never be exposed publicly.

## 21. Structured logging

Add a small standard-library JSON formatter for production. Development may retain readable console logs.

Structured records should support safe fields such as:

* timestamp;
* level;
* logger;
* event/message;
* request ID;
* generation-attempt/job ID;
* design UUID where already safe and operationally necessary;
* exception type at controlled boundaries.

Never include:

* API keys;
* cookies;
* session keys;
* emails;
* raw IPs;
* questionnaire answers;
* refinement notes;
* prompts;
* DesignSpec narrative;
* provider request/response bodies;
* output URLs;
* signed URLs;
* storage keys;
* S3/MinIO credentials;
* raw exception text where it can contain sensitive values.

Preserve the repository rule that sensitive boundary logs record exception type, not arbitrary exception content.

## 22. Sentry

Add privacy-safe Sentry integration for Django and Next.js, using pinned versions compatible with the repository’s existing Python, Django, Node, React and Next.js versions.

Do not opportunistically upgrade unrelated dependencies.

Backend requirements:

* disabled when no DSN is configured;
* `send_default_pii=false`;
* request bodies disabled;
* cookies and authorisation/CSRF headers stripped;
* safe environment and release tags;
* request ID and attempt ID added as tags when available;
* no prompt, answer, storage or provider payload capture;
* tests make no Sentry network request.

Frontend requirements:

* disabled when no DSN is configured;
* no session replay;
* no user email or account identity;
* zero tracing sample rate by default unless explicitly configured later;
* no request body, cookie or signed-image URL leakage;
* query strings stripped where they may contain signed URL parameters;
* no source-map upload requiring a real token during ordinary local builds or CI;
* production build continues to succeed without Sentry credentials.

Add only the minimum Sentry files required by the current framework integration. Do not let an installer rewrite unrelated configuration without review.

# Non-goals

Do not implement:

* a spend dashboard;
* public quota or remaining-budget endpoints;
* a `BudgetWindow` database table unless Redis is demonstrated insufficient and the phase is blocked for approval;
* payments or subscriptions;
* public galleries or showcase endpoints;
* a demo-only API;
* a second generation state machine;
* a second frontend API client;
* user uploads;
* reference-image conditioning;
* image-to-image refinement;
* multiple refinements;
* a different Anthropic or FLUX model;
* live-provider calls during tests or implementation;
* automatic provider-pricing scraping;
* automatic enabling of live generation;
* deployment or a full production runbook, which remain Phase 18;
* a general-purpose observability platform;
* a generic quota framework;
* trusted `X-Forwarded-For` handling;
* OAuth, JWT or browser-stored authentication tokens;
* deletion of rights records or frozen evaluation evidence.

# Required automated tests

## Cost-control tests

Add tests proving:

1. Integer arithmetic never under-reserves.
2. Invalid, negative or missing critical live-cost settings fail closed.
3. Budget keys use UTC-day windows and bounded expiry.
4. Reservation replay is idempotent.
5. Reconciliation is idempotent.
6. Release cannot make totals negative.
7. Missing provider usage retains the conservative reservation.
8. Definitive pre-spend failure releases the reservation.
9. Ambiguous submission retains the full reservation.
10. An accepted image prediction retains/account for cost even if polling, downloading, staging or ingest later fails.
11. Anthropic validation retry uses a distinct reservation but cannot reserve twice for the same retry.
12. Demo attempts create no budget keys and record zero live cost.
13. Redis outage prevents provider invocation.
14. No public serializer or OpenAPI schema contains private cost fields.

Run a real Redis concurrency test:

* choose `N`;
* configure a ceiling that fits exactly `N-1` equal reservations;
* issue `N` parallel reservation attempts;
* assert exactly `N-1` succeed;
* assert the Redis total equals exactly `(N-1) × reservation`;
* assert no provider double is invoked for the rejected attempt.

Do not substitute a mocked lock or sequential test for this concurrency proof.

## Admission-control tests

Prove:

* session limit produces 429 and `generation_limit_reached`;
* IP limit produces the same stable response;
* `Retry-After` exists and is bounded;
* global daily count cannot exceed its configured limit under concurrency;
* idempotent replay consumes no additional daily slot;
* queue failure releases only a definitely unused count reservation;
* demo generation and demo refinement consume no live count;
* raw IP and session values do not appear in Redis keys;
* Redis failure is a controlled 503;
* a foreign or nonexistent design remains 404 even while the caller is throttled or the budget is exhausted;
* initial generation and refinement behave consistently;
* `DEMO_MODE=false` plus `LIVE_GENERATION_ENABLED=false` returns `live_generation_disabled`;
* unavailable demo mode does not fall back to live.

## Maintenance tests

Prove:

* purge deletes database rows plus permanent original, thumbnail and staging objects;
* purge tolerates already-missing objects;
* storage failure retains the row for retry;
* catalogue and shared demo-source objects are untouched;
* batching is bounded;
* active attempts are skipped;
* a stale unlocked attempt becomes failed;
* an attempt whose advisory lock is held is skipped;
* unresolved submission markers and cost reservations are preserved;
* definitely pre-spend stuck work may safely release its reservation;
* duplicate maintenance task delivery is idempotent.

## Security tests

Prove production-like configuration has:

* secure cookies;
* HTTPS redirect;
* HSTS as configured;
* frame denial;
* nosniff;
* referrer policy;
* no wildcard CORS;
* restrictive API CSP;
* production frontend CSP without `unsafe-eval`;
* no internal API hostname in the browser bundle;
* admin route absent when production admin is disabled;
* admin still requires staff/superuser when enabled.

Run `manage.py check --deploy` under a complete production-like test environment and treat security warnings as failures unless a narrowly documented framework false positive is unavoidable.

## Observability tests

Prove:

* malformed request IDs are replaced;
* valid request IDs are bounded and returned;
* correlation context does not leak between requests or tasks;
* raw session keys, IPs, emails, prompts, notes, signed URLs and storage keys are absent from captured logs;
* Sentry is disabled without DSNs;
* tests perform no Sentry network calls;
* Sentry event scrubbing removes request bodies, cookies, sensitive headers and signed URL queries;
* Next.js builds without a Sentry token or DSN.

## Frontend tests

Cover initial generation and refinement for:

* disabled live generation;
* session/IP/daily limit exhaustion;
* budget exhaustion;
* temporary admission-control outage;
* no automatic retry for terminal quota/budget responses;
* accessible error announcements;
* preservation of draft/result state;
* no public budget amount displayed.

Regenerate and verify:

* `apps/api/openapi/schema.json`;
* generated TypeScript schema/client files.

Never hand-edit them.

# Manual abuse drill

Add a provider-free local abuse drill as a focused management command or script.

It must use fake/injected providers and local Redis only.

The drill must demonstrate:

1. Rapid requests from one simulated session/IP reach `generation_limit_reached`.
2. A second simulated IP has an independent IP counter while still respecting global limits.
3. A global daily count of one admits exactly one newly created live attempt.
4. A cost ceiling below one required reservation returns `live_generation_budget_exhausted`.
5. The provider fake was not called after budget rejection.
6. `LIVE_GENERATION_ENABLED=false` returns `live_generation_disabled`.
7. Demo mode completes without touching live count or cost keys.
8. No real credentials are required.

Do not label the drill as a successful paid live checkpoint.

# Baseline and final verification

At the beginning run:

```
git status --short
git log -15 --oneline
git rev-parse HEAD
docker compose config
docker compose up -d
docker compose ps
```

Run focused tests during each part.

Before the final phase council, run every command from `.claude/phase-council.json`, including:

```
docker compose build api
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api python -m pip check

docker compose exec web npm run lint
docker compose exec web npm run typecheck
docker compose exec web npm test -- --run
docker compose exec web npm run build
```

Also run:

```
docker compose exec api python manage.py check --deploy
```

Use a complete production-like environment for the deploy check rather than weakening production validation.

If dependencies change:

* modify only the direct dependency manifests;
* regenerate locks using the exact repository-pinned workflows;
* regenerate twice and confirm deterministic output;
* do not permit unrelated upgrades.

Confirm:

* no provider client was constructed by tests except injected fakes;
* no Anthropic, Replicate or Sentry network request occurred;
* no real credentials were used;
* no object storage became public;
* no raw IP/session/email was stored;
* no Phase 2 evidence changed;
* questionnaire fingerprints and existing prompt/schema/processor versions remain unchanged unless the implementation genuinely requires and documents a version bump;
* demo mode remains deterministic and zero-cost;
* live generation remains disabled by default after the phase.

# Documentation and ADR

Create:

```
docs/phases/phases-16.md
docs/decisions/0017-live-generation-security-and-cost-controls.md
```

Document:

* exact Redis key and reservation semantics without exposing production keys;
* integer cost unit;
* UTC day boundary;
* pricing configuration and verification responsibility;
* reservation/reconciliation state machine;
* ambiguous-spend policy;
* count-limit semantics;
* ownership-first throttle ordering;
* retention and stuck-job policy;
* Redis persistence/no-eviction operational requirement;
* security-header and CSP decisions;
* admin enablement policy;
* correlation and log-redaction rules;
* Sentry privacy configuration;
* why no `BudgetWindow` table or dashboard was added;
* why no showcase fallback was added.

Update as appropriate:

* `README.md`;
* `.env.example`;
* `compose.yaml`;
* `docs/PROPOSAL.md`, correcting stale Phase 16/showcase wording where necessary;
* `docs/phases/PHASES.md`, recording the delivered implementation without erasing the original requirement;
* `CLAUDE.md`, marking Phases 1–16 delivered and Phase 17 next;
* affected earlier ADRs with narrow Phase 16 implementation notes.

Do not claim current provider prices were verified unless they actually were checked against official provider sources on a recorded date. The phase may ship with live mode still disabled and pricing values left for operator configuration.

# Final workflow outcome

Continue through planning, implementation, focused tests, per-commit council review, full-phase verification, pushing and draft-PR creation until exactly one terminal state:

* `PR_READY`
* `BLOCKED`
* `ABORTED_SAFELY`

Do not merge the PR or mark it ready for review.

Return only:

* outcome;
* changed areas;
* important security/cost-accounting decisions;
* tests and checks actually run with results;
* unresolved manual pricing, Redis deployment or live-checkpoint requirements;
* provider-call, secret, storage and Phase 2 integrity confirmation;
* commit SHAs;
* draft PR URL;
* hosted CI status.
