# Sitara

AI-assisted South Asian bridalwear **concept design**. A guided questionnaire, an optional pick of up to three rights-cleared inspiration images, and an AI-generated concept: a FLUX-rendered visual plus a structured design description authored by Claude, with one constrained refinement round.

> Sitara is for concept visualisation only. It does not produce sewing patterns or manufacturing specifications, and does not guarantee a garment can be constructed exactly as shown.

## Status

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

Paid generation will eventually require **both** `DEMO_MODE=false` **and** `ALLOW_PAID_AI_CALLS=true` **and** an actual paid-provider implementation: the public `generation_enabled` flag comes from a capability policy that also checks the code-level `PAID_PROVIDERS_IMPLEMENTED` constant, so the API can never advertise generation that does not exist — even with both environment gates open. Tests prove all of it.

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
# from the repo root
cd apps/api; python manage.py spectacular --format openapi-json --file openapi/schema.json --validate --fail-on-warn; cd ..
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

Deliberately **not yet** implemented (later phases): signed image delivery, email verification and password recovery, user selection of inspiration images on a design (max 3), the single-refinement limit enforcement, retention/deletion, quotas and cost ledgers.
