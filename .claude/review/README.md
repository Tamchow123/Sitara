# Phase-council review workflow (globalised)

The `/run-phase` phase-council system that used to live in this repository is now
installed **user-globally** under `~/.claude/` and works in any local repository:

| Piece | Global location |
| --- | --- |
| `/run-phase` orchestrator + engine + schemas + stage references | `~/.claude/skills/run-phase/` |
| `/resume-phase` recovery command | `~/.claude/skills/resume-phase/` |
| Six council reviewers + chair | `~/.claude/agents/phase-council/` |
| Phase-gated git-guard and Stop hooks | `~/.claude/hooks/phase-council/` (registered in `~/.claude/settings.json`) |

Sitara keeps only what is repository-specific:

- `.claude/phase-council.json` — Sitara's base branch, protected branches, and the
  exact docker-compose build/test/lint/format/typecheck/extra-check commands
  (see `CLAUDE.md` §20). Validated against the schema bundled with the global skill.
- `.claude/review/runtime/` — live phase state for THIS repository (gitignored).
- `.claude/review/reports/` — generated review reports for THIS repository (gitignored).

Run a phase with `/run-phase <phase-id> <requirements-file-or-description>`;
recover an interrupted one with `/resume-phase`. The global hooks are inert
unless `.claude/review/runtime/active-phase.json` records an unfinished phase,
so normal Claude Code work is unaffected.
