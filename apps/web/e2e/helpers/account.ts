import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { expect, type Page } from "@playwright/test";

// The one account every generating journey shares, and how it is registered.
//
// Since Phase 21 an account is required for the last press before a concept is
// produced (ADR 0023). Registration is throttled per IP per hour, and the suite
// arrives from one IP, so it registers ONCE in `auth.setup.ts` and every spec
// that generates reuses the resulting session — see that file for the full
// reasoning. `journeys.spec.ts` deliberately does not, because journeys 1-5 exist
// to prove the questionnaire is still walkable with no account at all.
//
// The label selectors below were established by running the real form, and one is
// subtle enough to be worth stating: `/^password \(/` is anchored rather than
// exact, because the real label is "Password (at least 12 characters)" — so
// `{ exact: true }` on "Password" matched nothing, and an unanchored match also
// hits "Confirm password".

const AUTH_DIR = join(__dirname, "..", ".auth");

/** Where the shared session cookie is kept. Gitignored; never a password. */
export const STYLIST_STATE_PATH = join(AUTH_DIR, "stylist.json");

const EMAIL_PATH = join(AUTH_DIR, "stylist-email.txt");

const PASSWORD = "a-strong-test-password-123";

let counter = 0;

/** A fresh address, unique across repeat runs against the same database. */
export function uniqueTestEmail(prefix: string): string {
  counter += 1;
  return `${prefix}-${process.pid}-${Date.now()}-${counter}@example.test`;
}

/** Record the shared account's address for specs that need to assert on it. */
export function writeStylistEmail(email: string): void {
  mkdirSync(dirname(EMAIL_PATH), { recursive: true });
  writeFileSync(EMAIL_PATH, email, "utf8");
}

/**
 * The shared account's address, as written by `auth.setup.ts`.
 *
 * Throws rather than returning a placeholder if the setup project did not run:
 * an assertion against an address that is not the one in the browser would pass
 * for the wrong reason.
 */
export function stylistEmail(): string {
  return readFileSync(EMAIL_PATH, "utf8").trim();
}

/**
 * Register a brand-new account and wait until the shell shows it.
 *
 * Called once per run by `auth.setup.ts`. A spec that calls it directly is
 * spending one of five registrations available in the hour, so do not.
 */
export async function registerAccount(page: Page, prefix = "e2e"): Promise<string> {
  const email = uniqueTestEmail(prefix);
  await page.goto("/register");
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/^password \(/i).fill(PASSWORD);
  await page.getByLabel(/confirm password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /create account|register|sign up/i }).click();
  await expect(page.getByText(email)).toBeVisible({ timeout: 30_000 });
  return email;
}
