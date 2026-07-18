# Sitara run-phase review council

This directory holds the automation that lets a whole development phase run from a single
user command:

```
/run-phase <phase-identifier> <requirements-file-or-description>
# e.g.
/run-phase phase-5 docs/phases/phase-5.md
```

After that one command, Claude (the **orchestrator**) autonomously plans, implements in
small reviewed slices, runs a six-member read-only review council on **every** commit,
fixes blocking findings, verifies the whole phase, runs an **independent Codex review**,
pushes a `phase/…` branch and opens a **draft** pull request into `main` that is ready for
your manual review and merge. It never merges, never touches `main`, and never makes paid
AI calls.

You never need to invoke `commit-council`, `phase-review`, `codex-review`, `security-review`
or `code-review` yourself during a normal run.

## Pieces

| Path | Role |
| --- | --- |
| `.claude/skills/run-phase/` | **Public** entry command (`disable-model-invocation: true`). |
| `.claude/skills/resume-phase/` | **Public** recovery command after an interruption. |
| `.claude/skills/phase-start/` | Internal: validate repo, branch, init state. |
| `.claude/skills/commit-council/` | Internal: six-reviewer council + chair per proposed commit + fix loop. |
| `.claude/skills/phase-review/` | Internal: full deterministic verification + full-phase council. |
| `.claude/skills/codex-review/` | Internal: independent read-only Codex review + cross-system loop. |
| `.claude/skills/pr-finalize/` | Internal: push branch + open draft PR. |
| `.claude/skills/ci-verify/` | Internal: watch and repair CI. |
| `.claude/agents/review-*.md` | Six read-only council reviewers (Read/Grep/Glob only). |
| `.claude/agents/council-chair.md` | Read-only chair that consolidates the reviewers. |
| `.claude/hooks/stop-guard.mjs` | Stop hook: refuses to end the turn while a phase is unfinished. |
| `.claude/hooks/git-guard.mjs` | PreToolUse hook: blocks main-writes, force-push, resets, PR merges, etc. |
| `.claude/review/bin/phase.mjs` | Deterministic engine: state, staged-diff hashing, council decision. |
| `.claude/review/schema/*.json` | JSON Schemas for reviewer/council/codex reports. |
| `.claude/review/prompts/codex-review.md` | Prompt handed to the Codex CLI. |
| `.claude/review/runtime/active-phase.json` | Live phase state (gitignored). |
| `.claude/review/reports/` | Generated per-slice and per-phase reports (gitignored). |

Internal skills are `user-invocable: false` (hidden from the `/` menu) but **not**
`disable-model-invocation`, so the orchestrator can invoke them. Only `run-phase` and
`resume-phase` are user-invocable.

## Council decision rules (deterministic, in `phase.mjs`)

Severities: `P0_CRITICAL`, `P1_MAJOR`, `P2_MODERATE`, `P3_MINOR`.
Reviewer/chair decisions: `GOOD_TO_PROCEED`, `PROCEED_WITH_MINOR_ISSUES`,
`CHANGES_REQUIRED`, `BLOCKED_HIGH_PRIORITY`.

- Any confirmed **P0** → `BLOCKED_HIGH_PRIORITY`.
- Else any confirmed **P1/P2** → `CHANGES_REQUIRED`.
- Else any **P3** → `PROCEED_WITH_MINOR_ISSUES`.
- Else → `GOOD_TO_PROCEED`.
- Any missing/failed/invalid reviewer → `CHANGES_REQUIRED`.

A commit is created only on `GOOD_TO_PROCEED`/`PROCEED_WITH_MINOR_ISSUES` **and** the
staged-diff SHA-256 still matching the approved hash (`phase.mjs gate`). Changing files
after approval invalidates the approval.

## Retry limits → BLOCKED

3 implementation attempts/task · 4 council cycles/commit · 5 full-phase cycles ·
3 CI cycles · 3 attempts per finding.

## Terminal states

`PR_READY` · `BLOCKED` · `ABORTED_SAFELY`. See `active-phase.schema.md` for the state shape.

## One-time setup

See the repository response / `CLAUDE.md` §"run-phase". In short: the committed
`.claude/settings.json` grants the exact command allow-list and destructive-op deny-list and
registers the two hooks, so a normal run does not prompt. `codex` and `gh` must be installed
and authenticated on the host; Node ≥ 18 is required for the hooks and `phase.mjs`.
