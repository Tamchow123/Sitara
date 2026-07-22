// Production security headers for the Next.js app (Phase 16, Part D).
//
// Extracted from next.config so the Content-Security-Policy is unit-testable.
// The policy is deliberately strict: no wildcard `*`, no `unsafe-eval` in
// production (dev needs it for React Fast Refresh), exact `connect-src 'self'`
// for the same-origin /api proxy in production, tight frame/base-uri/form-action
// /object-src, and an image allowlist limited to `'self' data:` plus a
// server-configured signed-storage origin list — never a wildcard. Development
// allowances (unsafe-eval, ws: for HMR, the local MinIO origin) are excluded
// from the production policy.

export type SecurityHeaderInput = {
  isProd: boolean;
  // Space-separated allowlist of origins that may serve signed images
  // (the S3/MinIO signing endpoint the browser fetches <img> from). Never `*`.
  imageOrigins?: string;
};

export type Header = { key: string; value: string };

// Parse the operator-configured image-origin allowlist into EXACT, safe origins.
// The raw string is never concatenated verbatim into the CSP: a wildcard,
// scheme-wide source, or an injected `;`/`,` could otherwise widen img-src or
// smuggle in an entire extra directive. Each whitespace-separated entry must be a
// bare http(s) origin (scheme://host[:port]) with no wildcard, userinfo, path,
// query or fragment; anything else is dropped (fail closed to a stricter policy).
// In production only https origins are accepted. The emitted value is the parsed
// `URL.origin`, so nothing beyond a normalised origin can ever reach the header.
export function sanitizeImageOrigins(raw: string, isProd: boolean): string[] {
  const origins: string[] = [];
  for (const entry of raw.split(/\s+/).filter(Boolean)) {
    if (/[*;,'"]/.test(entry)) continue; // no wildcard, CSP delimiters or quotes
    let url: URL;
    try {
      url = new URL(entry);
    } catch {
      continue;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") continue;
    if (isProd && url.protocol !== "https:") continue;
    if (url.username || url.password) continue; // no userinfo
    if ((url.pathname && url.pathname !== "/") || url.search || url.hash) continue;
    if (!origins.includes(url.origin)) origins.push(url.origin);
  }
  return origins;
}

export function buildContentSecurityPolicy({ isProd, imageOrigins }: SecurityHeaderInput): string {
  const scriptSrc = isProd
    ? "'self' 'unsafe-inline'"
    : "'self' 'unsafe-inline' 'unsafe-eval'";
  // Same-origin API in production; dev also needs ws: (HMR) and the API host.
  const connectSrc = isProd ? "'self'" : "'self' ws: wss:";
  const imgSrc = ["'self'", "data:", "blob:", ...sanitizeImageOrigins(imageOrigins ?? "", isProd)];

  return [
    "default-src 'self'",
    `script-src ${scriptSrc}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src ${imgSrc.join(" ")}`,
    `connect-src ${connectSrc}`,
    "font-src 'self'",
    "frame-ancestors 'none'",
    "frame-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ].join("; ");
}

export function buildSecurityHeaders(input: SecurityHeaderInput): Header[] {
  return [
    { key: "Content-Security-Policy", value: buildContentSecurityPolicy(input) },
    { key: "Referrer-Policy", value: "same-origin" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ];
}
