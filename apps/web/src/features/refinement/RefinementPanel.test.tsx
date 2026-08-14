import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RefinementPanel } from "./RefinementPanel";
import {
  REFINEMENT_CHANGE_TYPE_OPTIONS,
  REFINEMENT_NOTE_MAX_LENGTH,
} from "./refinement-options";
import { axeViolations } from "@/test-utils/axe";

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
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    selectChip(/colour story/i);
    selectChip(/neckline/i);
    expect(screen.getByRole("radio", { name: /colour story/i })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: /neckline/i })).toBeChecked();
  });

  it("does not enable submission without a chip selected", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    acknowledge();
    expect(screen.getByRole("button", { name: /request refinement/i })).toBeDisabled();
  });

  // ADR 0028: a chip now says which ANSWER it will change, not just its
  // category name. Before this phase a refinement could not alter the image at
  // all, so naming the field would have been a promise the product could not
  // keep.
  it("names the answer each chip will change", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    expect(screen.getByText(/changes the fabrics you chose/i)).toBeInTheDocument();
    expect(screen.getByText(/changes the neckline you chose/i)).toBeInTheDocument();
    expect(
      screen.getByText(/changes the coverage you chose — sleeves, back and midriff/i),
    ).toBeInTheDocument();
  });

  it("associates each effect with its own radio rather than leaving it decoration", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    for (const option of REFINEMENT_CHANGE_TYPE_OPTIONS) {
      const radio = screen.getByRole("radio", { name: new RegExp(option.label, "i") });
      const describedBy = radio.getAttribute("aria-describedby");
      expect(describedBy).toBe(`refinement-effect-${option.value}`);
      expect(document.getElementById(describedBy!)).toHaveTextContent(option.effect);
    }
  });

  it("names each radio by its category alone, so the effect is not read twice", () => {
    // The label wraps both spans, so without an explicit aria-labelledby the
    // effect sentence would land in the accessible NAME as well as the
    // description — the same sentence announced twice, seven times over.
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    for (const option of REFINEMENT_CHANGE_TYPE_OPTIONS) {
      const radio = screen.getByRole("radio", { name: new RegExp(`^${option.label}$`, "i") });
      expect(radio).toBeInTheDocument();
    }
    expect(screen.queryByRole("radio", { name: /changes the fabrics you chose/i })).toBeNull();
  });

  it("offers the seven requestable categories and never the retired one", () => {
    // The panel is the REQUEST side. `styling_details` is still labelled for a
    // historical result (see CHANGE_TYPE_LABELS), but offering it here would
    // present a control the API answers with a 400.
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    expect(screen.getAllByRole("radio")).toHaveLength(7);
    expect(screen.queryByRole("radio", { name: /styling details/i })).not.toBeInTheDocument();
  });
});

describe("RefinementPanel — note", () => {
  it("shows a remaining count and declares the native 300-character limit", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    const textarea = screen.getByLabelText(/optional note/i);
    fireEvent.change(textarea, { target: { value: "a".repeat(50) } });
    expect(screen.getByText(`${REFINEMENT_NOTE_MAX_LENGTH - 50} characters remaining`)).toBeInTheDocument();
    expect(textarea).toHaveAttribute("maxLength", String(REFINEMENT_NOTE_MAX_LENGTH));
  });

  it("blocks submission with a visible error if the note somehow exceeds the limit", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
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
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    expect(screen.getByText(/fresh AI-generated image/i)).toBeInTheDocument();
    expect(screen.getByText(/pose, composition, face, garment details/i)).toBeInTheDocument();
    expect(screen.getByText(/continuity aid, not a guarantee/i)).toBeInTheDocument();
  });

  it("requires the acknowledgement checkbox before submission is enabled", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    selectChip(/colour story/i);
    expect(screen.getByRole("button", { name: /request refinement/i })).toBeDisabled();
    acknowledge();
    expect(screen.getByRole("button", { name: /request refinement/i })).toBeEnabled();
  });

  it("shows honest demo disclosure text and never claims image editing or seed continuity", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} isDemo />);
    const disclaimer = screen.getByRole("note", { name: /refinement disclaimer/i });
    expect(disclaimer).toHaveTextContent(/deterministic design brief/i);
    expect(disclaimer).toHaveTextContent(/another curated image may be selected/i);
    expect(disclaimer).toHaveTextContent(/not edited/i);
    expect(disclaimer).toHaveTextContent(/never sent anywhere/i);
    expect(disclaimer.textContent).not.toMatch(/seed/i);
    expect(disclaimer.textContent).not.toMatch(/fresh AI-generated image/i);
  });

  it("associates the disclosure with the submit button", () => {
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
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
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
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
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
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
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
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
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
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

  // Uses a confirmed-but-retryable code deliberately: the invariant under test
  // is "any confirmed HTTP response consumes the key", and the terminal codes
  // no longer render a retry control to click (see the two tests below).
  it("mints a fresh key after a confirmed failure, never reusing the consumed one", async () => {
    mocks.startDesignRefinement.mockResolvedValueOnce({
      ok: false,
      status: 400,
      code: "refinement_invalid",
      message: "Please choose one change and check your note, then try again.",
    });
    mocks.startDesignRefinement.mockResolvedValueOnce({ ok: true, data: { job: { id: "j1" } } });
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
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

  it("offers no retry control once this design's refinements are all used", async () => {
    mocks.startDesignRefinement.mockResolvedValue({
      ok: false,
      status: 409,
      code: "refinement_limit_reached",
      message: "You have used every refinement available for this concept.",
    });
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={1} />);
    selectChip(/colour story/i);
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/used every refinement/i);
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });

  it("offers no retry control when a newer version has superseded this one", async () => {
    // A stale tab: the customer refined from another tab, so this page's source
    // is no longer the design's latest. Retrying the same source cannot ever
    // succeed, and the recheck refetches the design so the page can say so.
    const onRequiresRecheck = vi.fn();
    mocks.startDesignRefinement.mockResolvedValue({
      ok: false,
      status: 409,
      code: "refinement_source_unavailable",
      message: "server message",
    });
    render(
      <RefinementPanel
        designId="d1"
        sourceVersionId="v1"
        refinementsRemaining={2}
        onRequiresRecheck={onRequiresRecheck}
      />,
    );
    selectChip(/colour story/i);
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/most recent concept/i);
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
    expect(onRequiresRecheck).toHaveBeenCalled();
  });

  it("offers no retry control when generation is turned off or its limit is reached", async () => {
    for (const code of [
      "live_generation_disabled",
      "generation_limit_reached",
      "live_generation_budget_exhausted",
    ]) {
      mocks.startDesignRefinement.mockResolvedValue({
        ok: false,
        status: 429,
        code,
        message: "unused — the panel substitutes its own copy",
      });
      const view = render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
      selectChip(/colour story/i);
      acknowledge();
      fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
      await screen.findByRole("alert");
      expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
      view.unmount();
      mocks.startDesignRefinement.mockReset();
    }
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
      <RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} onRequiresRecheck={onRequiresRecheck} />,
    );
    selectChip(/colour story/i);
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    await screen.findByRole("alert");
    expect(onRequiresRecheck).toHaveBeenCalledTimes(1);
  });

  it("touches no browser storage", async () => {
    mocks.startDesignRefinement.mockResolvedValue({ ok: true, data: { job: { id: "j1" } } });
    render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
    selectChip(/colour story/i);
    fireEvent.change(screen.getByLabelText(/optional note/i), { target: { value: "a note" } });
    acknowledge();
    fireEvent.click(screen.getByRole("button", { name: /request refinement/i }));
    await vi.waitFor(() => expect(mocks.startDesignRefinement).toHaveBeenCalled());
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  describe("accessibility", () => {
    it("has no axe violations on arrival and once a change is chosen", async () => {
      const { container } = render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
      // Arrival: submit disabled, nothing acknowledged. Then the enabled state,
      // which turns aria-describedby hints and the acknowledgement on.
      expect(await axeViolations(container)).toHaveNoViolations();
      selectChip(/colour story/i);
      acknowledge();
      expect(await axeViolations(container)).toHaveNoViolations();
    });

    it("has no axe violations in the note-too-long error state", async () => {
      const { container } = render(<RefinementPanel designId="d1" sourceVersionId="v1" refinementsRemaining={3} />);
      selectChip(/colour story/i);
      fireEvent.change(screen.getByLabelText(/optional note/i), {
        target: { value: "x".repeat(REFINEMENT_NOTE_MAX_LENGTH + 1) },
      });
      await screen.findByText(/please shorten your note/i);
      expect(await axeViolations(container)).toHaveNoViolations();
    });
  });
});
