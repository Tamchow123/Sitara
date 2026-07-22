import { describe, expect, it } from "vitest";

import {
  GENERATION_SUBMIT_MESSAGES,
  GENERATION_SUBMIT_TERMINAL_CODES,
  generationSubmitErrorMessage,
} from "./submit-errors";

// The Phase 16 admission states must each have non-technical copy that never
// leaks an internal budget amount, price, provider, model or infrastructure name.
const ADMISSION_CODES = [
  "live_generation_disabled",
  "generation_limit_reached",
  "live_generation_budget_exhausted",
  "generation_unavailable",
  "queue_unavailable",
];

const FORBIDDEN = [
  "anthropic",
  "replicate",
  "claude",
  "flux",
  "redis",
  "micro",
  "usd",
  "$",
  "pricing",
  "ceiling",
  "reservation",
  "token",
];

describe("generation submit errors", () => {
  it("covers every admission code with a non-empty message", () => {
    for (const code of ADMISSION_CODES) {
      const message = GENERATION_SUBMIT_MESSAGES[code];
      expect(message, code).toBeTruthy();
      expect(message.length).toBeGreaterThan(10);
    }
  });

  it("never leaks internal budget/provider/infrastructure detail", () => {
    for (const code of ADMISSION_CODES) {
      const lower = GENERATION_SUBMIT_MESSAGES[code].toLowerCase();
      for (const term of FORBIDDEN) {
        expect(lower.includes(term), `${code} contains ${term}`).toBe(false);
      }
      // No bare digits (an internal amount/limit would surface as a number).
      expect(/\d/.test(GENERATION_SUBMIT_MESSAGES[code]), code).toBe(false);
    }
  });

  it("marks quota/budget/disabled states as terminal-for-now (no auto retry)", () => {
    expect(GENERATION_SUBMIT_TERMINAL_CODES.has("live_generation_disabled")).toBe(true);
    expect(GENERATION_SUBMIT_TERMINAL_CODES.has("generation_limit_reached")).toBe(true);
    expect(GENERATION_SUBMIT_TERMINAL_CODES.has("live_generation_budget_exhausted")).toBe(true);
    // Transient technical states are not terminal.
    expect(GENERATION_SUBMIT_TERMINAL_CODES.has("generation_unavailable")).toBe(false);
  });

  it("falls back to the server message for an unrecognised code", () => {
    expect(generationSubmitErrorMessage("something_new", "server said so")).toBe("server said so");
    expect(generationSubmitErrorMessage("live_generation_disabled", "raw")).toBe(
      GENERATION_SUBMIT_MESSAGES.live_generation_disabled,
    );
  });
});
