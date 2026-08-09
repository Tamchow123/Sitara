import { afterEach, describe, expect, it } from "vitest";

import { undoModifierLabel } from "./platform";

/**
 * The userAgent is a getter on the prototype, so it is replaced per-test and put
 * back afterwards rather than mutated in place.
 */
function withUserAgent(userAgent: string | null) {
  if (userAgent === null) {
    Object.defineProperty(globalThis, "navigator", {
      value: undefined,
      configurable: true,
    });
    return;
  }
  Object.defineProperty(window.navigator, "userAgent", {
    value: userAgent,
    configurable: true,
  });
}

const realNavigator = globalThis.navigator;
const realUserAgent = globalThis.navigator?.userAgent;

afterEach(() => {
  Object.defineProperty(globalThis, "navigator", {
    value: realNavigator,
    configurable: true,
  });
  if (realUserAgent !== undefined) {
    Object.defineProperty(window.navigator, "userAgent", {
      value: realUserAgent,
      configurable: true,
    });
  }
});

describe("undoModifierLabel", () => {
  // Both branches are exercised here because the component tests cannot: jsdom
  // reports a win32 userAgent, so an integration test only ever sees "Ctrl", and
  // asserting the Mac branch there would mean asserting whatever jsdom happens to
  // claim rather than the mapping this function performs.
  it("names the Apple modifier on Apple platforms", () => {
    for (const userAgent of [
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
      "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)",
    ]) {
      withUserAgent(userAgent);
      expect(undoModifierLabel()).toBe("⌘");
    }
  });

  it("names Ctrl everywhere else", () => {
    for (const userAgent of [
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Mozilla/5.0 (X11; Linux x86_64)",
      "Mozilla/5.0 (win32) AppleWebKit/537.36 jsdom/25.0.1",
    ]) {
      withUserAgent(userAgent);
      expect(undoModifierLabel()).toBe("Ctrl");
    }
  });

  it("falls back to Ctrl where there is no navigator at all", () => {
    // Server rendering. Returning undefined here would put the string "undefined"
    // into a tooltip, and throwing would take the whole render down.
    withUserAgent(null);
    expect(undoModifierLabel()).toBe("Ctrl");
  });
});
