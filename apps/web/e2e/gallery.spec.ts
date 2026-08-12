import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

import { STYLIST_STATE_PATH } from "./helpers/account";
import { completeQuestionnaire, waitForDesignQuiescent } from "./helpers/wizard";

// Phase 21: the account gallery, against real server state.
//
// Deferred here from the T7 e2e restructuring because the gallery did not exist
// yet. Zero cost throughout — the concept is produced by the same deterministic
// demo pipeline every other spec drives — and zero SMTP, since nothing here
// touches delivery.
//
// What this covers that a unit test cannot: that a concept generated through
// the real pipeline is findable afterwards from the account page, with links
// that actually resolve to the concept and its workspace. The unit tests own
// every rendering branch (loading, empty, failed thumbnail, malformed body);
// this owns the round trip.

// Phase 22 (ADR 0027): the gallery now ships behind ACCOUNT_GALLERY_ENABLED,
// default FALSE, because one account's whole history on a shared shop-floor
// iPad means one customer's concepts in front of the next. Gated, not deleted —
// so this spec covers BOTH states rather than deleting the ADR 0024 assertions.
//
// The gate is read from the stack under test rather than assumed, and the
// enabled-only tests skip visibly when it is closed. That is a legitimate
// conditional skip: unlike the catalogue skip this file's neighbour used to
// carry, the condition is one flag an operator can set, not a rights checkpoint
// that was never going to be run.
test.describe.configure({ mode: "serial", timeout: 240_000 });

// The gallery only exists for an account, so every test here is signed in as
// the run's shared stylist.
test.use({ storageState: STYLIST_STATE_PATH });

async function galleryIsEnabled(request: APIRequestContext): Promise<boolean> {
  // Read from the stack, not from a build-time constant: the flag is an
  // operator decision and this spec has to describe whichever stack it is
  // pointed at. The endpoint advertises it so the frontend can say WHY the
  // gallery is absent — it is not a permission, the list endpoint refuses on
  // its own.
  const response = await request.get("/api/v1/config/public");
  expect(response.status()).toBe(200);
  const config = (await response.json()) as { account_gallery_enabled?: boolean };
  return config.account_gallery_enabled === true;
}

test.describe("the account gallery, switched off", () => {
  test("says so plainly instead of showing an empty shelf", async ({ page, request }) => {
    test.skip(await galleryIsEnabled(request), "gallery is enabled on this stack");

    // The dangerous failure is an empty gallery, which would tell a shop its
    // concepts are gone. A refusal has to read as a refusal.
    const [response] = await Promise.all([
      page.waitForResponse(
        (res) =>
          res.url().includes("/api/v1/designs/") &&
          !res.url().includes("/images/") &&
          res.request().method() === "GET",
      ),
      page.goto("/account"),
    ]);

    expect(response.status()).toBe(503);
    expect(((await response.json()) as { error?: { code?: string } }).error?.code).toBe(
      "gallery_disabled",
    );

    await expect(page.getByText(/switched off for this account/i)).toBeVisible();
    // And it is honest about the consequence ADR 0027 accepts, rather than
    // implying the concept can be retrieved some other way.
    await expect(page.getByText(/no way back to it here/i)).toBeVisible();
    await expect(page.locator("article.gallery-card")).toHaveCount(0);
  });

  test("does not stop a walk-in starting a new concept", async ({ page, request }) => {
    test.skip(await galleryIsEnabled(request), "gallery is enabled on this stack");

    // POST /designs/ is deliberately NOT gated: the walk-in flow starts there,
    // and gating it would end the product rather than the gallery.
    await page.goto("/account");
    await expect(page.getByText(/switched off for this account/i)).toBeVisible();

    const designId = await completeQuestionnaire(page);

    expect(designId).toBeTruthy();
  });
});

test.describe("the account gallery", () => {
  test.beforeEach(async ({ request }) => {
    test.skip(
      !(await galleryIsEnabled(request)),
      "ACCOUNT_GALLERY_ENABLED is false (the shipped default, ADR 0027)",
    );
  });

  test("a concept made through the pipeline is findable from the account page", async ({
    page,
  }) => {
    const designId = await completeQuestionnaire(page);
    await waitForDesignQuiescent(page, designId);
    await page.getByRole("button", { name: /generate my concept/i }).click();
    await page.waitForURL(/\/result\//, { timeout: 180_000 });

    // The title the gallery card must be findable by is the one the result
    // screen shows, read from the page rather than assumed.
    const conceptHeading = await page.getByRole("heading", { level: 1 }).textContent();
    expect(conceptHeading).toBeTruthy();
    const resultUrl = page.url();
    const versionId = resultUrl.split("/result/")[1]?.replace(/\/$/, "");
    expect(versionId).toBeTruthy();

    await page.goto("/account");

    const gallery = page.getByRole("region", { name: /your concepts/i });
    await expect(gallery).toBeVisible({ timeout: 30_000 });

    // Located by the LINK to the design just made, not by position: the shared
    // account accumulates concepts from every earlier spec in the run, so
    // "the first card" is whatever ran most recently.
    const card = gallery.locator("article.gallery-card", {
      has: page.locator(`a[href^="/design/${designId}/result/"]`),
    });
    await expect(card).toHaveCount(1);

    // The card must be named the same thing the concept is named. Asserting
    // equality rather than mere visibility, because `toBeVisible` passes on an
    // empty <h3> — which is exactly the defect this spec caught: `Design.title`
    // is blank for every questionnaire concept, so a card built on it renders a
    // nameless heading and jsdom fixtures that set a title hide it.
    const cardHeading = card.getByRole("heading", { level: 3 });
    await expect(cardHeading).toBeVisible();
    expect((await cardHeading.textContent())?.trim()).toBe(conceptHeading?.trim());

    // The links must point at the real design and version — the whole point of
    // the gallery is getting back to a concept, so a card that renders but
    // links nowhere would pass a weaker assertion while being useless.
    const view = card.getByRole("link", { name: /^view /i });
    await expect(view).toHaveAttribute("href", `/design/${designId}/result/${versionId}`);
    await expect(card.getByRole("link", { name: /^annotate /i })).toHaveAttribute(
      "href",
      `/design/${designId}/result/${versionId}/annotate`,
    );

    // The version number reaches each link's accessible name, so the two links
    // in a card are tellable apart without the visual grouping.
    await expect(view).toHaveAccessibleName(/version 1/i);

    // Demo mode, so the label must say so rather than claiming a live render.
    await expect(card.getByText("Demo concept")).toBeVisible();
    await expect(gallery.getByText("AI-generated concept")).toHaveCount(0);

    // The thumbnail resolves against the real signed-URL endpoint. Asserting
    // naturalWidth rather than mere visibility, because a broken <img> is still
    // "visible" to Playwright and would hide exactly the failure that matters.
    const image = card.locator("img.gallery-thumb");
    await expect(image).toBeVisible();
    await expect
      .poll(async () => image.evaluate((node: HTMLImageElement) => node.naturalWidth), {
        timeout: 30_000,
      })
      .toBeGreaterThan(0);

    // Following the link lands on the concept it advertised.
    await view.click();
    await expect(page).toHaveURL(new RegExp(`/design/${designId}/result/`));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      conceptHeading as string,
    );
  });

  test("the gallery carries no signed URL, storage key or hash in its own payload", async ({
    page,
  }) => {
    // The list response is the thing under test, not the rendered page: a
    // bearer URL for every row is the failure this endpoint was shaped to
    // avoid, and it would be invisible on screen.
    const [response] = await Promise.all([
      page.waitForResponse(
        (res) => res.url().includes("/api/v1/designs/") && !res.url().includes("/images/"),
      ),
      page.goto("/account"),
    ]);

    const body = await response.text();
    expect(response.status()).toBe(200);
    // The signed-URL query parameters MinIO/S3 presigning always emits.
    expect(body).not.toContain("X-Amz-Signature");
    expect(body).not.toContain("X-Amz-Credential");
    expect(body).not.toContain("design-images/");
    expect(body).not.toContain("generation-staging/");
    // Job snapshot fields that belong to design detail, not to a gallery row.
    expect(body).not.toContain("error_code");
    expect(body).not.toContain("generation_kind");
    // The DesignSpec's DESCRIPTION of the garment. `display_title` (its name) is
    // deliberately present — see the backend's `_display_title` — so this pins
    // the half of that boundary which stays closed.
    expect(body).not.toContain("concept_summary");
    expect(body).not.toContain("garment_breakdown");
    expect(body).not.toContain("colour_story");
    expect(body).not.toContain("image_alt_text");
    expect(body).not.toContain("image_prompt");

    // And nothing about the gallery is written to browser storage.
    const stored = await page.evaluate(() => ({
      local: window.localStorage.length,
      session: window.sessionStorage.length,
    }));
    expect(stored).toEqual({ local: 0, session: 0 });
  });
});
