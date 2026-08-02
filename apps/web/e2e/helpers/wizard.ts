import { expect, type Locator, type Page } from "@playwright/test";

// Shared driving of the one-question-per-screen questionnaire.
//
// Two things about this wizard shape every helper below, and both were
// established by driving the real application rather than by reading the code:
//
//  1. A single-choice screen AUTO-ADVANCES once an option is chosen. There is a
//     Continue button, but on those screens it is for keyboard users and for
//     screens that do not advance themselves. So "select then wait for the
//     heading to change" is the correct wait, not "select then click Continue".
//  2. Every option input is `visually-hidden` inside the label that draws the
//     card, so an unforced click is refused as landing on a hidden control.
//     Every selection therefore goes through `chooseOption` below, which forces
//     the click onto the real input — which is also what a keyboard user
//     activates — and tolerates the remount at design creation.
//
// The first answer on /design/new creates the Design and the app then replaces
// the URL with /design/<uuid>, so callers must not assume the URL is stable
// across the first selection.

/**
 * Wait until no draft save is in flight.
 *
 * The wizard reports "Saving…" then "Saved" in a live region. Both the absence
 * of "Saving…" and a settled network are required: the indicator flips to
 * "Saved" on the response, but a debounced save queued a moment earlier may not
 * have been sent yet.
 */
export async function waitForDraftSaved(page: Page): Promise<void> {
  await expect
    .poll(
      async () => {
        const statuses = await page.locator('[role="status"]').allInnerTexts();
        return statuses.some((text) => /saving/i.test(text));
      },
      { message: "draft save never settled", timeout: 20_000 },
    )
    .toBe(false);
  await page.waitForLoadState("networkidle");
}

/**
 * Wait until the Design row stops being written to.
 *
 * Measured on the real stack: after arriving at the review screen the design's
 * `updated_at` can still move — observed 156ms AFTER a generation attempt was
 * created, which is enough for the pipeline to reject the job as
 * `design_changed` at its stage-A freshness check. Polling the server's own
 * view of the row is the only reliable quiescence signal; `networkidle` is not
 * enough, because the write that matters may be issued right as the generation
 * POST goes out.
 *
 * This is test hygiene, not a workaround for a test-only artefact — the same
 * race is reachable by a fast human, and it is recorded as a Phase 17 finding.
 */
export async function waitForDesignQuiescent(page: Page, designId: string): Promise<void> {
  let previous = "";
  let stableFor = 0;
  await expect
    .poll(
      async () => {
        const updatedAt = await page.evaluate(async (id) => {
          const response = await fetch(`/api/v1/designs/${id}/`, { credentials: "same-origin" });
          if (!response.ok) return "";
          return (await response.json()).updated_at ?? "";
        }, designId);
        stableFor = updatedAt && updatedAt === previous ? stableFor + 1 : 0;
        previous = updatedAt;
        return stableFor;
      },
      { message: "the design never stopped being written to", timeout: 30_000, intervals: [500] },
    )
    .toBeGreaterThanOrEqual(3);
}

/**
 * Choose one option control, tolerating the remount at design creation.
 *
 * `check()` clicks and then VERIFIES the control ended up checked, throwing
 * "Clicking the checkbox did not change its state" if not. On the first answer
 * that verification is racy through no fault of the application: that answer
 * creates the Design, the app replaces the URL with /design/<uuid>, and the
 * wizard remounts — so the control Playwright just clicked can be replaced by a
 * fresh unchecked one before the check is read back. Observed once in about
 * thirty full-suite runs.
 *
 * So: click, then accept EITHER outcome that means the click was taken — the
 * control is checked, or the wizard has already moved on — and retry a bounded
 * number of times otherwise. Never swallows a genuine failure: if neither
 * becomes true, the last attempt throws exactly as `check()` would.
 */
export async function chooseOption(page: Page, control: Locator): Promise<void> {
  const before = await currentQuestion(page);
  for (let attempt = 0; attempt < 3; attempt += 1) {
    // Re-read before every click after the first. The retry exists for a
    // control that never took the click — not one that took it late. Clicking a
    // CHECKBOX a second time would toggle it back off, turning a slow render
    // into a wrong answer, and `stepForward` does drive checkboxes.
    if (attempt > 0 && (await control.isChecked().catch(() => false))) return;
    await control.click({ force: true });
    for (let poll = 0; poll < 20; poll += 1) {
      if (await control.isChecked().catch(() => false)) return;
      if ((await currentQuestion(page).catch(() => before)) !== before) return;
      await page.waitForTimeout(100);
    }
  }
  // Bounded retries exhausted: fail the way check() would, with its message.
  await control.check({ force: true, timeout: 5_000 });
}

/** The heading text of the screen currently on show. */
export async function currentQuestion(page: Page): Promise<string> {
  return (await page.locator("h1").first().innerText()).trim();
}

/**
 * Choose one option on a single-choice screen and wait for the wizard to move.
 * `option` is the visible label ("Nikah"), not the machine value.
 */
export async function answerAndAdvance(page: Page, option: string): Promise<void> {
  const before = await currentQuestion(page);
  await chooseOption(page, page.getByRole("radio", { name: option, exact: true }).first());

  // Most single-choice screens advance themselves. The screen immediately
  // after the first answer does not reliably do so: that answer creates the
  // Design and the app replaces the URL with /design/<uuid>, remounting the
  // wizard, and a selection made across that remount does not carry its own
  // advance. Continue is the same forward step a user takes there, so fall
  // back to it rather than failing.
  const advanced = await page
    .waitForFunction((t) => document.querySelector("h1")?.textContent?.trim() !== t, before, {
      timeout: 5_000,
    })
    .then(() => true)
    .catch(() => false);
  if (advanced) return;

  await continueScreen(page);
}

/** Skip an optional screen and wait for the wizard to move. */
export async function skipScreen(page: Page): Promise<void> {
  const before = await currentQuestion(page);
  await page.getByRole("button", { name: /^skip$/i }).click();
  await expect.poll(async () => currentQuestion(page), { timeout: 20_000 }).not.toBe(before);
}

/** Press Continue and wait for the wizard to move. */
export async function continueScreen(page: Page): Promise<void> {
  const before = await currentQuestion(page);
  const button = page.getByRole("button", { name: /^continue$/i });
  await expect(button).toBeEnabled({ timeout: 20_000 });
  await button.click();
  await expect.poll(async () => currentQuestion(page), { timeout: 20_000 }).not.toBe(before);
}

/**
 * The three-part colours screen: one colour for each of the fabric, the
 * embroidery and the dupatta. Each role is its own named group holding one flat
 * palette, and the same colour appears in all three — so the role has to be
 * named explicitly or a swatch matches three times over.
 */
export async function answerColours(
  page: Page,
  choices: { fabric: string; embroidery: string; dupatta: string },
): Promise<void> {
  for (const [role, colour] of [
    ["The fabric", choices.fabric],
    ["The embroidery", choices.embroidery],
    ["The dupatta", choices.dupatta],
  ] as const) {
    await chooseOption(
      page,
      page
        .getByRole("group", { name: role })
        .getByRole("radio", { name: colour, exact: true })
        .first(),
    );
  }
}

/**
 * Take one forward step on whatever screen is showing, answering it in the
 * least interesting way that is still valid. Returns false when there is no
 * forward step left (the inspiration screen, whose control is "Review").
 *
 * Shared by `completeQuestionnaire`, `advanceUntilQuestion` and the keyboard
 * journeys so they cannot drift in how they treat a screen shape.
 */
export async function stepForward(page: Page): Promise<boolean> {
  const before = await currentQuestion(page);
  const roleGroups = page.getByRole("group", { name: /^The (fabric|embroidery|dupatta)$/ });

  if ((await roleGroups.count()) === 3) {
    await answerColours(page, { fabric: "Scarlet", embroidery: "Antique gold", dupatta: "Scarlet" });
    await continueScreen(page);
    return true;
  }

  const radios = page.getByRole("radio");
  if ((await radios.count()) > 0) {
    await chooseOption(page, radios.first());
    const advanced = await page
      .waitForFunction((t) => document.querySelector("h1")?.textContent?.trim() !== t, before, {
        timeout: 4_000,
      })
      .then(() => true)
      .catch(() => false);
    if (!advanced) await continueScreen(page);
    return true;
  }

  const checkboxes = page.getByRole("checkbox");
  if ((await checkboxes.count()) > 0) {
    await chooseOption(page, checkboxes.first());
    await continueScreen(page);
    return true;
  }

  const skip = page.getByRole("button", { name: /^skip$/i });
  if (await skip.count()) {
    await skipScreen(page);
    return true;
  }

  return false;
}

/**
 * Walk forward until the question heading matches, answering each screen on
 * the way. The wizard refuses to jump past unanswered required questions, so a
 * `?q=` deep link alone cannot reach a screen the user has not got to yet —
 * this is the only way in.
 */
export async function advanceUntilQuestion(page: Page, heading: RegExp): Promise<void> {
  for (let screen = 0; screen < 30; screen += 1) {
    if (heading.test(await currentQuestion(page))) return;
    if (!(await stepForward(page))) break;
  }
  expect(await currentQuestion(page), `never reached a question matching ${heading}`).toMatch(
    heading,
  );
}

/**
 * Walk from a fresh /design/new to the review screen, answering every required
 * question and skipping every optional one.
 *
 * Deliberately data-driven rather than hard-coded to a screen count: the
 * questionnaire is a versioned schema and a v5 that inserts a question should
 * make this helper adapt, not silently answer the wrong screen. It answers
 * whatever is in front of it, which is exactly what a bride does.
 */
export async function completeQuestionnaire(page: Page): Promise<string> {
  await page.goto("/design/new");
  await page.getByRole("heading", { level: 1 }).waitFor();

  // A hard bound so a wizard that stops advancing fails the test rather than
  // hanging until the suite timeout.
  for (let screen = 0; screen < 30; screen += 1) {
    if (page.url().includes("/review")) break;

    // The final screen is Inspiration, whose forward control is "Review", not
    // "Continue". Choosing no inspiration is a valid answer, so this walks
    // straight past it — journey 5 covers selecting and uploading separately.
    const review = page.getByRole("button", { name: /^review$/i });
    if (await review.count()) {
      // The draft saves on a debounce. Leaving while one is still in flight
      // lets it land AFTER the generation job has been enqueued, and the
      // pipeline then rejects the job with design_changed — a real race, not a
      // flake, and the reason this wait is here rather than a fixed sleep.
      await waitForDraftSaved(page);
      await review.click();
      break;
    }

    // Every screen shape is handled in ONE place, `stepForward`. This used to
    // repeat that whole colours/radios/checkboxes/skip ladder inline, which
    // meant a questionnaire v5 adding a control type had to be taught to two
    // functions — and would have half-worked if it were taught to only one.
    if (await stepForward(page)) continue;

    // No answerable control and no Skip. The inspiration screen is already
    // handled above by its Review button, so what is left is a screen whose
    // only forward move is Continue.
    await continueScreen(page);
  }

  await expect(page).toHaveURL(/\/review$/, { timeout: 30_000 });
  const designId = page.url().split("/design/")[1].split("/")[0];
  return designId;
}
