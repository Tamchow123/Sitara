// Next.js server instrumentation hook (Phase 16, Part E). Loads the server-side
// Sentry config on the Node.js runtime only. Sentry is disabled without a DSN,
// so this is inert in local dev, tests and CI.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
}
