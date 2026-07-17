// Generated typed API client (Phase 6).
//
// Wraps openapi-fetch with the SAME same-origin transport as lib/api.ts
// (credentials: same-origin, cache: no-store, 5s timeout — see lib/transport)
// so there is one request policy, not two. The base URL is the CURRENT page
// origin, resolved at runtime: requests never leave the same origin and no
// NEXT_PUBLIC backend host is ever baked into the bundle. The Next.js rewrite
// proxies /api/* to Django server-side.
//
// This client is for SAFE (GET) operations only for now. Registration,
// login, logout and design mutations continue through lib/api.ts's tested
// CSRF-aware flow; an unsafe typed client that silently omitted the
// X-CSRFToken header is deliberately NOT provided (see ADR 0007). The client
// stores no credentials, cookies or CSRF tokens.

import createClient from "openapi-fetch";

import type { paths } from "@/api/schema";
import { fetchWithTimeout } from "@/lib/transport";

// Same-origin: the page's own origin in the browser; empty (relative) in any
// non-browser context. Never a configured backend URL.
const sameOriginBaseUrl =
  typeof window !== "undefined" ? window.location.origin : "";

export const apiClient = createClient<paths>({
  baseUrl: sameOriginBaseUrl,
  // Route every request through the shared timeout/same-origin/no-store
  // transport. openapi-fetch hands us a Request; we add the abort signal and
  // the same-origin/no-store policy without a competing implementation.
  fetch: (request) => fetchWithTimeout(request),
});
