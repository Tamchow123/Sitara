// Whether the frontend should offer to generate (or refine) a concept at all.
//
// This exists because `generation_enabled` does NOT mean "generation works".
// The backend defines it narrowly as the LIVE paid-generation capability, and
// `sitara/health/views.py` says so explicitly: it "stays False in demo mode,
// exactly as before Phase 15". Reading it directly therefore disabled the
// Generate and Refine controls for every demo user, even though the backend
// would have admitted the request — `enforce_live_admission` returns "demo"
// and never consults `LIVE_GENERATION_ENABLED` for a demo attempt.
//
// `generation_mode` is the additive three-way field Phase 15 added for exactly
// this question ("demo" | "live" | "unavailable"), so the offer is decided
// from that, with `generation_enabled` still required for the live path.
//
// The gate this replaces was never a cost control: demo generation cannot
// spend money by construction, and every real spending gate (paid-provider
// authorisation, the daily budget, the per-session and hashed-IP throttles)
// lives in the backend and is unaffected by anything here. The frontend can
// only ever offer a button; admission still decides.

import type { PublicConfig } from "@/lib/api";

export function generationIsOffered(config: PublicConfig | null | undefined): boolean {
  if (!config) return false;
  // Demo: zero-cost and locally served. "unavailable" (an unready fixture
  // pack) correctly falls through to false rather than being offered.
  if (config.generation_mode === "demo") return true;
  // Live: unchanged. The operator's capability flag still decides, so a live
  // deployment behaves exactly as it did before this helper existed.
  return config.generation_enabled === true;
}
