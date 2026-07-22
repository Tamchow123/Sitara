// Client-side (browser) Sentry init (Phase 16, Part E). Disabled without
// NEXT_PUBLIC_SENTRY_DSN, so no client is constructed and no network call is
// made in local dev, tests or CI. No session replay, no user identity, tracing
// off, and every event is scrubbed of bodies/cookies/signed-URL query strings.
import * as Sentry from "@sentry/nextjs";

import { scrubSentryEvent, type SentryEvent } from "./src/lib/sentry-scrub";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

Sentry.init({
  dsn,
  enabled: Boolean(dsn),
  sendDefaultPii: false,
  tracesSampleRate: 0,
  replaysSessionSampleRate: 0,
  replaysOnErrorSampleRate: 0,
  beforeSend(event) {
    return scrubSentryEvent(event as unknown as SentryEvent) as unknown as typeof event;
  },
});
