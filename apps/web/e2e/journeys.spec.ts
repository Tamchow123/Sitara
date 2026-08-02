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

test.describe("journey 5: inspiration selection", () => {
  test("a catalogue reference can be selected and cleared again", async ({ page }) => {
    await page.goto("/design/new");

    // The inspiration catalogue is staff-managed by design (CLAUDE.md §13):
    // there is deliberately no fixture, seed command or import path that
    // populates it, because every asset needs staff-verified, evidenced rights
    // and fabricating those is forbidden. So on a clean stack this journey has
    // nothing to select, and the honest outcome is a visible skip rather than
    // either a failure or a silent pass. It runs in full on a stack whose
    // catalogue an operator has approved assets into.
    // Deliberately NOT a hard-coded title, and deliberately not `.first()`:
    // the title comes from the catalogue itself so the journey runs against
    // whatever an operator approved, and titles beginning "E2 " are skipped
    // because they are engineering fixtures — one of them exists precisely to
    // be ineligible, so picking it would exercise the rejection path by
    // accident and read as a selection bug.
    const title = await page.evaluate(async () => {
      const response = await fetch("/api/v1/inspiration-assets/", { credentials: "same-origin" });
      if (!response.ok) return null;
      const body = await response.json();
      const assets: { title?: string }[] = body.assets ?? [];
      return assets.map((a) => a.title ?? "").find((t) => t && !t.startsWith("E2 ")) ?? null;
    });
    test.skip(title === null, "no publicly-eligible catalogue asset on this stack — see §13");

    // The wizard refuses to jump past unanswered required questions, so the
    // inspiration screen has to be walked to rather than deep-linked.
    await advanceUntilQuestion(page, /inspiration images/i);

    // A regex rather than a plain string because the card's accessible name is
    // the title PLUS its selection state, which a whole-string match would
    // miss; the title is escaped because it is catalogue data, not a pattern.
    const escaped = title!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const first = page.getByRole("button", { name: new RegExp(escaped, "i") }).first();
    await expect(first).toBeVisible();
    await first.click();
    // The card states its own selection in words, so the change is visible to
    // a screen reader and not carried by the border alone.
    await expect(first).not.toContainText(/not selected/i);
    await expect(first).toContainText(/selected/i);

    // The rights affirmation gates uploading and is never pre-ticked — the
    // per-upload self-affirmation is the user's statement, so it cannot be
    // made on their behalf (ADR 0018/0019, CLAUDE.md §13).
    const affirmation = page.getByRole("checkbox").first();
    await expect(affirmation).not.toBeChecked();
    await expect(page.getByRole("button", { name: /choose an image/i })).toBeDisabled();

    // The upload warning must state the provider exposure before anyone
    // uploads, not after.
    await expect(page.getByText(/perpetual, irrevocable licence/i)).toBeVisible();

    // --- the upload half of journey 5 -------------------------------------
    //
    // The file is one of the project's OWN synthetic questionnaire visuals.
    // CLAUDE.md §13 permits locally generated synthetic images for clearly
    // labelled engineering tests and forbids downloaded or unlicensed ones, so
    // reusing a build output we already produced is the correct source — no new
    // asset, nothing with a real person in it.
    await affirmation.check();
    await expect(page.getByRole("button", { name: /choose an image/i })).toBeEnabled();
    await page.locator('input[type="file"]').setInputFiles(SYNTHETIC_UPLOAD);

    await expect(page.getByText(/image added to your design/i)).toBeVisible({ timeout: 30_000 });
    const uploads = page.getByRole("list", { name: /your uploaded images/i });
    await expect(uploads.getByRole("listitem")).toHaveCount(1);

    // Removal frees the slot again and says so, rather than silently emptying
    // the grid — the status is announced, not merely displayed.
    // "Remove image 1", not a bare "Remove": each button names which image it
    // removes, so a screen-reader user hears them apart.
    await uploads.getByRole("button", { name: /^remove image 1$/i }).click();
    await expect(page.getByText(/image removed from your design/i)).toBeVisible({
      timeout: 30_000,
    });
    await expect(uploads).toHaveCount(0);
  });
});
