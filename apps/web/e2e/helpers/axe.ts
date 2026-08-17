import { join } from "node:path";

import { expect, type Page } from "@playwright/test";

// Extracted from accessibility.spec.ts, unchanged, so a second spec can point
// the SAME engine with the SAME disabled rules at a screen that spec cannot
// reach cheaply. The comparison view exists only after a real refinement has
// run, which generation.spec.ts already drives; re-driving it inside the
// accessibility spec would buy a second three-minute generation and nothing
// else, and a hand-copied axe helper would be free to drift from this one.

// axe-core is injected from node_modules — it is already present as a
// transitive dependency of jest-axe, which the Vitest component tests use, so
// this adds no dependency. Injecting the same engine keeps the component-level
// and page-level accessibility checks honest with each other.
const AXE_PATH = join(__dirname, "..", "..", "node_modules", "axe-core", "axe.min.js");

// Contrast is checked separately by the design-token tests and by manual
// inspection against the handoff palette: axe cannot evaluate contrast through
// a background image, and the landing hero and every option card sit on one,
// which produces false positives rather than findings. Nothing else is
// disabled — in particular every name/role/value, landmark and label rule runs.
const DISABLED_RULES = ["color-contrast"];

export type AxeViolation = {
  id: string;
  impact: string | null;
  help: string;
  nodes: { target: string[] }[];
};

/** Run axe against the whole page and return only serious/critical findings. */
export async function seriousViolations(page: Page): Promise<AxeViolation[]> {
  await page.addScriptTag({ path: AXE_PATH });
  return page.evaluate(async (disabled) => {
    const rules = Object.fromEntries(disabled.map((id) => [id, { enabled: false }]));
    // @ts-expect-error injected at runtime by addScriptTag
    const results = await window.axe.run(document, { rules });
    return results.violations
      .filter((v: AxeViolation) => v.impact === "serious" || v.impact === "critical")
      .map((v: AxeViolation) => ({
        id: v.id,
        impact: v.impact,
        help: v.help,
        nodes: v.nodes.map((n) => ({ target: n.target })),
      }));
  }, DISABLED_RULES);
}

/** A readable failure message — an id alone does not tell you what to fix. */
export function describeViolations(violations: AxeViolation[]): string {
  return violations
    .map(
      (v) =>
        `${v.impact}: ${v.id} — ${v.help}\n    ${v.nodes.map((n) => n.target.join(" ")).join("\n    ")}`,
    )
    .join("\n  ");
}

export async function expectNoSeriousViolations(page: Page, route: string): Promise<void> {
  const violations = await seriousViolations(page);
  expect(
    violations,
    `serious/critical axe violations on ${route}:\n  ${describeViolations(violations)}`,
  ).toEqual([]);
}
