---
name: resume-phase
description: Recover an interrupted /run-phase run. Loads the persisted phase state, verifies repository and branch consistency, determines what completed before the interruption, and resumes from the first incomplete stage without repeating commits or recreating the PR. USER-INVOKED ONLY; not part of the normal workflow.
disable-model-invocation: true
argument-hint: (no arguments needed — reads .claude/review/runtime/active-phase.json)
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Task
  - Agent
  - Skill
  - Bash(node .claude/review/bin/phase.mjs:*)
  - Bash(date:*)
  - Bash(mkdir:*)
  - Bash(git status:*)
  - Bash(git branch:*)
  - Bash(git switch:*)
  - Bash(git diff:*)
  - Bash(git log:*)
  - Bash(git show:*)
  - Bash(git add:*)
  - Bash(git restore --staged:*)
  - Bash(git commit:*)
  - Bash(git fetch:*)
  - Bash(git rev-parse:*)
  - Bash(git push -u origin:*)
  - Bash(git push origin HEAD:*)
  - Bash(docker compose:*)
  - Bash(npm:*)
  - Bash(pytest:*)
  - Bash(ruff:*)
  - Bash(gh pr create:*)
  - Bash(gh pr view:*)
  - Bash(gh pr checks:*)
  - Bash(gh pr list:*)
  - Bash(gh run view:*)
  - Bash(gh run list:*)
  - Bash(gh api:*)
  - Bash(codex exec:*)
---

# /resume-phase — recover an interrupted run

Use this only if Claude Code, the terminal, the machine or the network was interrupted
mid-phase. It does not start new work of its own.

## Steps
1. **Load state:** read `.claude/review/runtime/active-phase.json`
   (`node .claude/review/bin/phase.mjs get`). If there is no active phase, or its `status`
   is already `PR_READY` / `BLOCKED` / `ABORTED_SAFELY`, report that and stop — there is
   nothing to resume.

2. **Verify repository consistency:**
   - `git branch --show-current` must be the recorded `phase_branch`. If not,
     `git switch <phase_branch>` (never touch `main`).
   - `git rev-parse HEAD`, `git log --oneline <base_sha>..HEAD`, `git status --short` —
     confirm the recorded commits exist and no unrelated changes are present.
   - `git fetch origin` and, if a `pr_number` is recorded, `gh pr view <pr_number>` to
     confirm the PR still exists.

3. **Determine progress:** compare state against reality — which ledger tasks are
   `completed`, which commits exist, whether the phase council/Codex approved, whether the
   branch is pushed, whether the PR exists, and the CI status. Trust the git/gh reality over
   stale flags; reconcile the state file if they disagree.

4. **Resume from the first incomplete stage** by re-entering the corresponding run-phase
   step:
   - unfinished tasks -> implementation loop + `Skill(commit-council)`;
   - all committed but phase not verified/approved -> `Skill(phase-review)`;
   - Claude-approved but Codex not -> `Skill(codex-review)`;
   - both approved but not pushed / no PR -> `Skill(pr-finalize)`;
   - PR exists but CI unverified -> `Skill(ci-verify)`;
   - everything done -> write the final report and set the terminal status.

5. **Never** repeat an already-created commit or recreate an existing PR. If a commit for a
   completed task already exists, do not remake it. If `pr_number` is set and the PR is
   open, update it rather than opening a new one.

Then continue exactly as `/run-phase` would until a terminal state is reached.
