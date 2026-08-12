import { join } from "node:path";

import { expect, test } from "@playwright/test";

import {
  advanceUntilQuestion,
  answerAndAdvance,
  currentQuestion,
  stepForward,
  waitForDraftSaved,
} from "./helpers/wizard";

// Phase 17 §25 journeys 1-5: the parts of the flow that do not need a
// generation to have run. Journeys 6-10 live in generation.spec.ts.

// One of the project's own synthetic build outputs, reused as upload input.
const SYNTHETIC_UPLOAD = join(
  __dirname,
  "..",
  "public",
  "questionnaire-visuals",
  "fabrics",
  "fabric_silk.webp",
);

test.describe("journey 1: start, answer, leave via Home, come back to the draft", () => {
  test("the draft survives going Home and is resumed where it was left", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Start designing" }).first().click();

    await expect(page.getByRole("heading", { level: 1 })).toContainText(/which ceremony/i);
    await answerAndAdvance(page, "Nikah");
    await expect(page).toHaveURL(/\/design\/[0-9a-f-]{36}/, { timeout: 30_000 });
    const designUrl = page.url();
    await answerAndAdvance(page, "Lehenga");
    await waitForDraftSaved(page);

    // Leaving via Home must not read as, or be, throwing the work away — the
    // shell says so, and this proves the copy is telling the truth.
    await expect(page.getByText(/answers are saved as you go/i)).toBeVisible();
    await page.getByRole("link", { name: "Sitara — Home" }).click();
    await expect(page).toHaveURL(/\/$/);

    // Back to the same design: both answers survived, and the wizard resumes
    // at an unanswered question rather than starting over.
    //
    // Deliberately not asserting the exact screen: resume goes to the first
    // incomplete REQUIRED question, so it steps over an optional one the user
    // had merely reached. Pinning the heading here would encode that skipping
    // rule into a journey test and break whenever the schema gains a question.
    await page.goto(designUrl);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    const designId = designUrl.split("/design/")[1];
    const answers = await page.evaluate(async (id) => {
      const response = await fetch(`/api/v1/designs/${id}/`, { credentials: "same-origin" });
      return (await response.json()).answers ?? {};
    }, designId);
    expect(answers).toMatchObject({ ceremony: "nikah", garment_type: "lehenga" });
    expect(await currentQuestion(page)).not.toMatch(/which ceremony/i);
  });
});

test.describe("journey 2: keyboard-only wizard", () => {
  test("a keyboard user can reach and choose an option, and see the choice taken", async ({
    page,
  }) => {
    await page.goto("/design/new");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(/which ceremony/i);

    // Tab until a radio in the question's group has focus, then choose it with
    // the keyboard alone. Bounded so a focus trap fails the test rather than
    // hanging.
    let reached = false;
    for (let i = 0; i < 40 && !reached; i += 1) {
      await page.keyboard.press("Tab");
      reached = await page.evaluate(
        () => document.activeElement?.getAttribute("type") === "radio",
      );
    }
    expect(reached, "no radio reachable by Tab alone").toBe(true);

    const before = await currentQuestion(page);
    await page.keyboard.press("Space");
    await expect
      .poll(async () => currentQuestion(page), { timeout: 20_000 })
      .not.toBe(before);
  });

  test("Back and Skip are reachable and operable by keyboard alone", async ({ page }) => {
    await page.goto("/design/new");
    await advanceUntilQuestion(page, /which garment/i);

    // Back, by keyboard: focus it, activate with Enter, land on the previous
    // question. A control that can only be clicked is not navigable.
    const back = page.getByRole("button", { name: /^back$/i });
    await back.focus();
    await expect(back).toBeFocused();
    await page.keyboard.press("Enter");
    await expect
      .poll(async () => currentQuestion(page), { timeout: 20_000 })
      .toMatch(/which ceremony/i);

    // Skip, by keyboard, on the first optional screen the walk reaches. Skip
    // exists only on optional questions, so this walks to one rather than
    // assuming a position that a schema change would move.
    for (let screen = 0; screen < 30; screen += 1) {
      const skip = page.getByRole("button", { name: /^skip$/i });
      if (await skip.count()) {
        const before = await currentQuestion(page);
        await skip.focus();
        await expect(skip).toBeFocused();
        await page.keyboard.press("Enter");
        await expect.poll(async () => currentQuestion(page), { timeout: 20_000 }).not.toBe(before);
        return;
      }
      if (!(await stepForward(page))) break;
    }
    throw new Error("no optional question with a Skip control was reached");
  });

  test("the skip link moves focus to the main content", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: /skip to main content/i });
    await expect(skip).toBeFocused();
    await page.keyboard.press("Enter");
    expect(page.url()).toContain("#main-content");
  });
});

test.describe("journey 3: the information drawer, by keyboard", () => {
  test("opens, closes on Escape and returns focus to the control that opened it", async ({
    page,
  }) => {
    await page.goto("/design/new");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(/which ceremony/i);

    const trigger = page.getByRole("button", { name: /more about nikah/i });
    await trigger.focus();
    await page.keyboard.press("Enter");

    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    // Focus must come back to where it was, or a keyboard user is dropped at
    // the top of the document.
    await expect(trigger).toBeFocused();
  });
});

test.describe("journey 4: the custom colour picker", () => {
  test("a colour can be added from the bounded picker and then removed", async ({ page }) => {
    await page.goto("/design/new");
    await advanceUntilQuestion(page, /which colours are yours/i);

    // One picker per colour role (fabric, embroidery, dupatta), so the role has
    // to be named or all three match.
    const fabric = page.getByRole("group", { name: "The fabric" });
    const picker = fabric.getByRole("button", { name: /any colour/i });
    await expect(picker).toBeVisible();
    await expect(picker).toHaveAttribute("aria-expanded", "false");
    await picker.click();
    await expect(picker).toHaveAttribute("aria-expanded", "true");

    await fabric.getByRole("textbox").first().fill("#123456");
    await fabric.getByRole("button", { name: /^add$/i }).click();

    // The added colour becomes a selectable swatch of its own, in the same flat
    // palette, in every colour question — not only the one that added it.
    await expect(fabric.getByRole("radio", { name: "#123456" })).toHaveCount(1);
    await expect(
      page.getByRole("group", { name: "The dupatta" }).getByRole("radio", { name: "#123456" }),
    ).toHaveCount(1);
  });
});

test.describe("journey 5: the reference step", () => {
  // Rewritten for Phase 22. It used to select a curated catalogue asset and
  // skipped on any stack that had none — which, since no asset was ever
  // approved anywhere, was every stack. ADR 0025 retired the catalogue, so the
  // step is now the customer's own photographs and this journey runs for real
  // rather than skipping.
  //
  // Narrowed again after Phase 22 (ADR 0026's amendment): the iPad's camera and
  // file picker are gone, so the phone is the only way a reference arrives. The
  // upload and removal that used to be driven through this device's picker are
  // driven through the phone's page below, from a separate browser context.

  test("offers no catalogue, and no way in from this device", async ({ page }) => {
    await page.goto("/design/new");
    await advanceUntilQuestion(page, /inspiration images/i);

    // The catalogue is gone from the product, asserted at the screen rather
    // than by the absence of a request: what matters is that nobody is offered
    // a grid of somebody else's approved looks.
    //
    // Asserted on AFFORDANCES, not on the word "catalogue" appearing anywhere.
    // A blanket text ban failed against the privacy line "never added to
    // Sitara's catalogue" — which is a guarantee ADR 0025 wants said out loud,
    // so a test that pressured someone into deleting it would be the wrong
    // test. What must be absent is a way IN, not a mention.
    await expect(page.getByRole("button", { name: /not selected/i })).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: /browse|catalogue|inspiration library/i }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: /browse|catalogue|inspiration library/i }),
    ).toHaveCount(0);
    // And nothing on the screen is being served by the retired endpoints.
    await expect(page.locator('img[src*="inspiration-assets"]')).toHaveCount(0);

    // No file input at all — neither the picker nor the camera. Asserted as an
    // absence of the CONTROL rather than of two particular labels, because a
    // renamed one would slip past that.
    await expect(page.locator('input[type="file"]')).toHaveCount(0);

    // And no rights affirmation on this device. Not a weakening: this checkbox
    // only ever gated the two controls above, and an affirmation ticked by
    // whoever holds the shop's screen was never allowed to satisfy the phone's
    // (ADR 0026). The one that does the work is taken on the phone, per upload,
    // from the person who chose the photograph — asserted in the handoff test
    // below, where it is enforced.
    await expect(page.getByRole("checkbox")).toHaveCount(0);

    // The code is simply there. Nothing to press first, because the answer to
    // "shall I show the code" on a step whose only way in IS the code was never
    // going to be no.
    await expect(
      page.getByRole("img", { name: /scan this to send a photograph/i }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("hands off to a phone by QR, and stops when the stylist says so", async ({ page }) => {
    await page.goto("/design/new");
    await advanceUntilQuestion(page, /inspiration images/i);

    const code = page.getByRole("img", { name: /scan this to send a photograph/i });
    await expect(code).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/this code works for about/i)).toBeVisible();

    // The typed fallback carries the same URL the QR encodes, with the secret
    // in the fragment — which is what keeps it out of a server access log.
    await page.getByText(/the camera will not scan it/i).click();
    await expect(page.getByText(/\/r#/)).toBeVisible();

    // And it really stops. Unlike a signed storage URL this resolves through
    // Sitara, so revocation is a fact rather than a hope.
    await page.getByRole("button", { name: /stop accepting photos/i }).click();
    await expect(code).toHaveCount(0, { timeout: 30_000 });

    // A stop stays stopped. The panel shows a code on arrival, but not again
    // after the stylist has deliberately killed one — the button is the way
    // back, and pressing it is a decision rather than a re-render.
    await expect(page.getByRole("button", { name: /show a new code/i })).toBeVisible();
  });

  test("a photograph sent from the phone arrives, and can be taken off", async ({
    page,
    browser,
  }) => {
    // The upload and removal this covers used to run through the iPad's own
    // picker. With that gone the phone is the only way in, so leaving the path
    // uncovered at the moment it became the sole one would be the wrong trade.
    //
    // A SEPARATE browser context, not a second tab: the phone shares no cookie,
    // no session and no storage with the shop's iPad, which is the situation
    // the grant exists for — the scanned code is the whole authorisation.
    //
    // What this still does not prove is the handoff itself: a real camera, a
    // real second device, a code read off glass. That wants the manual
    // checkpoint and cannot be faked here.
    await page.goto("/design/new");
    await advanceUntilQuestion(page, /inspiration images/i);

    await expect(
      page.getByRole("img", { name: /scan this to send a photograph/i }),
    ).toBeVisible({ timeout: 30_000 });
    await page.getByText(/the camera will not scan it/i).click();
    // textContent, not innerText: the raw DOM string cannot be reshaped by
    // whatever wrapping the panel's CSS does to a very long code.
    const handoffUrl = ((await page.getByText(/\/r#/).textContent()) ?? "").trim();
    expect(handoffUrl).toMatch(/\/r#.+/);

    const phone = await browser.newContext();
    try {
      const phonePage = await phone.newPage();
      await phonePage.goto(handoffUrl);
      await expect(phonePage.getByRole("heading", { name: /send a photograph/i })).toBeVisible();

      // The disclosure is readable, and the picker unusable, BEFORE anything is
      // chosen — in the same words as the iPad used to show, from the shared
      // component (ADR 0019/0026).
      await expect(phonePage.getByText(/perpetual, irrevocable licence/i)).toBeVisible();
      // The library input, told apart from the camera one by the absence of
      // `capture` rather than by a React useId — those contain colons, are
      // invalid in a CSS selector unescaped, and change whenever the tree does.
      const library = phonePage.locator('input[type="file"]:not([capture])').first();
      await expect(library).toBeDisabled();

      // The file is one of the project's OWN synthetic questionnaire visuals.
      // CLAUDE.md §13 permits locally generated synthetic images for clearly
      // labelled engineering tests and forbids downloaded or unlicensed ones,
      // so reusing a build output we already produced is the correct source —
      // no new asset, nothing with a real person in it.
      await phonePage.getByRole("checkbox").check();
      await expect(library).toBeEnabled();
      await library.setInputFiles(SYNTHETIC_UPLOAD);
      await expect(phonePage.getByText(/1 photograph so far/i)).toBeVisible({ timeout: 30_000 });

      // The phone is told nothing about the design's contents — no gallery, no
      // remaining count. A grant grants upload and nothing else.
      await expect(phonePage.getByRole("list", { name: /your uploaded images/i })).toHaveCount(0);
      await expect(phonePage.getByText(/\b[0-9]+ of [0-9]+ free/i)).toHaveCount(0);
    } finally {
      await phone.close();
    }

    // Back on the shop's screen, which polls: the arrival is announced, not
    // merely displayed, because the stylist may be looking at the customer.
    await expect(page.getByText(/1 photograph arrived from the phone/i)).toBeVisible({
      timeout: 30_000,
    });
    const uploads = page.getByRole("list", { name: /your uploaded images/i });
    await expect(uploads.getByRole("listitem")).toHaveCount(1);

    // Removal is the stylist's job — the phone deliberately cannot do it.
    // "Remove image 1", not a bare "Remove": each button names which image it
    // removes, so a screen-reader user hears them apart.
    await uploads.getByRole("button", { name: /^remove image 1$/i }).click();
    await expect(page.getByText(/image removed from your design/i)).toBeVisible({
      timeout: 30_000,
    });
    await expect(uploads).toHaveCount(0);
  });

  test("a phone with no code is told so rather than shown a broken page", async ({ page }) => {
    // The customer's page, reached without scanning anything.
    await page.goto("/r");

    await expect(page.getByRole("heading", { name: /nothing to send to/i })).toBeVisible();
    await expect(page.getByText(/choose from your photos/i)).toHaveCount(0);
  });
});
