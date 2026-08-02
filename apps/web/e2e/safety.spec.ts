import { expect, test } from "@playwright/test";

import { chooseOption } from "./helpers/wizard";

// Phase 17 §25's last line: "Prove demo mode constructs no provider client and
// makes no provider request."
//
// The proof has two halves and this file owns the outer one — that the stack
// under test is genuinely in demo mode, and that nothing the browser does
// reaches a paid provider. The inner half (no SDK client is ever constructed)
// is a backend concern and is covered by the Django suite, which asserts it at
// the ai_gateway boundary where a browser cannot see.
//
// This spec is its own Playwright PROJECT, which every other project declares
// as a dependency (see playwright.config.ts). That is deliberate and load-
// bearing: filename order would NOT put it first — alphabetically
// `generation.spec.ts` and `journeys.spec.ts` sort ahead of it, so a full
// generation journey would otherwise run against a possibly live-capable stack
// before anything here checked the gates were closed. As a dependency, a
// failure skips every dependent project, so the whole run is invalid rather
// than merely failing later. It is deliberately cheap for the same reason.

const PROVIDER_HOSTS = [
  "api.anthropic.com",
  "anthropic.com",
  "api.replicate.com",
  "replicate.com",
  "replicate.delivery",
  "bfl.ai",
  "blackforestlabs.ai",
];

test.describe("demo-mode safety", () => {
  test("the stack under test reports demo mode and no live generation", async ({ request }) => {
    const response = await request.get("/api/v1/config/public");
    expect(response.status()).toBe(200);
    const config = await response.json();

    // demo_mode true and generation_mode "demo" together mean the backend
    // resolved the DEMO branch, which by construction never builds a paid
    // client. generation_enabled stays false in demo mode by design — see
    // features/generation/generation-availability.ts.
    expect(config.demo_mode).toBe(true);
    expect(config.generation_mode).toBe("demo");
    expect(config.generation_enabled).toBe(false);
  });

  test("no browser request reaches a paid provider while browsing the app", async ({ page }) => {
    const offending: string[] = [];
    page.on("request", (request) => {
      const host = new URL(request.url()).hostname.toLowerCase();
      if (PROVIDER_HOSTS.some((p) => host === p || host.endsWith(`.${p}`))) {
        offending.push(`${request.method()} ${request.url()}`);
      }
    });

    // The pages that would carry a provider URL if one ever leaked into the
    // browser: the landing config probe, and the two information pages that
    // describe provider behaviour.
    //
    // Deliberately NOT a full generation journey — this stays cheap so it can
    // run first and cheaply invalidate the whole run. Provider abstinence
    // during generation itself is not a thing a browser can observe anyway: it
    // is asserted backend-side at the ai_gateway boundary, and the concept
    // image reaches the browser only through the ownership-checked signed-URL
    // endpoint, never a provider origin.
    for (const path of ["/", "/privacy", "/concepts"]) {
      await page.goto(path);
      await page.getByRole("heading", { level: 1 }).waitFor();
    }

    expect(offending, `browser requested a provider host: ${offending.join(", ")}`).toEqual([]);
  });

  test("the questionnaire stores nothing in browser storage", async ({ page }) => {
    // Not strictly a provider concern, but the same class of guarantee and the
    // cheapest place to keep proving it against the real browser rather than
    // jsdom: CLAUDE.md §9 forbids browser-stored auth or draft state.
    await page.goto("/design/new");
    await page.getByRole("heading", { level: 1 }).waitFor();
    await chooseOption(page, page.getByRole("radio").first());
    await expect(page).toHaveURL(/\/design\/[0-9a-f-]{36}/, { timeout: 30_000 });

    const stored = await page.evaluate(() => ({
      local: Object.keys(window.localStorage),
      session: Object.keys(window.sessionStorage),
    }));
    expect(stored.local).toEqual([]);
    expect(stored.session).toEqual([]);
  });
});
