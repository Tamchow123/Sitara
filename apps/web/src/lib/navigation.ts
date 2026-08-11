// Open-redirect protection for the post-login `next` parameter: only
// same-origin absolute paths are honoured; anything external, scheme-ful or
// protocol-relative falls back to the account page.

export const DEFAULT_AUTHENTICATED_PATH = "/account";

export function safeNextPath(raw: string | null | undefined): string {
  if (!raw) return DEFAULT_AUTHENTICATED_PATH;
  if (!raw.startsWith("/")) return DEFAULT_AUTHENTICATED_PATH;
  if (raw.startsWith("//")) return DEFAULT_AUTHENTICATED_PATH;
  if (raw.includes("://") || raw.includes("\\")) return DEFAULT_AUTHENTICATED_PATH;
  return raw;
}

// Where to send someone who has to authenticate before finishing what they were
// doing (Phase 21: an account is required to generate a concept). The
// destination always passes through `safeNextPath` HERE, on the way out, as well
// as on the way back in — so a value that arrived from the address bar cannot be
// laundered into a link this application printed itself.
export function signInHref(next: string | null | undefined): string {
  return `/login?next=${encodeURIComponent(safeNextPath(next))}`;
}

export function registerHref(next: string | null | undefined): string {
  return `/register?next=${encodeURIComponent(safeNextPath(next))}`;
}
