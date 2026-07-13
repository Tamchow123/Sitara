# Sitara — Phased Implementation Roadmap

Companion to [PROPOSAL.md](PROPOSAL.md). Each phase lists: **Scope / Non-goals / Commands / Automated tests / Manual checkpoint / Suggested commit.** Phases are sequential; a phase's checkpoint gates the next.

Standing rules across all phases:
- Paid provider calls happen only inside `generation/providers/` wrappers, gated by `ALLOW_PROVIDER_CALLS=true` + real keys. Automated tests and CI can never spend money.
- `DEMO_MODE` is strictly zero-cost and deterministic; live generation is separately gated by `LIVE_GENERATION_ENABLED`.
- All provider model names are environment variables; none are hard-coded.
- Section references (§) point at PROPOSAL.md.

---

## Phase 1 — Planning documents
- **Scope:** commit the approved proposal and phase plan to the repo: `docs/PROPOSAL.md`, `docs/PHASES.md` (this file), `docs/decisions/template.md` (decision-record template), top-level `README.md` stub describing the project and pointing at the docs.
- **Non-goals:** no scaffolding of Next.js/Django/Docker/Postgres/Redis/Celery, no package installation, no application code, no CI config.
- **Commands:** `git add docs README.md && git commit`.
- **Automated tests:** none (documentation only).
- **Manual checkpoint:** docs render correctly on the repo host; proposal reflects every agreed constraint.
- **Commit:** `docs: add Sitara proposal, phase plan, and decision-record template`

## Phase 2 — Image-model feasibility evaluation
- **Scope:** *first step:* inspect the currently available official Replicate / Black Forest Labs model catalogue and select the candidate set — one inexpensive/fast model, one balanced production-quality model, one highest-quality model, optionally one reference-image/editing-capable model — recording the exact provider model identifiers and versions to be tested. Then `experiments/model-eval/` standalone scripts: prompt matrix (garments: lehenga, saree, gharara, sharara, anarkali, shalwar kameez; ceremonies: nikah, mehndi, baraat, walima; modest full-sleeve; heavy/minimal embroidery), runner that calls the selected candidates with an explicit per-run budget cap and dry-run mode, contact-sheet generator, scoring sheet, refinement-drift sub-experiment (same seed, one attribute changed). Decision record `docs/decisions/0001-image-model.md` including the exact identifiers/versions tested.
- **Non-goals:** no Django/Next.js code, no database, no Anthropic calls (prompts are hand-written), no automation of scoring, no model selection before the experiment completes.
- **Commands:** `python experiments/model-eval/run.py --dry-run`; then `python experiments/model-eval/run.py --models $CANDIDATES --budget-usd N`; `python experiments/model-eval/contact_sheet.py`.
- **Automated tests:** unit tests for the prompt-matrix builder and budget guard (dry-run asserts zero network calls); none of these touch paid APIs.
- **Manual checkpoint:** all rubric dimensions (garment accuracy, cultural coherence, fabric realism, embroidery, dupatta styling, anatomy, prompt adherence, refinement consistency) scored for every model × prompt cell; decision record written naming default + fast model and documenting refinement-drift findings; spend within budget.
- **Commit:** `experiment: image-model feasibility evaluation and decision record`

## Phase 3 — Next.js and Django scaffold
- **Scope:** monorepo layout `frontend/` (Next.js 16, TS, Tailwind, shadcn/ui baseline) and `backend/` (Django 5.2, DRF, settings split with env-driven config incl. `DEMO_MODE`, `LIVE_GENERATION_ENABLED`, `ALLOW_PROVIDER_CALLS`, `MAX_DESIGN_VERSIONS`, model-name vars), `docker-compose.yml` (Postgres, Redis), Next.js `/api/` rewrite to Django so the browser only ever makes same-origin API calls (prod reverse-proxy documented for Phase 18), `/healthz` (liveness only) and `/readyz` (Postgres+Redis), CI pipeline (lint, typecheck, both test suites), provider wrapper *stubs* that raise without the env gate.
- **Non-goals:** no models beyond Django defaults, no API endpoints beyond health, no Celery tasks, no frontend pages beyond a landing stub.
- **Commands:** `docker compose up -d`; `cd backend && python manage.py runserver`; `cd frontend && npm run dev`; `npm run lint && npm run typecheck`; `pytest`.
- **Automated tests:** `/healthz` returns 200 with DB down (mocked); `/readyz` returns 503 with Redis or Postgres unreachable; wrapper stubs raise when `ALLOW_PROVIDER_CALLS` unset; trivial frontend render test. CI green.
- **Manual checkpoint:** `docker compose up` + both dev servers yield a working stub page and healthy `/readyz`.
- **Commit:** `chore: scaffold Next.js and Django monorepo with health endpoints and CI`

## Phase 4 — Minimal database and session foundation
- **Scope:** DesignSession, Design, DesignVersion, GenerationAttempt models + migrations + admin registration; anonymous sessions via **Django's standard session framework** (DesignSession created lazily and associated with the Django session; Django-issued CSRF token validated on unsafe requests); session-scoping helpers (cross-session → 404). DesignVersion: positive-integer `version_number` with a unique `(design, version_number)` constraint; `MAX_DESIGN_VERSIONS` enforced in application validation only.
- **Non-goals:** no questionnaire content, no catalogue, no generation logic, no retention purge, no custom auth cookie of any kind.
- **Commands:** `docker compose exec backend python manage.py makemigrations` then `... migrate`; `pytest backend/`.
- **Automated tests:** model constraints (idempotency-key uniqueness, `(design, version_number)` uniqueness, positive version_number); application-level rejection of a version beyond `MAX_DESIGN_VERSIONS`; DesignSession creation idempotent per Django session; CSRF enforced on unsafe endpoints; cross-session design access returns 404.
- **Manual checkpoint:** create a session and a draft design via `curl`/httpie (observing the CSRF flow); inspect in Django admin.
- **Commit:** `feat(backend): core design models on Django sessions with CSRF-protected API`

## Phase 5 — Taxonomy and rights-controlled catalogue administration
- **Scope:** QuestionnaireVersion, InspirationAsset, UsageRights models; admin flows (upload → EXIF strip → WebP/thumbnail → rights record → approve); approval blocked without verified rights; public read-only catalogue endpoint (approved only) and questionnaire endpoint; seed one QuestionnaireVersion fixture with the full garment/ceremony/embellishment taxonomy **including machine-readable validation/compatibility constraints per question** (the source the frontend later derives Zod from, and the same constraints Django's authoritative validator applies).
- **Non-goals:** no frontend catalogue UI yet, no design-to-asset linking UI, no S3 (local storage only).
- **Commands:** `docker compose exec backend python manage.py migrate`; `... loaddata questionnaire_v1`; `pytest backend/`.
- **Automated tests:** unapproved/rights-less assets never appear in the API; approval without verified rights rejected; image ingest produces WebP + thumb and strips EXIF; questionnaire endpoint serves the active version.
- **Manual checkpoint:** upload 3 real rights-cleared images through admin end-to-end; confirm attribution fields render in API output.
- **Commit:** `feat(backend): questionnaire taxonomy and rights-controlled inspiration catalogue`

## Phase 6 — OpenAPI contract generation
- **Scope:** drf-spectacular wired with correct typing on all existing endpoints; `npm run generate:api` running openapi-typescript into `frontend/src/api/schema.d.ts` (committed); openapi-fetch client wrapper; CI step regenerating the schema and failing on diff (contract-drift check).
- **Non-goals:** no new endpoints; no hand-written TS API interfaces anywhere (documented exception process if one ever becomes necessary).
- **Commands:** `python manage.py spectacular --file schema.yaml`; `npm run generate:api`; `git diff --exit-code` in CI.
- **Automated tests:** CI drift check; a frontend compile-time usage of the generated types (typecheck is the test).
- **Manual checkpoint:** intentionally change a serializer field locally and confirm CI-style drift check fails; revert.
- **Commit:** `feat: OpenAPI schema generation with committed TS types and drift check`

## Phase 7 — Questionnaire vertical slice
- **Scope:** frontend wizard (RHF, schema-driven from `/questionnaire/`; per-step Zod validators **derived from the backend's machine-readable constraints** rather than hand-duplicated, plus static Zod validation of the stable submission envelope; progress persisted per session), inspiration browsing/selection UI (≤3, attribution shown), draft Design create/update via typed client, review-summary screen. Django re-validates every submission against the questionnaire version and takes precedence.
- **Non-goals:** no generation button behaviour beyond a disabled stub, no results page, no manual duplication of individual question rules in Zod.
- **Commands:** `npm run dev`; `npm run test` (frontend); `pytest backend/`.
- **Automated tests:** constraint-to-Zod derivation (a constraint change in a fixture schema changes derived validation without code edits); backend rejects a submission that bypasses client validation (authority test); wizard step validation, resume-after-refresh, 4th-selection rejection (server-side test + client guard), draft PATCH round-trip.
- **Manual checkpoint:** complete the full questionnaire in the browser, select inspirations, land on the review screen; refresh mid-wizard and resume.
- **Commit:** `feat: questionnaire wizard and inspiration selection vertical slice`

## Phase 8 — Structured DesignSpec generation
- **Scope:** Pydantic `DesignSpec` model; JSON schema derivation; Anthropic wrapper using structured outputs (model from `ANTHROPIC_MODEL`); system prompt as a source-controlled constant with `SPEC_TEMPLATE_VERSION`; input-safety layer (caps, denylist incl. designer names, delimited untrusted free text); Django-side re-validation; recorded-fixture responses for tests; one manual budgeted live invocation command.
- **Non-goals:** no image generation, no Celery (synchronous management command for now), no refinement.
- **Commands:** `pytest backend/` (fixtures only); manual: `ALLOW_PROVIDER_CALLS=true python manage.py generate_spec --design <id>` (run inside the backend container).
- **Automated tests:** fixture-driven spec validation (valid spec persists; invalid spec → one retry → hard fail); denylist blocks pre-spend; wrapper raises without env gate (**asserting zero live calls in CI**); Pydantic re-validation rejects a deliberately malformed fixture.
- **Manual checkpoint:** one budgeted live run produces a valid, culturally coherent DesignSpec from a real questionnaire submission; review output quality by hand.
- **Commit:** `feat(generation): schema-constrained DesignSpec via Anthropic structured outputs`

## Phase 9 — Deterministic image-prompt builder
- **Scope:** `prompt_builder.py` with `PROMPT_BUILDER_VERSION`, fixed field order, presentation + exclusion instructions, sanitised slot-limited free-text handling, no named-designer interpolation; golden DesignSpec fixtures; snapshot tests; versions recorded onto DesignVersion.
- **Non-goals:** no Replicate calls; builder output is inspected as text only.
- **Commands:** `pytest backend/apps/generation/ -k prompt_builder`.
- **Automated tests:** snapshot tests over the golden fixture set (every garment/ceremony from the Phase 2 matrix represented); property-style tests: designer names in free text never appear in output; field order stable; version bump required when snapshots change (CI check comparing version constant against snapshot hash).
- **Manual checkpoint:** eyeball generated prompts for 5 diverse fixtures against Phase 2 learnings about what prompt shapes worked.
- **Commit:** `feat(generation): deterministic versioned image-prompt builder with snapshot tests`

## Phase 10 — Celery and Replicate asynchronous generation
- **Scope:** Celery app + `generation` queue; `generate_design_version` task chaining pipeline Steps A–D (§9); Replicate wrapper (model from `REPLICATE_IMAGE_MODEL`, optional reference-image params accepted but unused, seed recorded); GenerationAttempt status transitions; `/designs/{id}/generate/` (202, idempotency key, 409 on in-progress) and `/jobs/{id}/`; retries resume at image stage; stable error codes per §8.
- **Non-goals:** no permanent storage pipeline beyond raw save (Phase 11), no results UI (Phase 12), no rate limits (Phase 16).
- **Commands:** Celery runs as a Compose service — `docker compose up -d worker` (or `docker compose exec worker celery -A sitara worker -Q generation` for a foreground run, per the final Compose configuration); never instruct native `celery` invocation on Windows hosts. `pytest backend/`; manual live: `ALLOW_PROVIDER_CALLS=true` + trigger via API.
- **Automated tests:** full task pipeline on fakes/recorded fixtures with zero live calls (asserted); idempotency (duplicate key → same job); double-generate → 409; transient image-stage failure retries without re-calling the text stage (fixture call-count assertion); permanent failure sets `failed` + `error_code`.
- **Manual checkpoint:** one budgeted end-to-end live run: questionnaire → spec → prompt → FLUX image on disk; kill the worker mid-job and confirm safe failure state and safe retry.
- **Commit:** `feat(generation): async Celery pipeline with Replicate image rendering`

## Phase 11 — Permanent image storage
- **Scope:** django-storages abstraction finalised; WebP transcode + thumbnail on ingest; storage layout per §10; S3-compatible backend config (exercised against MinIO in compose, or a real bucket); signed-URL issuance for design images with short expiry; shareability caveat documented in code comments and `docs/`.
- **Non-goals:** no CDN setup, no backend image proxy (documented as the upgrade path), no retention purge yet.
- **Commands:** `docker compose up -d minio`; `pytest backend/`; env-switch smoke: run ingest against both backends.
- **Automated tests:** ingest produces WebP + thumb on both storage backends; signed URL expires (time-mocked); design image URL absent from any response that isn't session-authorised.
- **Manual checkpoint:** generated image retrievable via signed URL; the same URL fails after expiry; catalogue images publicly readable.
- **Commit:** `feat(storage): unified local/S3 image storage with signed design-image URLs`

## Phase 12 — Results page
- **Scope:** staged progress screen driven by `/jobs/{id}/` polling (TanStack Query, backoff); concept view rendering the DesignSpec-derived description (title, garment breakdown, colour story, embellishments, styling notes, construction caveats, cultural context), image with spec-derived alt text, download/copy actions, constructability + AI-concept disclaimers; friendly error states mapped from domain error codes.
- **Non-goals:** no refinement UI, no showcase gallery.
- **Commands:** `npm run dev`; `npm run test`.
- **Automated tests:** polling state machine (queued/running_text/running_image/succeeded/failed → correct UI states); description renders every DesignSpec section from a fixture; error-code → message mapping.
- **Manual checkpoint:** full browser journey questionnaire → progress → concept view against a locally generated (or fixture) result; disclaimers visible without scrolling hunt.
- **Commit:** `feat(frontend): generation progress and concept results page`

## Phase 13 — Optional inspiration metadata/reference support
- **Scope:** wire selected InspirationAsset tags/metadata into the Claude context (text-only, labelled as the MVP mechanism); design-note in `docs/decisions/0002-inspiration-influence.md` recording text-only as an MVP limitation and the upgrade path (Replicate wrapper already accepts reference images — enabling image conditioning is a wrapper + eval change, not a redesign); optionally a spike behind a dev-only flag if Phase 2 findings justify it.
- **Non-goals:** no production image conditioning; no new FLUX model commitments without repeating a scoped Phase-2-style eval.
- **Commands:** `pytest backend/`; manual budgeted live run with and without inspirations selected.
- **Automated tests:** spec-generation context includes selected asset tags (fixture assertion); ≤3 enforcement still holds through generation; no image bytes sent to Anthropic (wrapper-level assertion).
- **Manual checkpoint:** two live runs differing only in inspiration selection show visible, sensible influence; decision record updated with observations.
- **Commit:** `feat(generation): inspiration metadata influence with documented image-conditioning upgrade path`

## Phase 14 — Refinement
- **Scope:** refinement chips + capped note UI; `/designs/{id}/refine/`; Claude spec-edit flow ("change only what was asked", same structured-output schema); prompt rebuild + regeneration (seed reuse as continuity aid only); v1/v2 side-by-side UI **with explicit copy that the refined image is a fresh generation and may differ substantially**; refinement limit enforced in application code via `MAX_DESIGN_VERSIONS=2`.
- **Non-goals:** no multi-round refinement, no image-to-image editing.
- **Commands:** `pytest backend/ && npm run test`; manual budgeted live refinement.
- **Automated tests:** second refinement rejected (server-side); fixture spec-diff asserts only requested fields changed; refinement job reuses pipeline guarantees (idempotency, resume-at-image).
- **Manual checkpoint:** live refinement round; verify the drift disclaimer sets accurate expectations against the actual output; compare drift with Phase 2 findings.
- **Commit:** `feat: single-round design refinement with honest drift expectations`

## Phase 15 — Strict zero-cost demo mode
- **Scope:** `DEMO_MODE=true` path: curated pre-generated DesignSpecs + images stored as fixtures (`demo/` storage + JSON fixtures, generated once manually with real keys and committed/uploaded); demo pipeline maps questionnaire answers to the nearest fixture deterministically; identical API response shapes; simulated status transitions (queued → running_text → running_image → succeeded) with optional configurable artificial delay; demo designs flagged `is_demo`; showcase gallery endpoint + landing carousel from the same fixtures.
- **Non-goals:** demo mode never imports or reaches provider wrappers (separate code path, not a mocked wrapper); no rate-limited-paid anything labelled "demo".
- **Commands:** `DEMO_MODE=true docker compose up`; `pytest backend/ -k demo`; `npm run test`.
- **Automated tests:** with `DEMO_MODE=true`, a full generate + refine journey completes with **zero provider-wrapper invocations (asserted via instrumentation)** and no network egress to provider hosts (socket-blocking test fixture); response shapes validate against the OpenAPI schema identically to live mode; status transitions occur in order.
- **Manual checkpoint:** run the entire user journey in demo mode with no API keys configured at all; confirm it is indistinguishable in shape (and honest in labelling) from live mode.
- **Commit:** `feat: strict zero-cost deterministic demo mode with pre-generated fixtures`

## Phase 16 — Security and live-generation cost controls
- **Scope:** `LIVE_GENERATION_ENABLED` flag with `503 live_generation_disabled` when off; per-session + per-IP DRF/Redis throttles and daily generation count limit (`429 generation_limit_reached`); **hard daily cost ceiling with atomic reserve-before-spend**: compute a conservative maximum estimated cost for the call, atomically reserve it in Redis via a Lua script (or equivalent atomic transaction) *before* provider invocation, reject with `503 live_generation_budget_exhausted` if the reservation would exceed the ceiling, reconcile the reservation down to the estimated actual cost after the call, and release unused reservation on failure paths where no spend occurred — no check-then-increment race between workers. Security hardening pass (headers, CSP, cookie flags, admin lockdown; CORS surface stays minimal thanks to same-origin `/api/`); retention purge + stuck-job reconciler beat tasks; Sentry + structured logging correlation.
- **Non-goals:** no BudgetWindow table unless the Redis mechanism proves insufficient; no spend dashboard (ad-hoc GenerationAttempt queries only).
- **Commands:** `pytest backend/`; `docker compose exec backend python manage.py check --deploy`; manual abuse drill script.
- **Automated tests:** disabled live generation → 503 + code; limits → 429 + code; ceiling exhaustion → `503 live_generation_budget_exhausted` and showcase degrade, never an unhandled 5xx; **concurrency test: N parallel reservation attempts against a ceiling that fits N−1 admit exactly N−1** (proves atomicity); reservation reconciled after success and released after pre-spend failure; purge deletes rows *and* storage objects; stuck job (>10 min) reconciled to failed; throttle counters keyed by session and hashed IP.
- **Manual checkpoint:** self-run abuse drill locally (rapid-fire generates from two simulated IPs → throttled; force ceiling to $0 → showcase fallback with the budget error code; flip `LIVE_GENERATION_ENABLED` off → correct 503 UX).
- **Commit:** `feat: live-generation gating, rate limits, atomic cost ceiling, and security hardening`

## Phase 17 — UI polish and accessibility
- **Scope:** visual design pass (typography, spacing, colour system suited to bridal aesthetic), responsive behaviour, WCAG 2.1 AA pass on wizard/catalogue/results (focus management in the wizard, aria-live on progress states, alt text everywhere, contrast), empty/loading/error state polish, privacy + disclaimer pages.
- **Non-goals:** no new features; no i18n beyond string-structure readiness.
- **Commands:** `npm run test`; Lighthouse/axe runs (`npx @axe-core/cli` or browser audit).
- **Automated tests:** axe checks in component tests for wizard, catalogue, results; keyboard-navigation tests for the wizard.
- **Manual checkpoint:** Lighthouse accessibility ≥ 90 on landing, wizard, results; full keyboard-only journey; screen-reader spot check on the progress screen.
- **Commit:** `feat(frontend): UI polish, accessibility pass, and legal/disclaimer pages`

## Phase 18 — End-to-end tests and deployment
- **Scope:** Playwright E2E suite running the full journey **against demo mode** (zero cost, deterministic — the CI E2E environment sets `DEMO_MODE=true` and no provider keys); deployment config (assumption: single VPS or Railway/Render-class PaaS + managed Postgres + Redis + S3-compatible bucket) serving frontend and API from one public origin with `/api/` reverse-proxied to Django (§6), `DEMO_MODE=true, LIVE_GENERATION_ENABLED=false` as the public default; post-deploy smoke script hitting every public endpoint incl. `/healthz` and `/readyz`; `docs/RUNBOOK.md` (deploy, rotate keys, enable live generation safely, budget knobs).
- **Non-goals:** no autoscaling, no CDN beyond the storage host's default, no uptime SLOs.
- **Commands:** `npx playwright test`; deploy per runbook; `python scripts/smoke.py --base-url https://…`.
- **Automated tests:** E2E: full demo journey (questionnaire → generate → results → refine), throttle behaviour, error-state rendering; smoke script asserts 200s, `/readyz` health, and OpenAPI schema availability; CI runs E2E on every merge to main.
- **Manual checkpoint:** public deployment reachable; full journey works for an anonymous visitor at zero provider cost; flipping `LIVE_GENERATION_ENABLED=true` with real keys on a private instance performs one successful budgeted live generation; runbook followed verbatim by a cold read.
- **Commit:** `feat: Playwright E2E suite, deployment configuration, and runbook`
