import type { NextConfig } from "next";

// Same-origin API proxy: the browser only ever calls relative /api/* URLs
// on the Next.js origin; this rewrite forwards them to Django server-side.
// API_INTERNAL_BASE_URL is SERVER-ONLY (never NEXT_PUBLIC_*), so no backend
// host is exposed to the browser. Cookies and Set-Cookie headers pass
// through rewrites unchanged.
const apiInternalBaseUrl =
  process.env.API_INTERNAL_BASE_URL ?? "http://localhost:8000";

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
};

export default nextConfig;
