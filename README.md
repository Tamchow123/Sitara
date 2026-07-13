# Sitara

AI-assisted South Asian bridalwear **concept design**. A guided questionnaire, an optional pick of up to three rights-cleared inspiration images, and an AI-generated concept: a FLUX-rendered visual plus a structured design description authored by Claude, with one constrained refinement round.

> Sitara is for concept visualisation only. It does not produce sewing patterns or manufacturing specifications, and does not guarantee a garment can be constructed exactly as shown.

## Status

Planning. No application code exists yet — implementation follows the phased roadmap, starting with an image-model feasibility evaluation before any scaffolding.

## Documentation

- [docs/PROPOSAL.md](docs/PROPOSAL.md) — product purpose, requirements, architecture, data model, API outline, generation pipeline, security/copyright/cost strategy, and assumptions.
- [docs/PHASES.md](docs/PHASES.md) — the 18-phase implementation roadmap with per-phase scope, non-goals, commands, tests, checkpoints, and commits.
- [docs/decisions/](docs/decisions/) — decision records (see [template](docs/decisions/template.md)).

## Planned stack

Next.js 16 + React/TypeScript + Tailwind + shadcn/ui frontend; Django 5.2 + DRF + PostgreSQL + Celery/Redis backend; Claude Sonnet 5 (Anthropic API) for design specifications; FLUX via Replicate for imagery; local image storage in development and S3-compatible object storage in production.

Key ground rules: user designs are private by default; every catalogue image carries recorded usage rights; generation is asynchronous; automated tests can never trigger paid provider calls; the public demo deployment is strictly zero-cost.
