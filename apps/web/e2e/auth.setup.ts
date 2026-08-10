import { test as setup } from "@playwright/test";

import { STYLIST_STATE_PATH, registerAccount, writeStylistEmail } from "./helpers/account";

// ONE account for the whole run, registered once and reused by every spec that
// generates.
//
// This is a rate-limit constraint before it is a tidiness one. Registration is
// throttled at `AUTH_REGISTER_IP_LIMIT` (5) per IP per hour, and the whole suite
// arrives from a single IP. Phase 21 made every generating journey need an
// account (ADR 0023), which is five or more registrations across the desktop and
// mobile projects — comfortably over the ceiling, and the failure is a real
// `429`-backed refusal rendered as "Too many attempts", not a flake. Lowering the
// limit for the e2e stack would be weakening a security control to make a test
// pass; registering once is simply what a stylist does.
//
// It also happens to model the product better: one account with several concepts
// in it is the shape the account gallery exists to show.
//
// The session lands in a storage-state file — the session COOKIE, which is
// exactly what a browser holds. No password is written anywhere, and the file
// lives under a gitignored directory.

setup("register the one account every generating journey shares", async ({ page }) => {
  const email = await registerAccount(page, "stylist");
  writeStylistEmail(email);
  await page.context().storageState({ path: STYLIST_STATE_PATH });
});
