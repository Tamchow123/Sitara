---
name: run-phase
description: Run one complete Sitara development phase end-to-end and autonomously — plan, implement in reviewed slices, run the six-member review council on every commit, fix blocking findings, verify the whole phase, run the independent Codex review, push a phase branch and open a fully-reviewed DRAFT pull request into main that is ready for the user's manual merge. This is the only command the user runs to start a phase. USER-INVOKED ONLY.
disable-model-invocation: true
argument-hint: <phase-identifier> <requirements-file-or-description>
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
  - Bash(git status --short:*)
  - Bash(git branch:*)
  - Bash(git checkout -b:*)
  - Bash(git switch:*)
  - Bash(git switch -c:*)
  - Bash(git diff:*)
  - Bash(git diff --cached:*)
  - Bash(git log:*)
  - Bash(git show:*)
  - Bash(git add:*)
  - Bash(git restore --staged:*)
  - Bash(git commit:*)
  - Bash(git fetch:*)
  - Bash(git rev-parse:*)
  - Bash(git stash list:*)
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

# /run-phase — autonomous phase orchestrator

You (the main Claude session) are the **phase orchestrator**. After the user runs
`/run-phase <phase-id> <requirements>` you own the entire workflow and continue
**without routine user intervention** until the run reaches exactly one terminal
state: `PR_READY`, `BLOCKED`, or `ABORTED_SAFELY`.

Arguments: `$ARGUMENTS` = `<phase-identifier> <requirements-file-or-complete-description>`.
The first token is the phase identifier; the rest is either a path to a requirements
file or an inline phase description.

## Hard rules (never violate)
- **Only you edit application files.** Reviewers and the chair are read-only.
- **Never commit, push to, or merge into `main`.** Work only on a `phase/…` branch.
- **Never merge the PR** and never mark it ready — it must stay a **draft**.
- **Never** force-push, `git reset --hard`, `git clean -f`, discard user changes, or run destructive DB ops. (The git-guard hook also blocks these.)
- **Never** make paid AI calls or enable a provider because a key exists. Tests/CI stay provider-free. Respect every CLAUDE.md non-negotiable.
- **Never** claim a check passed without running it and observing the result.
- A commit is allowed **only** after all six reviewers ran and the chair returned `GOOD_TO_PROCEED` or `PROCEED_WITH_MINOR_ISSUES` on the **exact** staged-diff hash that is still current.

## State
All progress lives in `.claude/review/runtime/active-phase.json`, managed with
`node .claude/review/bin/phase.mjs`. **Update it after every meaningful transition**
(stage change, ledger update, commit, approval, push, PR, CI). The Stop hook reads
this file and will refuse to let you stop while work remains, so keep it accurate.

Per-slice and phase report JSON/Markdown live under `.claude/review/reports/`.

## Retry limits (then -> BLOCKED)
- 3 implementation attempts per task (a materially different approach counts as progress).
- 4 council fix-and-review cycles per proposed commit.
- 5 full-phase fix-and-review cycles.
- 3 CI diagnosis-and-fix cycles.
- 3 attempts to resolve the same finding.
Track counters in state. On exhaustion, write the blocked report and set status `BLOCKED`.

## Workflow — run these stages in order

Each stage has a dedicated internal skill with the detailed procedure. Invoke it via
the Skill tool. Do the work described; do not ask the user to invoke anything.

1. **phase-start** — `Skill(phase-start)`: parse args, read requirements + CLAUDE.md
   + docs + ADRs, inspect git/CI, preserve unrelated user changes, `git fetch`,
   create the `phase/<id>-<slug>` branch, and `phase.mjs init` the state. If unrelated
   uncommitted user changes would be swept in, stop as `ABORTED_SAFELY`.

2. **Plan** — analyse the full requirements, explore code paths and conventions,
   convert requirements into testable acceptance criteria, split into small coherent
   slices, and build the task ledger (`phase.mjs ledger-add`) with id, requirement,
   likely files, dependencies, verification method, status=`pending`. Record risks and
   reasonable assumptions. Do **not** stop for routine decisions — choose the least
   surprising option, document it, continue. Only stop for a genuine unresolvable
   blocker (missing credentials, contradictory requirements, unavoidable destructive op,
   unavailable infrastructure, already-corrupt repo).

3. **Implement + review loop** — for each dependency-ready task:
   - Inspect code, implement the smallest complete vertical slice, add/update tests,
     format, run targeted build/lint/type/test, and exercise the behaviour directly
     where possible (never claim success from inspection alone).
   - Review the diff, remove debug code, stage **only** this slice's files.
   - `Skill(commit-council)`: hash the staged diff, run all six reviewers in parallel,
     then the chair. If the chair blocks, `Skill(commit-council)` also drives the
     fix-and-re-review loop (you apply the fixes + regression tests). A commit is created
     only on approval with the staged hash still matching; then mark ledger tasks
     completed and continue automatically to the next task. Do **not** pause after a commit.

4. **phase-review** — `Skill(phase-review)`: after every task has an approved commit,
   run full build/format/lint/typecheck/tests/integration/e2e/migration checks and the
   full-phase six-member council + chair over the whole `base..HEAD` diff. Fix + re-run
   (bounded by the phase-cycle limit) until approved. Save Markdown + JSON phase reports.

5. **codex-review** — `Skill(codex-review)`: run the independent read-only Codex reviewer
   over the complete phase diff. Confirmed Codex P0/P1/P2 are blocking; you fix them, run
   the per-commit council on those fixes before committing, then re-run the full Claude
   phase council **and** Codex. Neither system may approve based only on the other.

6. **pr-finalize** — `Skill(pr-finalize)`: confirm clean tree / no unrelated commits /
   main untouched, push the phase branch, and open a **draft** PR into `main` with the
   full report body. Record PR number + URL.

7. **ci-verify** — `Skill(ci-verify)`: watch the PR's CI checks; wait for in-progress ones;
   on failure caused by this phase, apply the smallest safe fix, re-run the commit council,
   commit, push and re-watch (max 3 cycles). Never weaken tests/security to go green. If CI
   is unavailable, record `ci_status="unavailable"` and say so — do not claim it passed.

8. **Terminal** — set the terminal status and write the final report
   (`.claude/review/reports/<phase>/final.md`, path recorded in state). Return the concise
   final response per the "Final response" section below.

## Terminal states
- **PR_READY** — only when *all* hold: every task complete; tree clean; full deterministic
  verification passed; Claude phase council approved; Codex approved; branch pushed; draft
  PR created into main; available required CI checks green (or explicitly unavailable);
  final report saved.
- **BLOCKED** — unrecoverable condition or an exhausted retry limit. Write the blocked
  report (branch, done/remaining tasks, blocking finding/failure with exact output,
  attempts made, why more autonomy won't help, safe next action, whether a PR/push exists).
- **ABORTED_SAFELY** — continuing would overwrite user work, touch main, or perform an
  unsafe op. Explain and stop without doing it.

Never report PR_READY unless every condition is truly satisfied.

## Final response (keep it compact — no per-iteration narration)
For **PR_READY**: PR title + link, branch, commit count, final Claude council decision,
final Codex decision, build/test summary, CI summary, remaining P3 technical debt, and
anything needing manual review.
For **BLOCKED**: the concise blocked report; do not imply the phase is complete.

Begin now with `Skill(phase-start)`.
