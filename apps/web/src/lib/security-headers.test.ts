import { describe, expect, it } from "vitest";

import { buildContentSecurityPolicy, buildSecurityHeaders } from "./security-headers";

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
