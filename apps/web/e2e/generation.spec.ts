import { expect, test } from "@playwright/test";

import { STYLIST_STATE_PATH } from "./helpers/account";
import { completeQuestionnaire, waitForDesignQuiescent } from "./helpers/wizard";

// Phase 17 §25 journeys 6-10: a real generation driven by real server state,
// then resume, then a failed image with the brief intact, then the one
// refinement, then the two-version history.
//
// Nothing here fakes a stage. The demo pipeline is a genuine Celery job moving
// through queued → running_text → running_image → succeeded, and these tests
// wait for what the server actually reports. That is the whole point of §12's
// "never auto-advance on a timer": if the UI ever invented progress, this spec
// would still pass while the real thing was stuck, so the assertions are
// written against the terminal state the server produces.

// A full demo generation runs two pipeline stages and an image ingest.
test.describe.configure({ mode: "serial", timeout: 180_000 });

// Signed in as the run's shared account: since Phase 21 the last press before a
// concept is produced needs one (ADR 0023). The questionnaire itself is still
// walked exactly as an anonymous visitor walks it — only that button is gated —
// and journeys.spec.ts covers the anonymous half without ever registering.
test.use({ storageState: STYLIST_STATE_PATH });

test.describe("generation, result, refinement and history", () => {
  test("journeys 6-10: generate, resume, refine once, then compare versions", async ({ page }) => {
    // --- Journey 6: questionnaire → generation → result -------------------
    const designId = await completeQuestionnaire(page);

    const generate = page.getByRole("button", { name: /generate my concept/i });
    await expect(
      generate,
      "the Generate button must be offered in demo mode — see generation-availability.ts",
    ).toBeEnabled();
    // The design must be quiescent before enqueuing: a write landing after the
    // attempt is created makes the pipeline reject the job as design_changed.
    await waitForDesignQuiescent(page, designId);
    await generate.click();

    await expect(page).toHaveURL(/\/generation\/[0-9a-f-]{36}/, { timeout: 30_000 });
    const generationUrl = page.url();

    // The three narrated stages, and never a percentage or an estimate.
    await expect(page.getByRole("listitem")).toHaveCount(3);
    const body = await page.locator("body").innerText();
    expect(body).not.toMatch(/\d+\s*%/);
    expect(body).not.toMatch(/minutes? (left|remaining)|almost done/i);

    // --- Journey 7: refresh mid-flight and resume the same job ------------
    await page.reload();
    expect(page.url()).toBe(generationUrl);
    await expect(page.getByRole("listitem")).toHaveCount(3);

    // --- Journey 6 continued: the real terminal state ---------------------
    await page.waitForURL(/\/result\/[0-9a-f-]{36}/, { timeout: 150_000 });
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    // Honestly labelled as demo, never as a fresh provider render.
    await expect(page.getByText(/demo/i).first()).toBeVisible();

    const originalResultUrl = page.url();

    // --- Journey 8: the image fails, the brief survives --------------------
    //
    // The result page fetches the curated result and the signed image as two
    // INDEPENDENT queries precisely so one failing cannot take the other down
    // (CLAUDE.md §14). Failing the image endpoint is the only honest way to
    // prove that: with it aborted, the brief must still render in full and the
    // image area must offer a retry rather than blanking the page.
    await page.route("**/versions/*/images/**", (route) => route.abort());
    await page.reload();

    const brief = page.getByRole("heading", { level: 1 });
    await expect(brief).toBeVisible();
    // Scoped to the IMAGE's own alert, not `getByRole("alert").first()`: the
    // result route has several alert regions (route error, refinement failure,
    // comparison), so a loose match could pass on an unrelated one and prove
    // nothing about the image query at all.
    const imageAlert = page.locator(".result-image-state[role='alert']");
    await expect(imageAlert).toBeVisible({ timeout: 30_000 });
    await expect(imageAlert.getByRole("button", { name: /try again|refresh image/i })).toBeVisible();
    // The brief's own content is still there — not an error page.
    await expect(page.getByText(/scarlet/i).first()).toBeVisible();

    await page.unroute("**/versions/*/images/**");
    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    // --- Journey 9: refinement, three rounds per concept -------------------
    const refineHeading = page.getByRole("heading", { name: /what would you change/i });
    await expect(
      refineHeading,
      "the refinement panel must be offered on an eligible version 1 in demo mode",
    ).toBeVisible({ timeout: 30_000 });

    await page.getByRole("radio", { name: /colour story/i }).check({ force: true });
    await page.getByRole("checkbox").first().check({ force: true });
    const request = page.getByRole("button", { name: /request refinement/i });
    await expect(request).toBeEnabled();
    await request.click();

    await expect(page).toHaveURL(/\/generation\/[0-9a-f-]{36}/, { timeout: 30_000 });
    // The progress URL carries the source version, which is what makes the
    // "back to your original concept" link render while the job runs.
    //
    // The link itself is asserted in GenerationProgress.test.tsx rather than
    // here: a refinement reuses the existing DesignSpec, so it has no text
    // stage and finishes faster than a browser can reliably observe the
    // progress screen — even at DEMO_STAGE_DELAY_MS=5000, its maximum. Racing
    // it here would produce a test that passes on a slow machine and fails on
    // a fast one, which is worse than no coverage at all.
    expect(page.url()).toMatch(/[?&]from=[0-9a-f-]{36}/);

    await page.waitForURL(/\/result\/[0-9a-f-]{36}/, { timeout: 150_000 });
    const refinedResultUrl = page.url();
    expect(refinedResultUrl).not.toBe(originalResultUrl);

    // --- Journey 10: both versions, latest first --------------------------
    await expect(page.getByRole("heading", { name: /previous design/i })).toBeVisible({
      timeout: 30_000,
    });

    // --- Journey 9 continued: the chain carries on from the NEW version ---
    // The refined concept is refinable in turn (ADR 0029), and the offer is
    // below the comparison rather than absent.
    await expect(
      page.getByRole("heading", { name: /what would you change/i }),
      "a refined concept must itself be refinable while rounds remain",
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/2 refinements left/i).first()).toBeVisible();

    // --- Journey 9 continued: the ORIGINAL version is now read-only -------
    // Not because the budget is spent — two rounds remain — but because a
    // newer version exists and a lineage is a chain, never a tree.
    await page.goto(originalResultUrl);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: /what would you change/i })).toHaveCount(0);
    await expect(page.getByText(/earlier version of your design/i).first()).toBeVisible({
      timeout: 30_000,
    });
    // It says where to carry on from, rather than leaving a dead end.
    await expect(page.getByRole("link", { name: /most recent concept/i })).toBeVisible();

    // Refining THIS version is not reachable by any control on the page.
    await expect(page.getByRole("button", { name: /request refinement/i })).toHaveCount(0);

    // Nothing private leaked into a visible label anywhere in this journey.
    const finalBody = await page.locator("body").innerText();
    expect(finalBody).not.toContain(designId);
  });
});
