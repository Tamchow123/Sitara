# Sitara Repository Instructions

This file applies to the whole repository. Read it before making changes.

Task-specific instructions may add constraints but must not weaken the security, privacy, rights, cost-control, or evidence-integrity rules below. When documentation and implementation disagree, inspect the current code and tests, identify the discrepancy, and preserve the safer behaviour until the documentation is corrected.

## 1. Project purpose

Sitara is an AI-assisted South Asian bridalwear **concept-design** application. A user will: complete a guided bridalwear questionnaire; optionally select up to three rights-approved inspiration images; receive a structured bridal design description; receive a FLUX-generated visual concept; request one constrained refinement.

Sitara is for **concept visualisation only** — no sewing patterns, manufacturing specs, or construction guarantees.

## 2. Product principles

- Cultural accuracy matters. Do not flatten distinct garments, regions, communities, or ceremonies into generic "South Asian" styling.
- Privacy is the default. Designs are never public merely because their UUID is known.
- Image rights must be documented and verified before catalogue approval or AI use.
- Accessibility is a product requirement, not later polish.
- Demo mode makes zero paid AI calls. Paid-provider access fails closed and stays explicitly gated.
- Keep Django/Next.js flows understandable; prefer small, reviewable vertical slices over speculative infrastructure.

## 3. Current repository state

Phases 1–12 are delivered on `main`. Phase 13 (rights-safe inspiration metadata influence, ADR 0014) is delivered on `phase/phase-13-inspiration-metadata`, pending merge to `main`; Phase 14 (constrained refinement) is next. Delivered: Phase 2 image-model evaluation; app foundation; session auth/CSRF; anonymous + authenticated design ownership; versioned questionnaire; rights-controlled catalogue; OpenAPI-generated client; structured DesignSpec generation; deterministic image-prompt builder; async Celery/Replicate generation; permanent design-image storage; generation-progress and private results; curated inspiration-metadata influence on generation.

`docs/phases/PHASES.md` is authoritative for future work — always inspect the current branch and that file rather than relying on this paragraph.

Selected image model (default and fast) unless a later documented evaluation changes it:

```text
black-forest-labs/flux-1.1-pro
```

## 4. Read these files first

For any substantial task, read the relevant code plus `README.md`, `docs/PROPOSAL.md`, `docs/phases/PHASES.md`, `docs/decisions/`, `compose.yaml`, `.github/workflows/ci.yml`. For a phase task with its own spec file, read that file in full before editing.

ADRs currently on record: 0001 image model, 0002 application foundation, 0003 session authentication, 0004 private design ownership, 0005 versioned questionnaire schema, 0006 rights-controlled inspiration catalogue, 0007 OpenAPI generated client, 0008 questionnaire draft and wizard, 0009 structured design-spec generation, 0010 deterministic image-prompt builder, 0011 asynchronous generation pipeline, 0012 private design-image storage, 0013 generation progress and results, 0014 rights-safe inspiration metadata influence.

## 5. Repository layout

```text
apps/api/       Django + Django REST Framework backend
apps/web/       Next.js App Router frontend with strict TypeScript
infra/minio/    Local private-bucket initialisation
experiments/    Phase 2 model-evaluation implementation and evidence
docs/           Proposal, roadmap, ADRs and project documentation
compose.yaml    Local PostgreSQL, Redis, MinIO, API, web and Celery stack
```

Django apps under `apps/api/sitara/`: `accounts`, `designs`, `questionnaire`, `catalogue`, `health`, `ai_gateway` (fail-closed provider gateway: gating policy, Anthropic/Replicate wrappers), `generation` (pipeline orchestration, DesignSpec generation, prompt builder/service, Celery tasks, demo fixtures). `apps/api/sitara/media/` is a support package (image processing, ingest, signed delivery) for permanent design images — not a Django app.

## 6. Technology and version discipline

Use the versions pinned by the repository; do not opportunistically upgrade. Baselines: Python 3.12.7 (CI/image); Node 22 (CI); Django/DRF from `apps/api/requirements.in`; Next.js/React/TypeScript from `apps/web/package.json`; PostgreSQL/Redis/MinIO from `compose.yaml`. An upgrade must be justified by the task, narrowly scoped, tested, and documented.

## 7. Non-negotiable AI and cost controls

Safety gates (`apps/api/config/settings.py`): `DEMO_MODE=true`, `ALLOW_PAID_AI_CALLS=false`, `LIVE_GENERATION_ENABLED=false`. `LIVE_GENERATION_ENABLED` gates the PUBLIC end-to-end generation API — a present token, both provider gates open, and complete provider config are still not enough; the operator must also set this flag. Public live generation must not be enabled before Phase 16 rate-limit/cost-ceiling safeguards exist.

Related settings: `DEFAULT_IMAGE_MODEL`, `FAST_IMAGE_MODEL`, `ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY`, `REPLICATE_API_TOKEN`, `REPLICATE_TIMEOUT_SECONDS`, `REPLICATE_POLL_INTERVAL_SECONDS`/`_TIMEOUT_SECONDS`, `GENERATION_RAW_MAX_BYTES`/`_MAX_PIXELS`, `DESIGN_SPEC_MAX_INPUT_CHARS`/`_MAX_OUTPUT_TOKENS`, `ANTHROPIC_TIMEOUT_SECONDS`, `MAX_DESIGN_VERSIONS`, `MAX_INSPIRATION_IMAGES`/`MAX_REFINEMENTS`. Some older roadmap text uses superseded names (e.g. `ALLOW_PROVIDER_CALLS`); do not reintroduce them without an explicit migration decision.

Rules:

- A present API key must never enable a provider call by itself.
- Automated tests and CI make zero Anthropic or Replicate calls; do not introduce such network calls in tests.
- Do not call providers manually unless the user explicitly authorises a budgeted live checkpoint with all documented gates satisfied.
- Never log or return API keys/tokens, provider request bodies containing private user data, or provider credentials.
- All provider access goes through the `ai_gateway` fail-closed wrapper boundary, never directly from views, serializers, models, or frontend code.
- Do not change the selected model without a scoped, documented evaluation and decision update.
- Demo mode uses deterministic local fixtures and stays structurally separate from paid-provider execution.
- The image prompt is built only by the deterministic, versioned `build_image_prompt` (`generation/prompt_builder.py`) from a validated DesignSpec: one positive natural-language prompt, no negative prompt, no JSON prompt, no hard-coded model id, no provider call, no construction caveats/alt text/inspiration metadata/raw questionnaire text/provider metadata. Persisted `image_prompt`/`prompt_builder_version` are immutable audit data; a builder change requires a `PROMPT_BUILDER_VERSION` bump plus a reviewed snapshot/manifest update.

## 8. Secrets and production configuration

Never commit real credentials, tokens, cookies, connection strings, rights evidence, or private storage URLs. `.env` is local and gitignored; `.env.example` holds placeholders only. Production config fails closed on missing, placeholder, or dev-only values. Config-failure messages name the setting and a safe reason, never the rejected value. Boolean/positive-integer parsing stays strict. Do not weaken host, CORS, CSRF, cookie, storage, or production-startup validation to make a test pass. Do not trust arbitrary proxy headers; trusted-proxy behaviour requires an explicit decision.

## 9. Authentication and CSRF invariants

Authentication uses Django database sessions only. Never add JWTs, refresh tokens, DRF token auth, browser-stored tokens (localStorage/sessionStorage/IndexedDB), Auth.js/NextAuth, or custom auth cookies. Cookies: `sitara_sessionid`, `sitara_csrftoken`.

Preserve: HttpOnly session cookie; `SameSite=Lax`; Secure cookies outside debug; JSON CSRF failure responses; session-key rotation on login; server-confirmed logout before the frontend clears authenticated state; generic login failure messages; Redis-backed auth throttling with hashed identifiers and fail-closed cache-outage behaviour.

DRF `SessionAuthentication` alone does not protect anonymous unsafe requests — any anonymous POST/PATCH/PUT/DELETE needs explicit normal Django CSRF enforcement; never `csrf_exempt` to bypass it. `GET /api/v1/auth/csrf/` intentionally materialises the Django session so anonymous design operations can coordinate.

## 10. Same-origin frontend transport

Browser requests use relative `/api/...` paths through the Next.js rewrite. Preserve `API_INTERNAL_BASE_URL`, `credentials: "same-origin"`, `cache: "no-store"`, a 5-second request timeout. Never reintroduce `NEXT_PUBLIC_API_BASE_URL` or expose the internal Django host in the browser bundle. CSRF tokens are held in memory only; unsafe requests send `X-CSRFToken`; at most one CSRF retry. Next.js middleware is a navigation optimisation only — Django permissions and ownership queries are the security boundary.

## 11. Private design ownership

- Anonymous designs belong to the current Django session workspace; authenticated designs belong to the user via `DesignSession` rows. The workspace UUID lives in session data under `sitara_design_session_id`; domain tables never store a raw session key.
- Login preserves the anonymous workspace pointer; the next design request lazily claims it for the authenticated user. A workspace owned by another user is never transferred or reused.
- Inaccessible, nonexistent, and foreign designs all return the same 404, not 403. Ownership filtering happens before object lookup. A list request must not create an empty workspace.
- Concurrent first creates sharing one session serialise on the database session row. Never add a public design slug or public-by-default sharing without a separate approved phase.
- Use transactions and row locks for lifecycle operations where concurrency could split ownership, exceed a limit, or duplicate numbering.

## 12. Questionnaire rules

The active backend schema is authoritative — do not duplicate its rules in frontend code; frontend validation is derived from the machine-readable schema. Django revalidates and remains authoritative. Stable machine IDs are persistence contracts; do not casually rename them. Published versions are immutable (corrections need a new draft + activation); at most one version is active. Schema validation is total over arbitrary JSON — malformed input becomes a controlled schema error, never a raw `TypeError`/`KeyError`/traceback. No `eval`, executable expressions, imports, or generic rules engine in questionnaire JSON.

Cultural distinctions to keep intact: gharara vs. sharara are different constructions; saree draping is distinct from lehenga styling; regional influences are optional and non-prescriptive; modest coverage options remain represented; designer/brand names are not part of the controlled taxonomy.

## 13. Inspiration catalogue and image rights

Staff-managed only. Never add without a separately approved phase: user uploads, remote URL imports, scraping, automatic rights verification, public object ACLs, or unverified images sent to AI providers.

Public eligibility requires, on every request: approved status, verified and unexpired rights, and public-display, AI-input, derivative-generation, and commercial-use all allowed. The central `publicly_eligible()` queryset is the single definition — use it for catalogue JSON and every image variant.

Ingestion: staff bytes only; decoded JPEG/PNG/single-frame WebP only; reject corrupt/animated/multi-frame/oversized/decompression-bomb input; apply EXIF orientation then strip EXIF/GPS/XMP/ICC/comments; composite transparency; encode clean RGB WebP; retain no raw original; server-generated keys with no filename/identity; private storage; clean up partial objects on failure.

Never expose storage keys, object-store URLs, image hashes, rights evidence, internal notes, verifier identities, or staff details through public APIs. Before any rights API, importer, or additional non-admin write path, add model/service-level immutability for rights records backing approved assets — the current admin freeze alone must not become the only protection.

The manual checkpoint using three genuine rights-cleared images is still pending. Never substitute downloaded/unlicensed images or fabricate rights evidence; locally generated synthetic images are fine for clearly labelled engineering tests.

Selected inspiration image bytes, URLs and storage keys must never be sent to any AI provider (Phase 13, ADR 0014). Provider-facing inspiration influence is restricted to the frozen `garment_type`/`alt_text`/`cultural_context` fields, built only through `generation/inspiration_context.py`'s versioned, hashed `InspirationContextSnapshot` — re-validated against `publicly_eligible()` and the generated-content safety scan every time a design is generated, never trusted from a prior selection. Asset UUID, title and public attribution may be persisted for private audit/acknowledgement display but must never reach a provider. A `DesignVersion`'s persisted `inspiration_context`/`_schema_version`/`_sha256` is immutable historical audit data (read-only in admin, all-or-none database constraints) — a later asset retirement, expiry or rights revocation blocks future selection but must never rewrite an existing design's stored snapshot or acknowledgement. Reference-image conditioning stays fail-closed (`ReferenceImagesNotEnabled`); enabling it requires a separately approved phase with its own rights, pricing and provider-terms review — do not add a flag or partial implementation ahead of that phase.

## 14. Storage rules

Object storage is private by default. Preserve `default_acl = None`, `querystring_auth = True`, `file_overwrite = False`. Never expose MinIO/S3 endpoints or credentials via API responses, schemas, logs, or browser code. Catalogue images stream through eligibility-checked Django endpoints.

Permanent design images (`media/` package, Phase 11+):

- All permanent generated-image operations use the `design_images` storage alias resolved at call time via `django.core.files.storage.storages` — never a module-level storage instance.
- `DesignVersion` image provenance is immutable audit data (all-or-none). A changed processor requires a `DESIGN_IMAGE_PROCESSOR_VERSION` bump plus a reviewed golden-manifest update, producing new `DesignVersion`s — never rewrites.
- Signed design-image URLs are issued only by the ownership-checked images endpoint (`GET /designs/<uuid>/versions/<uuid>/images/`, with an inline/attachment `disposition` param). They are temporary bearer URLs: short TTL, never persisted/cached/logged anywhere, never presented as revocable or non-shareable. A backend proxy is the documented upgrade path.
- The filesystem design-image backend is development-only: no public base URL, browser delivery fails closed, production refuses it.
- Phase 10 staging objects/metadata are retained after ingest for crash recovery; purging them is Phase 16 work.

Phase 12 results (`GET /designs/<uuid>/versions/<uuid>/result/`) return a curated, DesignSpec-derived result independent of the signed-image endpoint — frontend fetches result data and the signed image via two independent queries so one failing doesn't block the other. Job status is polled at `GET /jobs/<uuid>/`; `Design` detail responses carry an additive `latest_job` field for resume navigation.

## 15. API conventions

Keep global DRF permissions authenticated by default; public/anonymous-session endpoints opt into `AllowAny` explicitly and document why. Identity-free public GET endpoints use `authentication_classes = []` and must not create sessions or `DesignSession` rows. Return JSON for API errors, never Django HTML error pages. Use stable machine error codes and safe user-facing messages. Sensitive/revocable responses use `Cache-Control: no-store`. Private-resource enumeration failures return indistinguishable 404s. Reject unknown/immutable write fields rather than ignoring them. Validate content type, malformed JSON, bounds, and exact response shapes. Never expose model serializers wholesale when they include internal fields. Sensitive-failure-path logs contain only safe operation names, row UUIDs, and exception types — avoid exception text/tracebacks that may carry secrets, input, storage keys, or rights data. Broad exception containment is only for a deliberate API/admin boundary; domain code uses narrow exceptions and transaction rollback.

Runtime routes support slash-optional forms where the Next.js rewrite needs them; documentation/generated contracts expose one canonical route.

## 16. Backend implementation style

Standard Django/DRF patterns: models hold durable state and constraints; services hold multi-row transactions, lifecycle transitions, storage coordination, and concurrency-sensitive operations; serializers validate shapes; views stay thin; QuerySet helpers centralise security-sensitive visibility; admin actions call the same services as any future trusted write interface. Use `transaction.atomic()`/`select_for_update()` where invariants span concurrent requests. Database constraints are final backstops, not substitutes for clear application errors. Do not add repository/command-bus/generic-handler/use-case layers around simple Django operations. Avoid signals when an explicit service call is clearer. Do not bypass invariants with `QuerySet.update()` except in migrations, narrow concurrency logic, or tests intentionally simulating corruption. UUIDs for externally referenced domain objects; all timestamps timezone-aware.

## 17. Frontend implementation style

Next.js App Router, strict TypeScript. Prefer small local context/hooks over global state dependencies. Provide accessible labels, focus handling, loading/error states, and `aria` relationships. Never persist passwords, CSRF tokens, cookies, or session state in browser storage. Do not treat route guards as authorization. Keep server wire types generated from the OpenAPI contract; do not hand-maintain competing interfaces. Client-only result unions may stay handwritten when they describe frontend behaviour rather than API wire contracts. TanStack Query (`@tanstack/react-query`) is approved and in use for the Phase 12 generation-progress/result polling flow only (backoff 1s/2s/5s) — do not expand it into a general data layer, and do not add Axios/Redux or other large dependencies without an explicit phase need.

## 18. Dependency and generated-file workflow

**Python**: direct deps in `apps/api/requirements.in`; pinned hash-verified lock in `apps/api/requirements.txt`. Regenerate only after a genuine direct dependency change, using the exact pinned toolchain:

```powershell
docker run --rm -v "${PWD}\apps\api:/app" -w /app python:3.12.7-slim-bookworm `
  sh -c "python -m pip install --upgrade pip==26.0.1 && python -m pip install pip-tools==7.5.3 && python -m piptools compile --generate-hashes --output-file requirements.txt requirements.in"
```

Regenerate a second time and verify determinism; do not allow unrelated upgrades.

**Node**: `npm`, commit `apps/web/package-lock.json`; CI installs with `npm ci`.

**Generated files**: never hand-edit generated OpenAPI schemas, generated TypeScript types, migrations after generation, or dependency locks — change the source and regenerate.

## 19. Database and migrations

```powershell
docker compose exec api python manage.py makemigrations
docker compose exec api python manage.py migrate
docker compose exec api python manage.py makemigrations --check --dry-run
```

Keep migrations deterministic and reviewable; add named constraints for durable invariants; test constraints against PostgreSQL, not only SQLite/mocks. Do not rewrite or delete applied migrations without an explicit strategy. Avoid migrations importing mutable runtime application functions.

## 20. Testing commands

Start with `git status --short`, `git log -5 --oneline`, `docker compose ps`. Run targeted tests during development; run the full regression suite before committing a substantive phase.

**Backend**: `docker compose config`; `docker compose build api`; `docker compose up -d`; then inside `api`: `manage.py check`, `manage.py makemigrations --check --dry-run`, `pip check`, `pytest`, `ruff check .`, `ruff format --check .`.

**Frontend**: inside `web`: `npm run lint`, `npm run typecheck`, `npm test -- --run`, `npm run build`.

**Celery**: `docker compose exec api python -c "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"`.

**Phase 2 evidence integrity**: `cd experiments\model-eval && .venv\Scripts\python -m pytest tests\test_model_decision.py -q && cd ..\.. && git status --short -- experiments/`.

Do not claim checks passed unless they were actually run and their results observed.

## 21. Phase 2 evidence is frozen

Never modify, regenerate, delete, stage, or reformat anything under `experiments/model-eval/outputs/`, or alter locked evaluation evidence/hashes, unless the user explicitly commissions a new evaluation phase with a documented budget and review process.

## 22. CI expectations

Runs on every push and PR. Backend: Python 3.12.7, Postgres and Redis services, hash-verified install, dependency-lock freshness, Ruff lint/format, Django checks, migration consistency, an OpenAPI schema freshness/contract-drift check (`spectacular ... --validate --fail-on-warn` + `git diff --exit-code`), pytest. Frontend: Node 22, `npm ci`, a generated-API-types drift check, lint, typecheck, tests, production build. A local green run is not a substitute for hosted CI when the task requires CI confirmation.

## 23. Standard task workflow

Read this file, the task spec, relevant ADRs, and affected code. Run `git status --short` and note HEAD; never overwrite unrelated user changes. Establish a focused test baseline. Plan the smallest safe implementation and avoid scope creep. Implement the requested slice only. Add tests for success, failure, privacy, security, concurrency, and rollback where relevant. Run targeted tests, then required full regression checks. Review the diff for secrets, unsafe logs, provider calls, public storage, and accidental evidence changes. Update docs/ADRs when an architectural decision or delivered phase changes. Commit only when requested; never amend/squash/rewrite history unless explicitly instructed; push only when explicitly requested or already authorised.

When a genuine blocker exists, stop before an unsafe assumption and report the exact blocker.

## 24. Commit policy

One focused commit per independently reviewable concern; separate prerequisite fixes from feature work. Conventional, descriptive messages (e.g. `fix(questionnaire): validate enum field types`, `feat(catalogue): add rights-controlled inspiration catalogue`). Do not mix dependency upgrades, formatting sweeps, or unrelated refactors into a feature commit. Keep the working tree clean after a requested commit.

## 25. Response format

Keep final reports compact; do not repeat the task specification. Return only: outcome and unresolved blockers; files/areas changed; important security/architecture decisions; tests and checks actually run with results; provider-call, secret, storage, and Phase 2 integrity confirmation where relevant; commit SHA and hosted CI result when requested. No long numbered report unless explicitly requested.

## 26. Prohibited actions

Never do the following without explicit, task-specific authorisation: `docker compose down --volumes`; delete/reset dev volumes; force-push or rewrite Git history; commit real secrets or `.env`; make paid AI calls; enable provider calls because a token exists; change the image model without evaluation; scrape/import unlicensed images; fabricate rights evidence; create public S3/MinIO ACLs; expose private storage keys/URLs; weaken CSRF/ownership/cookie/rate-limit/production validation; introduce JWT or browser-stored auth tokens; reveal whether an inaccessible private object exists; modify Phase 2 output evidence; claim tests/manual checks/provider-call absence/CI success without evidence.

## 27. Efficient task prompt

```text
Read CLAUDE.md, docs/phases/PHASES.md, the relevant ADRs, and <task-spec-file>.
Inspect the current repository and implement the task exactly as specified.
Run the required checks, create the requested focused commit(s), and return only
outcome, changed areas, test results, unresolved issues, commit SHA(s), and CI status.
```

## 28. Automated phase development — `/run-phase`

`/run-phase <phase-identifier> <requirements-file-or-description>` is the normal phase-development command. The workflow is provided by the user-level phase-council installation under `~/.claude/` (skills `run-phase`/`resume-phase`, six council reviewers and a chair under `agents/phase-council/`, and phase-gated safety hooks in user settings). This repository contributes only `.claude/phase-council.json` (base branch, protected branches, exact build/test/lint/format/typecheck commands from §20); runtime state and reports stay under `.claude/review/` (see its `README.md` for the pointer map).

Once started, Claude acts as phase orchestrator and continues without routine user intervention through planning, implementation, per-commit review, fixing, committing, full-phase verification, pushing, and draft-PR creation, until reaching exactly one terminal state: `PR_READY`, `BLOCKED`, or `ABORTED_SAFELY`.

Binding rules, in addition to every rule above:

- Every commit must pass the six-reviewer read-only council (functionality, clean-code, architecture, security, testing, reliability) plus the chair, on the exact staged diff whose SHA-256 still matches the approved hash. Every phase must pass the full council over the whole `base..HEAD` diff.
- Reviewers and the chair are read-only; only the orchestrator edits application files.
- Fix blocking findings automatically (add regression tests, re-review) rather than accepting or downgrading them without evidence. Unresolved P3s become PR technical debt.
- Never commit or push directly to `main`, never merge the PR or mark it ready — the outcome is a fully-reviewed draft PR into `main` for manual merge.
- Requirements, executable evidence, and code evidence override any implementation summary or done-claim.
- Retry limits (then `BLOCKED`): 3 implementation attempts/task, 4 council cycles/commit, 5 full-phase cycles, 3 CI cycles, 3 attempts/finding.

`/resume-phase` recovers an interrupted run from `.claude/review/runtime/active-phase.json` — not part of the normal workflow. While a phase is active, the user-level Stop hook prevents ending the turn early, and the PreToolUse git-guard hook blocks protected-branch writes, force-pushes, history rewrites, hard resets, destructive cleans, PR merges, and unapproved commits. Both hooks are inert when no phase is active; the `run-phase` skill is the workflow engine, the hooks are deterministic safety nets.
