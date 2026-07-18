# `active-phase.json` state shape

Written and read only by `.claude/review/bin/phase.mjs`. Lives at
`.claude/review/runtime/active-phase.json` and is **gitignored**. The Stop hook reads it to
decide whether the turn may end.

```jsonc
{
  "schema": 1,
  "phase_id": "phase-5",
  "requirements_source": "docs/phases/phase-5.md" /* or "inline" */,
  "base_branch": "main",
  "base_sha": "3c5bc64…",
  "phase_branch": "phase/phase-5-design-generation",
  "initial_worktree": "clean" /* or "preserved-user-changes" */,
  "start_timestamp": "2026-07-18T10:40:00Z",

  "status": "PLANNING", /* PLANNING | IMPLEMENTING | PHASE_REVIEW | CODEX_REVIEW | FINALISING | CI | PR_READY | BLOCKED | ABORTED_SAFELY */
  "stage": "phase-start",

  "task_ledger": [
    {
      "id": "T1",
      "requirement": "…",
      "files": ["apps/api/…"],
      "dependencies": [],
      "verification": "pytest path::test",
      "status": "pending" /* pending | in_progress | completed | cancelled */
    }
  ],

  "commits": [ { "task": "T1", "sha": "…", "report": ".claude/review/reports/T1/council.json" } ],

  "retry_counters": {
    "implementation": { "T1": 0 },
    "council_cycles": { "T1": 0 },
    "phase_cycles": 0,
    "ci_cycles": 0,
    "finding_attempts": { "SEC-001": 0 }
  },

  "review_reports": [".claude/review/reports/T1/council.json"],
  "unresolved_findings": [ /* reviewer-finding objects; P3 = tracked technical debt */ ],

  "uncommitted_phase_code": false,
  "phase_verification_passed": false,
  "claude_council_approved": false,
  "codex_approved": false,
  "branch_pushed": false,
  "pr_number": null,
  "pr_url": null,
  "ci_status": null, /* null | pending | success | failure | unavailable */
  "final_report_path": null,

  "updated_at": "2026-07-18T10:40:00Z"
}
```

The Stop hook blocks ending the turn while, for a non-terminal `status`, any of these hold:
incomplete ledger tasks · unresolved P0/P1/P2 findings · `uncommitted_phase_code` ·
`!phase_verification_passed` · `!claude_council_approved` · `!codex_approved` ·
`!branch_pushed` · no `pr_number` · `ci_status` neither `success` nor `unavailable` ·
no `final_report_path`.
