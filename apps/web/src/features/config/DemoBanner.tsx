"use client";

// A persistent, site-wide, non-dismissible banner shown whenever the public
// configuration reports generation_mode=="demo" (Phase 15 spec §34). Mounted
// once, high in the tree, so it is announced on load rather than repeatedly
// per navigation. Never implies a provider generated the displayed image and
// never exposes any technical configuration.

import { useQuery } from "@tanstack/react-query";

import { fetchPublicConfig } from "@/lib/api";

// Mounted app-wide (every route), so a positive staleTime avoids an extra
// public-config request on every navigation — this value is informational
// only (never security-sensitive), so briefly lagging an operator's live
// generation_mode flip is an acceptable trade-off.
const STALE_TIME_MS = 60_000;

export function DemoBanner() {
  const configQuery = useQuery({
    queryKey: ["public-config"],
    queryFn: () => fetchPublicConfig(),
    retry: false,
    staleTime: STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });

  if (configQuery.data?.generation_mode !== "demo") return null;

  return (
    <div className="demo-banner" role="status" aria-live="polite">
      <strong>Demo mode</strong> — no paid AI services are being called. Sitara creates a
      deterministic design brief locally and selects a pre-generated concept image that best
      matches your choices.
    </div>
  );
}
