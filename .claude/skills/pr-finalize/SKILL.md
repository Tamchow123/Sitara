---
name: pr-finalize
description: Internal run-phase step. Confirms safety pre-conditions, pushes the phase branch and opens a DRAFT pull request into main with the full review report body. Never merges or marks the PR ready. Invoked by the run-phase orchestrator; not for direct user use.
user-invocable: false
---

# Internal: pr-finalize

Invoked by `/run-phase` after both the Claude phase council and Codex approve.

## 1. Safety pre-conditions (all must hold)
- `git status --short` is empty (clean working tree).
- The branch contains only this phase's commits: `git log --oneline <base_sha>..HEAD`
  shows nothing unrelated.
- `main` was never modified: current branch is the `phase/…` branch, not `main`.
- State shows `claude_council_approved` and `codex_approved` both true.

If any fails, stop (do not push) and report the discrepancy.

## 2. Push the phase branch
```
git push -u origin HEAD
```
(Never push to `main`; never force-push — the git-guard hook blocks both.) Set state
`branch_pushed=true`.

## 3. Open a DRAFT PR into main
```
gh pr create --draft --base main --head phase/<id>-<slug> \
  --title "<phase-id>: <concise objective>" \
  --body-file .claude/review/reports/<phase-id>/pr-body.md
```
- Target **main** (never another feature branch, unless the recorded phase base requires a
  stacked PR — then target that base and note it).
- Keep it a **draft**. Never `gh pr merge` and never `gh pr ready`.

Record `pr_number` and `pr_url` in state.

## 4. PR body (`pr-body.md`) — clear GitHub Markdown, must include
- Phase objective and requirements source.
- Implementation summary.
- Acceptance criteria with evidence.
- Commit list (SHA + message).
- Test/build commands executed and their results.
- Council members and their decisions (per-commit summary + full-phase decision).
- Deduplicated findings resolved.
- Codex decision.
- Remaining **P3 technical debt**.
- Migrations / deployment notes.
- Security considerations.
- Known limitations.
- Manual verification guidance for the reviewer.
- Paths to the saved review reports.

Then hand back to the orchestrator for CI monitoring (`ci-verify`).
