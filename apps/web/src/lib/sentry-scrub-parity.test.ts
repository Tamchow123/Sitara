// Guards the one rule the browser scrubber and the server scrubber must agree
// on: what gets cut out of a URL before it can reach Sentry.
//
// CLAUDE.md §13 states it once, unconditionally — "the Sentry scrubber
// therefore cuts at the first `?` **or** `#`, breadcrumbs included" — but there
// are two scrubbers, hand-mirrored in two languages with no shared source. The
// claim is therefore only ever as true as the weaker one, and that is not a
// hypothetical: the phase review found exactly this, with the frontend cutting
// at both markers and the backend still cutting only at `?`, which meant
// `/r#<handoff-secret>` (ADR 0026 — a URL with no query string at all) went
// through the server's scrubber untouched.
//
// Following the precedent of render-parity.test.ts and design-tokens.test.ts,
// this reads both real sources and compares them, and deliberately THROWS
// rather than skipping when a source cannot be found: a silently-passing parity
// test is worse than no parity test. Like those two it reads outside apps/web
// and only works where the whole repository is checked out — the host and CI,
// not the narrower tree the web container mounts (CLAUDE.md §20).

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { BREADCRUMB_URL_FIELDS, URL_CUT_MARKERS, scrubUrl } from "./sentry-scrub";

/** Walk up to the repository root to find the server-side scrubber. */
function serverScrubber(): string {
  const relative = path.join("apps", "api", "config", "sentry.py");
  let dir = __dirname;
  for (;;) {
    const candidate = path.join(dir, relative);
    if (existsSync(candidate)) return readFileSync(candidate, "utf8");
    const parent = path.dirname(dir);
    if (parent === dir) {
      throw new Error(
        `could not find ${relative} in an ancestor of ${__dirname} — it holds the ` +
          "server-side scrubbing rule this test exists to compare against, and " +
          "passing without it would assert nothing",
      );
    }
    dir = parent;
  }
}

/** `URL_CUT_MARKERS = "?#"` -> the set of characters the server cuts at. */
function serverCutMarkers(): Set<string> {
  const match = serverScrubber().match(/^URL_CUT_MARKERS\s*=\s*"([^"]*)"/m);
  if (!match) throw new Error("URL_CUT_MARKERS not found in apps/api/config/sentry.py");
  if (!match[1].length) throw new Error("URL_CUT_MARKERS parsed but was empty");
  return new Set(match[1].split(""));
}

/** `BREADCRUMB_URL_FIELDS = ("url", "to", "from")` -> the field names. */
function serverBreadcrumbFields(): string[] {
  const block = serverScrubber().match(/^BREADCRUMB_URL_FIELDS\s*=\s*\(([^)]*)\)/m);
  if (!block) throw new Error("BREADCRUMB_URL_FIELDS not found in apps/api/config/sentry.py");
  const names = [...block[1].matchAll(/"(\w+)"/g)].map(([, name]) => name);
  if (!names.length) throw new Error("BREADCRUMB_URL_FIELDS parsed but held no names");
  return names;
}

/** Which characters does the BROWSER's rule actually cut at? Derived by
 * exercising scrubUrl rather than by re-reading its regex, so the assertion is
 * about behaviour and not about how the regex happens to be written. */
function browserCutMarkers(): Set<string> {
  const printable = Array.from({ length: 95 }, (_, index) => String.fromCharCode(32 + index));
  return new Set(printable.filter((character) => scrubUrl(`/path${character}tail`) === "/path"));
}

describe("the two Sentry scrubbers implement one rule", () => {
  it("cuts a URL at the same characters on both sides", () => {
    expect(browserCutMarkers()).toEqual(serverCutMarkers());
  });

  it("cuts at `?` and `#` specifically, which is what CLAUDE.md §13 promises", () => {
    // Pinned literally as well as compared, so making both sides equally WRONG
    // does not make this test pass.
    expect([...serverCutMarkers()].sort()).toEqual(["#", "?"]);
    expect(URL_CUT_MARKERS.source).toBe("[?#]");
  });

  it("scrubs the same breadcrumb data fields on both sides", () => {
    expect(serverBreadcrumbFields()).toEqual([...BREADCRUMB_URL_FIELDS]);
  });

  it("cuts a breadcrumb's own message on both sides", () => {
    // A navigation crumb's message is very often just the URL, so a scrubber
    // that skipped it would leave the secret in the most human-readable field
    // Sentry shows.
    expect(serverScrubber()).toMatch(/crumb\["message"\]\s*=\s*_cut_url/);
    const crumb: { message?: string } = { message: "https://shop.example/r#GRANT-SECRET" };
    if (typeof crumb.message === "string") crumb.message = scrubUrl(crumb.message);
    expect(crumb.message).toBe("https://shop.example/r");
  });
});
