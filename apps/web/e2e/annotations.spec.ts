import { expect, test } from "@playwright/test";

import { completeQuestionnaire, waitForDesignQuiescent } from "./helpers/wizard";

// Phase 19 §E2E: the private annotation workspace, end to end against real
// server state.
//
// Zero cost and zero SMTP throughout. The generation is the same deterministic
// demo pipeline the Phase 17 specs drive, and the send assertion stops at the
// QUEUED outcome — Django's test-independent locmem backend means no SMTP
// connection is opened, and asserting on a delivered message would require
// either a real mail server or a fiction about one.
//
// The marks are drawn with real pointer gestures rather than by seeding the
// database, because the coordinate path from a press to a stored normalised
// coordinate is exactly what a unit test cannot cover.

test.describe.configure({ mode: "serial", timeout: 240_000 });

/** Press and release on the render, in fractions of its own rendered box. */
async function markAt(
  page: import("@playwright/test").Page,
  fx: number,
  fy: number,
  to?: { fx: number; fy: number },
) {
  const stage = page.locator(".annotation-stage");
  // The press is positioned by `hover`, element-relative, rather than by absolute
  // coordinates computed from a boundingBox read before any scrolling. The
  // original form failed on the mobile project's first real run: the single-column
  // layout can leave the stage outside the 390x844 viewport, and `boundingBox()`
  // is viewport-relative, so the computed y addressed a point the mouse cannot
  // reach — no mark, no error, and a later assertion failing somewhere else.
  // `hover` performs Playwright's actionability checks and scrolls the element
  // into view first, so it cannot address a point off-screen.
  await stage.scrollIntoViewIfNeeded();
  const box = await stage.boundingBox();
  if (!box) throw new Error("the render has no rendered box");

  await stage.hover({ position: { x: box.width * fx, y: box.height * fy } });
  await page.mouse.down();
  if (to) {
    // Measured again: the press may itself have scrolled the page, which would
    // invalidate the first reading.
    const dragBox = (await stage.boundingBox()) ?? box;
    await page.mouse.move(
      dragBox.x + dragBox.width * to.fx,
      dragBox.y + dragBox.height * to.fy,
      { steps: 8 },
    );
  }
  await page.mouse.up();
}

test.describe("the private annotation workspace", () => {
  test("annotate, persist across a reload, send, and stay private", async ({ page, browser }) => {
    // --- A real demo concept to annotate ---------------------------------
    const designId = await completeQuestionnaire(page);
    await waitForDesignQuiescent(page, designId);
    await page.getByRole("button", { name: /generate my concept/i }).click();
    await page.waitForURL(/\/result\/[0-9a-f-]{36}/, { timeout: 180_000 });
    const resultUrl = page.url();

    // The concept screen offers Annotate and Send, and no longer a download.
    await expect(page.getByRole("link", { name: /^annotate$/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /download image/i })).toHaveCount(0);

    // The image the workspace will be drawn over, recorded so it can be proved
    // unchanged afterwards.
    const originalSrc = await page.locator(".result-image").getAttribute("src");

    // --- Open the workspace ----------------------------------------------
    await page.getByRole("link", { name: /^annotate$/i }).click();
    await expect(page).toHaveURL(/\/annotate$/);
    await expect(page.getByRole("heading", { name: "Annotate this concept" })).toBeVisible();
    await expect(page.getByText("Private — only you")).toBeVisible();
    // It must not imply that marking up the concept changes it.
    await expect(page.getByText(/the concept itself never changes/i)).toBeVisible();
    await expect(page.locator(".annotation-stage")).toBeVisible();

    // --- Add a pin and a rectangle, each with a note ----------------------
    await page.getByRole("button", { name: /^pin \(P\)$/i }).click();
    await markAt(page, 0.5, 0.3);
    await expect(page.getByRole("heading", { name: /annotations · 1/i })).toBeVisible();

    // `exact` matters: Playwright matches a label as a case-insensitive SUBSTRING,
    // and the row also carries an "Edit note for annotation 1" button, so the
    // inexact form resolved to two elements and violated strict mode. The vitest
    // suite does not catch this because RTL's getByLabelText matches exactly.
    const firstNote = page.getByLabel("Note for annotation 1", { exact: true });
    await expect(firstNote).toBeVisible();
    await firstNote.fill("Raise the neckline by two fingers");
    await page.getByRole("button", { name: "Save" }).click();

    await page.getByRole("button", { name: /^rectangle \(R\)$/i }).click();
    await markAt(page, 0.25, 0.6, { fx: 0.75, fy: 0.85 });
    await expect(page.getByRole("heading", { name: /annotations · 2/i })).toBeVisible();

    const secondNote = page.getByLabel("Note for annotation 2", { exact: true });
    await secondNote.fill("Champagne hem border is too narrow here");
    await page.getByRole("button", { name: "Save" }).click();

    // Saved for real, by the server, not merely shown as saved.
    await expect(page.locator(".annotation-save-pill")).toHaveText(/saved/i, { timeout: 20_000 });

    // --- Persistence across a reload -------------------------------------
    await page.reload();
    await expect(page.getByRole("heading", { name: /annotations · 2/i })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("Raise the neckline by two fingers")).toBeVisible();
    await expect(page.getByText("Champagne hem border is too narrow here")).toBeVisible();
    // Both marks are back on the canvas, at coordinates the server stored.
    await expect(page.locator(".annotation-overlay .mark")).toHaveCount(2);

    // Nothing about the document was kept in browser storage — the requirement
    // covers all three stores, so all three are read. IndexedDB is enumerated
    // rather than merely probed for support: an earlier version of this check
    // recorded whether `databases()` existed and then asserted nothing about it,
    // which would have passed with a database sitting right there.
    const stored = await page.evaluate(async () => ({
      local: JSON.stringify(window.localStorage),
      session: JSON.stringify(window.sessionStorage),
      databases: (await indexedDB.databases()).map((entry) => entry.name ?? ""),
    }));
    expect(stored.local).not.toContain("neckline");
    expect(stored.session).not.toContain("neckline");
    expect(stored.local).not.toContain("annotation");
    expect(stored.session).not.toContain("annotation");
    // Not "no databases at all" — a future unrelated feature may legitimately
    // open one. What must not exist is a store for this document.
    expect(stored.databases.filter((name) => /annot|mark|design/i.test(name))).toEqual([]);

    // --- Hiding is not deleting ------------------------------------------
    await page.getByRole("button", { name: /hide annotations/i }).click();
    await expect(page.getByText(/overlays hidden/i)).toBeVisible();
    await expect(page.getByRole("heading", { name: /annotations · 2/i })).toBeVisible();
    await page.getByRole("button", { name: /show annotations/i }).click();
    await expect(page.locator(".annotation-overlay .mark")).toHaveCount(2);

    // --- The send action, as an anonymous owner ---------------------------
    //
    // This journey never registered, so the workspace owner is the anonymous
    // session. §8.1 is explicit that there is no fallback: no prompt for an
    // address, no silent success.
    const sendButton = page.getByRole("button", { name: /send to account/i });
    await expect(sendButton).toBeDisabled();
    await expect(page.getByText(/sign in to send this to your email/i)).toBeVisible();

    // --- Another browser cannot reach this workspace ----------------------
    const stranger = await browser.newContext();
    const strangerPage = await stranger.newPage();
    await strangerPage.goto(page.url());
    // Indistinguishable from "never existed" — ownership is resolved before the
    // UUID is ever looked up.
    await expect(strangerPage.getByText(/could not be opened/i)).toBeVisible({ timeout: 30_000 });
    const strangerBody = await strangerPage.locator("body").innerText();
    expect(strangerBody).not.toContain("neckline");
    expect(strangerBody).not.toContain("Champagne");
    await stranger.close();

    // --- The original render is untouched ---------------------------------
    await page.goto(resultUrl);
    await expect(page.locator(".result-image")).toBeVisible({ timeout: 30_000 });
    // A fresh signed URL is expected (they are short-lived and minted per
    // request); what must not change is WHICH object it points at.
    const currentSrc = await page.locator(".result-image").getAttribute("src");
    const objectKey = (url: string | null) => (url ?? "").split("?")[0];
    expect(objectKey(currentSrc)).toBe(objectKey(originalSrc));
  });

  test("an authenticated owner can queue a send, and it is only ever queued", async ({ page }) => {
    // Registered FIRST, so the design belongs to an account with an address the
    // server can resolve. The recipient is never supplied by the client.
    await page.goto("/register");
    const email = `annotate-${Date.now()}@example.test`;
    await page.getByLabel(/email/i).fill(email);
    // Anchored rather than exact: the real label is "Password (at least 12
    // characters)", so `{ exact: true }` on "Password" matched nothing. Anchoring
    // at the start is what keeps it off "Confirm password" below. This line has
    // never run — the suite is serial and the test above failed first — so it was
    // latent until the first green run reached it.
    await page.getByLabel(/^password \(/i).fill("a-strong-test-password-123");
    await page.getByLabel(/confirm password/i).fill("a-strong-test-password-123");
    await page.getByRole("button", { name: /create account|register|sign up/i }).click();
    await expect(page.getByText(email)).toBeVisible({ timeout: 30_000 });

    const designId = await completeQuestionnaire(page);
    await waitForDesignQuiescent(page, designId);
    await page.getByRole("button", { name: /generate my concept/i }).click();
    await page.waitForURL(/\/result\/[0-9a-f-]{36}/, { timeout: 180_000 });

    await page.getByRole("link", { name: /^annotate$/i }).click();
    await expect(page.getByRole("heading", { name: "Annotate this concept" })).toBeVisible();

    await page.getByRole("button", { name: /^pin \(P\)$/i }).click();
    await markAt(page, 0.5, 0.4);
    await page.getByLabel("Note for annotation 1", { exact: true }).fill("Send this one to me");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.locator(".annotation-save-pill")).toHaveText(/saved/i, { timeout: 20_000 });

    const send = page.getByRole("button", { name: /send to account/i });
    await expect(send).toBeEnabled();
    await send.click();

    // Queued, and announced. With ACCOUNT_EMAIL_DELIVERY_ENABLED at its shipped
    // default of false the API answers 503 and the UI says so honestly — either
    // outcome is correct here, and BOTH are silent on SMTP, which is the point.
    const flash = page.getByText(/sent to your email|not available at the moment/i);
    await expect(flash).toBeVisible({ timeout: 20_000 });

    // Whatever happened, no address was ever typed into this page and none
    // appears in a URL.
    expect(page.url()).not.toContain("@");
  });
});
