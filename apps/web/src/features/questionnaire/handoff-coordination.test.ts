// The handoff's cross-mount coordination, exercised directly.
//
// PhoneHandoff's own suite proves these behaviours through the component, which
// is where they matter — but that proof runs through render timing, effects and
// promise ordering all at once. This file holds the module to its own
// invariants without any of that, so a bug in the bookkeeping is distinguishable
// from a bug in the panel.
//
// The invariants are ADR 0026's, not conveniences: one mint in flight per
// design (two racing mints leave a live-looking QR the server already killed),
// a revoke fired only by the mount that holds the grant (a design-scoped revoke
// otherwise kills the code that replaced its own), and a stop that outlives the
// mount that took it.

import { beforeEach, describe, expect, it, vi } from "vitest";

const createGrant = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, createReferenceGrant: (...args: unknown[]) => createGrant(...args) };
});

import {
  claimGrant,
  clearStopped,
  holdsGrant,
  isStopped,
  markStopped,
  mintShared,
  releaseGrant,
  resetHandoffCoordination,
} from "./handoff-coordination";

function grant(token = "a-plaintext-handoff-secret-value") {
  return {
    ok: true as const,
    grant: {
      id: "grant-1",
      token,
      expires_at: new Date(Date.now() + 900_000).toISOString(),
      slots_remaining: 3,
    },
  };
}

beforeEach(() => {
  createGrant.mockReset();
  resetHandoffCoordination();
});

describe("mintShared", () => {
  it("returns the same promise while one is in flight for the design", async () => {
    let settle: (value: ReturnType<typeof grant>) => void = () => {};
    createGrant.mockReturnValue(
      new Promise<ReturnType<typeof grant>>((resolve) => {
        settle = resolve;
      }),
    );

    const first = mintShared("d1");
    const second = mintShared("d1");

    // Identity, not equality: a second promise resolving to the same value
    // would still mean a second grant was minted server-side.
    expect(second).toBe(first);
    expect(createGrant).toHaveBeenCalledTimes(1);

    // One object, compared by identity — `grant()` stamps expires_at from the
    // clock, so building it twice compares two different codes.
    const minted = grant();
    settle(minted);
    await expect(first).resolves.toBe(minted);
  });

  it("keeps designs apart", () => {
    createGrant.mockResolvedValue(grant());
    void mintShared("d1");
    void mintShared("d2");
    expect(createGrant).toHaveBeenCalledTimes(2);
    expect(createGrant).toHaveBeenNthCalledWith(1, "d1");
    expect(createGrant).toHaveBeenNthCalledWith(2, "d2");
  });

  it("mints again once the previous one has settled", async () => {
    createGrant.mockResolvedValue(grant());
    await mintShared("d1");
    await mintShared("d1");
    // Not a cache: leaving the reference step revokes, so the next visit needs
    // a genuinely new code rather than the last one handed back.
    expect(createGrant).toHaveBeenCalledTimes(2);
  });

  it("stops sharing a mint that failed, so the next attempt is a real one", async () => {
    createGrant.mockRejectedValueOnce(new Error("offline"));
    await expect(mintShared("d1")).rejects.toThrow("offline");

    const minted = grant();
    createGrant.mockResolvedValue(minted);
    await expect(mintShared("d1")).resolves.toBe(minted);
    expect(createGrant).toHaveBeenCalledTimes(2);
  });
});

describe("grant ownership", () => {
  it("is held by the last mount to claim it", () => {
    const first = Symbol("first");
    const second = Symbol("second");
    claimGrant("d1", first);
    claimGrant("d1", second);

    // The mount on screen owns the code. If ownership stuck to the mount that
    // left, nothing would revoke on leaving the step at all.
    expect(holdsGrant("d1", second)).toBe(true);
    expect(holdsGrant("d1", first)).toBe(false);
  });

  it("is not held by a mount that never claimed", () => {
    expect(holdsGrant("d1", Symbol("never"))).toBe(false);
  });

  it("ignores a release from a mount that does not hold it", () => {
    const holder = Symbol("holder");
    claimGrant("d1", holder);
    releaseGrant("d1", Symbol("stranger"));
    expect(holdsGrant("d1", holder)).toBe(true);
  });

  it("releases only its own design", () => {
    const mount = Symbol("mount");
    claimGrant("d1", mount);
    claimGrant("d2", mount);
    releaseGrant("d1", mount);
    expect(holdsGrant("d1", mount)).toBe(false);
    expect(holdsGrant("d2", mount)).toBe(true);
  });
});

describe("a stop that outlives its mount", () => {
  it("is remembered per design, not globally", () => {
    markStopped("d1");
    expect(isStopped("d1")).toBe(true);
    // Or one customer's stop would silence the next customer's screen.
    expect(isStopped("d2")).toBe(false);
  });

  it("is cleared by asking for a code again", () => {
    markStopped("d1");
    clearStopped("d1");
    expect(isStopped("d1")).toBe(false);
  });
});

describe("resetHandoffCoordination", () => {
  it("clears all three, as a fresh tab would", async () => {
    // The mint is left deliberately PENDING across the reset. A settled one
    // removes itself from the in-flight map via its own `.finally()`, so
    // awaiting it first would leave nothing for the reset to clear and the
    // assertion below would pass whether or not it cleared anything.
    let settle: (value: ReturnType<typeof grant>) => void = () => {};
    createGrant.mockReturnValue(
      new Promise<ReturnType<typeof grant>>((resolve) => {
        settle = resolve;
      }),
    );
    const mount = Symbol("mount");
    void mintShared("d1");
    claimGrant("d1", mount);
    markStopped("d1");

    resetHandoffCoordination();

    expect(holdsGrant("d1", mount)).toBe(false);
    expect(isStopped("d1")).toBe(false);
    // The in-flight map is not directly observable, so it is asserted through
    // behaviour: a mint after a reset is a fresh call rather than one joining
    // the pending mint the reset was supposed to forget.
    createGrant.mockClear();
    void mintShared("d1");
    expect(createGrant).toHaveBeenCalledTimes(1);

    settle(grant());
  });
});
