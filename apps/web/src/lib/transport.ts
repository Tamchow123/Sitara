// Single same-origin request transport shared by BOTH the hand-written
// CSRF-aware client (lib/api.ts) and the generated typed client
// (api/client.ts), so they never drift into competing request policies.
//
// Every request is same-origin, never cached, carries an Accept: application/
// json default, and is aborted after a hard timeout so the UI can never hang.
// This transport stores NOTHING — no cookies (the session is an HttpOnly
// cookie the JS cannot read), no CSRF token (held in memory by lib/api.ts).

export const REQUEST_TIMEOUT_MS = 5000;

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  // Abort half-open connections so the UI can never hang forever; timeouts,
  // network errors and malformed JSON all surface as thrown errors.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(input, {
      credentials: "same-origin",
      cache: "no-store",
      ...init,
      headers: { Accept: "application/json", ...(init.headers ?? {}) },
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}
