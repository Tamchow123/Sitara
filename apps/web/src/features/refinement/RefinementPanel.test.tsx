import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RefinementPanel } from "./RefinementPanel";
import { REFINEMENT_NOTE_MAX_LENGTH } from "./refinement-options";

const mocks = vi.hoisted(() => ({
  startDesignRefinement: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    startDesignRefinement: mocks.startDesignRefinement,
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

function selectChip(name: RegExp) {
  fireEvent.click(screen.getByRole("radio", { name }));
}

function acknowledge() {
  fireEvent.click(screen.getByRole("checkbox"));
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RefinementPanel — chip selection", () => {
  it("allows exactly one chip to be selected at a time", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    selectChip(/colour story/i);
    selectChip(/neckline/i);
    expect(screen.getByRole("radio", { name: /colour story/i })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: /neckline/i })).toBeChecked();
  });

  it("does not enable submission without a chip selected", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    acknowledge();
    expect(screen.getByRole("button", { name: /request refinement/i })).toBeDisabled();
  });
});

describe("RefinementPanel — note", () => {
  it("shows a remaining count and declares the native 300-character limit", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    const textarea = screen.getByLabelText(/optional note/i);
    fireEvent.change(textarea, { target: { value: "a".repeat(50) } });
    expect(screen.getByText(`${REFINEMENT_NOTE_MAX_LENGTH - 50} characters remaining`)).toBeInTheDocument();
    expect(textarea).toHaveAttribute("maxLength", String(REFINEMENT_NOTE_MAX_LENGTH));
  });

  it("blocks submission with a visible error if the note somehow exceeds the limit", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    selectChip(/colour story/i);
    acknowledge();
    // A paste can bypass the native maxLength attribute — the component must
    // still enforce the limit itself before allowing submission.
    fireEvent.change(screen.getByLabelText(/optional note/i), {
      target: { value: "a".repeat(REFINEMENT_NOTE_MAX_LENGTH + 1) },
    });
    expect(screen.getByRole("button", { name: /request refinement/i })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/300 characters or fewer/i);
  });
});

describe("RefinementPanel — drift acknowledgement", () => {
  it("shows the accurate drift warning text", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    expect(screen.getByText(/fresh AI-generated image/i)).toBeInTheDocument();
    expect(screen.getByText(/pose, composition, face, garment details/i)).toBeInTheDocument();
    expect(screen.getByText(/continuity aid, not a guarantee/i)).toBeInTheDocument();
  });

  it("requires the acknowledgement checkbox before submission is enabled", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    selectChip(/colour story/i);
    expect(screen.getByRole("button", { name: /request refinement/i })).toBeDisabled();
    acknowledge();
    expect(screen.getByRole("button", { name: /request refinement/i })).toBeEnabled();
  });

  it("shows honest demo disclosure text and never claims image editing or seed continuity", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" isDemo />);
    const disclaimer = screen.getByRole("note", { name: /refinement disclaimer/i });
    expect(disclaimer).toHaveTextContent(/deterministic design brief/i);
    expect(disclaimer).toHaveTextContent(/another curated image may be selected/i);
    expect(disclaimer).toHaveTextContent(/not edited/i);
    expect(disclaimer).toHaveTextContent(/never sent anywhere/i);
    expect(disclaimer.textContent).not.toMatch(/seed/i);
    expect(disclaimer.textContent).not.toMatch(/fresh AI-generated image/i);
  });

  it("associates the disclosure with the submit button", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    const button = screen.getByRole("button", { name: /request refinement/i });
    expect(button.getAttribute("aria-describedby")).toContain("refinement-disclaimer");
  });
});

describe("RefinementPanel — submission", () => {
  it("a double click submits exactly once", async () => {
    let resolveRequest: (value: unknown) => void = () => {};
    mocks.startDesignRefinement.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    selectChip(/colour story/i);
    acknowledge();
    const button = screen.getByRole("button", { name: /request refinement/i });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mocks.startDesignRefinement).toHaveBeenCalledTimes(1);
    resolveRequest({ ok: true, data: { job: { id: "j1" } } });
  });

  it("sends the source version id, one change_type, and the note", async () => {
    mocks.startDesignRefinement.mockResolvedValue({ ok: true, data: { job: { id: "j1" } } });
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    selectChip(/embellishment/i);
    fireEvent.change(screen.getByLabelText(/optional note/i), { target: { value: "more gold" } });
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    await vi.waitFor(() => expect(mocks.startDesignRefinement).toHaveBeenCalled());
    expect(mocks.startDesignRefinement).toHaveBeenCalledWith(
      "d1",
      { source_version_id: "v1", change_type: "embellishment", note: "more gold" },
      expect.any(String),
    );
  });

  it("navigates to the generation progress route with the source version id on success", async () => {
    mocks.startDesignRefinement.mockResolvedValue({ ok: true, data: { job: { id: "j9" } } });
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    selectChip(/colour story/i);
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    await vi.waitFor(() =>
      expect(mocks.replace).toHaveBeenCalledWith("/design/d1/generation/j9?from=v1"),
    );
  });

  it("a transport failure retry reuses the exact same idempotency key", async () => {
    mocks.startDesignRefinement.mockResolvedValueOnce({
      ok: false,
      status: 0,
      code: "unavailable",
      message: "The service could not be reached.",
    });
    mocks.startDesignRefinement.mockResolvedValueOnce({ ok: true, data: { job: { id: "j1" } } });
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    selectChip(/colour story/i);
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await vi.waitFor(() => expect(mocks.startDesignRefinement).toHaveBeenCalledTimes(2));
    const firstKey = mocks.startDesignRefinement.mock.calls[0][2];
    const secondKey = mocks.startDesignRefinement.mock.calls[1][2];
    expect(secondKey).toBe(firstKey);
  });

  it("mints a fresh key after a confirmed conflict, never reusing the consumed one", async () => {
    mocks.startDesignRefinement.mockResolvedValueOnce({
      ok: false,
      status: 409,
      code: "refinement_limit_reached",
      message: "This design has already been refined.",
    });
    mocks.startDesignRefinement.mockResolvedValueOnce({ ok: true, data: { job: { id: "j1" } } });
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    selectChip(/colour story/i);
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await vi.waitFor(() => expect(mocks.startDesignRefinement).toHaveBeenCalledTimes(2));
    const firstKey = mocks.startDesignRefinement.mock.calls[0][2];
    const secondKey = mocks.startDesignRefinement.mock.calls[1][2];
    expect(secondKey).not.toBe(firstKey);
  });

  it("calls onRequiresRecheck when the backend reports the design can no longer be refined", async () => {
    mocks.startDesignRefinement.mockResolvedValue({
      ok: false,
      status: 409,
      code: "refinement_in_progress",
      message: "A refinement job is already in progress for this design.",
    });
    const onRequiresRecheck = vi.fn();
    render(
      <RefinementPanel designId="d1" sourceVersionId="v1" onRequiresRecheck={onRequiresRecheck} />,
    );
    selectChip(/colour story/i);
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    await screen.findByRole("alert");
    expect(onRequiresRecheck).toHaveBeenCalledTimes(1);
  });

  it("touches no browser storage", async () => {
    mocks.startDesignRefinement.mockResolvedValue({ ok: true, data: { job: { id: "j1" } } });
    render(<RefinementPanel designId="d1" sourceVersionId="v1" />);
    selectChip(/colour story/i);
    fireEvent.change(screen.getByLabelText(/optional note/i), { target: { value: "a note" } });
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    await vi.waitFor(() => expect(mocks.startDesignRefinement).toHaveBeenCalled());
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});
