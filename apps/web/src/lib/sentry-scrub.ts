// Privacy-safe Sentry event scrubbing for the Next.js app (Phase 16, Part E;
// extended in Phase 22 for the handoff fragment).
//
// Extracted so it is unit-testable independently of @sentry/nextjs. Sentry is
// disabled entirely without a DSN; when enabled, this beforeSend hook removes
// request bodies, cookies, and everything after the first `?` or `#` in a URL,
// drops user identity, and reduces any exception to its type only (the message
// may embed user input) — so no PII, no signed-image URL, no account email and
// no handoff code ever leaves the browser or server.
//
// The FRAGMENT half of that is Phase 22's addition and it is load-bearing.
// Sentry's browser SDK attaches the current page URL to every event by
// default, and since ADR 0026 the customer's phone sits on `/r#<token>` with a
// live bearer credential in the fragment. A scrub that only ran when a `?` was
// present — as this one did — would have let that whole URL through, because
// that page has no query string at all. "Never logged, not in Sentry" is one
// of the named bounds the project owner accepted the bearer design on, so this
// is the code that keeps it true. Breadcrumbs are scrubbed for the same reason:
// a navigation or fetch breadcrumb carries a URL too.

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

type SentryBreadcrumb = {
  data?: Record<string, unknown>;
  message?: unknown;
  [key: string]: unknown;
};

export type SentryEvent = {
  request?: SentryRequest;
  user?: unknown;
  exception?: { values?: SentryExceptionValue[] };
  breadcrumbs?: SentryBreadcrumb[] | { values?: SentryBreadcrumb[] };
  [key: string]: unknown;
};

const SENSITIVE_HEADERS = new Set(["cookie", "authorization", "x-csrftoken", "x-csrf-token"]);

/** Keep the path, drop everything a secret could be hiding in.
 *
 * Cuts at the FIRST `?` or `#`, whichever comes first, so a URL carrying both
 * loses both — and so a fragment is removed even when there is no query string
 * at all, which is exactly the shape `/r#<token>` has. */
export function scrubUrl(url: string): string {
  const cut = url.search(/[?#]/);
  return cut === -1 ? url : url.slice(0, cut);
}

function scrubBreadcrumb(crumb: SentryBreadcrumb): void {
  if (!crumb || typeof crumb !== "object") return;
  const data = crumb.data;
  if (data && typeof data === "object") {
    for (const key of ["url", "to", "from"]) {
      const value = data[key];
      if (typeof value === "string") data[key] = scrubUrl(value);
    }
  }
  // A navigation breadcrumb's message is often the URL itself.
  if (typeof crumb.message === "string") crumb.message = scrubUrl(crumb.message);
}

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
    if (typeof request.url === "string") {
      // Unconditionally, and cutting at `#` as well as `?`: a signed-URL
      // parameter hides in the query string, a handoff code in the fragment.
      request.url = scrubUrl(request.url);
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
  // Breadcrumbs carry URLs of their own. Both shapes are handled because the
  // SDK uses the bare array in-process and the enveloped form on the wire.
  const crumbs = event.breadcrumbs;
  if (Array.isArray(crumbs)) {
    crumbs.forEach(scrubBreadcrumb);
  } else if (crumbs && typeof crumbs === "object" && Array.isArray(crumbs.values)) {
    crumbs.values.forEach(scrubBreadcrumb);
  }
  return event;
}
