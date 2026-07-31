import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { advanceUntilQuestion, completeQuestionnaire, waitForDesignQuiescent } from "./helpers/wizard";

// Phase 17 §31 and §32, executed rather than asserted by hand.
//
// §31 wants "no serious/critical axe violations" on landing, a visual-card
// question, colours, generation and result. §32 wants reduced motion, 200%
// zoom, 320px width, no clipped sticky UI and no obscured focus. Both are
// machine-checkable, so they are checked by machine and the evidence is a test
// result rather than a claim in a report.
//
// axe-core is injected from node_modules — it is already present as a
// transitive dependency of jest-axe, which the Vitest component tests use, so
// this adds no dependency. Injecting the same engine keeps the component-level
// and page-level accessibility checks honest with each other.
const AXE_PATH = join(__dirname, "..", "node_modules", "axe-core", "axe.min.js");

// Contrast is checked separately by the design-token tests and by manual
// inspection against the handoff palette: axe cannot evaluate contrast through
// a background image, and the landing hero and every option card sit on one,
// which produces false positives rather than findings. Nothing else is
// disabled — in particular every name/role/value, landmark and label rule runs.
const DISABLED_RULES = ["color-contrast"];

type AxeViolation = {
  id: string;
  impact: string | null;
  help: string;
  nodes: { target: string[] }[];
};

/** Run axe against the whole page and return only serious/critical findings. */
async function seriousViolations(page: Page): Promise<AxeViolation[]> {
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
function describe(violations: AxeViolation[]): string {
  return violations
    .map((v) => `${v.impact}: ${v.id} — ${v.help}\n    ${v.nodes.map((n) => n.target.join(" ")).join("\n    ")}`)
    .join("\n  ");
}

async function expectNoSeriousViolations(page: Page, route: string): Promise<void> {
  const violations = await seriousViolations(page);
  expect(violations, `serious/critical axe violations on ${route}:\n  ${describe(violations)}`).toEqual([]);
}

/**
 * The page must not scroll sideways. This is the single assertion behind both
 * the 320px and the 200%-zoom checks: a layout that overflows horizontally is
 * exactly what both of those are looking for.
 */
async function expectNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return { scroll: doc.scrollWidth, client: doc.clientWidth };
  });
  expect(
    overflow.scroll,
    `${label}: content is ${overflow.scroll}px wide in a ${overflow.client}px viewport, so the page scrolls sideways`,
  ).toBeLessThanOrEqual(overflow.client + 1);
}

test.describe("§31 axe: no serious or critical violations", () => {
  test("landing, privacy and concepts", async ({ page }) => {
    for (const route of ["/", "/privacy", "/concepts"]) {
      await page.goto(route);
      await page.getByRole("heading", { level: 1 }).waitFor();
      await expectNoSeriousViolations(page, route);
    }
  });

  test("a visual-card question and the colours screen", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/design/new");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(/which ceremony/i);
    await expectNoSeriousViolations(page, "questionnaire: ceremony (visual cards)");

    // The information drawer is a dialog — its focus management and labelling
    // are exactly what axe should be pointed at, and it is only reachable open.
    await page.getByRole("button", { name: /more about nikah/i }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expectNoSeriousViolations(page, "questionnaire: information drawer");
    await page.keyboard.press("Escape");

    await advanceUntilQuestion(page, /which colours are yours/i);
    await expectNoSeriousViolations(page, "questionnaire: colours");
  });

  test("generation and result", async ({ page }) => {
    test.setTimeout(240_000);
    const designId = await completeQuestionnaire(page);
    await expectNoSeriousViolations(page, "review");

    await waitForDesignQuiescent(page, designId);
    await page.getByRole("button", { name: /generate my concept/i }).click();
    await expect(page).toHaveURL(/\/generation\//, { timeout: 30_000 });
    await expectNoSeriousViolations(page, "generation (in flight)");

    await page.waitForURL(/\/result\//, { timeout: 180_000 });
    await page.getByRole("heading", { level: 1 }).waitFor();
    await expectNoSeriousViolations(page, "result");
  });
});

test.describe("§32 reduced motion, zoom and narrow viewports", () => {
  test("reduced motion actually zeroes transitions and animations", async ({ page }) => {
    // The whole suite runs under contextOptions.reducedMotion = "reduce", so
    // this both proves the application honours the preference AND proves the
    // emulation is switched on — the config silently ignored an earlier,
    // wrongly-placed reducedMotion key, and nothing at runtime said so.
    await page.goto("/");
    await page.getByRole("heading", { level: 1 }).waitFor();

    expect(
      await page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches),
      "the browser context is not emulating reduced motion — check use.contextOptions",
    ).toBe(true);

    const moving = await page.evaluate(() =>
      Array.from(document.querySelectorAll("*"))
        .map((element) => {
          const style = getComputedStyle(element);
          const durations = [style.transitionDuration, style.animationDuration]
            .flatMap((value) => value.split(",").map((part) => part.trim()))
            .filter((value) => value !== "0s" && value !== "");
          return durations.length ? `${element.tagName.toLowerCase()}.${element.className}` : null;
        })
        .filter(Boolean),
    );
    expect(moving, `elements still animate under reduced motion: ${moving.join(", ")}`).toEqual([]);
  });

  test("320px width reflows without sideways scrolling", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 });
    for (const route of ["/", "/privacy", "/concepts", "/design/new"]) {
      await page.goto(route);
      await page.getByRole("heading", { level: 1 }).waitFor();
      await expectNoHorizontalOverflow(page, `320px ${route}`);
    }
  });

  test("200% zoom reflows without sideways scrolling", async ({ page }) => {
    // 200% zoom on a 1440x1000 desktop window presents the page with half the
    // CSS pixels in each axis, which is what this viewport reproduces. Testing
    // it this way rather than with the non-standard CSS `zoom` property means
    // the layout is exercised through the same reflow path a zooming user hits.
    await page.setViewportSize({ width: 720, height: 500 });
    for (const route of ["/", "/design/new"]) {
      await page.goto(route);
      await page.getByRole("heading", { level: 1 }).waitFor();
      await expectNoHorizontalOverflow(page, `200% zoom ${route}`);
    }
  });

  test("focus is never hidden behind sticky UI", async ({ page }) => {
    await page.goto("/design/new");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(/which ceremony/i);

    // Tab through the screen and, at each stop, confirm the focused element is
    // actually the topmost thing at its own centre. A sticky header that covers
    // a focused control passes every "is it focusable" check and still leaves a
    // keyboard user unable to see where they are.
    const obscured: string[] = [];
    for (let i = 0; i < 25; i += 1) {
      await page.keyboard.press("Tab");
      const hidden = await page.evaluate(() => {
        const active = document.activeElement as HTMLElement | null;
        if (!active || active === document.body) return null;
        const box = active.getBoundingClientRect();
        if (box.width === 0 || box.height === 0) return null;
        // Only meaningful for a control scrolled into view.
        if (box.top < 0 || box.bottom > window.innerHeight) return null;
        const topmost = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
        if (!topmost) return null;
        const covered = !active.contains(topmost) && !topmost.contains(active);
        return covered ? `${active.tagName.toLowerCase()} covered by ${topmost.tagName.toLowerCase()}.${topmost.className}` : null;
      });
      if (hidden) obscured.push(hidden);
    }
    expect(obscured, `focused controls obscured by overlying UI: ${obscured.join("; ")}`).toEqual([]);
  });
});
