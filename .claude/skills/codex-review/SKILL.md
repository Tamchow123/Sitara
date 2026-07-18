---
name: codex-review
description: Internal run-phase step. Runs the independent read-only Codex reviewer over the complete phase diff, treats confirmed P0/P1/P2 as blocking, and drives the cross-system fix loop. Invoked by the run-phase orchestrator; not for direct user use.
user-invocable: false
---

# Internal: codex-review

Invoked by `/run-phase` after the Claude phase council approves. Codex is an **independent
second review system**. Neither system may approve the phase based only on the other's
approval.

## 1. Run Codex read-only
Run Codex non-interactively in an ephemeral, read-only sandbox — it must not modify files:
```
BASE=$(node .claude/review/bin/phase.mjs get base_sha | tr -d '"')
codex exec --sandbox read-only \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  "$(cat .claude/review/prompts/codex-review.md)" \
  > .claude/review/reports/<phase-id>/codex.raw.txt
```
Provide to Codex (via the prompt file / stdin): the phase requirements, base branch + base
SHA, the complete phase diff (`git diff $BASE..HEAD`), the changed-file list, and the test
& build evidence. Require it to return **only** JSON conforming to
`.claude/review/schema/codex-report.schema.json`.

Extract the JSON object from Codex's output into
`.claude/review/reports/<phase-id>/codex.json` (Codex is read-only, so you write the file).

## 2. Decide
```
node .claude/review/bin/phase.mjs codex-decide --report .claude/review/reports/<phase-id>/codex.json
```
Confirmed Codex **P0/P1/P2** findings are blocking (`approved:false`). P3 -> technical debt.

## 3. Cross-system fix loop
If Codex blocks:
1. You (the orchestrator) implement the fixes — Codex never edits files.
2. Run deterministic checks after fixes.
3. Run `commit-council` before committing each fix (per-commit council still required).
4. Re-run the full **Claude** phase council (`phase-review` steps 2–3).
5. Re-run **Codex** (step 1).
6. Continue until **both** systems approve or the retry limit is reached (then `BLOCKED`).

On approval set state `codex_approved=true` and record the Codex decision, then hand back
to the orchestrator for push + draft PR.

> If the `codex` CLI is unavailable or unauthenticated, do not fake approval: record the
> limitation in state and the final report and treat it as a blocker for `PR_READY`
> (surface it clearly so the user can run Codex or waive it).
