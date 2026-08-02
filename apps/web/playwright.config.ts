import { defineConfig, devices } from "@playwright/test";

// Phase 17 §25/§26. These specs drive the REAL application against a running
// stack, always in demo mode — the whole point is that a journey which never
// touches a paid provider still produces a concept end to end.
//
// The stack is not started from here. `webServer` would only manage the Next.js
// process, and these journeys need Postgres, Redis, MinIO, the Django API and
// the Celery worker as well. Bringing those up is `docker compose up -d`, which
// the CI job and the local instructions in e2e/README.md both do explicitly, so
// that what CI runs and what a developer runs are the same thing.
//
// SAFETY: every run requires DEMO_MODE=true, ALLOW_PAID_AI_CALLS=false and
// LIVE_GENERATION_ENABLED=false. e2e/safety.spec.ts asserts the running stack
// actually reports demo mode before any other spec is allowed to matter, and
// the local .env on a developer machine may well have live generation on — so
// the documented command overrides those three variables explicitly rather
// than trusting whatever .env happens to hold.

// `localhost`, deliberately not `127.0.0.1`: Django's CSRF_TRUSTED_ORIGINS
// lists the localhost form, so driving the same port by IP makes every unsafe
// request fail with a 403 csrf_failed that looks like an application bug and
// is not one.
const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3001";

export default defineConfig({
  testDir: "./e2e",
  // A journey that walks sixteen questionnaire screens is not fast; the cap is
  // generous enough for a cold container but still bounded.
  timeout: 90_000,
  expect: {
    timeout: 15_000,
    toHaveScreenshot: {
      // Anti-aliasing of text differs slightly between machines even at the
      // same viewport. This tolerates that without tolerating a real layout
      // change, which moves far more than a few hundred pixels.
      maxDiffPixelRatio: 0.02,
      // Screenshots are captured with animations disabled, so a caret or a
      // shimmer cannot land mid-frame.
      animations: "disabled",
      caret: "hide",
      scale: "css",
    },
  },
  // A flaky-passing suite is worse than none: never retry locally, and allow
  // CI a single retry only so an infrastructure blip is distinguishable from a
  // real failure in the report.
  retries: process.env.CI ? 1 : 0,
  // Always one worker, locally as well as in CI. These journeys drive ONE
  // shared backend with ONE Celery worker, so two of them in parallel contend
  // for the same generation queue and the same per-design advisory lock — the
  // desktop and mobile projects failed together and passed individually until
  // this was pinned. Parallelism here buys a little wall-clock and costs
  // reproducibility.
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: BASE_URL,
    // Determinism: the app honours prefers-reduced-motion, so asking for it
    // removes every transition the visual baselines would otherwise catch
    // mid-flight. It also exercises the reduced-motion path §22 requires.
    //
    // It MUST go under contextOptions. Playwright has no top-level `use`
    // option of this name and silently ignores unknown keys, so writing it
    // directly on `use` type-errors but still runs — with reduced motion never
    // applied and nothing at runtime to say so. `npm run typecheck` is what
    // caught that; it is why this comment exists rather than a tidier line.
    contextOptions: { reducedMotion: "reduce" },
    // A fixed locale and timezone keep any rendered date or number stable.
    locale: "en-GB",
    timezoneId: "UTC",
    // Traces record the full network log, which includes the short-TTL SIGNED
    // design-image URL and the session cookie. CLAUDE.md §14 says a signed URL
    // is never persisted or logged anywhere — and a CI artifact uploaded to
    // GitHub with a retention window outlives the URL's own TTL and sits
    // outside the application's audit boundary entirely. So: traces locally,
    // where they stay on the developer's own disk and are the fastest way to
    // debug a flake, and never in CI. CI keeps failure screenshots and the
    // `docker compose logs` step, neither of which carries a URL or a cookie.
    trace: process.env.CI ? "off" : "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      // The safety gate is its own project that every other project DEPENDS on,
      // so it is structurally first rather than first-by-filename. That
      // distinction is not academic: alphabetically `generation.spec.ts` and
      // `journeys.spec.ts` sort BEFORE `safety.spec.ts`, so relying on file
      // order meant a full generation journey could run against a live-capable
      // stack before anything checked the gates were closed. Playwright skips
      // dependent projects when a dependency fails, which is exactly the
      // "treat the whole run as invalid" behaviour this gate is for.
      //
      // It runs once, not per viewport: demo mode is not a function of screen
      // size.
      name: "safety",
      testMatch: /safety\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
    },
    {
      name: "desktop",
      testIgnore: /safety\.spec\.ts/,
      dependencies: ["safety"],
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
    },
    {
      name: "mobile",
      testIgnore: /safety\.spec\.ts/,
      dependencies: ["safety"],
      // The 390x844 viewport §28 names, driven as a real touch device so the
      // mobile-only progress bar and single-column layouts are the ones under
      // test rather than a narrow desktop window.
      use: { ...devices["Pixel 5"], viewport: { width: 390, height: 844 } },
    },
  ],
});
