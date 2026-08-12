// "Finish and hand back" (Phase 22, ADR 0027).
//
// The load-bearing behaviour is honesty about what happened. A stylist taps
// this and then turns the screen towards the next customer, so the control
// must never say it cleared a session the server did not clear — and must
// never navigate away on the strength of a request that failed.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { FinishAndHandBack } from "./FinishAndHandBack";

const endSession = vi.fn();
const replace = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, endWalkInSession: (...args: unknown[]) => endSession(...args) };
});

// A FULL document load, not a soft router.push(). Review (SEC-001) pointed out
// that a soft navigation keeps the whole JavaScript context alive in a tab the
// next customer is now holding — the TanStack cache with the previous
// customer's result payload in it, the signed image URLs, any annotation state.
// So the assertion below is on window.location.replace, and `replace` rather
// than `assign` so this screen is not one back-press away.
beforeAll(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, replace },
  });
});

function start(): void {
  fireEvent.click(screen.getByRole("button", { name: /Finish and hand back/i }));
}

function confirm(): void {
  fireEvent.click(screen.getByRole("button", { name: /Yes, hand back/i }));
}

beforeEach(() => {
  endSession.mockReset();
  replace.mockReset();
  endSession.mockResolvedValue({ ok: true });
});

describe("FinishAndHandBack", () => {
  it("asks before clearing, because the screen is not the stylist's alone", () => {
    render(<FinishAndHandBack />);

    start();

    expect(screen.getByText(/Clear this screen for the next customer/i)).toBeInTheDocument();
    expect(endSession).not.toHaveBeenCalled();
  });

  it("clears nothing if the stylist backs out", () => {
    render(<FinishAndHandBack />);
    start();

    fireEvent.click(screen.getByRole("button", { name: /Not yet/i }));

    expect(endSession).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Finish and hand back/i })).toBeInTheDocument();
  });

  it("ends the session on the server and returns to a neutral screen", async () => {
    render(<FinishAndHandBack />);
    start();

    confirm();

    await waitFor(() => expect(endSession).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  it("reloads the document, so no in-memory copy of the last customer survives", async () => {
    // The point of a full load over router.push(): a soft navigation keeps the
    // query cache, the signed image URLs and every mounted component alive.
    render(<FinishAndHandBack />);
    start();

    confirm();

    await waitFor(() => expect(replace).toHaveBeenCalledTimes(1));
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("does not navigate when the server did not confirm", async () => {
    // The dangerous failure: a neutral start screen that looks cleared while
    // the workspace and any live handoff code are still there.
    endSession.mockResolvedValue({ ok: false });
    render(<FinishAndHandBack />);
    start();

    confirm();

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be cleared/i);
    expect(replace).not.toHaveBeenCalled();
  });

  it("tells the stylist to try again before handing the screen over", async () => {
    endSession.mockResolvedValue({ ok: false });
    render(<FinishAndHandBack />);
    start();
    confirm();

    expect(await screen.findByText(/before handing it over/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Try again/i })).toBeInTheDocument();
  });

  it("navigates once a retry succeeds", async () => {
    endSession.mockResolvedValueOnce({ ok: false });
    render(<FinishAndHandBack />);
    start();
    confirm();
    await screen.findByRole("alert");

    endSession.mockResolvedValue({ ok: true });
    fireEvent.click(screen.getByRole("button", { name: /Try again/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  it("announces that it is working", async () => {
    let release!: (value: { ok: boolean }) => void;
    endSession.mockReturnValue(
      new Promise<{ ok: boolean }>((resolve) => {
        release = resolve;
      }),
    );
    render(<FinishAndHandBack />);
    start();

    confirm();

    expect(await screen.findByRole("status")).toHaveTextContent(/Clearing the screen/i);
    release({ ok: true });
  });

  it("offers no sign-out of its own", () => {
    // ADR 0027: the account is the boutique's, and the next customer using it
    // is the intended state. Signing out here would put a password between
    // every customer for no privacy gain.
    render(<FinishAndHandBack />);
    start();

    expect(screen.queryByRole("button", { name: /sign out/i })).not.toBeInTheDocument();
  });

  it("does not describe itself as deleting anything", () => {
    // The concepts are the shop's work product. Saying "delete" would be
    // wrong about what happens and wrong about who owns it.
    render(<FinishAndHandBack />);
    start();

    const text = document.body.textContent?.toLowerCase() ?? "";
    for (const word of ["delete", "deleted", "erase", "permanently"]) {
      expect(text).not.toContain(word);
    }
  });
});
