import { expect, test, type Page } from "@playwright/test";

import { advanceUntilQuestion, completeQuestionnaire, waitForDesignQuiescent } from "./helpers/wizard";

// Phase 17 §26: deterministic mobile and desktop screenshots.
//
// Both projects run this file, so every baseline below exists twice — once at
// 1440x1000 and once at 390x844. Determinism comes from four places:
//
//  * `contextOptions.reducedMotion` in playwright.config.ts, which the
//    application honours by zeroing every transition and animation, plus
//    `animations: "disabled"` and `caret: "hide"` at capture time.
//  * A fixed locale and timezone, so no rendered date or number drifts.
//  * `settle()` below, which waits for fonts, for every image to have decoded
//    (a half-loaded hero is the classic flapping baseline) and — the part that
//    actually mattered here — for every pending query to have landed, because
//    an unresolved one displaces the whole page rather than one component.
//  * `mask` on anything genuinely non-deterministic. §26 forbids baselining
//    signed URLs, emails, random ids or infrastructure status, so the concept
//    image itself is masked: it is delivered by a short-TTL signed URL and the
//    demo pack may legitimately be re-rendered. Its FRAME is still compared,
//    which is what the layout tests care about.

/**
 * Wait until fonts and images have settled, so a baseline cannot flap.
 *
 * `networkIdle` is opt-out for the generation screen alone: that page polls the
 * job on a 1s/2s/5s backoff, so it is never idle for 500ms while a job is in
 * flight, and waiting for idle there would burn the very window the shot has to
 * happen in.
 */
async function settle(page: Page, { networkIdle = true } = {}): Promise<void> {
  if (networkIdle) await page.waitForLoadState("networkidle");

  // The site-wide demo banner renders `null` until the public config resolves,
  // and it is ~43px tall — so a shot taken before it appears is not slightly
  // different from the baseline, it is the whole page displaced by 43px. It is
  // mounted on every route, and safety.spec.ts proves the stack under test is
  // in demo mode, so its presence is a reliable positive signal that config has
  // landed. Waiting for the ABSENCE of a pending state could not catch this
  // one: the questionnaire announces no "Checking…" text while config is in
  // flight, it simply renders nothing where the banner will go.
  await expect(page.locator(".demo-banner")).toBeVisible({ timeout: 20_000 });

  // No other query may still be in flight either. The remaining pending states
  // ARE announced in a live region ("Checking your design…"), and each occupies
  // vertical space that vanishes when it resolves. Both the review and the
  // result baselines flapped this way. `networkidle` alone did not catch it,
  // because React renders the resolved markup a frame after the response lands.
  await expect
    .poll(
      async () => {
        const live = await page.locator('[role="status"]').allInnerTexts();
        return live.some((text) => /checking|loading/i.test(text));
      },
      { message: "a pending query never resolved", timeout: 20_000 },
    )
    .toBe(false);

  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(
      Array.from(document.images)
        .filter((image) => !image.complete)
        .map(
          (image) =>
            new Promise((resolve) => {
              image.addEventListener("load", resolve, { once: true });
              image.addEventListener("error", resolve, { once: true });
            }),
        ),
    );
  });
}

/** Everything a screenshot must never bake in. */
function masks(page: Page) {
  return [
    // Signed, short-TTL image delivery.
    //
    // `.comparison-grid`, not `.comparison`: VersionComparison renders
    // `version-comparison` / `comparison-grid` and no bare `comparison` class,
    // so the obvious-looking selector matched nothing. Both comparison images
    // happen to be caught by `.result-image-figure img` above today, which is
    // exactly why the dead selector was invisible — the mask protecting the
    // history baseline must be load-bearing in its own right, not incidentally
    // covered by a sibling rule that a refactor could move.
    page.locator(".result-image-figure img"),
    page.locator(".comparison-grid img"),
    // The generated concept's own copy varies with the demo selection.
    page.locator(".result-image-state"),
  ];
}

async function shoot(page: Page, name: string, options: { networkIdle?: boolean } = {}): Promise<void> {
  await settle(page, options);
  await expect(page).toHaveScreenshot(`${name}.png`, {
    fullPage: true,
    mask: masks(page),
  });
}

test.describe("visual regression: static routes", () => {
  test("landing", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("heading", { level: 1 }).waitFor();
    await shoot(page, "landing");
  });

  test("privacy", async ({ page }) => {
    await page.goto("/privacy");
    await page.getByRole("heading", { level: 1 }).waitFor();
    await shoot(page, "privacy");
  });

  test("concepts", async ({ page }) => {
    await page.goto("/concepts");
    await page.getByRole("heading", { level: 1 }).waitFor();
    await shoot(page, "concepts");
  });
});

test.describe("visual regression: questionnaire screens", () => {
  // One walk, several shots: re-walking per screen would triple the runtime
  // for no extra coverage.
  test("ceremony, garment, colours, fabric, coverage and drape", async ({ page }) => {
    test.setTimeout(180_000);
    await page.goto("/design/new");

    await advanceUntilQuestion(page, /which ceremony/i);
    await shoot(page, "questionnaire-ceremony");

    await advanceUntilQuestion(page, /which garment/i);
    await shoot(page, "questionnaire-garment");

    await advanceUntilQuestion(page, /which colours are yours/i);
    await shoot(page, "questionnaire-colours");

    await advanceUntilQuestion(page, /fabric|cloth/i);
    await shoot(page, "questionnaire-fabric");

    // §26 names "fabric/embroidery" as one required state, and the walk used to
    // step straight from fabric to coverage — skipping embroidery entirely. It
    // matters because embroidery and richness are framed SQUARE where fabric is
    // 3:2, so a regression in that grid had no baseline to fail against.
    await advanceUntilQuestion(page, /embroidery|embellishment|handwork/i);
    await shoot(page, "questionnaire-embroidery");

    await advanceUntilQuestion(page, /coverage|sleeve|midriff|back/i);
    await shoot(page, "questionnaire-coverage");

    await advanceUntilQuestion(page, /dupatta|drape/i);
    await shoot(page, "questionnaire-drape");
  });
});

test.describe("visual regression: generation, result, refinement and history", () => {
  test("review, generation, result, refinement and comparison", async ({ page }) => {
    test.setTimeout(240_000);

    const designId = await completeQuestionnaire(page);
    await shoot(page, "review");

    await waitForDesignQuiescent(page, designId);
    await page.getByRole("button", { name: /generate my concept/i }).click();
    await expect(page).toHaveURL(/\/generation\//, { timeout: 30_000 });
    // Pin WHICH stage is current before capturing. Without this the shot lands
    // on whichever of the three stages happens to be active when it is taken,
    // and the baseline flaps between runs — it did, on mobile, which is why
    // this wait exists.
    //
    // "Design brief" (running_text) is the anchor, not "Preparing": queued is
    // transient because the worker picks the job up immediately, so waiting for
    // it would simply time out. running_text is held for DEMO_STAGE_DELAY_MS,
    // and running_image is held for the same again behind it, so there is a
    // wide margin before the page navigates to the result and nothing to race.
    // That is also why e2e/README.md requires DEMO_STAGE_DELAY_MS=5000.
    await expect(page.getByRole("listitem").nth(1)).toHaveAttribute("aria-current", "step", {
      timeout: 30_000,
    });
    await shoot(page, "generation", { networkIdle: false });

    await page.waitForURL(/\/result\//, { timeout: 180_000 });
    await page.getByRole("heading", { level: 1 }).waitFor();
    await shoot(page, "result");

    // The refinement form, in its available state, on the same page.
    await expect(page.getByRole("heading", { name: /what would you change/i })).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("radio", { name: /colour story/i }).check({ force: true });
    await page.getByRole("checkbox").first().check({ force: true });
    await shoot(page, "refinement");

    await page.getByRole("button", { name: /request refinement/i }).click();
    await page.waitForURL(/\/result\//, { timeout: 180_000 });
    await expect(page.getByRole("heading", { name: /previous design/i })).toBeVisible({
      timeout: 30_000,
    });
    await shoot(page, "history");
  });
});
