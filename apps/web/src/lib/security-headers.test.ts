import { describe, expect, it } from "vitest";

import {
  buildContentSecurityPolicy,
  buildSecurityHeaders,
  sanitizeImageOrigins,
} from "./security-headers";

describe("production Content-Security-Policy", () => {
  const prod = buildContentSecurityPolicy({ isProd: true, imageOrigins: "https://media.example.com" });

  it("uses no wildcard source", () => {
    expect(prod).not.toContain("*");
  });

  it("forbids unsafe-eval in production", () => {
    expect(prod).not.toContain("unsafe-eval");
  });

  it("locks down connect-src, frames, base-uri, form-action and object-src", () => {
    expect(prod).toContain("connect-src 'self'");
    expect(prod).toContain("frame-ancestors 'none'");
    expect(prod).toContain("frame-src 'none'");
    expect(prod).toContain("base-uri 'self'");
    expect(prod).toContain("form-action 'self'");
    expect(prod).toContain("object-src 'none'");
  });

  it("includes the server-configured signed-image origin in img-src, never a wildcard", () => {
    expect(prod).toContain("img-src 'self' data: blob: https://media.example.com");
    expect(prod).not.toContain("img-src *");
  });

  it("omits development-only allowances", () => {
    expect(prod).not.toContain("ws:");
    expect(prod).not.toContain("localhost");
  });
});

describe("development Content-Security-Policy", () => {
  const dev = buildContentSecurityPolicy({ isProd: false });

  it("allows unsafe-eval for React Fast Refresh", () => {
    expect(dev).toContain("unsafe-eval");
  });

  it("still never uses a wildcard source", () => {
    expect(dev).not.toContain("*");
  });
});

describe("sanitizeImageOrigins", () => {
  it("accepts exact http(s) origins and normalises them", () => {
    expect(sanitizeImageOrigins("https://a.example https://b.example:8443", false)).toEqual([
      "https://a.example",
      "https://b.example:8443",
    ]);
    // Trailing slash / default port noise normalises to the bare origin.
    expect(sanitizeImageOrigins("https://a.example/", false)).toEqual(["https://a.example"]);
  });

  it("drops wildcards, scheme-wide sources and CSP-delimiter injections", () => {
    expect(sanitizeImageOrigins("*", true)).toEqual([]);
    expect(sanitizeImageOrigins("https:", true)).toEqual([]);
    expect(sanitizeImageOrigins("https://*.example.com", true)).toEqual([]);
    // A semicolon/comma inside an entry can never smuggle a directive into the CSP.
    expect(sanitizeImageOrigins("https://ok.example;connect-src=evil", true)).toEqual([]);
    expect(sanitizeImageOrigins("https://ok.example,https://evil.com", true)).toEqual([]);
  });

  it("rejects userinfo, paths, queries and fragments", () => {
    expect(sanitizeImageOrigins("https://user:pass@a.example", true)).toEqual([]);
    expect(sanitizeImageOrigins("https://a.example/path", true)).toEqual([]);
    expect(sanitizeImageOrigins("https://a.example?q=1", true)).toEqual([]);
    expect(sanitizeImageOrigins("https://a.example#f", true)).toEqual([]);
  });

  it("requires https in production but allows http in development", () => {
    expect(sanitizeImageOrigins("http://localhost:9000", true)).toEqual([]);
    expect(sanitizeImageOrigins("http://localhost:9000", false)).toEqual(["http://localhost:9000"]);
  });

  it("keeps a valid origin even when a sibling entry is rejected", () => {
    expect(sanitizeImageOrigins("https://ok.example https://bad.example/x *", true)).toEqual([
      "https://ok.example",
    ]);
  });
});

describe("CSP never admits an injected directive via image origins", () => {
  it("keeps a valid image origin but drops a semicolon directive-injection attempt", () => {
    const csp = buildContentSecurityPolicy({
      isProd: true,
      imageOrigins: "https://ok.example ;connect-src=https://evil.example",
    });
    // Exactly one connect-src, still locked to 'self' — nothing broke out of img-src.
    expect(csp).toContain("connect-src 'self'");
    expect(csp.match(/connect-src/g)?.length).toBe(1);
    expect(csp).not.toContain("evil.example");
    expect(csp).toContain("img-src 'self' data: blob: https://ok.example");
  });
});

describe("security headers bundle", () => {
  it("includes CSP plus referrer, nosniff, frame and COOP headers", () => {
    const headers = buildSecurityHeaders({ isProd: true, imageOrigins: "" });
    const keys = headers.map((h) => h.key);
    expect(keys).toContain("Content-Security-Policy");
    expect(keys).toContain("Referrer-Policy");
    expect(keys).toContain("X-Content-Type-Options");
    expect(keys).toContain("X-Frame-Options");
    expect(keys).toContain("Cross-Origin-Opener-Policy");
    expect(headers.find((h) => h.key === "X-Frame-Options")?.value).toBe("DENY");
  });
});
