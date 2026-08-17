import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SendDialogCoordinator, useSendDialogCoordination } from "./send-dialog-coordination";
import { SendToAccountButton } from "./SendToAccountButton";

const api = vi.hoisted(() => ({
  fetchRenderSendState: vi.fn(),
  sendRenderToAccount: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...api,
}));

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchRenderSendState.mockResolvedValue({ used: 0, limit: 3, suggestedFilename: "Concept" });
  api.sendRenderToAccount.mockResolvedValue({ ok: true });
});

// The coordinator's whole contract is three sentences, and each one is a thing
// that would silently strand a control if it were wrong.

function Probe({ id }: { id: string }) {
  const { mayOpen, claim, release } = useSendDialogCoordination();
  return (
    <p>
      <span data-testid={`may-${id}`}>{mayOpen(id) ? "yes" : "no"}</span>
      <button type="button" onClick={() => claim(id)}>
        claim {id}
      </button>
      <button type="button" onClick={() => release(id)}>
        release {id}
      </button>
    </p>
  );
}

function may(id: string) {
  return screen.getByTestId(`may-${id}`).textContent;
}

describe("send-dialog coordination", () => {
  it("allows everything when there is no provider, so single-send surfaces are untouched", () => {
    // The default is deliberately permissive rather than absent. Every screen
    // that shows ONE send control — the annotation workspace, and the concept
    // screen — renders no coordinator at all, and none of them may become
    // conditional on a provider somebody remembered to add.
    render(<Probe id="only" />);
    expect(may("only")).toBe("yes");
  });

  it("lends the claim to one key at a time", () => {
    render(
      <SendDialogCoordinator>
        <Probe id="a" />
        <Probe id="b" />
      </SendDialogCoordinator>,
    );
    expect(may("a")).toBe("yes");
    expect(may("b")).toBe("yes");

    fireEvent.click(screen.getByRole("button", { name: "claim a" }));
    expect(may("a")).toBe("yes");
    expect(may("b")).toBe("no");

    fireEvent.click(screen.getByRole("button", { name: "release a" }));
    expect(may("b")).toBe("yes");
  });

  it("keeps the claim with its first holder", () => {
    // Two simultaneous claims cannot arise from user input — one dialog has to
    // be open before its sibling is reachable — but refusing to overwrite means
    // a late claim can never take a claim away from the control currently
    // showing a dialog, whose own release would then be a no-op forever.
    render(
      <SendDialogCoordinator>
        <Probe id="a" />
        <Probe id="b" />
      </SendDialogCoordinator>,
    );
    fireEvent.click(screen.getByRole("button", { name: "claim a" }));
    fireEvent.click(screen.getByRole("button", { name: "claim b" }));
    expect(may("a")).toBe("yes");
    expect(may("b")).toBe("no");
  });

  it("hands the claim back when the holding control unmounts mid-dialog", async () => {
    // Reachable for real: the image query can fail under an open dialog, which
    // unmounts the whole actions row along with the button holding the claim.
    // Without a release on unmount the sibling control stays disabled for the
    // life of the screen, with no way for anyone to give it back.
    function Screen({ showFirst }: { showFirst: boolean }) {
      return (
        <SendDialogCoordinator>
          {showFirst && (
            <SendToAccountButton
              designId="d1"
              versionId="v1"
              kind="plain"
              label="Send to account"
              accountEmail="stylist@example.com"
              qualifier="version 1"
            />
          )}
          <SendToAccountButton
            designId="d1"
            versionId="v2"
            kind="plain"
            label="Send to account"
            accountEmail="stylist@example.com"
            qualifier="version 2"
          />
        </SendDialogCoordinator>
      );
    }

    const { rerender } = render(<Screen showFirst />);
    const second = screen.getByRole("button", { name: /send to account — version 2/i });
    fireEvent.click(screen.getByRole("button", { name: /send to account — version 1/i }));
    await waitFor(() => expect(second).toBeDisabled());

    rerender(<Screen showFirst={false} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /send to account — version 2/i })).toBeEnabled(),
    );
  });

  it("ignores a release from a key that does not hold the claim", () => {
    // Releasing on unmount, on cancel and on every refusal path means release is
    // called more often than claim is. One of those calls landing from the wrong
    // control must not hand the screen back while a dialog is still open.
    render(
      <SendDialogCoordinator>
        <Probe id="a" />
        <Probe id="b" />
      </SendDialogCoordinator>,
    );
    fireEvent.click(screen.getByRole("button", { name: "claim a" }));
    fireEvent.click(screen.getByRole("button", { name: "release b" }));
    expect(may("b")).toBe("no");
  });
});
