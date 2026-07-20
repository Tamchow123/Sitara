// Shared signed-image refresh scheduling (Phase 12 Part C, reused by Phase 14's
// version comparison for the parent version's own independent image query).
// Refresh at ~80% of the observed remaining lifetime, never below a minimum
// positive delay, and never at all once there is no data or the expiry
// timestamp is unusable. Kept as one pure function plus one focus-triggered
// effect so both call sites can never silently drift apart.

import { useEffect } from "react";

import type { DesignImages } from "@/lib/api";

export const MIN_REFRESH_DELAY_MS = 1_000;
export const NEAR_EXPIRY_MS = 15_000;

export function imageRefetchIntervalMs(
  images: DesignImages | undefined,
  now: number,
): number | false {
  if (!images) return false;
  const expiresAt = Date.parse(images.expires_at);
  if (Number.isNaN(expiresAt)) return false;
  const remaining = expiresAt - now;
  if (remaining <= 0) return false;
  return Math.max(MIN_REFRESH_DELAY_MS, remaining * 0.8);
}

// Immediate refresh on window focus, but only when the current URL is
// genuinely near expiry — not on every focus, which would defeat the
// 80%-lifetime schedule above and risk a tight refresh loop.
export function useImageFocusRefresh(
  images: DesignImages | undefined,
  refetch: () => void,
): void {
  useEffect(() => {
    function onFocus() {
      if (!images) return;
      const expiresAt = Date.parse(images.expires_at);
      if (Number.isNaN(expiresAt)) return;
      if (expiresAt - Date.now() <= NEAR_EXPIRY_MS) {
        refetch();
      }
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
    // refetch is stable for a given query key; only the observed data needs
    // to retrigger this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [images]);
}
