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
