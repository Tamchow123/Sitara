import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// ADR 0027's control is only as good as its reach. It was first shipped by
// hand-adding `actions={<FinishAndHandBack />}` to four page files, and review
// caught that two had been missed — including the annotation workspace, the one
// screen the ADR names by name as the sharpest case. Nothing structural tied the
// control to "every design route", so coverage rested on remembering.
//
// This test is that structural tie. It reads the route tree rather than
// rendering, because the question is not "does the component work" (the sibling
// test covers that) but "is it on every screen a walk-in customer's work can be
// left on". A new route under app/design/ now fails here until it decides.
const DESIGN_ROUTES_DIR = join(__dirname, "..", "..", "app", "design");

function pageFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...pageFiles(path));
    } else if (entry.name === "page.tsx") {
      found.push(path);
    }
  }
  return found;
}

describe("every design route offers Finish and hand back", () => {
  const routes = pageFiles(DESIGN_ROUTES_DIR);

  it("finds the design routes at all, so an empty sweep cannot pass silently", () => {
    // A refactor that moves or renames the route tree must fail loudly here
    // rather than turn this whole file into a vacuous pass over zero files.
    expect(routes.length).toBeGreaterThanOrEqual(6);
  });

  it.each(routes.map((path) => [path.slice(DESIGN_ROUTES_DIR.length + 1), path]))(
    "design/%s renders the control",
    (_label, path) => {
      const source = readFileSync(path, "utf8");
      expect(source).toContain("FinishAndHandBack");
      // Imported but never placed would satisfy the line above, so assert the
      // prop that actually puts it on the screen.
      expect(source).toContain("actions={<FinishAndHandBack />}");
    },
  );
});
