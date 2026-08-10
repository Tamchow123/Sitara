import { describe, expect, it } from "vitest";

import {
  DEFAULT_AUTHENTICATED_PATH,
  registerHref,
  safeNextPath,
  signInHref,
} from "./navigation";

// Open-redirect protection for the post-login `next` parameter. Until Phase 21
// this was exercised only through the login page; it now also builds the links
// the review screen prints itself, so it is worth pinning directly.

describe("safeNextPath", () => {
  it("keeps a same-origin absolute path", () => {
    expect(safeNextPath("/design/d1/review")).toBe("/design/d1/review");
    expect(safeNextPath("/design/d1/review?q=garment_type")).toBe(
      "/design/d1/review?q=garment_type",
    );
    // A fragment is kept rather than rejected. It cannot cause a redirect — a
    // fragment never leaves the browser — so refusing it would only break a
    // legitimate deep link. Pinned so the behaviour is a decision, not a gap.
    expect(safeNextPath("/design/d1/review#garment")).toBe("/design/d1/review#garment");
  });

  it.each([
    ["nothing at all", null],
    ["an empty string", ""],
    ["a relative path", "design/d1"],
    ["a protocol-relative host", "//evil.example/phish"],
    ["an absolute URL", "https://evil.example/phish"],
    ["a scheme without a host", "javascript:alert(1)"],
    ["a backslash host", "/\\evil.example"],
  ])("falls back to the account page for %s", (_label, raw) => {
    expect(safeNextPath(raw)).toBe(DEFAULT_AUTHENTICATED_PATH);
  });
});

describe("signInHref and registerHref", () => {
  it("encode the destination so it survives as one parameter", () => {
    expect(signInHref("/design/d1/review")).toBe("/login?next=%2Fdesign%2Fd1%2Freview");
    expect(registerHref("/design/d1/review")).toBe("/register?next=%2Fdesign%2Fd1%2Freview");
  });

  it("encode a query string in the destination rather than letting it split the URL", () => {
    // Without encoding, `&` would introduce a second parameter to /login itself.
    expect(signInHref("/design/d1?a=1&b=2")).toBe("/login?next=%2Fdesign%2Fd1%3Fa%3D1%26b%3D2");
  });

  it("refuse an off-site destination on the way OUT, not only on the way back", () => {
    // These helpers build links this application prints, so a hostile value must
    // not be laundered into one of them and then trusted on the return trip.
    expect(signInHref("https://evil.example/phish")).toBe("/login?next=%2Faccount");
    expect(registerHref("//evil.example/phish")).toBe("/register?next=%2Faccount");
    expect(signInHref(null)).toBe("/login?next=%2Faccount");
  });
});
