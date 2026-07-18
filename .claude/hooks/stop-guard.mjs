#!/usr/bin/env node
// Stop hook: deterministic safety net for /run-phase.
//
// It does NOT drive the workflow (the run-phase skill does that). It only
// refuses to let the turn end while an active phase still has outstanding
// work, so an interrupted or prematurely-stopping orchestrator is nudged to
// continue instead of leaving a half-finished phase.
//
// Contract: reads a JSON event on stdin. If `stop_hook_active` is true we
// MUST allow the stop (prevents an infinite Stop -> block -> Stop loop).
// To block, print {"decision":"block","reason":"..."} and exit 0.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STATE_FILE = path.resolve(__dirname, "..", "review", "runtime", "active-phase.json");

function allow() {
  process.exit(0); // silent allow
}

let raw = "";
try {
  raw = fs.readFileSync(0, "utf8");
} catch {
  allow();
}

let event = {};
try {
  event = JSON.parse(raw || "{}");
} catch {
  allow();
}

// Never recurse.
if (event.stop_hook_active) allow();

// No active phase file -> nothing to guard.
if (!fs.existsSync(STATE_FILE)) allow();

let state;
try {
  state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
} catch {
  allow();
}

// Terminal states are done: let the turn end.
if (["PR_READY", "BLOCKED", "ABORTED_SAFELY"].includes(state.status)) allow();

// Recompute outstanding blockers (mirror of phase.mjs `remaining`).
const blockers = [];
const ledger = state.task_ledger || [];
const incomplete = ledger.filter((t) => t.status !== "completed" && t.status !== "cancelled");
if (incomplete.length) blockers.push(`${incomplete.length} incomplete ledger task(s)`);
if ((state.unresolved_findings || []).some((f) => ["P0_CRITICAL", "P1_MAJOR", "P2_MODERATE"].includes(f.severity)))
  blockers.push("unresolved P0/P1/P2 finding(s) must be fixed");
if (state.uncommitted_phase_code) blockers.push("uncommitted phase code");
if (!state.phase_verification_passed) blockers.push("complete phase verification has not passed");
if (!state.claude_council_approved) blockers.push("Claude phase council has not approved");
if (!state.codex_approved) blockers.push("Codex has not approved");
if (!state.branch_pushed) blockers.push("phase branch has not been pushed");
if (!state.pr_number) blockers.push("draft PR has not been created");
if (state.ci_status && !["success", "unavailable"].includes(state.ci_status))
  blockers.push(`CI status is "${state.ci_status}", not success`);
if (!state.final_report_path) blockers.push("final report has not been written");

if (blockers.length === 0) allow();

const reason =
  `Active phase "${state.phase_id}" is not finished. Continue the run-phase workflow — ` +
  `do NOT stop until it reaches PR_READY, BLOCKED or ABORTED_SAFELY. Outstanding:\n- ` +
  blockers.join("\n- ") +
  `\n\nIf the phase is genuinely blocked, set status to BLOCKED (or ABORTED_SAFELY) in ` +
  `.claude/review/runtime/active-phase.json and write the blocked report before stopping.`;

process.stdout.write(JSON.stringify({ decision: "block", reason }));
process.exit(0);
