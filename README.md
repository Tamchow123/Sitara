# Sitara

AI-assisted South Asian bridalwear **concept design**. A guided questionnaire, an optional pick of up to three rights-cleared inspiration images, and an AI-generated concept: a FLUX-rendered visual plus a structured design description authored by Claude, with one constrained refinement round.

> Sitara is for concept visualisation only. It does not produce sewing patterns or manufacturing specifications, and does not guarantee a garment can be constructed exactly as shown.

## Status

**Phase 4 — private design ownership.** On top of the Phase 3A/3B foundation (Next.js frontend, Django/DRF backend, PostgreSQL, Redis + Celery, private MinIO/S3 storage, health endpoints, fail-closed AI-provider boundary, session authentication with optional accounts — ADR 0003), Sitara now has its core design domain: `DesignSession` / `Design` / `DesignVersion` / `GenerationAttempt` models and a private design API (`/api/v1/designs/`). Designs are owned either by the anonymous browser session or by an authenticated user; an anonymous workspace is claimed automatically after login; anything inaccessible answers 404 — see `docs/decisions/0004-private-design-ownership.md`. The bridal questionnaire, design-spec generation and image generation arrive in later phases. Phase 2 (image-model evaluation) selected **`black-forest-labs/flux-1.1-pro`** — see `docs/decisions/0001-image-model.md` / `.json`.

## Layout

```
apps/api      Django + DRF backend (config/, sitara/{accounts,designs,health,ai_gateway})
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

## Security & privacy foundations (Phases 3A–4)

Implemented now: private storage bucket with no public ACLs; CORS/CSRF explicit allowlists; DRF authenticated-by-default; JSON-only API; secrets only via environment; tokens never logged or returned; demo mode provably unable to call paid providers; session authentication with HttpOnly cookies, JSON CSRF failure handling, hashed-identifier rate limiting and `Cache-Control: no-store` on all auth responses; private-by-construction designs (ownership filtering before every lookup, 404 for anything inaccessible, no raw session keys in domain tables, no public design URLs). Django endpoint permissions are the authorization boundary — the Next.js middleware redirect on `/account` is a navigation nicety only.

Deliberately **not yet** implemented (later phases): signed image delivery, email verification and password recovery, inspiration-image uploads with rights confirmation (max 3), the single-refinement limit enforcement, retention/deletion, quotas and cost ledgers.
