---
name: ci-verify
description: Internal run-phase step. Watches the draft PR's CI checks, waits for in-progress checks, and repairs phase-caused failures with the smallest safe fix (re-reviewed and committed) for up to three cycles. Invoked by the run-phase orchestrator; not for direct user use.
user-invocable: false
---

# Internal: ci-verify

Invoked by `/run-phase` after the draft PR is created.

## 1. Inspect checks
```
gh pr checks <pr-number> --watch --interval 30    # waits for in-progress checks
```
or poll `gh pr checks <pr-number>` / `gh run list --branch phase/<id>-<slug>`.

- If all required checks **pass** -> set state `ci_status="success"` and continue to final reporting.
- If CI is **unavailable** (no runs, no remote CI, not authorised) -> set
  `ci_status="unavailable"` and clearly report that CI status could not be verified. Do
  **not** claim it passed.

## 2. Repair loop (max 3 cycles)
For a failing required check:
1. `gh run view <run-id> --log-failed` — inspect the logs.
2. Decide whether this phase caused the failure (vs. a flaky/infra/unrelated issue).
3. Reproduce locally where possible (run the same command from CLAUDE.md §20).
4. Implement the **smallest safe fix**. Never modify CI config to hide a real failure, and
   never remove tests, weaken assertions or disable security checks to go green.
5. Run deterministic verification for the fix.
6. Stage the fix and run `commit-council` (the per-commit council is still mandatory).
7. Commit after approval; `git push origin HEAD`.
8. Re-watch CI (step 1).

Track `retry_counters.ci_cycles`. On the 3rd unsuccessful cycle -> `BLOCKED` with the CI
logs and what was attempted.

## 3. Done
When required checks are green (or definitively unavailable), update `ci_status` and return
to the orchestrator for the terminal report.
