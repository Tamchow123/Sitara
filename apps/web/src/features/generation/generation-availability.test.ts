import { describe, expect, it } from "vitest";

import { generationIsOffered } from "./generation-availability";
import type { PublicConfig } from "@/lib/api";

// The combinations here are the ones the BACKEND actually produces. That
// matters: the bug this helper fixes survived because a frontend fixture used
// demo_mode: true together with generation_enabled: true, which the real
// public-config endpoint never returns — sitara/health/views.py keeps
// generation_enabled false in demo mode on purpose.

function config(overrides: Partial<PublicConfig> = {}): PublicConfig {
  return {
    demo_mode: true,
    generation_enabled: false,
    generation_mode: "demo",
    max_inspiration_images: 3,
    max_refinements: 1,
    ...overrides,
  } as PublicConfig;
}

describe("generationIsOffered", () => {
  it("offers generation in demo mode, where generation_enabled is false by design", () => {
    // The exact payload the running stack returns with DEMO_MODE=true:
    // {"demo_mode":true,"generation_enabled":false,"generation_mode":"demo"}
    expect(generationIsOffered(config())).toBe(true);
  });

  it("withholds generation when the demo pack is not ready", () => {
    expect(generationIsOffered(config({ generation_mode: "unavailable" }))).toBe(false);
  });

  it("still requires the operator's capability flag for live generation", () => {
    // Unchanged behaviour for a live deployment: the flag alone decides, so
    // this helper can never turn on paid generation that was off.
    expect(
      generationIsOffered(
        config({ demo_mode: false, generation_mode: "live", generation_enabled: true }),
      ),
    ).toBe(true);
    expect(
      generationIsOffered(
        config({ demo_mode: false, generation_mode: "live", generation_enabled: false }),
      ),
    ).toBe(false);
  });

  it("withholds generation when the mode is unavailable outside demo mode", () => {
    expect(
      generationIsOffered(
        config({ demo_mode: false, generation_mode: "unavailable", generation_enabled: false }),
      ),
    ).toBe(false);
  });

  it("fails closed when the config could not be loaded", () => {
    expect(generationIsOffered(null)).toBe(false);
    expect(generationIsOffered(undefined)).toBe(false);
  });
});
