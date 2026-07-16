# Sitara

AI-assisted South Asian bridalwear **concept design**. A guided questionnaire, an optional pick of up to three rights-cleared inspiration images, and an AI-generated concept: a FLUX-rendered visual plus a structured design description authored by Claude, with one constrained refinement round.

> Sitara is for concept visualisation only. It does not produce sewing patterns or manufacturing specifications, and does not guarantee a garment can be constructed exactly as shown.

## Status

**Phase 3A — application foundation.** The production-shaped monorepo skeleton is in place: Next.js frontend, Django/DRF backend, PostgreSQL, Redis + Celery, private MinIO/S3 storage, health endpoints, a fail-closed AI-provider boundary, tests and CI. The bridal questionnaire, design-spec generation and image generation arrive in later Phase 3 tasks. Phase 2 (image-model evaluation) selected **`black-forest-labs/flux-1.1-pro`** — see `docs/decisions/0001-image-model.md` / `.json`.

## Layout

```
apps/api      Django + DRF backend (config/, sitara/{accounts,health,ai_gateway})
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

Paid generation will eventually require **both** `DEMO_MODE=false` **and** `ALLOW_PAID_AI_CALLS=true`; no paid provider is implemented in Phase 3A at all, and tests prove the gates hold.

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

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/health/live
Invoke-RestMethod http://localhost:8000/api/v1/health/ready
Invoke-RestMethod http://localhost:8000/api/v1/config/public
```

### 7. Frontend

Open <http://localhost:3001> — the foundation page shows backend connection, database/Redis/storage readiness, and the demo-mode badge.

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

## Security & privacy foundations (Phase 3A)

Implemented now: private storage bucket with no public ACLs; CORS/CSRF explicit allowlists; DRF authenticated-by-default; JSON-only API; secrets only via environment; tokens never logged or returned; demo mode provably unable to call paid providers.

Deliberately **not yet** implemented (later phases): signed image delivery, authentication endpoints, inspiration-image uploads with rights confirmation (max 3), the single-refinement limit enforcement, retention/deletion, quotas and cost ledgers.
