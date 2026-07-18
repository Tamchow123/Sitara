---
name: phase-start
description: Internal run-phase step. Validates the repository, preserves unrelated user work, fetches the remote, creates the phase/ branch and initialises phase state. Invoked by the run-phase orchestrator; not for direct user use.
user-invocable: false
---

# Internal: phase-start

Invoked by `/run-phase`. Perform initial validation and branch/state setup.

## Steps

1. **Parse arguments.** First whitespace token of the run-phase arguments = phase
   identifier (e.g. `phase-5`). The remainder = requirements source: if it is a path
   that exists, read it in full; otherwise treat it as the inline phase description.

2. **Read context:** `CLAUDE.md`; the requirements; `README.md`, `docs/PROPOSAL.md`,
   `docs/PHASES.md`, relevant `docs/decisions/*`; `.github/workflows/ci.yml`;
   `compose.yaml`. Note the current architecture and the exact build/test/lint/format
   commands (CLAUDE.md §20).

3. **Inspect git:**
   - `git status --short` — capture the working-tree state.
   - `git branch --show-current` and `git rev-parse HEAD` — record base branch + base SHA.
   - `git fetch origin` — get the latest remote refs.
   - If relevant, `gh pr list --head <branch>` for any open PR on the current branch.

4. **Protect user work.** If `git status --short` shows **unrelated uncommitted changes**
   (files not part of this phase), do **not** discard, reset, restore, stash-drop or
   overwrite them. If they cannot be safely isolated from the phase, stop the whole run as
   **ABORTED_SAFELY** with an explanation. A clean tree is the normal case.

5. **Never touch main.** You must not commit/push/merge to `main`. Create a phase branch
   from the current base:
   ```
   git switch -c phase/<phase-id>-<short-slug>
   ```
   where `<short-slug>` is a 2–4 word kebab summary of the phase. If the branch already
   exists (e.g. a resumed run), switch to it instead of recreating it.

6. **Initialise state:**
   ```
   node .claude/review/bin/phase.mjs init \
     --id <phase-id> \
     --requirements "<path-or-'inline'>" \
     --base-branch <base-branch> \
     --base-sha <base-sha> \
     --branch phase/<phase-id>-<slug> \
     --worktree "<clean|preserved-user-changes>" \
     --timestamp "<ISO-8601 now>"
   ```
   Use a real timestamp (run `date -u +%Y-%m-%dT%H:%M:%SZ` via Bash if needed — that is
   allowed as it is not a git/gh/build command; otherwise record the wall-clock you know).

7. Set stage to `planning` (`phase.mjs stage planning`) and return control to the
   orchestrator to begin planning.

Do not implement any code in this step.
