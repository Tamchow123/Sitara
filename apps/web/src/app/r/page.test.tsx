// The customer's phone (Phase 22, ADR 0026).
//
// The load-bearing behaviours are consent and reach, not layout. The full ADR
// 0019 disclosure has to be readable before the picker is usable; the
// affirmation has to be this person's, given here; the photo library has to be
// the primary control because that is where the picture already is; and the
// secret has to arrive in the fragment and stay in memory.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PhoneReferencePage from "./page";

const uploadByGrant = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    uploadReferenceByGrant: (...args: unknown[]) => uploadByGrant(...args),
  };
});

const TOKEN = "a-plaintext-handoff-secret-value";

function arriveWith(fragment: string): void {
  window.location.hash = fragment;
}

function file(name = "from-my-phone.jpg"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "image/jpeg" });
}

function library(): HTMLInputElement {
  return screen.getByLabelText(/Choose from your photos/i) as HTMLInputElement;
}

function camera(): HTMLInputElement {
  return screen.getByLabelText(/Take a photo now/i) as HTMLInputElement;
}

function affirm(): void {
  fireEvent.click(screen.getByRole("checkbox"));
}

beforeEach(() => {
  uploadByGrant.mockReset();
  uploadByGrant.mockResolvedValue({ ok: true });
  arriveWith("");
});

describe("the phone page — arriving", () => {
  it("takes the code from the fragment", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);

    expect(await screen.findByRole("heading", { name: /Send a photograph/i })).toBeInTheDocument();
    affirm();
    fireEvent.change(library(), { target: { files: [file()] } });

    await waitFor(() =>
      expect(uploadByGrant).toHaveBeenCalledWith(TOKEN, expect.any(File), true),
    );
  });

  it("explains itself rather than failing silently when there is no code", async () => {
    arriveWith("");
    render(<PhoneReferencePage />);

    expect(await screen.findByRole("heading", { name: /Nothing to send to/i })).toBeInTheDocument();
    expect(screen.queryByLabelText(/Choose from your photos/i)).not.toBeInTheDocument();
  });

  it("never writes the code to browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });

    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });
});

describe("the phone page — consent", () => {
  it("shows the provider disclosure in full before anything can be sent", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });

    // The two clauses that make ADR 0019's exposure what it is. If a smaller
    // screen ever prompts someone to trim the copy, this fails.
    expect(screen.getByText(/perpetual, irrevocable licence/i)).toBeInTheDocument();
    expect(screen.getByText(/no time limit on how long they/i)).toBeInTheDocument();
    expect(screen.getByText(/unresolved whether those terms differ/i)).toBeInTheDocument();
    // And it is readable before the picker works.
    expect(library()).toBeDisabled();
  });

  it("wires the affirmation to the disclosure for a screen reader", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);

    const box = await screen.findByRole("checkbox");
    const described = box.getAttribute("aria-describedby");
    expect(described).toBeTruthy();
    expect(document.getElementById(described!)).toHaveTextContent(/perpetual, irrevocable/i);
  });

  it("sends nothing until this person affirms, even if the input is re-enabled", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });

    // A stray programmatic change must not be enough: the handler checks the
    // affirmation itself rather than trusting the disabled attribute.
    library().disabled = false;
    fireEvent.change(library(), { target: { files: [file()] } });

    await waitFor(() => expect(uploadByGrant).not.toHaveBeenCalled());
  });

  it("passes this device's own affirmation, never a default", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });
    affirm();

    fireEvent.change(library(), { target: { files: [file()] } });

    await waitFor(() => expect(uploadByGrant).toHaveBeenCalled());
    expect(uploadByGrant.mock.calls[0][2]).toBe(true);
  });
});

describe("the phone page — reaching the picture", () => {
  it("makes the photo library the primary control", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });

    const chooser = screen.getByText(/Choose from your photos/i);
    expect(chooser).toHaveClass("phone-label-primary");
    // The camera is present but secondary — no primary class, and it comes
    // second in the document.
    const shooter = screen.getByText(/Take a photo now/i);
    expect(shooter).not.toHaveClass("phone-label-primary");
    expect(chooser.compareDocumentPosition(shooter)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("asks the camera for the rear lens but accepts a picker instead", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });

    expect(camera()).toHaveAttribute("capture", "environment");
    // `capture` is a hint. Both inputs run the same handler, so whichever
    // dialogue a browser opens, the result is one chosen file.
    affirm();
    fireEvent.change(camera(), { target: { files: [file()] } });
    await waitFor(() => expect(uploadByGrant).toHaveBeenCalled());
  });

  it("narrows the accept list rather than using image/*", async () => {
    // On iOS the narrow list is what makes the picker hand back a JPEG rather
    // than the HEIC the device stores natively, which the sanitiser refuses.
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });

    for (const input of [library(), camera()]) {
      expect(input).toHaveAttribute("accept", "image/jpeg,image/png,image/webp");
    }
  });

  it("lets the same photograph be retried after a failure", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });
    affirm();
    uploadByGrant.mockResolvedValueOnce({
      ok: false,
      status: 503,
      code: "storage_unavailable",
      message: "The image could not be stored. Please try again.",
    });

    fireEvent.change(library(), { target: { files: [file()] } });
    expect(await screen.findByText(/could not be stored/i)).toBeInTheDocument();
    // The input was cleared, so re-choosing the SAME file fires change again.
    expect(library().value).toBe("");
    fireEvent.change(library(), { target: { files: [file()] } });

    await waitFor(() => expect(uploadByGrant).toHaveBeenCalledTimes(2));
  });
});

describe("the phone page — what it says back", () => {
  it("announces a successful send in a live region", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });
    affirm();

    fireEvent.change(library(), { target: { files: [file()] } });

    const status = await screen.findByRole("status");
    await waitFor(() => expect(status).toHaveTextContent(/Sent — 1 photograph so far/i));
  });

  it("counts what this phone sent, not what the design holds", async () => {
    // The server refuses to say how full the design is, because a count would
    // let this page work out how many references were already there. Anything
    // shown here is therefore about this phone's own actions only.
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });
    affirm();

    fireEvent.change(library(), { target: { files: [file("one.jpg")] } });
    await screen.findByText(/1 photograph so far/i);
    fireEvent.change(library(), { target: { files: [file("two.jpg")] } });

    expect(await screen.findByText(/2 photographs so far/i)).toBeInTheDocument();
  });

  it("says what to do when the code has stopped working", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });
    affirm();
    uploadByGrant.mockResolvedValue({
      ok: false,
      status: 404,
      code: "reference_upload_unavailable",
      message: "This link is no longer usable. Ask for a new code on the shop's screen.",
    });

    fireEvent.change(library(), { target: { files: [file()] } });

    expect(await screen.findByText(/Ask for a new code/i)).toBeInTheDocument();
  });

  it("turns a dropped connection into a stated failure, never a silent one", async () => {
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });
    affirm();
    uploadByGrant.mockRejectedValue(new Error("offline"));

    fireEvent.change(library(), { target: { files: [file()] } });

    expect(await screen.findByText(/did not finish sending/i)).toBeInTheDocument();
  });

  it("offers no way to see or remove what is on the design", async () => {
    // A grant grants upload and nothing else. A gallery here would be a read
    // capability, which is a permanent ADR 0026 non-goal.
    arriveWith(`#${TOKEN}`);
    render(<PhoneReferencePage />);
    await screen.findByRole("heading", { name: /Send a photograph/i });
    affirm();
    fireEvent.change(library(), { target: { files: [file()] } });
    await screen.findByText(/1 photograph so far/i);

    expect(screen.queryByRole("img", { name: /uploaded/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});
