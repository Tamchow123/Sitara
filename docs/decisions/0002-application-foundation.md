# 0002 — Application foundation (Phase 3A)

- **Status:** accepted (implementation and full validation passed 2026-07-16)
- **Date:** 2026-07-16
- **Deciders:** project owner
- **Phase:** Phase 3A (first application task after the Phase 2 model decision)

## Context

Phase 2 closed with `black-forest-labs/flux-1.1-pro` selected as both the
default production and paid fast/development image model, and demo mode
defined as fixture-only with zero paid AI calls
(`docs/decisions/0001-image-model.md` / `.json`). Phase 3A lays the smallest
production-shaped foundation that later tasks (questionnaire, design-spec
generation, image generation, refinement) can build on without re-architecting.

## Decision

- **Monorepo layout** — `apps/api` (Django) and `apps/web` (Next.js) beside
  the frozen `experiments/` evidence and `docs/`; one `compose.yaml` runs the
  whole stack. No microservices: the backend is ONE modular Django project
  (`sitara.accounts`, `sitara.health`, `sitara.ai_gateway`).
- **Next.js / Django boundary** — the browser talks only to the DRF API
  (`NEXT_PUBLIC_API_BASE_URL`); DRF defaults to authenticated access with
  JSON-only rendering, and only health/public-config endpoints allow
  anonymous access. CORS and CSRF origins are explicit allowlists.
- **PostgreSQL** — relational source of truth, configured via `DATABASE_URL`.
- **Redis + Celery** — queue and result backend for the asynchronous
  generation pipeline to come; proven now with a harmless `health.tasks.ping`
  task and no task-enqueueing endpoint. No Celery Beat yet.
- **Private S3-compatible storage** — MinIO locally, any S3-compatible
  provider in production, via Django's `STORAGES` with django-storages:
  no public ACLs, signed query auth, no file overwrite. Signed delivery /
  authenticated streaming is a later phase.
- **Custom user model first** — `accounts.User` (UUID primary key, unique
  email as the login identifier, standard Django password handling, admin
  compatible) exists before any other production migration, avoiding the
  classic mid-project user-model migration. Authentication ENDPOINTS are a
  later Phase 3 task.
- **Fail-closed demo/paid-AI policy** — committed defaults `DEMO_MODE=true`
  and `ALLOW_PAID_AI_CALLS=false`; a configured token never enables paid
  calls; paid generation will require BOTH gates, and Phase 3A ships no paid
  provider at all. CI runs with the gates closed and no provider secrets.
- **Provider abstraction** — `sitara.ai_gateway` defines
  `StructuredDesignProvider` / `ImageGenerationProvider` protocols with
  deterministic, network-free demo implementations, and a policy factory
  that is the only sanctioned way to obtain a provider. Tests prove demo
  mode always wins, tokens don't bypass it, both gates are required, and no
  network client is invoked.
- **Configuration-based FLUX 1.1 Pro default** — `DEFAULT_IMAGE_MODEL` /
  `FAST_IMAGE_MODEL` environment variables default to the Phase 2 winner;
  no model id is hard-coded in application logic.

## Why no domain workflows yet

The questionnaire, design-spec schema, prompt builder, generation pipeline
and refinement each carry meaningful design decisions (validated in the
Phase 2 experiment) and deserve small, reviewable tasks on top of a proven
substrate. Shipping them inside the scaffold task would couple foundational
review to domain review and invite architecture-by-accident. Phase 3A
therefore proves only: services start, dependencies are healthy, the
frontend talks to the backend, safety gates hold, and quality tooling runs.

## Consequences

Later tasks add domain models/migrations on top of a stable user model,
plug real providers in behind the existing policy gates (never around
them), and inherit CI that always runs fail-closed. Local port 3001 is used
for the web app on the development machine because 3000 is occupied by an
unrelated project (`WEB_PORT` overrides).

## Hardening amendments (2026-07-16, same phase)

Applied before authentication/questionnaire work begins:

- **Strict environment classification** — `APP_ENV` must be exactly one of
  `development | test | production`; anything else (e.g. `prod`,
  `Production`) refuses startup instead of silently running as development.
- **Production placeholder rejection** — production startup rejects empty
  values, the internal development secret, `change-me`/`__REPLACE_ME__`
  sentinels and the committed development/CI example values for the secret
  key, database URL, storage credentials and allowed hosts; browser-origin
  allowlists must be set explicitly (or `SAME_ORIGIN_DEPLOYMENT=true`
  declared). Refusal messages name only the variable, never the value.
- **Case-insensitive email identity** — emails are trimmed and lower-cased
  on every save, authentication lookup is canonical, and PostgreSQL
  enforces a `Lower(email)` unique constraint (migration
  `accounts.0002`, which fails loudly on pre-existing collisions rather
  than merging accounts).
- **Capability-aware generation status** — `generation_is_available()`
  combines the two environment gates with the code-level
  `PAID_PROVIDERS_IMPLEMENTED` flag (deliberately NOT an environment
  variable), so `/api/v1/config/public` can never claim generation exists
  while no paid provider is implemented — even with both gates open — and
  can never contradict the provider factory.
- **Credential-safe readiness logging** — dependency-check failures log
  only the check name and exception type; never `str(exception)`,
  connection strings or tracebacks.
- **Loopback-only local ports** — all host-published dev services bind to
  `${BIND_HOST:-127.0.0.1}`; the web container waits for a HEALTHY api.
- **Frontend request timeout** — a 5s `AbortController` timeout turns
  half-open connections, network errors and malformed JSON into the
  explicit backend-unavailable state.
- **Fully locked dependencies** — `requirements.in` (direct pins) compiles
  with pinned pip-tools into a hash-verified `requirements.txt`; Docker and
  CI install with `--require-hashes`, and CI fails on a stale lock.

## Alternatives considered

- Single Next.js full-stack app — rejected: the Python evaluation/provider
  tooling, Celery pipeline and Django admin are core to the plan.
- Skipping the custom user model until auth work — rejected: swapping user
  models after real migrations exist is notoriously painful.
- Implementing the paid provider clients now behind the gates — rejected:
  no caller exists yet, and unexercised provider code is risk without value.
