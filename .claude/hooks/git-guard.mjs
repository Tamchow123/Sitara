#!/usr/bin/env node
// PreToolUse hook (matcher: Bash): deterministic guard-rail that denies
// destructive or main-touching git/gh operations regardless of what the
// orchestrator "intends". This is defence in depth on top of the narrow
// allow-list in settings.json.
//
// Contract: reads the PreToolUse event on stdin. To deny, print a
// hookSpecificOutput block with permissionDecision "deny" and exit 0.
// Anything else -> allow (exit 0, no output).

import fs from "node:fs";
import { execFileSync } from "node:child_process";

function currentBranch() {
  try {
    return execFileSync("git", ["branch", "--show-current"], { encoding: "utf8" }).trim();
  } catch {
    return "";
  }
}

function allow() {
  process.exit(0);
}

function deny(reason) {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: reason,
      },
    }),
  );
  process.exit(0);
}

let event = {};
try {
  event = JSON.parse(fs.readFileSync(0, "utf8") || "{}");
} catch {
  allow();
}

if (event.tool_name !== "Bash") allow();
const cmd = String(event.tool_input?.command ?? "");
if (!cmd) allow();

// Normalise for matching: collapse whitespace, lower-case.
const norm = cmd.replace(/\s+/g, " ").trim();
const low = norm.toLowerCase();

// Split on shell separators so each sub-command is inspected.
const parts = norm.split(/&&|\|\||;|\|/).map((p) => p.trim());

const DENY = [
  { re: /\bgit\s+push\b[^\n]*\borigin\s+(main|HEAD:main|main:main)\b/i, why: "push to main is forbidden" },
  { re: /\bgit\s+push\b[^\n]*(--force\b|-f\b|--force-with-lease\b)/i, why: "force push is forbidden" },
  { re: /\bgit\s+push\b[^\n]*(\+)/, why: "forced (+refspec) push is forbidden" },
  { re: /\bgit\s+reset\s+--hard\b/i, why: "git reset --hard is forbidden (would discard work)" },
  { re: /\bgit\s+clean\s+-[a-z]*f/i, why: "git clean -f is forbidden (would delete files)" },
  { re: /\bgit\s+checkout\s+--\s/i, why: "git checkout -- <path> is forbidden (would discard changes)" },
  { re: /\bgit\s+restore\b(?![^\n]*--staged\s*$)/i, why: "git restore of working-tree files is forbidden" },
  { re: /\bgit\s+branch\s+-D\b/i, why: "force branch deletion is forbidden" },
  { re: /\bgit\s+(rebase|filter-branch|reflog\s+expire)\b/i, why: "history rewriting is forbidden" },
  { re: /\bgit\s+push\b[^\n]*--delete\b/i, why: "remote branch deletion is forbidden" },
  { re: /\bgh\s+pr\s+merge\b/i, why: "merging a PR is forbidden — leave it as a draft for manual review" },
  { re: /\bgh\s+pr\s+ready\b/i, why: "marking the PR ready is forbidden — it must stay a draft" },
  { re: /\bgh\s+pr\s+close\b/i, why: "closing PRs is forbidden" },
  { re: /docker\s+compose\s+down\s+[^\n]*(--volumes|-v)\b/i, why: "docker compose down --volumes is forbidden" },
  { re: /\bdrop\s+(database|table|schema)\b/i, why: "destructive database operations are forbidden" },
];

for (const sub of [norm, ...parts]) {
  for (const rule of DENY) {
    if (rule.re.test(sub)) deny(`Blocked by git-guard: ${rule.why}.`);
  }
}

// Block committing / pushing while main (or master) is the checked-out branch.
if (/\bgit\s+(commit|push)\b/i.test(low)) {
  const branch = currentBranch();
  if (branch === "main" || branch === "master") {
    deny(
      `Blocked by git-guard: current branch is "${branch}". ` +
        `Create and switch to a phase/ branch before committing or pushing.`,
    );
  }
}

allow();
