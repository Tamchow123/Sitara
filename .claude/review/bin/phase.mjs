#!/usr/bin/env node
// Deterministic helper CLI for the /run-phase review-council workflow.
//
// This file contains NO application logic and never edits application code.
// It manages phase state, computes staged-diff hashes and computes the
// council decision from reviewer report JSON using the fixed rules in
// CLAUDE.md / the run-phase skill. Keeping this logic in one deterministic
// place means the orchestrator cannot "talk itself" past a blocking finding
// or reuse a stale approval.
//
// Usage:
//   node phase.mjs init --id <id> --requirements <src> --base-branch <b> \
//        --base-sha <sha> --branch <b>
//   node phase.mjs get [dotted.key]
//   node phase.mjs set <dotted.key> <json-value>
//   node phase.mjs stage <stage-name>
//   node phase.mjs ledger-add <json-task>
//   node phase.mjs ledger-set <taskId> <status>
//   node phase.mjs hash-staged
//   node phase.mjs gate --approved <sha256>          (exit 0 match, 3 mismatch)
//   node phase.mjs decide --reviewers <dir>          (per-commit / phase council)
//   node phase.mjs codex-decide --report <file>
//   node phase.mjs remaining                          (what still blocks Stop)
//
// Exit codes: 0 ok, 2 usage/error, 3 gate mismatch.

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REVIEW_DIR = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(REVIEW_DIR, "..", "..");
const RUNTIME_DIR = path.join(REVIEW_DIR, "runtime");
const STATE_FILE = path.join(RUNTIME_DIR, "active-phase.json");

const SEVERITIES = ["P0_CRITICAL", "P1_MAJOR", "P2_MODERATE", "P3_MINOR"];
const REVIEWER_KEYS = [
  "functionality",
  "clean-code",
  "architecture",
  "security",
  "testing",
  "reliability",
];
const REVIEWER_DECISIONS = [
  "GOOD_TO_PROCEED",
  "PROCEED_WITH_MINOR_ISSUES",
  "CHANGES_REQUIRED",
  "BLOCKED_HIGH_PRIORITY",
];

function die(msg) {
  process.stderr.write(`phase.mjs: ${msg}\n`);
  process.exit(2);
}

function readState() {
  if (!fs.existsSync(STATE_FILE)) die(`no active phase (missing ${STATE_FILE})`);
  return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
}

function writeState(state) {
  fs.mkdirSync(RUNTIME_DIR, { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2) + "\n");
}

function getArg(args, name, required = false) {
  const i = args.indexOf(`--${name}`);
  if (i === -1 || i === args.length - 1) {
    if (required) die(`missing --${name}`);
    return undefined;
  }
  return args[i + 1];
}

function setDotted(obj, dotted, value) {
  const parts = dotted.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (typeof cur[parts[i]] !== "object" || cur[parts[i]] === null) cur[parts[i]] = {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
}

function getDotted(obj, dotted) {
  return dotted.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
}

// ---- staged diff hashing --------------------------------------------------

function stagedDiff() {
  // --cached with a stable format; no color; ignore the working tree.
  return execFileSync("git", ["-c", "core.autocrlf=false", "diff", "--cached", "--no-color"], {
    cwd: REPO_ROOT,
    maxBuffer: 256 * 1024 * 1024,
    encoding: "utf8",
  });
}

function hashStaged() {
  return createHash("sha256").update(stagedDiff(), "utf8").digest("hex");
}

// ---- reviewer report validation & council decision ------------------------

const FINDING_REQUIRED = [
  "id",
  "severity",
  "confidence",
  "file",
  "lines",
  "evidence",
  "problem",
  "why_it_matters",
  "failure_scenario",
  "recommended_fix",
  "alternatives",
  "trade_offs",
  "verification",
  "introduced_by_change",
];

function validateReport(obj, key) {
  const errs = [];
  if (!obj || typeof obj !== "object") return [`${key}: not an object`];
  if (obj.reviewer !== key) errs.push(`${key}: reviewer field is "${obj.reviewer}", expected "${key}"`);
  if (!REVIEWER_DECISIONS.includes(obj.decision))
    errs.push(`${key}: invalid decision "${obj.decision}"`);
  if (!Array.isArray(obj.findings)) {
    errs.push(`${key}: findings must be an array`);
    return errs;
  }
  obj.findings.forEach((f, idx) => {
    for (const req of FINDING_REQUIRED) {
      if (!(req in f)) errs.push(`${key}: finding[${idx}] missing "${req}"`);
    }
    if (f.severity && !SEVERITIES.includes(f.severity))
      errs.push(`${key}: finding[${idx}] invalid severity "${f.severity}"`);
  });
  return errs;
}

// Count only findings the report marks confirmed (default true) — lets the
// chair record refuted findings without them still blocking.
function severityCounts(reports) {
  const counts = { P0_CRITICAL: 0, P1_MAJOR: 0, P2_MODERATE: 0, P3_MINOR: 0 };
  for (const r of reports) {
    for (const f of r.findings || []) {
      if (f.confirmed === false) continue;
      if (counts[f.severity] !== undefined) counts[f.severity]++;
    }
  }
  return counts;
}

function decisionFromCounts(counts, complete) {
  if (!complete) return "CHANGES_REQUIRED"; // missing/failed reviewer
  if (counts.P0_CRITICAL > 0) return "BLOCKED_HIGH_PRIORITY";
  if (counts.P1_MAJOR > 0 || counts.P2_MODERATE > 0) return "CHANGES_REQUIRED";
  if (counts.P3_MINOR > 0) return "PROCEED_WITH_MINOR_ISSUES";
  return "GOOD_TO_PROCEED";
}

function decide(reviewersDir) {
  const missing = [];
  const errors = [];
  const reports = [];
  for (const key of REVIEWER_KEYS) {
    const file = path.join(reviewersDir, `${key}.json`);
    if (!fs.existsSync(file)) {
      missing.push(key);
      continue;
    }
    let obj;
    try {
      obj = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch (e) {
      errors.push(`${key}: invalid JSON (${e.message})`);
      continue;
    }
    const errs = validateReport(obj, key);
    if (errs.length) errors.push(...errs);
    else reports.push(obj);
  }
  const complete = missing.length === 0 && errors.length === 0;
  const counts = severityCounts(reports);
  const decision = decisionFromCounts(counts, complete);
  return {
    decision,
    complete,
    reviewers_present: reports.map((r) => r.reviewer),
    missing,
    errors,
    counts,
    can_commit: complete && (decision === "GOOD_TO_PROCEED" || decision === "PROCEED_WITH_MINOR_ISSUES"),
  };
}

function codexDecide(reportFile) {
  if (!fs.existsSync(reportFile)) die(`codex report not found: ${reportFile}`);
  let obj;
  try {
    obj = JSON.parse(fs.readFileSync(reportFile, "utf8"));
  } catch (e) {
    return { approved: false, blocking: [], error: `invalid JSON: ${e.message}` };
  }
  const findings = Array.isArray(obj.findings) ? obj.findings : [];
  const blocking = findings.filter(
    (f) => f.confirmed !== false && ["P0_CRITICAL", "P1_MAJOR", "P2_MODERATE"].includes(f.severity),
  );
  return {
    approved: blocking.length === 0,
    blocking: blocking.map((f) => ({ id: f.id, severity: f.severity, file: f.file })),
    p3: findings.filter((f) => f.severity === "P3_MINOR").length,
  };
}

// ---- Stop-hook / terminal readiness ---------------------------------------

function remaining(state) {
  const s = state || (fs.existsSync(STATE_FILE) ? readState() : null);
  const blockers = [];
  if (!s) return { active: false, blockers: [] };
  if (s.status && ["PR_READY", "BLOCKED", "ABORTED_SAFELY"].includes(s.status)) {
    return { active: false, terminal: s.status, blockers: [] };
  }
  const ledger = s.task_ledger || [];
  const incomplete = ledger.filter((t) => t.status !== "completed" && t.status !== "cancelled");
  if (incomplete.length) blockers.push(`${incomplete.length} incomplete ledger task(s)`);
  if ((s.unresolved_findings || []).some((f) => ["P0_CRITICAL", "P1_MAJOR", "P2_MODERATE"].includes(f.severity)))
    blockers.push("unresolved P0/P1/P2 finding(s)");
  if (s.uncommitted_phase_code) blockers.push("uncommitted phase code");
  if (!s.phase_verification_passed) blockers.push("complete phase verification not passed");
  if (!s.claude_council_approved) blockers.push("Claude phase council not approved");
  if (!s.codex_approved) blockers.push("Codex not approved");
  if (!s.branch_pushed) blockers.push("phase branch not pushed");
  if (!s.pr_number) blockers.push("draft PR not created");
  if (s.ci_status && !["success", "unavailable"].includes(s.ci_status))
    blockers.push(`CI status is "${s.ci_status}" (not success)`);
  if (!s.final_report_path) blockers.push("final report not written");
  return { active: true, terminal: null, blockers };
}

// ---- command dispatch -----------------------------------------------------

const [, , cmd, ...args] = process.argv;

switch (cmd) {
  case "init": {
    if (fs.existsSync(STATE_FILE)) {
      const existing = readState();
      if (!["PR_READY", "BLOCKED", "ABORTED_SAFELY"].includes(existing.status)) {
        die(`an active phase already exists (${existing.phase_id}); use resume-phase`);
      }
    }
    const state = {
      schema: 1,
      phase_id: getArg(args, "id", true),
      requirements_source: getArg(args, "requirements", true),
      base_branch: getArg(args, "base-branch", true),
      base_sha: getArg(args, "base-sha", true),
      phase_branch: getArg(args, "branch", true),
      initial_worktree: getArg(args, "worktree") || "clean",
      start_timestamp: getArg(args, "timestamp", true),
      status: "PLANNING",
      stage: "phase-start",
      task_ledger: [],
      commits: [],
      retry_counters: {
        implementation: {},
        council_cycles: {},
        phase_cycles: 0,
        ci_cycles: 0,
        finding_attempts: {},
      },
      review_reports: [],
      unresolved_findings: [],
      uncommitted_phase_code: false,
      phase_verification_passed: false,
      claude_council_approved: false,
      codex_approved: false,
      branch_pushed: false,
      pr_number: null,
      pr_url: null,
      ci_status: null,
      final_report_path: null,
      updated_at: getArg(args, "timestamp", true),
    };
    writeState(state);
    process.stdout.write(JSON.stringify(state, null, 2) + "\n");
    break;
  }
  case "get": {
    const state = readState();
    process.stdout.write(
      (args[0] ? JSON.stringify(getDotted(state, args[0]), null, 2) : JSON.stringify(state, null, 2)) + "\n",
    );
    break;
  }
  case "set": {
    const state = readState();
    if (args.length < 2) die("set <dotted.key> <json-value>");
    let value;
    try {
      value = JSON.parse(args[1]);
    } catch {
      value = args[1]; // treat as raw string
    }
    setDotted(state, args[0], value);
    state.updated_at = getArg(args, "timestamp") || state.updated_at;
    writeState(state);
    process.stdout.write("ok\n");
    break;
  }
  case "stage": {
    const state = readState();
    if (!args[0]) die("stage <stage-name>");
    state.stage = args[0];
    writeState(state);
    process.stdout.write("ok\n");
    break;
  }
  case "ledger-add": {
    const state = readState();
    if (!args[0]) die("ledger-add <json-task>");
    const task = JSON.parse(args[0]);
    state.task_ledger.push(task);
    writeState(state);
    process.stdout.write("ok\n");
    break;
  }
  case "ledger-set": {
    const state = readState();
    const [taskId, status] = args;
    if (!taskId || !status) die("ledger-set <taskId> <status>");
    const t = state.task_ledger.find((x) => x.id === taskId);
    if (!t) die(`no ledger task ${taskId}`);
    t.status = status;
    writeState(state);
    process.stdout.write("ok\n");
    break;
  }
  case "hash-staged":
    process.stdout.write(hashStaged() + "\n");
    break;
  case "gate": {
    const approved = getArg(args, "approved", true);
    const current = hashStaged();
    if (approved === current) {
      process.stdout.write(`MATCH ${current}\n`);
      process.exit(0);
    }
    process.stderr.write(`MISMATCH approved=${approved} current=${current}\n`);
    process.exit(3);
    break;
  }
  case "decide": {
    const dir = getArg(args, "reviewers", true);
    process.stdout.write(JSON.stringify(decide(dir), null, 2) + "\n");
    break;
  }
  case "codex-decide": {
    const report = getArg(args, "report", true);
    process.stdout.write(JSON.stringify(codexDecide(report), null, 2) + "\n");
    break;
  }
  case "remaining": {
    process.stdout.write(JSON.stringify(remaining(), null, 2) + "\n");
    break;
  }
  case "validate-report": {
    const key = getArg(args, "reviewer", true);
    const file = getArg(args, "file", true);
    const obj = JSON.parse(fs.readFileSync(file, "utf8"));
    const errs = validateReport(obj, key);
    if (errs.length) {
      process.stderr.write(errs.join("\n") + "\n");
      process.exit(2);
    }
    process.stdout.write("valid\n");
    break;
  }
  default:
    die(`unknown command "${cmd || ""}". See header for usage.`);
}
