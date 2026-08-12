// The shop's side of the phone handoff (Phase 22, ADR 0026).
//
// What is load-bearing here is not the QR drawing. It is: the secret never
// leaves memory, the stylist can revoke and the revocation actually fires,
// leaving the step revokes too, and the panel is NOT gated behind the iPad's
// own rights affirmation — because the phone takes its own, from the person
// whose photograph it is.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { InspirationUpload as Upload } from "@/lib/api";

import { resetHandoffCoordination } from "./handoff-coordination";
import { PhoneHandoff } from "./PhoneHandoff";

const createGrant = vi.fn();
const revokeGrants = vi.fn();
const fetchReferencesMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    createReferenceGrant: (...args: unknown[]) => createGrant(...args),
    revokeReferenceGrants: (...args: unknown[]) => revokeGrants(...args),
    fetchDesignReferences: (...args: unknown[]) => fetchReferencesMock(...args),
  };
});

const TOKEN = "a-plaintext-handoff-secret-value";

function made(id: string, position = 1): Upload {
  return {
    id,
    position,
    width: 900,
    height: 1200,
    rights_acknowledged_at: "2026-08-11T00:00:00Z",
    created_at: "2026-08-11T00:00:00Z",
  };
}

function liveGrant(secondsAhead = 900) {
  return {
    ok: true as const,
    grant: {
      id: "grant-1",
      token: TOKEN,
      expires_at: new Date(Date.now() + secondsAhead * 1000).toISOString(),
      slots_remaining: 3,
    },
  };
}

function renderPanel(overrides: Partial<React.ComponentProps<typeof PhoneHandoff>> = {}) {
  const onUploadsChanged = vi.fn();
  const utils = render(
    <PhoneHandoff
      designId="design-1"
      uploads={[]}
      max={3}
      onUploadsChanged={onUploadsChanged}
      {...overrides}
    />,
  );
  return { ...utils, onUploadsChanged };
}

beforeEach(() => {
  // The handoff's cross-mount coordination outlives a component on purpose,
  // so a test that stops a code would otherwise suppress the next test's.
  resetHandoffCoordination();
  createGrant.mockReset();
  revokeGrants.mockReset();
  fetchReferencesMock.mockReset();
  revokeGrants.mockResolvedValue({ ok: true });
  createGrant.mockResolvedValue(liveGrant());
});

afterEach(() => {
  vi.useRealTimers();
});

describe("PhoneHandoff — showing a code", () => {
  it("draws a QR carrying the token in the URL fragment", async () => {
    renderPanel();
    const code = await screen.findByRole("img", {
      name: /Scan this to send a photograph/i,
    });
    expect(code.tagName.toLowerCase()).toBe("svg");
    // The fallback text is the same URL the QR encodes, so asserting against it
    // asserts against what the customer's phone will actually open.
    const url = await screen.findByText(new RegExp(`/r#${TOKEN}`));
    expect(url).toBeInTheDocument();
  });

  it("puts the secret after the '#', which browsers never send to a server", async () => {
    renderPanel();
    const url = (await screen.findByText(/\/r#/)).textContent ?? "";
    const [beforeHash, afterHash] = url.split("#");
    expect(beforeHash).not.toContain(TOKEN);
    expect(afterHash).toBe(TOKEN);
  });

  it("never writes the secret to browser storage", async () => {
    const setLocal = vi.spyOn(Storage.prototype, "setItem");
    renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });

    // Nothing at all goes to storage from this panel — asserted as "no write
    // happened" rather than "the token was not in a write", because the second
    // would pass if the token were written under an unexpected key.
    expect(setLocal).not.toHaveBeenCalled();
    setLocal.mockRestore();
  });

  it("does not put the secret in the shop's own address bar", async () => {
    const before = window.location.href;
    renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });

    expect(window.location.href).toBe(before);
    expect(window.location.href).not.toContain(TOKEN);
  });

  it("says how long the code lasts, erring early rather than late", async () => {
    // REL-004: a 15-minute server TTL reads as 14 minutes, because the
    // countdown subtracts a safety margin. Nothing enforces NTP on a shop
    // iPad, and erring early costs a re-mint while erring late sends a
    // customer to a page that refuses her — which looks like her phone's
    // fault, on the one screen the phase most needs to work.
    createGrant.mockResolvedValue(liveGrant(900));
    renderPanel();
    expect(await screen.findByText(/works for about 14 minutes/i)).toBeInTheDocument();
  });

  it("stops offering a code before the server would refuse it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // Eight seconds of server TTL is inside the ten-second margin, so the
    // panel must treat it as already gone rather than showing a live QR.
    createGrant.mockResolvedValue(liveGrant(8));
    renderPanel();
    await vi.advanceTimersByTimeAsync(1100);

    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: /Show a new code/i })).toBeTruthy(),
    );
  });

  it("reports a refusal instead of showing a dead code", async () => {
    createGrant.mockResolvedValue({
      ok: false,
      status: 409,
      code: "inspiration_limit_reached",
      message: "This design already has all three reference photographs.",
    });
    renderPanel();
    expect(await screen.findByText(/already has all three/i)).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Scan this/i })).not.toBeInTheDocument();
  });

  it("refuses to offer a code with an unreadable expiry", async () => {
    createGrant.mockResolvedValue({
      ok: true,
      grant: { id: "g", token: TOKEN, expires_at: "not a date", slots_remaining: 3 },
    });
    renderPanel();
    expect(await screen.findByText(/could not be created/i)).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(TOKEN))).not.toBeInTheDocument();
  });
});

describe("PhoneHandoff — the affirmation boundary", () => {
  it("shows a code without any affirmation on this device", async () => {
    // The whole point of ADR 0026's affirmation rule: the person holding the
    // shop's screen cannot consent on the customer's behalf. Gating the QR
    // behind a tick here would say the opposite. The code appears on its own,
    // with no checkbox in scope — and the phone takes its own affirmation
    // before its picker is usable.
    renderPanel();

    expect(
      await screen.findByRole("img", { name: /Scan this to send a photograph/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

describe("PhoneHandoff — stopping", () => {
  it("revokes when the stylist stops accepting photos", async () => {
    renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });

    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));

    await waitFor(() => expect(revokeGrants).toHaveBeenCalledWith("design-1"));
    expect(screen.queryByRole("img", { name: /Scan this/i })).not.toBeInTheDocument();
  });

  it("does not claim a stop it could not confirm", async () => {
    // REL-001 / SEC-002 regression. The stylist reads the idle screen as "that
    // code is dead" and hands the iPad to the next customer on the strength of
    // it. A revoke that failed must not look identical to one that worked.
    revokeGrants.mockResolvedValue({ ok: false });
    renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });

    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));

    expect(await screen.findByText(/could not be stopped/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Try stopping again/i }),
    ).toBeInTheDocument();
    // Specifically NOT back at the plain idle state, which since the code
    // shows itself is reachable only by a stop that actually succeeded.
    expect(screen.queryByRole("button", { name: /^Show a new code$/i })).not.toBeInTheDocument();
  });

  it("does not quietly mint a replacement once a stop succeeds", async () => {
    // TEST-001 regression, and the reason the auto-show guard is a permanent
    // ref rather than a check on `handoff.kind === "idle"`: idle is where a
    // deliberate stop lands too. Asserted on the MINT CALL, not on the DOM —
    // a re-mint that resolved and re-rendered would leave the screen looking
    // plausible either way, and it is the server-side grant that matters.
    //
    // This one pins the REF specifically. Verified by mutation: deleting the
    // ref check fails this test, and neutering the sticky-stop leaves it
    // passing — the two tests below are what pin that. Said explicitly because
    // a test that looks like it proves two guards while proving one is how a
    // guard gets deleted later without anything going red.
    //
    // If this ever fails, ADR 0026's "revocation is a fact rather than a hope"
    // is false at the exact moment it is relied on: handing the iPad to the
    // next customer.
    const { rerender } = renderPanel({ uploads: [made("a", 1)] });
    await screen.findByRole("img", { name: /Scan this/i });

    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));
    await screen.findByRole("button", { name: /Show a new code/i });
    createGrant.mockClear();

    // Then something perfectly ordinary happens: the stylist removes a
    // photograph. That changes `slotsFree` and `start`'s identity, so the
    // auto-show effect RE-RUNS — which is exactly when a guard keyed on state
    // rather than on a one-shot ref would hand back a fresh live code seconds
    // after the stylist killed the last one.
    rerender(
      <PhoneHandoff designId="design-1" uploads={[]} max={3} onUploadsChanged={vi.fn()} />,
    );
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(createGrant).not.toHaveBeenCalled();
    expect(screen.queryByRole("img", { name: /Scan this/i })).not.toBeInTheDocument();
  });

  it("does not hand back a code after a stop just because the step was re-entered", async () => {
    // The same property across a mount boundary. A stylist stops the code, the
    // customer wanders off to look at fabric, the stylist steps back a question
    // and forward again — and on arriving finds the code she killed is live
    // again, on a screen she has already handed to someone else.
    //
    // The panel must also not sit on "Creating a code…" for a mint that will
    // never run: the initial state and the auto-show effect answer the same
    // question, so they have to answer it the same way.
    const { unmount } = renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });
    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));
    await screen.findByRole("button", { name: /Show a new code/i });
    unmount();
    createGrant.mockClear();

    renderPanel();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(createGrant).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Show a new code/i })).toBeEnabled();
    expect(screen.queryByText(/Creating a code/i)).not.toBeInTheDocument();
  });

  it("shows a new code when the stylist asks for one after stopping", async () => {
    // The other half: the stop is sticky, not permanent. Asking again works,
    // and works on a fresh mount too.
    const { unmount } = renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });
    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));
    await screen.findByRole("button", { name: /Show a new code/i });
    unmount();
    createGrant.mockClear();

    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /Show a new code/i }));

    expect(await screen.findByRole("img", { name: /Scan this/i })).toBeInTheDocument();
    expect(createGrant).toHaveBeenCalledTimes(1);
  });

  it("says the same thing when the revoke request throws", async () => {
    revokeGrants.mockRejectedValue(new Error("offline"));
    renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });

    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));

    expect(await screen.findByText(/could not be stopped/i)).toBeInTheDocument();
  });

  it("tells the stylist the code may still work until it expires", async () => {
    // The actionable half: she can wait it out, and she needs to know that.
    revokeGrants.mockResolvedValue({ ok: false });
    renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });

    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));

    expect(await screen.findByText(/may still work until it/i)).toBeInTheDocument();
  });

  it("reaches idle once a retried stop succeeds", async () => {
    revokeGrants.mockResolvedValueOnce({ ok: false });
    renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });
    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));
    await screen.findByText(/could not be stopped/i);

    revokeGrants.mockResolvedValue({ ok: true });
    fireEvent.click(screen.getByRole("button", { name: /Try stopping again/i }));

    expect(await screen.findByRole("button", { name: /^Show a new code$/i })).toBeInTheDocument();
  });

  it("revokes when the step unmounts, so a photographed code does not outlive it", async () => {
    const { unmount } = renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });
    revokeGrants.mockClear();

    unmount();

    expect(revokeGrants).toHaveBeenCalledWith("design-1");
  });

  it("does not revoke on unmount when no code was ever shown", () => {
    // Nothing to revoke, so no request. Keeps a panel that was merely rendered
    // and navigated past from issuing a pointless DELETE.
    const { unmount } = renderPanel();

    unmount();

    expect(revokeGrants).not.toHaveBeenCalled();
  });

  it("stops offering a code once the design is full", async () => {
    const { rerender } = renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });

    rerender(
      <PhoneHandoff
        designId="design-1"
        uploads={[made("a", 1), made("b", 2), made("c", 3)]}
        max={3}
        onUploadsChanged={vi.fn()}
      />,
    );

    await waitFor(() => expect(revokeGrants).toHaveBeenCalledWith("design-1"));
    expect(screen.queryByRole("img", { name: /Scan this/i })).not.toBeInTheDocument();
  });

  it("will not offer a code for a design with no free slots", () => {
    renderPanel({ uploads: [made("a", 1), made("b", 2), made("c", 3)] });

    // Not merely a disabled button: nothing may be MINTED for a design that has
    // nowhere to put a photograph, or the customer scans a code that is dead on
    // arrival and blames her phone.
    expect(createGrant).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Show a new code/i })).toBeDisabled();
    expect(screen.getByText(/All 3 reference slots are used/i)).toBeInTheDocument();
  });

  it("shows a code by itself once a slot frees up on a full design", async () => {
    // The mirror of the two above, and the one case where the one-shot
    // auto-show guard is still unspent after a render: a design that was full
    // when the step opened never minted. Removing a photograph has to bring the
    // code back on its own — leaving the stylist to find a button on a screen
    // whose whole point is that the code is already there would be the worst of
    // both designs.
    const { rerender } = renderPanel({ uploads: [made("a", 1), made("b", 2), made("c", 3)] });
    expect(createGrant).not.toHaveBeenCalled();

    rerender(
      <PhoneHandoff
        designId="design-1"
        uploads={[made("a", 1), made("b", 2)]}
        max={3}
        onUploadsChanged={vi.fn()}
      />,
    );

    expect(await screen.findByRole("img", { name: /Scan this/i })).toBeInTheDocument();
    expect(createGrant).toHaveBeenCalledTimes(1);
  });

  it("offers a fresh code once the old one expires", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    createGrant.mockResolvedValue(liveGrant(2));
    renderPanel();
    await vi.waitFor(() => expect(screen.getByRole("img", { name: /Scan this/i })).toBeTruthy());

    await vi.advanceTimersByTimeAsync(3000);

    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: /Show a new code/i })).toBeTruthy(),
    );
    expect(screen.queryByRole("img", { name: /Scan this/i })).not.toBeInTheDocument();
  });
});

describe("PhoneHandoff — one action at a time", () => {
  it("joins the mint already in flight when the step is left and re-entered", async () => {
    // REL-001 regression. A stylist stepping Back and forward over this step
    // unmounts and remounts the panel, and since the code shows itself each
    // mount wants one. Two mints in flight at once are resolved by the server
    // in whichever order they reach the design's row lock, each revoking the
    // other's grant on the way — so the response that lands last in the browser
    // can describe a grant the server has already killed, and the panel shows
    // its QR with a ticking countdown that no phone can use.
    let settle: (value: ReturnType<typeof liveGrant>) => void = () => {};
    createGrant.mockReturnValue(
      new Promise<ReturnType<typeof liveGrant>>((resolve) => {
        settle = resolve;
      }),
    );

    const first = render(
      <PhoneHandoff designId="design-1" uploads={[]} max={3} onUploadsChanged={vi.fn()} />,
    );
    expect(createGrant).toHaveBeenCalledTimes(1);
    first.unmount();

    const second = render(
      <PhoneHandoff designId="design-1" uploads={[]} max={3} onUploadsChanged={vi.fn()} />,
    );

    // The second mount joined the first mint rather than starting its own.
    expect(createGrant).toHaveBeenCalledTimes(1);

    settle(liveGrant());
    expect(await screen.findByRole("img", { name: /Scan this/i })).toBeInTheDocument();

    // And the mount that left holding nothing revoked nothing: the code it had
    // asked for belongs to the mount still on screen, which will revoke it when
    // IT leaves. A design-scoped revoke here would kill the live code.
    expect(revokeGrants).not.toHaveBeenCalled();

    // Which it must actually do. Ownership has to land on the mount that is on
    // screen, or the grant ends up held by nobody and leaving the step stops
    // revoking it at all — a silent regression the assertion above would miss.
    second.unmount();
    await waitFor(() => expect(revokeGrants).toHaveBeenCalledWith("design-1"));
  });


  it("mints once for a double-tapped button", async () => {
    // REL-003 regression. A laggy shop iPad is the target device, and two
    // clicks dispatched before React commits the first render both see the
    // same state — the button vanishing is a consequence of the re-render, not
    // a lock. A second mint revokes the first server-side, so the customer's
    // half-scanned QR would stop working under her hands.
    let release!: (value: ReturnType<typeof liveGrant>) => void;
    createGrant.mockReturnValue(
      new Promise<ReturnType<typeof liveGrant>>((resolve) => {
        release = resolve;
      }),
    );
    const { rerender } = renderPanel();

    // The mint is in flight from mount. A re-render — an arriving photograph,
    // a parent state change — must not start a second one: a second mint
    // revokes the first server-side, so the customer's half-scanned QR would
    // stop working under her hands.
    rerender(
      <PhoneHandoff designId="design-1" uploads={[]} max={3} onUploadsChanged={vi.fn()} />,
    );

    expect(createGrant).toHaveBeenCalledTimes(1);
    release(liveGrant());
    await screen.findByRole("img", { name: /Scan this/i });
  });

  it("mints once for a double-tapped 'show a new code'", async () => {
    // The same guard, on the one button that still mints. A laggy shop iPad is
    // the target device, and two clicks dispatched before React commits the
    // first render both see the same state — the button vanishing is a
    // consequence of the re-render, not a lock.
    renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });
    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));
    const again = await screen.findByRole("button", { name: /^Show a new code$/i });
    createGrant.mockClear();
    let release!: (value: ReturnType<typeof liveGrant>) => void;
    createGrant.mockReturnValue(
      new Promise<ReturnType<typeof liveGrant>>((resolve) => {
        release = resolve;
      }),
    );

    fireEvent.click(again);
    fireEvent.click(again);

    expect(createGrant).toHaveBeenCalledTimes(1);
    release(liveGrant());
    await screen.findByRole("img", { name: /Scan this/i });
  });

  it("stops once for a double-tapped stop", async () => {
    renderPanel();
    await screen.findByRole("img", { name: /Scan this/i });
    let release!: (value: { ok: boolean }) => void;
    revokeGrants.mockReturnValue(
      new Promise<{ ok: boolean }>((resolve) => {
        release = resolve;
      }),
    );

    const button = screen.getByRole("button", { name: /Stop accepting photos/i });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(revokeGrants).toHaveBeenCalledTimes(1);
    release({ ok: true });
    await screen.findByRole("button", { name: /^Show a new code$/i });
  });

  it("recovers the button after a failed mint rather than jamming", async () => {
    createGrant.mockRejectedValueOnce(new Error("offline"));
    renderPanel();
    await screen.findByText(/could not be created/i);

    createGrant.mockResolvedValue(liveGrant());
    fireEvent.click(screen.getByRole("button", { name: /Try again/i }));

    expect(await screen.findByRole("img", { name: /Scan this/i })).toBeInTheDocument();
  });
});

describe("PhoneHandoff — watching for arrivals", () => {
  it("polls the narrow references read and reports what arrived", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchReferencesMock.mockResolvedValue([made("u1")]);
    const { onUploadsChanged } = renderPanel();
    await vi.waitFor(() => expect(screen.getByRole("img", { name: /Scan this/i })).toBeTruthy());

    await vi.advanceTimersByTimeAsync(2100);

    await vi.waitFor(() => expect(fetchReferencesMock).toHaveBeenCalledWith("design-1"));
    await vi.waitFor(() =>
      expect(onUploadsChanged).toHaveBeenCalledWith([expect.objectContaining({ id: "u1" })]),
    );
    await vi.waitFor(() =>
      expect(
        screen.getByRole("status", { name: /arriving from a phone/i }),
      ).toHaveTextContent(/1 photograph arrived/i),
    );
  });

  it("does not poll before a code is shown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchReferencesMock.mockResolvedValue([]);
    renderPanel();

    await vi.advanceTimersByTimeAsync(6000);

    expect(fetchReferencesMock).not.toHaveBeenCalled();
  });

  it("stops polling once the code is stopped", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchReferencesMock.mockResolvedValue([]);
    renderPanel();
    await vi.waitFor(() => expect(screen.getByRole("img", { name: /Scan this/i })).toBeTruthy());
    await vi.advanceTimersByTimeAsync(2100);
    const pollsWhileLive = fetchReferencesMock.mock.calls.length;
    expect(pollsWhileLive).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /Stop accepting photos/i }));
    await vi.advanceTimersByTimeAsync(6000);

    expect(fetchReferencesMock.mock.calls.length).toBe(pollsWhileLive);
  });

  it("ignores a slow poll that resolves after a newer one", async () => {
    // REL-002 regression. On shop wifi, with a phone uploading megabytes at the
    // same time, a poll sent first can land last. Applying it would drag the
    // arrival count — and the parent's uploads list — backwards.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const resolvers: ((value: Upload[]) => void)[] = [];
    fetchReferencesMock.mockImplementation(
      () =>
        new Promise<Upload[]>((resolve) => {
          resolvers.push(resolve);
        }),
    );
    const { onUploadsChanged } = renderPanel();
    await vi.waitFor(() => expect(screen.getByRole("img", { name: /Scan this/i })).toBeTruthy());

    // Two polls in flight.
    await vi.advanceTimersByTimeAsync(2100);
    await vi.advanceTimersByTimeAsync(2100);
    await vi.waitFor(() => expect(resolvers.length).toBeGreaterThanOrEqual(2));

    // The SECOND lands first, carrying two photographs...
    resolvers[1]([made("u1", 1), made("u2", 2)]);
    await vi.waitFor(() =>
      expect(
        screen.getByRole("status", { name: /arriving from a phone/i }),
      ).toHaveTextContent(/2 photographs arrived/i),
    );
    // ...then the stale FIRST arrives with only one. It must be discarded.
    resolvers[0]([made("u1", 1)]);
    await vi.advanceTimersByTimeAsync(50);

    expect(
      screen.getByRole("status", { name: /arriving from a phone/i }),
    ).toHaveTextContent(/2 photographs arrived/i);
    const lastCall = onUploadsChanged.mock.calls.at(-1);
    expect(lastCall?.[0]).toHaveLength(2);
  });

  it("swallows a dropped poll rather than alarming the stylist", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchReferencesMock.mockRejectedValue(new Error("offline"));
    const { onUploadsChanged } = renderPanel();
    await vi.waitFor(() => expect(screen.getByRole("img", { name: /Scan this/i })).toBeTruthy());

    await vi.advanceTimersByTimeAsync(2100);

    // The code stays up and nothing is announced: the customer's own screen
    // already told her whether her upload worked.
    expect(screen.getByRole("img", { name: /Scan this/i })).toBeInTheDocument();
    expect(onUploadsChanged).not.toHaveBeenCalled();
    expect(
      screen.getByRole("status", { name: /arriving from a phone/i }),
    ).toHaveTextContent("");
  });
});
