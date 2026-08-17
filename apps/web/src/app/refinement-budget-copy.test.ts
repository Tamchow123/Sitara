// A guard for a CLASS of defect, not an instance of it.
//
// ADR 0029 raised MAX_REFINEMENTS from 1 to 3. The code change was small; the
// stale CLAIMS about the old number were not. They were found in three separate
// sweeps, because each sweep was narrower than the problem:
//
//   1. `grep "single-round"` — missed capitalised "One refinement".
//   2. `grep -i "one refinement"` on *.ts/*.tsx — missed .css, .md and .py.
//   3. only then did the marketing page turn up, whose own test was asserting
//      `toMatch(/refined once/i)` AND `not.toMatch(/3 refinements/i)` — a green
//      test enforcing the falsehood.
//
// The worst of them was `apps/web/src/app/concepts/page.tsx`, linked from the
// footer of every screen, telling a boutique that "Each design may be refined
// once". That is a promise broken on the shop floor, not a stale comment.
//
// So this scans the frontend source itself. It is deliberately about phrasings
// that assert HOW MANY refinements exist, not about the word "refinement": a
// budget claim written as prose cannot be type-checked, so the only thing that
// catches it is a test that reads the prose.
//
// Scoped to apps/web so it needs no read outside the Docker build context (see
// CLAUDE.md §20's table of tests that deliberately do). The backend has its own
// equivalent in apps/api/sitara/generation/tests/test_refinement_budget_copy.py.
//
// That scoping is necessary but was not sufficient: e2e/ IS inside the build
// context, and was still only a snapshot taken at the last `docker compose
// build web`, because the web service bind-mounts src/ and public/ and nothing
// else. A stale claim added to a spec afterwards would have been invisible to
// this guard under `docker compose exec web npm test` — green, on bytes from a
// previous build. compose.yaml now mounts e2e/ read-only for exactly this scan.
// The four tests in §20's table fail LOUDLY in that container by design; this
// one would have passed quietly, which is the failure mode the whole file is
// about, so it is fixed rather than documented.
//
// PEER GUARD: apps/api/sitara/generation/tests/test_refinement_budget_copy.py.
// The two pattern lists are deliberately NOT shared. Sharing them would mean a
// file readable from both Docker build contexts, which is the very thing the
// scoping above avoids, and the lists are not the same list: this one carries
// customer-facing phrasings ("refine it once", "used your one") that no Python
// docstring will ever contain, and the peer carries docstring phrasings ("its
// one refinement") that no marketing page will. What they must share is the
// PROPOSITION — a claim about how many refinements a design gets. Add a new
// phrasing to whichever side can actually contain it, and to both when both
// can.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

const WEB_ROOT = join(__dirname, "..", "..");
const SCANNED_DIRS = ["src", "e2e"];
const SCANNED_EXTENSIONS = [".ts", ".tsx", ".css", ".md"];

// Each pattern is a claim about the SIZE of the refinement budget that was true
// when MAX_REFINEMENTS was 1 and is false now. Deliberately NOT matching
// "one refinement attempt", "a second refinement carries on from…" or similar,
// which describe a single instance rather than the limit.
const STALE_BUDGET_CLAIMS: ReadonlyArray<{ pattern: RegExp; why: string }> = [
  { pattern: /\brefined once\b/i, why: 'the budget is MAX_REFINEMENTS, not once' },
  { pattern: /\brefine it once\b/i, why: 'the budget is MAX_REFINEMENTS, not once' },
  { pattern: /\bmay be refined only\b/i, why: "reads as a cap of one" },
  { pattern: /\bexactly one refinement\b/i, why: "the budget is plural" },
  { pattern: /\bsingle refinement\b/i, why: "the budget is plural" },
  { pattern: /\bonly one refinement\b/i, why: "the budget is plural" },
  { pattern: /\byour one refinement\b/i, why: "the budget is plural" },
  { pattern: /\bused your one\b/i, why: "the budget is plural" },
  { pattern: /\btwo-version history\b/i, why: "a lineage can be four versions" },
  { pattern: /\+ one refinement\b/i, why: "a design holds one row per refinement" },
];

// Files whose mention is a CITATION of ADR 0015 by its real title, or this
// guard's own prose. Kept as exact relative paths so a new file cannot inherit
// an exemption by accident.
const ALLOWED_FILES = new Set([
  "src/app/refinement-budget-copy.test.ts",
  // The concepts-page test names the superseded phrasings in order to assert
  // they are ABSENT, which is the opposite of claiming them.
  "src/app/concepts/page.test.tsx",
]);

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".next" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      walk(full, out);
    } else if (SCANNED_EXTENSIONS.some((ext) => entry.endsWith(ext))) {
      out.push(full);
    }
  }
  return out;
}

describe("refinement-budget copy", () => {
  it("scans a non-trivial number of files, so a broken walk cannot pass vacuously", () => {
    const files = SCANNED_DIRS.flatMap((d) => walk(join(WEB_ROOT, d)));
    // An empty or tiny result would make every assertion below meaningless —
    // exactly how the previous sweeps failed.
    expect(files.length).toBeGreaterThan(100);
  });

  // Without this the guard could be silently inert: a green test proving
  // nothing, which is precisely what the concepts-page test was. Every line
  // below is prose that actually shipped, so a pattern that stops firing on
  // one of them has stopped guarding a real defect rather than a hypothetical.
  it.each([
    "Each design may be refined once.",
    "Refine it once",
    "the design's single refinement",
    "You have used your one refinement.",
    "Journey 10: the two-version history",
    "One generated concept iteration (initial concept + one refinement).",
  ])("has teeth: %s", (line) => {
    expect(STALE_BUDGET_CLAIMS.some(({ pattern }) => pattern.test(line))).toBe(true);
  });

  it("states no superseded claim about how many refinements a design gets", () => {
    const offences: string[] = [];

    for (const dir of SCANNED_DIRS) {
      for (const file of walk(join(WEB_ROOT, dir))) {
        const rel = relative(WEB_ROOT, file).replace(/\\/g, "/");
        if (ALLOWED_FILES.has(rel)) continue;
        const lines = readFileSync(file, "utf8").split("\n");
        lines.forEach((line, i) => {
          for (const { pattern, why } of STALE_BUDGET_CLAIMS) {
            if (pattern.test(line)) {
              offences.push(`${rel}:${i + 1} — ${pattern} (${why})\n    ${line.trim()}`);
            }
          }
        });
      }
    }

    expect(
      offences,
      `Superseded refinement-budget claims found. ADR 0029 raised MAX_REFINEMENTS to 3 and ` +
        `the rounds chain, so these read as promises the product does not keep:\n\n` +
        offences.join("\n\n"),
    ).toEqual([]);
  });
});
