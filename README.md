# Sitara

AI-assisted South Asian bridalwear **concept design**. A guided questionnaire, an optional pick of up to three rights-cleared inspiration images, and an AI-generated concept: a FLUX-rendered visual plus a structured design description authored by Claude, with one constrained refinement round. A deterministic, zero-cost demo mode runs the complete journey through the same pipeline with no paid provider calls.

> Sitara is for concept visualisation only. It does not produce sewing patterns or manufacturing specifications, and does not guarantee a garment can be constructed exactly as shown.

## Status

**Up next — Phase 17: UI polish and accessibility.** See `docs/phases/PHASES.md`.

**Phase 16B — questionnaire feedback, cultural expansion, and visual choice UX.** The first substantial user-feedback revision of the questionnaire and wizard. A new **questionnaire v3** draft (v1 stays active and fingerprint-locked, v2 untouched) adds satin (distinct from silk), a culturally-reviewed **Anand Karaj** Sikh ceremony, a dedicated optional single-choice `neckline_style` question (migrating the old `high_neckline` coverage tag out of `coverage_preferences`), an expanded curated colour vocabulary grouped into source-controlled buckets, and authoritative `restrict_options` coverage/neckline/head-covering/dupatta consistency rules; the strict option shape gains optional bounded `visual_key`/`group` machine-id metadata (never URLs/paths/HTML). Generation introduces **DesignSpec schema version 2** (`source_selections.neckline_style`) via a small explicit version registry with total, fail-safe dispatch — v1's committed JSON Schema stays byte-identical and every historical v1 spec still validates, never rewritten. `PROMPT_BUILDER_VERSION` moves **5.0.0 → 6.0.0** (the canonical neckline is rendered early beside coverage and restated at the close, and the model-authored neckline narrative is suppressed so it can never contradict the canonical choice; v1 golden snapshots are byte-identical, two new v2 fixtures added) and `SPEC_TEMPLATE_VERSION` **2.1.0 → 2.2.0** (Anand-Karaj/neckline-coherence/satin guidance). The deterministic demo engine stays aligned and zero-cost: the manifest schema is bumped to v2 (`+necklines`, expanded vocabulary), the selector to 2.0.0 (neckline scoring plus **fail-closed** hard constraints — an Anand Karaj design requires an asset explicitly tagged for it, a covered-head selection never matches an uncovered-head asset, a full-midriff selection never matches an exposed-midriff asset), and the spec engine to 2.0.0 (produces v2 with corrected head/midriff semantics), with a synthetic Anand Karaj development asset and a pack-wide coverage validator that requires every ceremony. The wizard refactors its schema-driven renderer into accessible visual choice cards, a compact grouped **colour-swatch selector** (grouped, counted, order-preserving, selection shown by ring and order badge — never colour alone), an expandable-description disclosure, and a reversible **"No preference — let Sitara decide"** control for optional single-choice questions (no preference = an absent answer, shown explicitly on the review screen), all backed by a frontend-owned, rights-controlled visual manifest of project-owned assets (colour hex swatches and original schematic neckline SVGs — never reused inspiration-catalogue assets, never sent to a provider, never influencing generation) with `jest-axe` accessibility tests. Activating v3 in production demo mode stays an operator step gated on an approved, culturally-reviewed Anand Karaj asset; the manual Anand-Karaj demo checkpoint and operator-only live cultural/coverage visual validation are recorded, not performed. Explanatory illustrations for garments/silhouettes/dupatta/saree drapes remain a documented text-fallback approved-asset gap — see `docs/decisions/0018-questionnaire-feedback-and-visual-choice-ux.md`.

**Phase 16 — live-generation security and cost controls.** The controls that must exist before public live generation can ever be switched on — built without switching it on. A hard daily cost ceiling is enforced by atomic **reserve-before-spend** accounting in integer micro-USD (never binary float) via Lua scripts on a dedicated, persistent, `noeviction` Redis: each billable provider stage reserves a conservative maximum immediately before submission, reconciles down to the estimated actual afterward, **retains** the reservation on an ambiguous outcome (fails toward assuming spend), and releases only on a proven no-spend failure — no check-then-increment race between workers, and durable per-attempt cost/token audit on `GenerationAttempt`. Admission control adds `503 live_generation_disabled`, ownership-first per-session and hashed-IP throttles plus a global UTC daily count limit (`429 generation_limit_reached`), and a budget preflight (`503 live_generation_budget_exhausted`). Celery Beat runs retention purge (deleting rows **and** storage objects) and stuck-job reconciliation. Production hardening adds a strict CSP, HSTS/cookie/referrer/COOP headers, admin mounted only when explicitly enabled, correlation-aware structured JSON logging (exception **type** only, never a traceback), and privacy-safe DSN-gated Sentry for Django and Next.js (no PII, no locals, no log→event). **Live generation stays disabled**; provider prices are operator-configured and **unverified** (default 0, ceiling 0), and the manual budgeted live checkpoint remains pending. Demo mode is untouched and stays zero-cost — see `docs/decisions/0017-live-generation-security-and-cost-controls.md`.

**Phase 15 — deterministic zero-cost demo engine.** `DEMO_MODE=true` (the safe local default) builds a real `DesignSpec` locally, reuses the existing deterministic image-prompt builder unchanged, deterministically selects a matching pre-generated concept image from a versioned, reviewed manifest, and runs the result through the same durable pipeline, storage, job/result APIs and UI as live generation — never a separate toy frontend and never a mock hidden behind the paid-provider wrapper. `resolve_generation_mode()` is the single three-mode source of truth (`demo`/`live`/`unavailable`): demo takes absolute precedence over every paid flag and key, with no live fallback if the demo asset pack is unavailable (`demo_assets_unavailable`), and no way for a settings change to make an already-queued demo attempt spend money — `GenerationAttempt.is_demo`/`DesignVersion.is_demo` freeze the mode at creation and it is never re-derived from live settings. The deterministic selector (`select_demo_asset`) applies exact garment-type hard filtering plus a documented weighted score with a stable SHA-256 tie-break, so the same input always selects the same asset. The frontend shows a persistent, accessible demo banner and honest, provider-free progress/result/refinement wording throughout. The reviewed production asset pack remains a separate, pending, manually budgeted content checkpoint; a development-only synthetic pack (never production content) lets local development and CI run the real pipeline without it — see `docs/decisions/0016-deterministic-demo-mode.md`.

**Phase 14 — single-round constrained refinement.** A design owner may now request exactly one bounded change to an already-generated concept. The frontend refinement panel offers a single-choice chip group over eight allowlisted DesignSpec categories (colour story, fabric and texture, embellishment, sleeves and coverage, neckline, dupatta or saree drape, silhouette detail, styling details) plus an optional 300-character note, gated behind a mandatory drift-acknowledgement checkbox; submission is idempotent (one in-memory `crypto.randomUUID()` key, reused verbatim on a transport-failure retry). `POST /api/v1/designs/<uuid>/refine/` enqueues one job on the same durable Celery pipeline Phase 10 built, now branched by a new `GenerationAttempt.generation_kind` (`initial`/`refinement`) only for its text stage; a separate, independently versioned structured-output prompt (`refinement_prompting.py`, `REFINEMENT_TEMPLATE_VERSION 1.0.0`) edits a copy of the source DesignSpec, and a strict per-category diff allowlist (`REFINEMENT_ALLOWED_PATHS`) rejects any candidate that touches a field outside the requested category or any immutable root — a structural check on the actual diff, never a trust placed in the model's own claim. The result is a **fresh text-to-image generation**, not image editing: the same deterministic `build_image_prompt` and `black-forest-labs/flux-1.1-pro` selection are reused unchanged, the original image's bytes are never sent to any provider, and a reused seed (when available) is documented everywhere as a continuity aid, not a guarantee. `MAX_REFINEMENTS = 1` and `DesignVersion.parent_version`/`refined_versions` enforce exactly one successful refinement per design; a cleanly-failed attempt may be retried, an attempt with unresolved provider spend may not. The refined version's historical inspiration-context snapshot is copied forward from its parent rather than rebuilt live. The result page renders a side-by-side version comparison (two independent result/image query pairs, one per version) with honest drift disclosure and the requested category shown in human-readable form — the raw note is never exposed in any result payload — see `docs/decisions/0015-single-round-refinement.md`.

**Phase 13 — rights-safe inspiration metadata influence.** A selected inspiration image now meaningfully shapes a concept without ever sending image bytes, a URL, or a storage key to any provider. Only its already-frozen `garment_type`/`alt_text`/`cultural_context` reach Anthropic, as a versioned, hashed snapshot (`inspiration_context.py`, schema version 1) re-validated against `publicly_eligible()` and the existing generated-content safety scan strictly before any provider is selected. The trusted structured-generation JSON gains a restricted `curated_inspiration_cues` array and the system prompt gains guidance keeping questionnaire selections authoritative over any compatible-only cue (`SPEC_TEMPLATE_VERSION` deliberately `1.0.0` → `2.0.0`; `DESIGN_SPEC_SCHEMA_VERSION` and `PROMPT_BUILDER_VERSION` unchanged); a post-output check rejects any generated text leaking a selected inspiration's title or attribution. Before persistence the selected `DesignInspiration`/`InspirationAsset`/`UsageRights` rows are locked in one documented order, the snapshot is rebuilt and compared by exact content and hash against the pre-provider version, and only a match is persisted atomically with the DesignSpec — a selection change, asset retirement, rights revocation, or a metadata/attribution edit during the call discards the result with no provider retry. The private result API additively gains `inspiration_acknowledgements` (title/attribution only, read solely from the persisted historical snapshot, never the live catalogue), and the questionnaire picker, review summary, and results page all honestly disclose that this is metadata-only influence, not reference-image conditioning. Direct image conditioning remains unimplemented and fail-closed (`ReferenceImagesNotEnabled`) — see `docs/decisions/0014-inspiration-metadata-influence.md`.

**Phase 12 — generation progress and private concept results (frontend).** A design can now be started, watched, and viewed end to end in the browser. `ReviewSummary`'s Generate action is live: an in-memory `crypto.randomUUID()` idempotency key (never browser storage) survives transport-failure retries, a synchronous double-click guard prevents duplicate submissions, and a 409 conflict resumes the existing attempt via the design detail's new `latest_job` field (additive, exposing only the existing public `GenerationJob` shape, selected deterministically by newest `created_at` then UUID). `/design/<id>/generation/<jobId>` polls the job with TanStack Query (`5.101.2`, one shared `QueryClient`) on a `created_at`-derived backoff schedule (1s under 10s, 2s to 30s, 5s after, stopped at any terminal state), renders only the four durable states honestly (no fake percentage, no invented estimate), and maps all 21 backend generation error codes through one exhaustive, compile-checked friendly-message table. A new dedicated `GET /api/v1/designs/<uuid>/versions/<uuid>/result/` endpoint (ownership-first indistinguishable 404, controlled 409/503, `no-store`, DesignSpec revalidated + safety-rescanned before delivery) returns a curated result payload — never the raw prompt, provider, token, or storage provenance — rendered at `/design/<id>/result/<versionId>` as the complete DesignSpec-derived brief with prominent concept-only/constructibility disclaimers, copy/download-brief and download-image actions. The result page runs two deliberately independent TanStack queries (stable result vs. short-lived signed image) so image-delivery trouble never hides the readable brief; signed image URLs (extended with a separately signed, fixed-filename `download_url` via a narrow `inline|attachment` signer parameter) are refreshed at ~80% of their observed remaining lifetime plus a near-expiry focus refresh, validated as genuine future timestamps, retried at most once per load-failure episode, and never persisted, cached beyond an in-memory `gcTime: 0`, or logged anywhere — see `docs/decisions/0013-generation-progress-and-results.md`.

**Phase 11 — permanent private image storage and signed delivery.** A successful generation now ends with a canonical, privately stored image instead of raw staging alone. A new `design_images` storage alias (strict `DESIGN_IMAGE_STORAGE_BACKEND`: `s3` for production/MinIO, `filesystem` for offline ingest testing only — refused in production, no public base URL) backs the `sitara/media` package: a pure deterministic key layout (`design-images/<design-uuid>/<version-uuid>/{original,thumbnail}.webp`, server UUIDs only), versioned canonical WebP processing (`DESIGN_IMAGE_PROCESSOR_VERSION 1.0.0` — full metadata strip, EXIF orientation, neutral alpha compositing, LANCZOS downscale-only, reopen-verified encodes, golden-manifest regeneration guard) and a crash-safe idempotent ingest service that reuses matching objects, never overwrites conflicting ones, recovers lost metadata across the non-atomic object-store/PostgreSQL boundary and holds no lock during I/O. `DesignVersion` carries the all-or-none permanent-image provenance (keys, SHA-256s, sizes, dimensions, processor version, ingest timestamp — immutable, admin read-only), and the pipeline's new stage E gates `succeeded`/`generated` on VERIFIED permanent ingest (`image_ingest_unverified`/`image_ingest_failed` never permit another paid submission; `manage.py ingest_design_image` is the provider-free operator recovery). Delivery is ownership-checked **before** signing: `GET /api/v1/designs/<uuid>/versions/<uuid>/images/` (AllowAny + ownership-first indistinguishable 404, controlled 409/503, `no-store` + `no-referrer`, zero provenance leak) returns GET-only SigV4 URLs signed against the browser-reachable `S3_SIGNED_URL_ENDPOINT_URL`, both expiring at one declared instant (strict 30–3600s TTL, default 300). A signed URL is a documented **temporary bearer URL** — usable by anyone holding it until expiry, not revoked by logout — so URLs are short-lived and never persisted, cached or logged; the frontend `fetchDesignImageUrls` wrapper is memory-only with strict result mapping. Filesystem delivery deliberately fails closed (no backend proxy in Phase 11); staging objects are retained for crash recovery until Phase 16 — see `docs/decisions/0012-private-design-image-storage.md`.

**Phase 10 — durable async generation pipeline and gated Replicate rendering (no live call in tests/CI).** A design can now be turned into a durable, resumable generation job. `GenerationAttempt` was reshaped to begin **before** the DesignVersion (required `design` FK, nullable version, per-Design idempotency, one in-progress attempt enforced by a partial unique constraint, private provenance + raw-staging fields, non-destructive backfill migration), and `Design` gains a `draft`/`generating`/`generated`/`generation_failed` lifecycle with recovery editing. `POST /api/v1/designs/<uuid>/generate/` (AllowAny + ownership-first 404, CSRF, an `Idempotency-Key` UUID header, `no-store`, a same-origin `Location`) enqueues one Celery job on a dedicated `generation` queue with the attempt UUID as the deterministic task id; `GET /api/v1/jobs/<uuid>/` returns a public payload exposing only the lifecycle — never a provider, model, prediction id, seed, storage key or image hash. The resumable `run_generation_attempt` state machine (text → prompt → image) is guarded by a two-integer PostgreSQL advisory lock, links the attempt to its DesignVersion atomically, and resumes from persisted markers (linked version skips Anthropic, existing prompt is reused, an accepted prediction is never resubmitted, a staged object is verified not regenerated). Part B adds `replicate==1.0.7` (hash-locked), strict `LIVE_GENERATION_ENABLED`/timeout/size settings, the capability gates (a token alone enables nothing; public generation stays off until `LIVE_GENERATION_ENABLED` is set on top of both provider gates and complete config), a lazy gated `ReplicateImageProvider` using only the public async `predictions.create/get/cancel` endpoints, a best-effort prediction-creation boundary (submit-once marker → conservative `image_submission_ambiguous` on any crash window; seed generated once and reused), and a hardened `*.replicate.delivery` HTTPS download boundary (host/redirect/credential/byte checks + Pillow/pixel verification + SHA-256) that stages the raw image to **private** storage. `DesignVersion.image_storage_key` stays blank — final image ingest is Phase 11. Every automated test and CI run makes **zero** Anthropic/Replicate calls (socket-denied, fixtures injected); the paid live checkpoint remains pending — see `docs/decisions/0011-asynchronous-generation-pipeline.md`.

**Phase 9 — deterministic image-prompt builder (no provider call).** A pure `build_image_prompt(spec)` (`apps/api/sitara/generation/prompt_builder.py`, `PROMPT_BUILDER_VERSION = "1.0.0"`) turns a validated `DesignSpec` into **one positive natural-language image prompt** for the selected FLUX model — no negative prompt, no JSON, no hard-coded model id and no network. Following the Phase 2 editorial path, safeguards are expressed positively (full-length studio photograph head-to-hem, clean background, original non-branded design, natural anatomy, soft even lighting); coverage comes only from the DesignSpec (no universal modesty suffix). The prompt renders in a fixed, snapshot-tested order through bounded narrative slots, adds tiny gharara/sharara/saree integrity cues, excludes construction caveats/alt-text/metadata, and is safety-scanned before and after assembly (overrun → controlled error, never a blind slice). `DesignVersion` gains read-only `image_prompt` + `prompt_builder_version` (all-or-none, requires-a-spec DB constraints; Phase 8/legacy rows stay valid). `manage.py build_image_prompt --design-version <uuid>` builds and stores it offline and atomically, with strict immutability, printing only safe provenance. Part A also made the Phase 8 final spec persistence atomic (freshness re-check + version creation under one Design row lock) and tightened claim-negation to be clause-aware. Golden snapshots + a combined-hash manifest guard wording drift; no Anthropic/Replicate call in tests or CI — see `docs/decisions/0010-deterministic-image-prompt-builder.md`.

**Phase 8 — structured DesignSpec generation (gated, no image, no API).** A strict Pydantic v2 `DesignSpec` is the authoritative concept-brief contract, with a deterministic committed JSON Schema (`apps/api/sitara/generation/schemas/design_spec_v1.json`, regenerated by `manage.py export_design_spec_schema`). `manage.py generate_spec --design <uuid>` turns a complete Design into one validated, provenance-tracked `DesignVersion` — offline via `--fixture` (zero network) or, behind `--confirm-live` and the `DEMO_MODE=false`/`ALLOW_PAID_AI_CALLS=true` gates, via Anthropic structured output (`beta.messages.parse`, `max_retries=0`, at most two requests). Every pre-spend gate runs first, a PostgreSQL advisory lock prevents double-spend, `source_selections` is verified to echo the trusted input exactly, generated text is scanned against a conservative designer/brand denylist, and no prompt, raw response or key is ever persisted. Capability gates are explicit (`STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED=True`, `IMAGE_PROVIDER_IMPLEMENTED=False`); the public config still reports generation unavailable and the Generate button stays disabled. No live Anthropic call is made in tests or CI; the paid quality checkpoint needs separate approval — see `docs/decisions/0009-structured-design-spec-generation.md`.

**Phase 7 — questionnaire wizard and validated design drafts.** The first end-to-end user journey is live: an accessible, schema-driven questionnaire wizard (`apps/web/src/features/questionnaire/`) captures a validated bridal design brief and up to three rights-approved inspiration selections into a private draft. Answers persist to the backend through a CSRF-aware API (never to browser storage) with prompt autosave and refresh-safe resume; the Design is pinned to a questionnaire version (assign-once), inspiration selections are an ordered ≤3 set re-checked for rights eligibility on every request, and a `POST /api/v1/designs/<uuid>/validate/` endpoint performs authoritative complete validation. The questionnaire rule semantics live once in the backend and are *mirrored* (not duplicated) on the frontend, with a shared `contracts/questionnaire-validation-cases.json` fixture run by both test suites. No generation, DesignSpec or provider calls — the Review screen's "Generate my concept" button is a disabled stub. See `docs/decisions/0008-questionnaire-draft-and-wizard.md`.

**Phase 6 — OpenAPI contract and generated TypeScript client.** The backend OpenAPI 3.0.3 schema (drf-spectacular) is committed at `apps/api/openapi/schema.json` and is the single source of the frontend's API types: `openapi-typescript` generates `apps/web/src/api/schema.d.ts` and `openapi-fetch` provides a same-origin typed client for safe reads, sharing one request transport with the tested CSRF-aware client. Backend and frontend CI both fail on contract drift. No runtime schema/Swagger endpoint is served, and unsafe typed mutations remain deferred — see `docs/decisions/0007-openapi-generated-client.md`.

**Phase 5B — rights-controlled inspiration catalogue.** On top of the Phase 3A/3B/4/5A foundation (Next.js frontend, Django/DRF backend, PostgreSQL, Redis + Celery, private MinIO/S3 storage, health endpoints, fail-closed AI-provider boundary, session authentication with optional accounts — ADR 0003, private design ownership — ADR 0004, versioned questionnaire taxonomy — ADR 0005), Sitara now has a staff-managed catalogue of rights-approved inspiration images: `UsageRights` records with a pending/verified/rejected lifecycle and four mandatory usage permissions, `InspirationAsset` drafts whose images are sanitised on ingestion (Pillow-only pipeline, metadata stripped, WebP derivatives, original discarded), a service-only approve/retire lifecycle, and public identity-free catalogue + image-streaming endpoints that re-check rights eligibility on every request — see `docs/decisions/0006-rights-controlled-inspiration-catalogue.md`. The frontend wizard will *derive* its Zod validation from the questionnaire schema (Phase 7); design-spec and image generation arrive later. Phase 2 (image-model evaluation) selected **`black-forest-labs/flux-1.1-pro`** — see `docs/decisions/0001-image-model.md` / `.json`.

## Layout

```
apps/api      Django + DRF backend (config/, sitara/{accounts,designs,questionnaire,health,ai_gateway})
apps/web      Next.js (App Router, strict TypeScript) frontend
infra/minio   local object-storage bucket initialisation
experiments/  Phase 2 model evaluation (frozen evidence; do not modify outputs/)
docs/         proposal, phase plan, decision records
compose.yaml  local development stack
```

## Safety defaults (fail closed)

```
DEFAULT_IMAGE_MODEL = black-forest-labs/flux-1.1-pro
FAST_IMAGE_MODEL    = black-forest-labs/flux-1.1-pro
DEMO_MODE           = true    -> no Anthropic / Replicate calls, fixtures only
ALLOW_PAID_AI_CALLS = false   -> a present API token NEVER enables paid calls
```

Paid generation requires **all** of `DEMO_MODE=false`, `ALLOW_PAID_AI_CALLS=true`, real provider keys, and — for the public end-to-end API — `LIVE_GENERATION_ENABLED=true`: the public `generation_enabled` flag comes from a capability policy that also checks the code-level `STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED`/`IMAGE_PROVIDER_IMPLEMENTED`/`FULL_GENERATION_PIPELINE_IMPLEMENTED` constants, so a present token alone enables nothing, demo mode enables nothing, and the API never advertises generation until an operator deliberately turns on `LIVE_GENERATION_ENABLED` on top of complete, gated provider configuration. Tests prove all of it.

Further hardening: `APP_ENV` is validated against an exact allowlist (`development|test|production` — typos refuse startup); production startup rejects placeholder/development credentials by name without echoing values; email identity is case-insensitive and whitespace-trimmed with a PostgreSQL `Lower(email)` unique constraint; readiness-check failures log only the check name and exception type (never connection strings); local dev ports bind to loopback (`BIND_HOST=127.0.0.1`, overridable); and the frontend aborts backend requests after 5 seconds instead of hanging on "Checking backend status…".

## Local development (Windows PowerShell)

### 1. Prerequisites

- Docker Desktop (Compose v2)
- Git
- Optional for working outside containers: Python 3.12, Node 22

### 2. Environment

```powershell
Copy-Item .env.example .env   # optional — Compose ships safe dev defaults
docker compose config         # validate the stack definition
```

`.env` is gitignored; `.env.example` contains placeholders only. Never commit real tokens.

### 3. Start the stack

```powershell
docker compose up --build
docker compose ps
```

Local ports: **web 3001** (3000 is occupied by another project on this machine — override with `WEB_PORT`), **api 8000**, postgres 5432, redis 6379, MinIO 9000 (console 9001). Migrations run automatically via the one-shot `migrate` service before the API starts; the `minio-init` service creates the private `sitara-media` bucket (no anonymous policy).

### 4. Migrations (manual, when you add models)

```powershell
docker compose exec api python manage.py makemigrations
docker compose exec api python manage.py migrate
```

### 5. Django superuser

```powershell
docker compose exec api python manage.py createsuperuser
# admin at http://localhost:8000/admin/
```

### 6. API health checks

The browser path goes through the Next.js same-origin rewrite on port 3001
(direct port-8000 access remains available for backend debugging):

```powershell
Invoke-RestMethod http://localhost:3001/api/v1/health/live
Invoke-RestMethod http://localhost:3001/api/v1/health/ready
Invoke-RestMethod http://localhost:3001/api/v1/config/public
```

### 7. Frontend

Open <http://localhost:3001> — the foundation page shows backend connection, database/Redis/storage readiness, and the demo-mode badge, plus **Sign in** / **Create account** links (`/login`, `/register`, `/account`).

### 7b. Authentication (Phase 3B)

Accounts use **Django server-side sessions only** — no JWT, no tokens in
browser storage. The session cookie `sitara_sessionid` is HttpOnly +
SameSite=Lax (Secure in production); the CSRF token is bootstrapped from
`GET /api/v1/auth/csrf/`, held in memory, and sent as `X-CSRFToken`.

All browser API traffic uses **relative `/api/` paths** through the Next.js
rewrite; the server-only `API_INTERNAL_BASE_URL` variable points the rewrite
at Django (`http://api:8000` in Docker, `http://localhost:8000` for native
`npm run dev`). `NEXT_PUBLIC_API_BASE_URL` no longer exists — no backend
host reaches the browser bundle.

Endpoints: `auth/csrf/`, `auth/register/`, `auth/login/`, `auth/logout/`,
`auth/me/` under `/api/v1/`. Login/registration are rate-limited per IP and
per IP+email via the Redis cache (`REDIS_CACHE_URL`, logical DB 1) and fail
closed (503) if the cache is down. Because of that fail-closed behaviour,
`REDIS_CACHE_URL` is **required in production** (startup rejects missing,
placeholder and committed development values) and the cache appears in
`/api/v1/health/ready` as the `auth_cache` check ("Authentication
protection" on the status page). Passwords need ≥ 12 characters and pass
Django's standard validators.

Not yet implemented (see ADR 0003): email verification, password reset,
account deletion, OAuth/MFA — public production registration is **not**
feature-complete until verification + recovery are designed.

### 7c. Private designs (Phase 4)

`GET/POST /api/v1/designs/` and `GET/PATCH /api/v1/designs/<uuid>/` manage
private draft designs (title-only writes for now; `status=draft`,
`answers={}` are server-controlled until the questionnaire phase). Ownership
is dual (ADR 0004): anonymous drafts are private to the browser session —
the Django session data holds an internal workspace UUID, never a raw
session key — and are **claimed automatically for the user on the next
design request after login**. Authenticated users reach their designs from
any signed-in browser. Anything inaccessible returns 404 (never 403), and
`MAX_DESIGN_VERSIONS` (default 2 = initial concept + one refinement) caps
version numbering at the application level.

Concurrency: the CSRF bootstrap (`GET /api/v1/auth/csrf/`) materialises the
Django database session, and concurrent design creates sharing one browser
session serialise on that session's database row — two tabs always end up
in the same workspace, with a controlled 503 (never unlocked creation) if
the session store fails. Domain tables still store no raw Django session
key, and a user may legitimately hold several workspaces across different
browser sessions.

### 7d. Questionnaire (Phase 5A)

`GET /api/v1/questionnaire/active/` serves the single active questionnaire
version as `{id, version, schema}` — a public, identity-free read (it never
creates a session or a design workspace) with `Cache-Control: no-store` and
a safe `503 questionnaire_unavailable` when no valid active version exists.
The schema is the **single authoritative source** of questionnaire rules
(ADR 0005): three question types (`single_choice`, `multi_choice`, `text`),
machine-readable constraints (selection caps, mandatory text length caps,
exclusive values) and allowlisted show/hide/require/restrict-options
compatibility rules — the same constraints Django will validate answers
against, and the source Phase 7 derives frontend Zod validation from.

Seed (or re-seed) version 1 with:

```powershell
docker compose exec api python manage.py loaddata questionnaire_v1
```

Lifecycle: staff create **drafts** in Django admin and publish them with the
"Activate selected questionnaire version" action; activation validates the
full schema, retires the previous active version in the same transaction,
and a PostgreSQL partial unique constraint guarantees at most one active
version. Once active or retired, a version's number and schema are
immutable and active versions cannot be deleted — corrections ship as new
versions.

### 7e. Inspiration catalogue (Phase 5B)

A small, staff-managed catalogue of **rights-approved** inspiration images
(ADR 0006). Staff create a `UsageRights` record (basis, holder, evidence,
the four usage permissions — public display, AI input, derivative
generation, commercial use — attribution, optional expiry) and verify it
through the "Verify selected rights record" admin action; a rejected record
can never become verified. Images enter ONLY through the admin upload on an
`InspirationAsset` draft: the bytes are decoded with Pillow (JPEG/PNG/WebP
only, single-frame, strict byte/pixel bounds), EXIF orientation is applied,
**all metadata (EXIF/GPS/XMP/ICC) is stripped**, transparency is
composited, and only two sanitised WebP derivatives (≤2048px main, 512px
thumbnail) are stored privately under server-generated keys — **the
original upload is discarded**. No user uploads, no URL fetching.

Approval ("Approve selected inspiration asset") requires the processed
image, title, alt text and verified, unexpired, fully-permissive rights;
retirement is immediate and terminal. Public, identity-free endpoints —

- `GET /api/v1/inspiration-assets/` (catalogue JSON, `no-store`)
- `GET /api/v1/inspiration-assets/<uuid>/image/`
- `GET /api/v1/inspiration-assets/<uuid>/thumbnail/`

— share one eligibility queryset (approved + verified + unexpired + all
permissions), stream WebP through Django (never a storage URL) and answer
an indistinguishable 404 for anything ineligible. Ingestion bounds are
configurable via `INSPIRATION_MAX_UPLOAD_BYTES`,
`INSPIRATION_MAX_IMAGE_PIXELS`, `INSPIRATION_OUTPUT_MAX_EDGE` and
`INSPIRATION_THUMBNAIL_EDGE`.

### 8. Celery ping test

```powershell
docker compose exec api python -c "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"
# -> {'pong': True, 'service': 'sitara-api'}
```

### 9. Backend tests & lint

```powershell
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
```

### 9b. Backend dependency lock

Direct dependencies live in `apps/api/requirements.in`; `requirements.txt`
is the fully-pinned, hash-verified lock installed by Docker and CI
(`--require-hashes`). After editing `requirements.in`, regenerate:

```powershell
docker run --rm -v "${PWD}\apps\api:/app" -w /app python:3.12.7-slim-bookworm `
  sh -c "python -m pip install --upgrade pip==26.0.1 && python -m pip install pip-tools==7.5.3 && python -m piptools compile --generate-hashes --output-file requirements.txt requirements.in"
```

The toolchain is pinned (pip 26.0.1 + pip-tools 7.5.3 on Python 3.12.7)
because pip-tools relies on pip internals and mismatched versions break the
CI freshness check.

CI fails if the lock is stale.

### 9c. OpenAPI contract & generated TypeScript types (Phase 6)

The backend is the single source of the API contract. drf-spectacular
produces the committed `apps/api/openapi/schema.json` (through the management
command only — no served schema/Swagger endpoint), and `openapi-typescript`
generates `apps/web/src/api/schema.d.ts` from it. **Never hand-edit either
generated file** — change the serializer or `requirements`/`package` and
regenerate. Backend and frontend CI both fail on drift (ADR 0007).

After changing any backend serializer, view annotation or the committed
schema, regenerate BOTH files and review the diffs. The commands below mount
your working tree into the existing images, so no image rebuild is needed
(the images already carry drf-spectacular and openapi-typescript).

```powershell
# 1. Backend schema — validated, warning-free; writes apps/api/openapi/schema.json
docker compose run --rm -v "${PWD}\apps\api:/app" api `
  python manage.py spectacular --format openapi-json --file openapi/schema.json --validate --fail-on-warn

# 2. Frontend types — regenerated from the committed schema
docker compose run --rm `
  -v "${PWD}\apps\web\src:/app/src" `
  -v "${PWD}\apps\api\openapi:/api/openapi:ro" `
  web npm run generate:api

# 3. Review BOTH generated diffs before committing
git diff -- apps/api/openapi/schema.json apps/web/src/api/schema.d.ts

# 4. Backend and frontend checks
docker compose exec api pytest
docker compose exec web npm run typecheck
docker compose exec web npm test -- --run
```

If you have host tooling installed (Python 3.12 with the hash-verified deps,
Node 22 with `npm --prefix apps/web ci`), the same generation runs natively:

```powershell
# from the repo root — Push/Pop-Location reliably returns to the root even if
# the schema command fails
Push-Location apps/api
python manage.py spectacular --format openapi-json --file openapi/schema.json --validate --fail-on-warn
Pop-Location
npm --prefix apps/web run generate:api
```

The bash equivalents (macOS/Linux) are the same commands with `$PWD` and
forward slashes.

### 10. Frontend tests & checks

```powershell
docker compose exec web npm run lint
docker compose exec web npm run typecheck
docker compose exec web npm test
docker compose exec web npm run build
```

### 11. Shut down

```powershell
docker compose down
```

### 12. Reset local data (destroys dev DB and MinIO volumes)

```powershell
docker compose down --volumes
```

## Deployment notes

`python manage.py check --deploy` under local development settings reports five expected warnings (W004 HSTS, W008 SSL redirect, W012/W016 secure cookies, W018 DEBUG) — all are governed by the `APP_ENV=production` settings branch, which enforces secure cookies, optional SSL redirect/HSTS, and fails startup when required production values (secret key, hosts, database, Redis, storage credentials) are missing.

## Security & privacy foundations (Phases 3A–5B)

Implemented now: private storage bucket with no public ACLs; CORS/CSRF explicit allowlists; DRF authenticated-by-default; JSON-only API; secrets only via environment; tokens never logged or returned; demo mode provably unable to call paid providers; session authentication with HttpOnly cookies, JSON CSRF failure handling, hashed-identifier rate limiting and `Cache-Control: no-store` on all auth responses; private-by-construction designs (ownership filtering before every lookup, 404 for anything inaccessible, no raw session keys in domain tables, no public design URLs); a rights-controlled inspiration catalogue whose staff-only ingestion sanitises every image (metadata stripped, WebP re-encode, original discarded) and whose public endpoints re-check rights eligibility on every request. Django endpoint permissions are the authorization boundary — the Next.js middleware redirect on `/account` is a navigation nicety only.

Deliberately **not yet** implemented (later phases): email verification and password recovery, retention/deletion, quotas and cost ledgers. The single-refinement limit arrived in Phase 14 (`MAX_REFINEMENTS = 1`, `DesignVersion.parent_version`/`refined_versions`). Signed design-image delivery arrived in Phase 11 as short-lived bearer URLs (ownership checked before issuance; no revocation before expiry — a backend proxy is the documented upgrade path).
