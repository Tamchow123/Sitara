// Privacy-safe Sentry event scrubbing for the Next.js app (Phase 16, Part E).
//
// Extracted so it is unit-testable independently of @sentry/nextjs. Sentry is
// disabled entirely without a DSN; when enabled, this beforeSend hook removes
// request bodies, cookies, and any query string (which may carry a signed-URL
// parameter), drops user identity, and reduces any exception to its type only
// (the message may embed user input) — so no PII, no signed-image URL and no
// account email ever leaves the browser or server.

type SentryRequest = {
  data?: unknown;
  cookies?: unknown;
  query_string?: unknown;
  headers?: Record<string, unknown>;
  url?: unknown;
};

type SentryExceptionValue = {
  value?: unknown;
  [key: string]: unknown;
};

export type SentryEvent = {
  request?: SentryRequest;
  user?: unknown;
  exception?: { values?: SentryExceptionValue[] };
  [key: string]: unknown;
};

const SENSITIVE_HEADERS = new Set(["cookie", "authorization", "x-csrftoken", "x-csrf-token"]);

export function scrubSentryEvent(event: SentryEvent): SentryEvent {
  const request = event.request;
  if (request && typeof request === "object") {
    delete request.data;
    delete request.cookies;
    delete request.query_string;
    if (request.headers && typeof request.headers === "object") {
      for (const name of Object.keys(request.headers)) {
        if (SENSITIVE_HEADERS.has(name.toLowerCase())) delete request.headers[name];
      }
    }
    if (typeof request.url === "string" && request.url.includes("?")) {
      // Strip a query string that could carry a signed-URL parameter.
      request.url = request.url.split("?")[0];
    }
  }
  // Never send user identity (email/username/ip).
  delete event.user;
  // Reduce every exception to its type only: the message may embed user input.
  const values = event.exception?.values;
  if (Array.isArray(values)) {
    for (const entry of values) {
      if (entry && typeof entry === "object") entry.value = "";
    }
  }
  return event;
}
