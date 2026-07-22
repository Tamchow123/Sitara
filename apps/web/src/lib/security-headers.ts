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

export function buildContentSecurityPolicy({ isProd, imageOrigins }: SecurityHeaderInput): string {
  const trimmedImageOrigins = (imageOrigins ?? "").trim();
  const scriptSrc = isProd
    ? "'self' 'unsafe-inline'"
    : "'self' 'unsafe-inline' 'unsafe-eval'";
  // Same-origin API in production; dev also needs ws: (HMR) and the API host.
  const connectSrc = isProd ? "'self'" : "'self' ws: wss:";
  const imgSrc = ["'self'", "data:", "blob:"];
  if (trimmedImageOrigins) imgSrc.push(trimmedImageOrigins);

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
