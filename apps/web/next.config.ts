import type { NextConfig } from "next";

import { buildSecurityHeaders } from "./src/lib/security-headers";

// Same-origin API proxy: the browser only ever calls relative /api/* URLs
// on the Next.js origin; this rewrite forwards them to Django server-side.
// API_INTERNAL_BASE_URL is SERVER-ONLY (never NEXT_PUBLIC_*), so no backend
// host is exposed to the browser. Cookies and Set-Cookie headers pass
// through rewrites unchanged.
const apiInternalBaseUrl =
  process.env.API_INTERNAL_BASE_URL ?? "http://localhost:8000";

const isProd = process.env.NODE_ENV === "production";
// Server-configured allowlist of origins that may serve signed images (the
// S3/MinIO signing endpoint the browser fetches <img> from). Never a wildcard;
// dev defaults to the local MinIO origin, production must be set explicitly.
const imageOrigins =
  process.env.CSP_IMAGE_ORIGINS ?? (isProd ? "" : "http://localhost:9000 http://127.0.0.1:9000");

const securityHeaders = buildSecurityHeaders({ isProd, imageOrigins });

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Django API paths end in "/"; without this Next would 308-redirect
  // /api/v1/auth/csrf/ -> /api/v1/auth/csrf BEFORE the rewrite runs and
  // break every auth endpoint. App-page navigation is unaffected in
  // practice (all internal links omit trailing slashes).
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiInternalBaseUrl}/api/:path*`,
      },
    ];
  },
  async headers() {
    // Apply the app security headers to every route EXCEPT the /api proxy,
    // whose responses already carry Django's own (stricter) CSP.
    return [
      {
        source: "/((?!api/).*)",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
