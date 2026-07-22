// Server-side Sentry init (Phase 16, Part E). Disabled without SENTRY_DSN, so
// local dev, tests and CI construct no client and make no network call. No PII,
// no request bodies (scrubbed), tracing off. Loaded by instrumentation.ts.
import * as Sentry from "@sentry/nextjs";

import { scrubSentryEvent, type SentryEvent } from "./src/lib/sentry-scrub";

const dsn = process.env.SENTRY_DSN;

Sentry.init({
  dsn,
  enabled: Boolean(dsn),
  sendDefaultPii: false,
  tracesSampleRate: 0,
  environment: process.env.SENTRY_ENVIRONMENT,
  beforeSend(event) {
    return scrubSentryEvent(event as unknown as SentryEvent) as unknown as typeof event;
  },
});
