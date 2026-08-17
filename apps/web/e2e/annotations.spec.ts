import { expect, test } from "@playwright/test";

import { STYLIST_STATE_PATH, stylistEmail } from "./helpers/account";
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

// Signed in as the run's shared account. A workspace can only exist under an
// account since Phase 21 (ADR 0023), so there is no anonymous variant of any test
// in this file — see the first test for what that displaced and where it went.
test.use({ storageState: STYLIST_STATE_PATH });

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
    //
    // Registered first, because since Phase 21 there is no such thing as an
    // anonymously generated concept (ADR 0023). What this journey used to prove
    // at the end — that an anonymous workspace owner is refused a send with no
    // fallback and no prompt for an address — is now UNREACHABLE from a browser,
    // since a workspace can only exist under an account. That rule has not been
    // dropped: it is proved at the API level instead, by
    // test_an_anonymous_owner_is_told_to_sign_in_and_nothing_is_sent in
    // apps/api/sitara/designs/tests/test_render_send_api.py, which builds an
    // anonymous-owned version directly and asserts 409
    // email_recipient_unavailable with an empty mail outbox. That test is now the
    // only proof of the rule, which is why it says so in its own docstring.
    const designId = await completeQuestionnaire(page);
    await waitForDesignQuiescent(page, designId);
    await page.getByRole("button", { name: /generate my concept/i }).click();
    await page.waitForURL(/\/result\/[0-9a-f-]{36}/, { timeout: 180_000 });
    const resultUrl = page.url();

    // The concept screen offers Annotate and Send, and no longer a download.
    await expect(page.getByRole("link", { name: /^annotate\b/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /download image/i })).toHaveCount(0);

    // The image the workspace will be drawn over, recorded so it can be proved
    // unchanged afterwards.
    const originalSrc = await page.locator(".result-image").getAttribute("src");

    // --- Open the workspace ----------------------------------------------
    await page.getByRole("link", { name: /^annotate\b/i }).click();
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

    // --- The send control is offered, and names no address ----------------
    //
    // The owner is an account, so the control is live. What must still hold here
    // is that the page never asks for an address and never puts one in a URL —
    // the recipient is resolved server-side and there is no field to type one
    // into. The send itself is exercised by the two tests below.
    await expect(page.getByRole("button", { name: /send to account/i })).toBeEnabled();
    await expect(page.getByRole("textbox", { name: /email|address|recipient/i })).toHaveCount(0);
    expect(page.url()).not.toContain("@");

    // --- Another browser cannot reach this workspace ----------------------
    //
    // The empty storage state is REQUIRED, not tidiness. `browser.newContext()`
    // inherits this file's `test.use` options, including the shared signed-in
    // session — so without this the "stranger" was the owner, reached the
    // workspace, and the test failed on the missing error rather than passing for
    // the wrong reason. It could just as easily have passed for the wrong reason
    // if the assertion had been weaker, which is why the next two lines prove the
    // context really is a stranger before anything is concluded from it.
    const stranger = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const strangerPage = await stranger.newPage();
    await strangerPage.goto("/");
    await expect(strangerPage.getByRole("link", { name: /^sign in$/i })).toBeVisible({
      timeout: 30_000,
    });

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

  test("an authenticated owner names the file, and the send is only ever queued", async ({
    page,
  }) => {
    // The design belongs to an account with an address the server can resolve.
    // The recipient is never supplied by the client — the address is read here
    // only to prove it never appears anywhere it should not.
    const email = stylistEmail();

    const designId = await completeQuestionnaire(page);
    await waitForDesignQuiescent(page, designId);
    await page.getByRole("button", { name: /generate my concept/i }).click();
    await page.waitForURL(/\/result\/[0-9a-f-]{36}/, { timeout: 180_000 });

    await page.getByRole("link", { name: /^annotate\b/i }).click();
    await expect(page.getByRole("heading", { name: "Annotate this concept" })).toBeVisible();

    await page.getByRole("button", { name: /^pin \(P\)$/i }).click();
    await markAt(page, 0.5, 0.4);
    await page.getByLabel("Note for annotation 1", { exact: true }).fill("Send this one to me");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.locator(".annotation-save-pill")).toHaveText(/saved/i, { timeout: 20_000 });

    // The allowance is stated BEFORE anything is spent, which is the whole point
    // of reading it on mount rather than only in the refusal that follows the
    // last send. Read from the server, not from a client-side guess.
    await expect(page.getByText(/of 3 emails left for this image/i)).toBeVisible({
      timeout: 20_000,
    });

    const send = page.getByRole("button", { name: /send to account/i });
    await expect(send).toBeEnabled();
    await send.click();

    // --- The naming prompt (Phase 21) -------------------------------------
    const dialog = page.getByRole("dialog", { name: /name this file/i });
    await expect(dialog).toBeVisible();
    const nameField = dialog.getByLabel(/file name/i);
    // Focus lands in the field, not on the dialog container: the first thing the
    // stylist does here is type.
    await expect(nameField).toBeFocused();

    // The accepted exposure is stated before they type, and worded as accepted —
    // ADR 0021 gave this up rather than mitigating it, and the copy must not
    // suggest otherwise.
    await expect(dialog.getByText(/email.*own headers/i)).toBeVisible();
    await expect(dialog.getByText(/outside sitara/i)).toBeVisible();

    // There is no address field here, and never has been: the recipient comes
    // from the session server-side.
    await expect(dialog.getByRole("textbox")).toHaveCount(1);

    await nameField.fill("Autumn lehenga v1");
    await dialog.getByRole("button", { name: /send to my email/i }).click();

    // Queued, and announced. With ACCOUNT_EMAIL_DELIVERY_ENABLED at its shipped
    // default of false the API answers 503 and the UI says so honestly — either
    // outcome is correct here, and BOTH are silent on SMTP, which is the point.
    //
    // This is also why the browser cannot drive the lifetime ceiling: nothing is
    // reserved while the gate is closed, so the counter never moves and a
    // "fourth send" is unreachable from here. The ceiling, the refusal wording
    // and the remembered name are proved instead against the real reservation in
    // apps/api/sitara/designs/tests/test_render_send_api.py —
    // test_the_fourth_send_is_refused_with_the_numbers_that_explain_it,
    // test_the_refusal_names_the_used_count_and_the_ceiling_separately and
    // test_the_name_is_remembered_for_the_next_send. Turning delivery on here to
    // reach it would mean configuring outbound mail on the e2e stack, which
    // CLAUDE.md §7 forbids.
    const flash = page.getByText(/sent to your email|not available at the moment/i);
    await expect(flash).toBeVisible({ timeout: 20_000 });

    // Whatever happened, no address was ever typed into this page, none appears
    // in a URL, and the name the stylist chose is not echoed into one either.
    expect(page.url()).not.toContain("@");
    expect(page.url()).not.toContain("Autumn");
    expect(page.url()).not.toContain(email);
  });

  test("a refused name is answered in the dialog, keeping what was typed", async ({ page }) => {
    // The one refusal the stylist can act on, and the reason the dialog stays
    // open for it: closing would throw away what they wrote and leave them to
    // guess what was wrong. Reachable with the delivery gate shut, because the
    // name is validated before the gate is consulted.
    const designId = await completeQuestionnaire(page);
    await waitForDesignQuiescent(page, designId);
    await page.getByRole("button", { name: /generate my concept/i }).click();
    await page.waitForURL(/\/result\/[0-9a-f-]{36}/, { timeout: 180_000 });

    await page.getByRole("link", { name: /^annotate\b/i }).click();
    await expect(page.getByRole("heading", { name: "Annotate this concept" })).toBeVisible();

    await page.getByRole("button", { name: /send to account/i }).click();
    const dialog = page.getByRole("dialog", { name: /name this file/i });
    const nameField = dialog.getByLabel(/file name/i);

    // A name that sanitises to nothing. Deliberately not a control character: a
    // single-line input discards CR and LF, typed or pasted, so a header-injection
    // name is not expressible here at all — the server refuses it as defence in
    // depth, and test_a_control_bearing_name_is_refused_and_nothing_is_sent
    // covers that path where it is reachable.
    await nameField.fill("///");
    await dialog.getByRole("button", { name: /send to my email/i }).click();

    // Answered where it was typed, and what they wrote is still there.
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("alert")).toBeVisible({ timeout: 20_000 });
    await expect(nameField).toHaveValue("///");

    // Correcting it and cancelling leaves the workspace exactly as it was.
    await dialog.getByRole("button", { name: /^cancel$/i }).click();
    await expect(page.getByRole("dialog", { name: /name this file/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /send to account/i })).toBeEnabled();
  });
});
