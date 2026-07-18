---
name: commit-council
description: Internal run-phase step. Runs the six-member read-only review council plus the chair on the exact staged diff for a proposed commit, drives the fix-and-re-review loop for blocking findings, and creates the commit only on approval. Invoked by the run-phase orchestrator; not for direct user use.
user-invocable: false
---

# Internal: commit-council

Invoked by `/run-phase` for every proposed commit. The orchestrator has already
implemented a slice, added tests, run targeted checks, and staged **only** that slice's
files. Do not commit until this process approves the exact staged state.

## 1. Snapshot the proposal
- `SLICE=<task-id>`; make `.claude/review/reports/<SLICE>/` (Write creates paths, or `mkdir -p`).
- `HEAD=$(git rev-parse HEAD)`.
- `HASH=$(node .claude/review/bin/phase.mjs hash-staged)` — the approval is bound to this hash.
- `git diff --cached --stat` and `git diff --cached` — capture the changed-file list and exact diff.
- Gather the build/lint/test output you already produced for this slice.

## 2. Run all six reviewers in parallel — independently
Launch the six review subagents in a **single message** (parallel), each with the
**same** package and **no** sight of another reviewer's report:
`review-functionality`, `review-clean-code`, `review-architecture`, `review-security`,
`review-testing`, `review-reliability`.

Give each reviewer: the phase requirements, the relevant acceptance criteria, the current
task description, base SHA, `HEAD`, the changed-file list, the **exact staged diff**, the
relevant surrounding code (paths to read), the build/lint/test output, `HASH`, and — on a
re-review — the prior findings for this slice.

Each reviewer returns one JSON object (reviewer-report schema). **You** write each returned
object to `.claude/review/reports/<SLICE>/<reviewer>.json` (reviewers are read-only and
cannot write). Validate each with
`node .claude/review/bin/phase.mjs validate-report --reviewer <key> --file <path>` and
re-request any malformed report.

## 3. Chair consolidation
Invoke the `council-chair` subagent with all six report file paths + requirements + base
SHA + `HEAD` + `HASH`. It returns the council-report JSON; write it to
`.claude/review/reports/<SLICE>/council.json`. Cross-check the binding decision with:
```
node .claude/review/bin/phase.mjs decide --reviewers .claude/review/reports/<SLICE>
```
A missing/failed/invalid reviewer -> `CHANGES_REQUIRED` automatically.

## 4. Branch on the decision
- **GOOD_TO_PROCEED / PROCEED_WITH_MINOR_ISSUES** -> go to step 6 (commit).
  Record any P3 findings in state `unresolved_findings` (as technical debt) — they do not block.
- **CHANGES_REQUIRED / BLOCKED_HIGH_PRIORITY** -> fix loop (step 5).

## 5. Automated fix-and-re-review loop (max 4 cycles/commit; 3 attempts/finding)
1. Do **not** commit. Preserve the exact reports.
2. Turn each confirmed blocking finding (P0/P1/P2) into a remediation task; record in state.
3. Fix in severity order. Add a **regression test** proving each defect is gone.
4. Run targeted verification for each fix.
5. Ask the reviewer that raised each finding to verify its specific resolution (re-invoke
   that single subagent with the finding + new diff); do not treat "I fixed it" as proof.
6. Re-stage only this slice's files; recompute `HASH` (it will change — that is expected).
7. Re-run the **full** six-member council (step 2) and chair (step 3) on the new staged diff.
8. Repeat until approved or the cycle/finding limit is hit. On exhaustion -> the orchestrator
   writes the blocked report and sets status `BLOCKED`. Never downgrade/accept a finding
   without evidence; P3 may proceed only if it hides no correctness/security/maintainability risk.

## 6. Create the commit (only when all are true)
- Relevant deterministic checks pass.
- All six reviewers completed; chair returned `GOOD_TO_PROCEED` or `PROCEED_WITH_MINOR_ISSUES`.
- Re-verify the approval binding — the staged diff has not changed since approval:
  ```
  node .claude/review/bin/phase.mjs gate --approved <HASH>   # exit 0 = MATCH; exit 3 = MISMATCH -> re-review
  ```
- `git rev-parse HEAD` still equals the `HEAD` reviewed.
- No unreviewed file is staged (re-check `git diff --cached --stat`).

Then:
```
git commit -m "<type(scope): accurate slice description>"
```
Record the commit SHA in state (`phase.mjs set commits ...`), associate it with
`.claude/review/reports/<SLICE>/council.json`, mark the ledger task(s) `completed`,
set `uncommitted_phase_code=false`, and **continue automatically** to the next task. Do
not pause after a successful commit.

If `gate` reports MISMATCH, the staged diff changed after approval — discard the approval
and return to step 1. An approved hash can never be reused once files change.
