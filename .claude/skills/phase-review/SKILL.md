---
name: phase-review
description: Internal run-phase step. Runs complete deterministic verification and the full six-member council plus chair over the entire phase diff, driving the phase-level fix loop until approved. Invoked by the run-phase orchestrator; not for direct user use.
user-invocable: false
---

# Internal: phase-review

Invoked by `/run-phase` once every implementation task has an approved commit.

## 1. Acceptance-criteria evidence
Confirm every acceptance criterion in the ledger has concrete executable/code evidence
(a passing test, observed behaviour, or a cited implementation). Missing evidence is a
blocking gap — send it back into the implementation loop.

## 2. Complete deterministic verification
Run the full suite (CLAUDE.md §20) and capture output. Backend, via docker compose:
```
docker compose config
docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api python -m pip check
docker compose exec api ruff format --check .
docker compose exec api ruff check .
docker compose exec api pytest
```
Frontend:
```
docker compose exec web npm run lint
docker compose exec web npm run typecheck
docker compose exec web npm test -- --run
docker compose exec web npm run build
```
Plus migration validation and any end-to-end/integration checks the repo configures, and
direct application verification where practical. If the stack is not up, `docker compose up -d`
first. Also run the OpenAPI/type contract-drift checks CI enforces.

Confirm the git working tree is clean (`git status --short` empty) — every phase change is
committed.

## 3. Full-phase council
Compute the whole-phase diff: `BASE=$(phase.mjs get base_sha)`, `git diff <BASE>..HEAD`.
Run the six review subagents in parallel (scope = full-phase) over the entire diff, then
the `council-chair` with scope `full-phase`. Persist reports under
`.claude/review/reports/<phase-id>/phase-council/` and run
`phase.mjs decide --reviewers <that-dir>` to cross-check.

The full-phase council must assess: requirement completeness, integration between
components, security boundaries, data integrity, backwards compatibility, test sufficiency,
architecture consistency, operational reliability, performance, and cross-commit defects
not visible in isolated slice reviews — i.e. whether the phase is actually ready to merge.

## 4. Phase fix loop (max 5 full-phase cycles)
If the phase council blocks, fix the findings (each new fix goes through `commit-council`
before it is committed — every commit in the phase must pass the per-commit council), then
re-run steps 2–3. On exhaustion -> `BLOCKED`.

## 5. Save reports and mark state
Write structured Markdown and JSON phase reports under
`.claude/review/reports/<phase-id>/`. On approval set state
`claude_council_approved=true` and `phase_verification_passed=true`, then hand back to the
orchestrator for the Codex review.
