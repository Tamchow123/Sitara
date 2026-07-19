// Single same-origin request transport shared by BOTH the hand-written
// CSRF-aware client (lib/api.ts) and the generated typed client
// (api/client.ts), so they never drift into competing request policies.
//
// Every request is same-origin, never cached, carries an Accept: application/
// json default, and is aborted after a hard timeout so the UI can never hang.
// This transport stores NOTHING — no cookies (the session is an HttpOnly
// cookie the JS cannot read), no CSRF token (held in memory by lib/api.ts).

// The backend's design-image delivery endpoint bounds its synchronous
// storage phase under this budget (apps/api/sitara/media/delivery.py
// EXISTENCE_DEADLINE_SECONDS) so its controlled 503 can actually reach the
// browser — check that bound before changing this value.
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
    // Merge headers deterministically: any already present on an incoming
    // Request first, then init.headers override them, then an Accept default
    // ONLY when the caller set none. A plain object (not a Headers instance)
    // is handed to fetch, and the original casing of a caller's plain-object
    // headers is preserved (so e.g. X-CSRFToken stays inspectable by key).
    const headers: Record<string, string> = {};
    // Duck-typed, not instanceof: under the test bundler/jsdom realm the
    // Headers/Request identities can differ, so we detect a Headers-like value
    // by its forEach method and an incoming Request by a headers property.
    const apply = (source: HeadersInit | undefined): void => {
      if (!source) return;
      if (Array.isArray(source)) {
        for (const [key, value] of source) headers[key] = value;
      } else if (typeof (source as Headers).forEach === "function") {
        (source as Headers).forEach((value, key) => {
          headers[key] = value;
        });
      } else {
        for (const [key, value] of Object.entries(source)) headers[key] = value;
      }
    };
    const incoming = input as { headers?: HeadersInit };
    if (typeof input === "object" && input !== null && "headers" in incoming) {
      apply(incoming.headers);
    }
    apply(init.headers);
    if (!Object.keys(headers).some((key) => key.toLowerCase() === "accept")) {
      headers.Accept = "application/json";
    }

    return await fetch(input, {
      ...init,
      headers,
      // Non-negotiable policy: placed AFTER the init spread so a caller can
      // never downgrade same-origin credentials or re-enable caching.
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}
