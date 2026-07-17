# Sitara Repository Instructions

This file applies to the whole repository. Read it before making changes.

Task-specific instructions may add constraints, but they must not weaken the security, privacy, rights, cost-control, or evidence-integrity rules below. When repository documentation and the implementation disagree, inspect the current code and tests, identify the discrepancy, and preserve the safer behaviour until the documentation is corrected.

## 1. Project purpose

Sitara is an AI-assisted South Asian bridalwear **concept-design** application.

A user will eventually:

1. complete a guided bridalwear questionnaire;
2. optionally select up to three rights-approved inspiration images;
3. receive a structured bridal design description;
4. receive a FLUX-generated visual concept;
5. request one constrained refinement.

Sitara is for **concept visualisation only**. It does not produce sewing patterns, manufacturing specifications, or guarantees that a garment can be constructed exactly as shown.

## 2. Product principles

Always preserve these principles:

- Cultural accuracy matters. Do not flatten distinct garments, regions, communities, or ceremonies into generic “South Asian” styling.
- Privacy is the default. Designs are never public merely because their UUID is known.
- Image rights must be documented and verified before catalogue approval or AI use.
- Accessibility is a product requirement, not a later polish task.
- Demo mode must make zero paid AI calls.
- Paid-provider access must fail closed and remain explicitly gated.
- Keep the application understandable and maintainable. Do not over-engineer simple Django or Next.js flows.
- Prefer small, reviewable vertical slices over broad speculative infrastructure.

## 3. Current repository state

At the time this file was created, Phase 5B was delivered on `main` and Phase 6 was next.

Delivered foundations include:

- Phase 2 image-model evaluation and locked evidence;
- Next.js and Django application foundation;
- Django session authentication and CSRF;
- anonymous and authenticated private design ownership;
- versioned questionnaire taxonomy;
- rights-controlled inspiration catalogue.

The roadmap is authoritative for future work. Always inspect the current branch and `docs/PHASES.md` rather than relying only on this status paragraph.

The current selected image model is:

```text
black-forest-labs/flux-1.1-pro
```

It is both the default and fast model unless a later, documented evaluation changes that decision.

## 4. Read these files first

For any substantial task, read the relevant code plus:

```text
README.md
docs/PROPOSAL.md
docs/PHASES.md
docs/decisions/
compose.yaml
.github/workflows/ci.yml
```

Then inspect the domain-specific files involved in the task.

Important decision records currently include:

```text
docs/decisions/0001-image-model.md
docs/decisions/0002-application-foundation.md
docs/decisions/0003-session-authentication.md
docs/decisions/0004-private-design-ownership.md
docs/decisions/0005-versioned-questionnaire-schema.md
docs/decisions/0006-rights-controlled-inspiration-catalogue.md
```

For a phase task stored in a separate specification file, read that file in full before editing.

## 5. Repository layout

```text
apps/api/       Django + Django REST Framework backend
apps/web/       Next.js App Router frontend with strict TypeScript
infra/minio/    Local private-bucket initialisation
experiments/    Phase 2 model-evaluation implementation and evidence
docs/           Proposal, roadmap, ADRs and project documentation
compose.yaml    Local PostgreSQL, Redis, MinIO, API, web and Celery stack
```

Django applications currently live under:

```text
apps/api/sitara/accounts/
apps/api/sitara/designs/
apps/api/sitara/questionnaire/
apps/api/sitara/catalogue/
apps/api/sitara/health/
apps/api/sitara/ai_gateway/
```

## 6. Technology and version discipline

Use the versions pinned by the repository. Do not opportunistically upgrade frameworks or dependencies.

Important runtime/tooling baselines:

- Python 3.12.7 in CI and the backend image;
- Node 22 in CI;
- Django and DRF versions from `apps/api/requirements.in`;
- Next.js, React and TypeScript versions from `apps/web/package.json`;
- PostgreSQL, Redis and MinIO versions from `compose.yaml`.

A dependency upgrade must be justified by the task, narrowly scoped, tested, and documented.

## 7. Non-negotiable AI and cost controls

Current safety gates are:

```text
DEMO_MODE=true
ALLOW_PAID_AI_CALLS=false
```

Rules:

- A present API key must never enable a provider call by itself.
- Automated tests and CI must make zero Anthropic or Replicate calls.
- Do not introduce network calls to paid providers in tests.
- Do not call providers manually unless the user explicitly authorises a budgeted live checkpoint and all documented gates are satisfied.
- Never log or return `ANTHROPIC_API_KEY`, `REPLICATE_API_TOKEN`, provider request bodies containing private user data, or provider credentials.
- All future provider access must go through the project’s fail-closed gateway/wrapper boundary, never directly from views, serializers, models, or frontend code.
- Do not change the selected model without a scoped, documented evaluation and decision update.
- Demo mode must use deterministic local fixtures and must remain structurally separate from paid-provider execution.

Use the current setting names from `config/settings.py`. Some older roadmap text contains superseded names; do not reintroduce old environment variables without an explicit migration decision.

## 8. Secrets and production configuration

- Never commit real credentials, tokens, cookies, connection strings, rights evidence documents, or private storage URLs.
- `.env` is local and gitignored. `.env.example` contains placeholders only.
- Production configuration must fail closed on missing, placeholder, or known development-only values.
- Error messages for configuration failures must name the setting and a safe reason, but never echo the rejected value.
- Strict boolean and positive-integer parsing must remain strict.
- Do not weaken host, CORS, CSRF, cookie, storage, or production-startup validation to make a test pass.
- Do not trust arbitrary proxy headers. Deployment-specific trusted-proxy behaviour requires an explicit decision.

## 9. Authentication and CSRF invariants

Authentication uses Django database sessions only.

Never add:

- JWTs;
- refresh tokens;
- DRF token authentication;
- access tokens in localStorage, sessionStorage or IndexedDB;
- Auth.js/NextAuth as a second authentication system;
- custom authentication cookies.

Cookie names are:

```text
sitara_sessionid
sitara_csrftoken
```

Preserve:

- HttpOnly session cookie;
- SameSite=Lax;
- Secure cookies outside debug mode;
- JSON CSRF failure responses;
- session-key rotation on login;
- server-confirmed logout before the frontend clears authenticated state;
- generic login failure messages that do not reveal whether an account exists;
- Redis-backed authentication throttling with hashed identifiers and fail-closed cache-outage behaviour.

Important CSRF rule: DRF `SessionAuthentication` does not by itself protect anonymous unsafe requests. Any anonymous POST, PATCH, PUT or DELETE endpoint must receive explicit normal Django CSRF enforcement. Never use `csrf_exempt` to bypass this.

`GET /api/v1/auth/csrf/` intentionally materialises the Django database session so later anonymous design operations can coordinate safely.

## 10. Same-origin frontend transport

Browser requests use relative `/api/...` paths through the Next.js rewrite.

Preserve:

```text
API_INTERNAL_BASE_URL
credentials: "same-origin"
cache: "no-store"
5-second request timeout
```

Never reintroduce `NEXT_PUBLIC_API_BASE_URL` or expose the internal Django host in the browser bundle.

CSRF tokens are held in memory only. Unsafe requests send `X-CSRFToken`. A CSRF retry may occur at most once.

Next.js middleware is a navigation optimisation only. Django permissions and ownership queries are the security boundary.

## 11. Private design ownership

Sitara supports both anonymous and authenticated ownership.

- Anonymous designs belong to the current Django browser session workspace.
- Authenticated designs belong to the user through one or more `DesignSession` rows.
- The browser’s internal workspace UUID is stored in Django session data under `sitara_design_session_id`.
- Domain tables never store a raw Django session key.
- Login preserves the anonymous workspace pointer; the next design request lazily claims it for the authenticated user.
- A workspace owned by another user must never be transferred or reused.
- Inaccessible, nonexistent and foreign designs return the same 404 response, not 403.
- Ownership filtering must happen before object lookup.
- A list request must not create an empty workspace.
- Concurrent first creates sharing one browser session must serialise on the database session row.
- Never add a public design slug or public-by-default sharing field without a separate approved phase.

Use transactions and row locks for lifecycle operations where concurrent requests could split ownership, exceed a limit, or create duplicate numbering.

## 12. Questionnaire rules

The active backend questionnaire schema is authoritative.

- Do not manually duplicate individual questionnaire rules in frontend code.
- Frontend validation must be derived from the machine-readable schema.
- Django must revalidate submitted answers and remains authoritative.
- Stable machine IDs are persistence contracts; do not casually rename them.
- Published questionnaire versions are immutable. Corrections require a new draft and activation.
- At most one version may be active.
- Schema validation must be total over arbitrary JSON-compatible data: malformed input must become a controlled schema error, never an incidental `TypeError`, `KeyError`, or traceback.
- Do not add executable expressions, `eval`, imports, arbitrary code, or a generic rules engine to questionnaire JSON.

Cultural distinctions already encoded in tests must remain intact, including:

- gharara and sharara are different constructions;
- saree draping is distinct from lehenga styling;
- regional influences are optional and non-prescriptive;
- modest coverage options remain represented;
- designer and brand names are not part of the controlled taxonomy.

## 13. Inspiration catalogue and image rights

The catalogue is staff-managed only.

Never add without a separately approved phase:

- user image uploads;
- remote URL imports;
- web scraping;
- automatic rights verification;
- public object ACLs;
- unverified images sent to AI providers.

Public eligibility requires all of the following on every request:

- asset status is approved;
- rights status is verified;
- rights are unexpired;
- public display is allowed;
- AI input is allowed;
- derivative generation is allowed;
- commercial use is allowed.

The central `publicly_eligible()` queryset is the single public-visibility definition. Use it for catalogue JSON and every image variant.

Image ingestion rules:

- staff upload bytes only;
- decoded JPEG, PNG or single-frame WebP only;
- reject corrupt, animated, multi-frame, oversized and decompression-bomb inputs;
- apply EXIF orientation;
- remove EXIF, GPS, XMP, ICC and comments;
- composite transparency;
- encode clean RGB WebP derivatives;
- retain no raw original;
- use server-generated keys containing no original filename or user identity;
- keep storage private;
- clean partial objects when storage or database persistence fails.

Do not expose storage keys, object-store URLs, image hashes, rights evidence, internal notes, verifier identities or staff details through public APIs.

Known future hardening: before adding any rights API, importer, or additional non-admin write path, add model/service-level immutability for rights records backing approved assets. The current admin freeze alone must not become the only protection once more write paths exist.

The manual checkpoint using three genuine rights-cleared images is still pending. Never substitute downloaded or unlicensed images or fabricate rights evidence. Locally generated synthetic images are acceptable for engineering tests when clearly labelled.

## 14. Storage rules

Object storage is private by default.

Preserve:

```text
default_acl = None
querystring_auth = True
file_overwrite = False
```

Do not expose MinIO/S3 endpoints or credentials through API responses, generated schemas, logs, or browser code.

Catalogue images currently stream through eligibility-checked Django endpoints. Design-image signed URLs arrive in a later phase and must remain ownership checked.

## 15. API conventions

- Keep global DRF permissions authenticated by default.
- Public or anonymous-session endpoints must opt into `AllowAny` explicitly and document why.
- Identity-free public GET endpoints should use `authentication_classes = []` and must not create Django sessions or `DesignSession` rows.
- Return JSON for API errors; do not leak Django HTML error pages.
- Use stable machine error codes and safe user-facing messages.
- Sensitive and immediately revocable responses use `Cache-Control: no-store`.
- Private-resource enumeration failures return indistinguishable 404 responses.
- Reject unknown and immutable write fields rather than silently ignoring them.
- Validate content type, malformed JSON, bounds and exact response shapes.
- Never expose model serializers wholesale when they include internal fields.
- Logs for sensitive failure paths should contain only safe operation names, row UUIDs where appropriate, and exception types. Avoid exception text and tracebacks when they may contain secrets, user input, storage keys or rights data.
- Broad exception containment is acceptable only at a deliberate API/admin boundary that returns a controlled response; domain code should use narrow exceptions and transaction rollback.

Runtime routes support slash-optional forms where required by the Next.js rewrite. Documentation and generated contracts should expose one canonical route only.

## 16. Backend implementation style

Prefer standard Django and DRF patterns.

- Models contain durable state and database constraints.
- Services contain multi-row transactions, lifecycle transitions, storage coordination and concurrency-sensitive operations.
- Serializers validate request/response shapes.
- Views remain thin.
- QuerySet helpers centralise security-sensitive visibility rules.
- Admin actions call the same services as any future trusted write interface.
- Use `transaction.atomic()` and `select_for_update()` where invariants span concurrent requests.
- Database constraints are final backstops, not substitutes for clear application errors.
- Do not add repository, command-bus, generic handler or use-case layers around simple Django operations.
- Avoid signals when an explicit service call is easier to understand and test.
- Do not bypass model/service invariants with `QuerySet.update()` except in migrations, narrowly scoped concurrency logic, or tests intentionally simulating corruption.
- UUIDs are used for externally referenced domain objects.
- All timestamps are timezone-aware.

## 17. Frontend implementation style

- Use the Next.js App Router and strict TypeScript.
- Prefer small local context/hooks over adding global state dependencies.
- Provide accessible labels, focus handling, loading states, error states and `aria` relationships.
- Do not persist passwords, CSRF tokens, cookies or session state in browser storage.
- Do not treat route guards as authorization.
- Keep server wire types generated from the OpenAPI contract once Phase 6 is delivered; do not hand-maintain competing interfaces.
- Client-only result unions may remain handwritten when they describe frontend behaviour rather than API wire contracts.
- Do not add Axios, Redux, React Query or other large dependencies unless a phase explicitly calls for them and the benefit is clear.

## 18. Dependency and generated-file workflow

### Python

Direct dependencies belong in:

```text
apps/api/requirements.in
```

The fully pinned, hash-verified lock is:

```text
apps/api/requirements.txt
```

Regenerate only after a genuine direct dependency change, using the exact pinned toolchain:

```powershell
docker run --rm -v "${PWD}\apps\api:/app" -w /app python:3.12.7-slim-bookworm `
  sh -c "python -m pip install --upgrade pip==26.0.1 && python -m pip install pip-tools==7.5.3 && python -m piptools compile --generate-hashes --output-file requirements.txt requirements.in"
```

Then regenerate a second time and verify determinism. Do not allow unrelated package upgrades.

### Node

Use `npm` and commit `apps/web/package-lock.json`. CI installs with `npm ci`.

### Generated files

Never hand-edit generated OpenAPI schemas, generated TypeScript types, migration files after generation, or dependency locks. Change the source and regenerate.

## 19. Database and migrations

When models change:

```powershell
docker compose exec api python manage.py makemigrations
docker compose exec api python manage.py migrate
docker compose exec api python manage.py makemigrations --check --dry-run
```

Rules:

- Keep migrations deterministic and reviewable.
- Add named constraints for durable invariants.
- Test database constraints against PostgreSQL, not only mocked or SQLite behaviour.
- Do not rewrite or delete applied migrations without an explicit migration strategy.
- Avoid migrations importing mutable runtime application functions; freeze migration logic where practical.

## 20. Testing commands

Start by inspecting the repository:

```powershell
git status --short
git log -5 --oneline
docker compose ps
```

For a focused change, run targeted tests during development. Before committing a substantive phase, run the full relevant regression suite.

### Backend

```powershell
docker compose config
docker compose build api
docker compose up -d
docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api python -m pip check
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
```

### Frontend

```powershell
docker compose exec web npm run lint
docker compose exec web npm run typecheck
docker compose exec web npm test -- --run
docker compose exec web npm run build
```

### Celery regression

```powershell
docker compose exec api python -c "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"
```

### Phase 2 evidence integrity

```powershell
cd experiments\model-eval
.venv\Scripts\python -m pytest tests\test_model_decision.py -q
cd ..\..
git status --short -- experiments/
```

Do not claim checks passed unless they were actually run and their results were observed.

## 21. Phase 2 evidence is frozen

Do not modify, regenerate, delete, stage or reformat anything under:

```text
experiments/model-eval/outputs/
```

Do not alter locked evaluation evidence or hashes unless the user explicitly commissions a new evaluation phase with a documented budget and review process.

## 22. CI expectations

CI runs on every push and pull request.

Backend CI includes:

- Python 3.12.7;
- PostgreSQL and Redis services;
- hash-verified dependency installation;
- dependency-lock freshness;
- Ruff lint and format checks;
- Django system checks;
- migration consistency;
- pytest.

Frontend CI includes:

- Node 22;
- `npm ci`;
- lint;
- typecheck;
- tests;
- production build.

Any generated-contract drift checks introduced by later phases are also mandatory. A local green run is not a substitute for hosted CI when the task requires CI confirmation.

## 23. Standard task workflow

For each implementation task:

1. Read this file, the task specification, relevant ADRs, and affected code.
2. Run `git status --short` and identify the current HEAD.
3. Do not overwrite unrelated user changes.
4. Establish a focused test baseline.
5. State the smallest safe implementation plan internally and avoid scope creep.
6. Implement the requested slice only.
7. Add tests for success, failure, privacy, security, concurrency and rollback where relevant.
8. Run targeted tests, then the required full regression checks.
9. Review the diff for secrets, unsafe logs, provider calls, public storage and accidental evidence changes.
10. Update docs and ADRs when an architectural decision or delivered phase changes.
11. Commit only when the task requests it.
12. Do not amend, squash or rewrite previous history unless explicitly instructed.
13. Push only when explicitly requested or when the task specification already authorises it.

When a genuine blocker exists, stop before making an unsafe assumption and report the exact blocker.

## 24. Commit policy

- Prefer one focused commit per independently reviewable concern.
- Separate prerequisite fixes from feature work.
- Use conventional, descriptive messages such as:

```text
fix(questionnaire): validate enum field types
feat(catalogue): add rights-controlled inspiration catalogue
```

- Do not mix dependency upgrades, formatting sweeps or unrelated refactors into a feature commit.
- Keep the working tree clean after a requested commit.

## 25. Response format

Keep final reports compact. Do not repeat the entire task specification.

Return only:

1. outcome and unresolved blockers;
2. files or areas changed;
3. important security/architecture decisions;
4. tests and checks actually run with results;
5. provider-call, secret, storage and Phase 2 integrity confirmation where relevant;
6. commit SHA and hosted CI result when requested.

Do not produce a long numbered report unless the task explicitly requests one.

## 26. Prohibited actions

Never do any of the following without explicit, task-specific authorisation:

- run `docker compose down --volumes`;
- delete or reset development volumes;
- force-push or rewrite Git history;
- commit real secrets or `.env`;
- make paid AI calls;
- enable provider calls because a token exists;
- change the chosen image model without evaluation;
- scrape or import unlicensed images;
- fabricate rights evidence;
- create public S3/MinIO ACLs;
- expose private storage keys or URLs;
- weaken CSRF, ownership, cookie, rate-limit or production validation;
- introduce JWT or browser-stored auth tokens;
- reveal whether an inaccessible private object exists;
- modify Phase 2 output evidence;
- claim tests, manual checks, provider-call absence or CI success without evidence.

## 27. Efficient task prompt

After this file is committed, a normal phase prompt can be concise:

```text
Read CLAUDE.md, docs/PHASES.md, the relevant ADRs, and <task-spec-file>.
Inspect the current repository and implement the task exactly as specified.
Run the required checks, create the requested focused commit(s), and return only
outcome, changed areas, test results, unresolved issues, commit SHA(s), and CI status.
```
