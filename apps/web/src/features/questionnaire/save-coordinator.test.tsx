import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QuestionnaireWizard } from "./QuestionnaireWizard";
import type { QuestionnaireSchema } from "./types";

const mocks = vi.hoisted(() => ({
  fetchActiveQuestionnaire: vi.fn(),
  fetchDesign: vi.fn(),
  createDesignDraft: vi.fn(),
  updateDesignDraft: vi.fn(),
  validateDesignDraft: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("./api", () => ({
  fetchActiveQuestionnaire: mocks.fetchActiveQuestionnaire,
  fetchDesign: mocks.fetchDesign,
  createDesignDraft: mocks.createDesignDraft,
  updateDesignDraft: mocks.updateDesignDraft,
  validateDesignDraft: mocks.validateDesignDraft,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  useParams: () => ({}),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const SCHEMA: QuestionnaireSchema = {
  schema_version: 1,
  key: "test",
  title: "Test",
  steps: [
    {
      id: "garment",
      title: "Garment step",
      questions: [
        {
          id: "garment_type",
          type: "single_choice",
          label: "Garment",
          required: true,
          options: [
            { value: "lehenga", label: "Lehenga" },
            { value: "saree", label: "Saree" },
          ],
        },
      ],
    },
    {
      id: "notes",
      title: "Notes step",
      questions: [
        {
          id: "final_notes",
          type: "text",
          label: "Notes",
          required: false,
          constraints: { min_length: 0, max_length: 50 },
        },
      ],
    },
  ],
  rules: [],
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    id: "d1",
    title: "",
    status: "draft",
    questionnaire: { id: "v1", version: 1, schema: SCHEMA },
    answers: {},
    selected_inspirations: [],
    created_at: "t",
    updated_at: "t",
    ...overrides,
  };
}

beforeEach(() => {
  // resetAllMocks clears both call history AND implementations, so a
  // deferred/return-value set in one test never leaks into the next.
  vi.resetAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  mocks.fetchActiveQuestionnaire.mockResolvedValue({ id: "v1", version: 1, schema: SCHEMA });
  mocks.createDesignDraft.mockResolvedValue({ ok: true, data: detail() });
  mocks.updateDesignDraft.mockResolvedValue({ ok: true, data: detail() });
  mocks.validateDesignDraft.mockResolvedValue({ ok: true, data: { valid: true } });
});

afterEach(() => {
  vi.resetAllMocks();
});

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("save coordinator", () => {
  it("1+3: two rapid initial changes cause exactly one POST, newest state wins", async () => {
    const create = deferred<{ ok: true; data: ReturnType<typeof detail> }>();
    mocks.createDesignDraft.mockReturnValue(create.promise);

    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    fireEvent.click(screen.getByRole("radio", { name: "Saree" }));

    // Observe the first POST via waitFor rather than a fixed microtask flush,
    // and assert it carries the correct non-empty questionnaire version.
    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalledTimes(1));
    expect(mocks.createDesignDraft).toHaveBeenCalledWith({
      questionnaire_version_id: "v1",
      answers: { garment_type: "lehenga" },
    });

    await act(async () => {
      create.resolve({ ok: true, data: detail({ answers: { garment_type: "saree" } }) });
    });

    // 2: the change made while the POST was pending is sent afterwards as the
    // newest PATCH — never an older snapshot.
    await waitFor(() => expect(mocks.updateDesignDraft).toHaveBeenCalled());
    const calls = mocks.updateDesignDraft.mock.calls;
    expect(calls[calls.length - 1]).toEqual(["d1", { answers: { garment_type: "saree" } }]);
    // No PATCH ever reverted to the older "lehenga" snapshot.
    for (const call of calls) {
      expect(call[1]).not.toEqual({ answers: { garment_type: "lehenga" } });
    }
    // Still exactly one design created — the queued change never re-POSTed.
    expect(mocks.createDesignDraft).toHaveBeenCalledTimes(1);
  });

  it("4: does not show Saved while a newer revision is still pending", async () => {
    const create = deferred<{ ok: true; data: ReturnType<typeof detail> }>();
    const patch = deferred<{ ok: true; data: ReturnType<typeof detail> }>();
    mocks.createDesignDraft.mockReturnValue(create.promise);
    mocks.updateDesignDraft.mockReturnValue(patch.promise);

    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    fireEvent.click(screen.getByRole("radio", { name: "Saree" }));
    await flushMicrotasks();

    // Create in flight, newer change pending → Saving, never Saved.
    expect(screen.getByText("Saving…")).toBeInTheDocument();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();

    await act(async () => {
      create.resolve({ ok: true, data: detail() });
    });
    // Create confirmed but the newer PATCH is still pending → still not Saved.
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();

    await act(async () => {
      patch.resolve({ ok: true, data: detail({ answers: { garment_type: "saree" } }) });
    });
    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument());
  });

  it("5: typing then immediately pressing Back flushes the save before navigating", async () => {
    mocks.createDesignDraft.mockResolvedValue({ ok: true, data: detail() });
    render(<QuestionnaireWizard />);
    // Reach the notes (text) step.
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const notes = await screen.findByRole("textbox", { name: "Notes" });

    mocks.updateDesignDraft.mockClear();
    fireEvent.change(notes, { target: { value: "Elegant" } });
    // Immediately press Back — the debounced text must be flushed, not dropped.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Back" }));
    });
    await waitFor(() => expect(mocks.updateDesignDraft).toHaveBeenCalled());
    const last = mocks.updateDesignDraft.mock.calls.at(-1);
    expect(last?.[1].answers.final_notes).toBe("Elegant");
  });

  it("6: a failed save prevents Continue from advancing", async () => {
    mocks.createDesignDraft.mockResolvedValue({
      ok: false,
      status: 503,
      code: "unavailable",
      message: "Could not save.",
    });
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    await screen.findByText("Could not save.");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    });
    // Still on the garment step — the failed save blocked advancing.
    expect(screen.getByRole("heading", { name: "Garment" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Notes" })).not.toBeInTheDocument();
  });

  it("9: retry after a failed answer save resends the latest answer snapshot", async () => {
    mocks.createDesignDraft
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        code: "unavailable",
        message: "Could not save.",
      })
      .mockResolvedValueOnce({ ok: true, data: detail() });
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Saree" }));
    await screen.findByText("Could not save.");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    });
    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalledTimes(2));
    expect(mocks.createDesignDraft).toHaveBeenLastCalledWith({
      questionnaire_version_id: "v1",
      answers: { garment_type: "saree" },
    });
  });

  it("10: unmounting clears the pending debounce timer (no save fires afterward)", async () => {
    mocks.createDesignDraft.mockResolvedValue({ ok: true, data: detail() });
    const view = render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const notes = await screen.findByRole("textbox", { name: "Notes" });

    mocks.updateDesignDraft.mockClear();
    fireEvent.change(notes, { target: { value: "later" } }); // schedules a 600ms debounce
    view.unmount();
    // Well past the debounce window: a timer that survived unmount would have
    // fired a save by now.
    await new Promise((resolve) => setTimeout(resolve, 700));
    expect(mocks.updateDesignDraft).not.toHaveBeenCalled();
  });
});

describe("save coordinator — inspiration step", () => {
  function completeDesign(overrides: Record<string, unknown> = {}) {
    return detail({ answers: { garment_type: "lehenga" }, ...overrides });
  }

  // Cases 7, 8, 15a and 15b covered the curated catalogue: waiting on a
  // selection save before Review, resending inspiration_asset_ids after a
  // failed one, and telling a catalogue outage apart from an empty catalogue.
  // ADR 0025 retired the catalogue, so none of those states can occur — there
  // is no selection to save and no catalogue to fetch.
  //
  // What replaces them asserts against the rendered DOM and against the save
  // client, never against a mock of the deleted `fetchCatalogue`: a mock of a
  // function no code can reach cannot be called, so an expectation on it would
  // pass no matter how wrong the component became.

  it("7: the inspiration step saves nothing of its own and reaches Review directly", async () => {
    mocks.fetchDesign.mockResolvedValue(completeDesign());
    render(<QuestionnaireWizard initialDesignId="d1" />);
    await screen.findByRole("heading", { name: "Inspiration images" });

    // There is no longer any draft field this screen writes, so Review is not
    // gated on a save — and no save is issued by arriving here at all.
    expect(mocks.updateDesignDraft).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Review" }));
    });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/design/d1/review"));
    expect(mocks.updateDesignDraft).not.toHaveBeenCalled();
  });

  it("8: the step renders the upload panel with no catalogue-shaped state", async () => {
    mocks.fetchDesign.mockResolvedValue(completeDesign());
    render(<QuestionnaireWizard initialDesignId="d1" />);
    await screen.findByRole("heading", { name: "Inspiration images" });

    expect(
      screen.queryByText(/Inspiration images are temporarily unavailable/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.queryByText(/No inspiration images are available yet/i)).not.toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: /Your own photographs/i }),
    ).toBeInTheDocument();
  });
});

// A two-required-step schema for validation-timing regressions.
const REQUIRED_TWO_STEP: QuestionnaireSchema = {
  schema_version: 1,
  key: "test",
  title: "Test",
  steps: [
    {
      id: "garment",
      title: "Garment step",
      questions: [
        {
          id: "garment_type",
          type: "single_choice",
          label: "Garment",
          required: true,
          options: [{ value: "lehenga", label: "Lehenga" }],
        },
      ],
    },
    {
      id: "silhouette_step",
      title: "Silhouette step",
      questions: [
        {
          id: "silhouette",
          type: "single_choice",
          label: "Silhouette",
          required: true,
          options: [{ value: "flared", label: "Flared" }],
        },
      ],
    },
  ],
  rules: [],
};

// One screen whose required question can be answered and still invalid.
const BOUNDED_MULTI: QuestionnaireSchema = {
  schema_version: 1,
  key: "test",
  title: "Test",
  steps: [
    {
      id: "handwork",
      title: "Handwork step",
      questions: [
        {
          id: "embellishment_styles",
          type: "multi_choice",
          label: "Embellishment",
          required: true,
          constraints: { min_items: 2, max_items: 3 },
          options: [
            { value: "zari", label: "Zari" },
            { value: "zardozi", label: "Zardozi" },
          ],
        },
      ],
    },
  ],
  rules: [],
};

describe("wizard initialisation race (synchronous refs)", () => {
  it("1: clicking an answer the instant the control appears sends one POST with version v1", async () => {
    const create = deferred<{ ok: true; data: ReturnType<typeof detail> }>();
    mocks.createDesignDraft.mockReturnValue(create.promise);

    render(<QuestionnaireWizard />);
    // Click immediately once the control appears — no artificial wait for the
    // version-sync effect (which no longer exists).
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));

    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalledTimes(1));
    expect(mocks.createDesignDraft).toHaveBeenCalledWith({
      questionnaire_version_id: "v1",
      answers: { garment_type: "lehenga" },
    });
  });

  it("2: the immediate first answer never triggers a client envelope failure", async () => {
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalled());
    // No local invalid_request / envelope-rejection message is shown.
    expect(
      screen.queryByText(/could not be prepared to save/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Could not save/i)).not.toBeInTheDocument();
  });

  it("3: the instant the first required screen appears, Continue is already unavailable", async () => {
    render(<QuestionnaireWizard />);
    // Wait only for the screen control, then try to Continue without answering.
    await screen.findByRole("radio", { name: "Lehenga" });
    const forward = screen.getByRole("button", { name: "Continue" });
    // Disabled from the first render — not enabled-then-corrected once some
    // effect catches up, which is what the synchronous refs exist to prevent.
    expect(forward).toBeDisabled();
    expect(screen.getByText("Choose an option to continue.")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(forward);
    });
    expect(screen.getByRole("heading", { name: "Garment" })).toBeInTheDocument();
    expect(mocks.createDesignDraft).not.toHaveBeenCalled();
  });

  it("4: Continue after moving to a new required step validates the NEW step, not the previous one", async () => {
    mocks.fetchActiveQuestionnaire.mockResolvedValue({
      id: "v1",
      version: 1,
      schema: REQUIRED_TWO_STEP,
    });
    mocks.createDesignDraft.mockResolvedValue({
      ok: true,
      data: detail({ answers: { garment_type: "lehenga" } }),
    });
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalled());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    });
    // Now on the silhouette screen; it is the NEW screen's required question
    // that governs, not the answered previous one.
    expect(await screen.findByRole("heading", { name: "Silhouette" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    });
    expect(screen.getByRole("heading", { name: "Silhouette" })).toBeInTheDocument();

    // Answering it releases the gate — proving the disabled state tracked the
    // current screen rather than being stuck from the previous one.
    fireEvent.click(screen.getByRole("radio", { name: "Flared" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Continue" })).not.toBeDisabled(),
    );
  });

  it("4b: an answered-but-invalid screen is still refused by the resolver, not just by the gate", async () => {
    // The disabled Continue only knows whether a required question HAS a value.
    // A bounded multi-choice can be answered and still invalid, so the derived
    // Zod resolver must remain the second line of defence.
    mocks.fetchActiveQuestionnaire.mockResolvedValue({
      id: "v1",
      version: 1,
      schema: BOUNDED_MULTI,
    });
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Zari" }));
    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalled());
    const forward = screen.getByRole("button", { name: "Continue" });
    expect(forward).not.toBeDisabled();

    await act(async () => {
      fireEvent.click(forward);
    });

    expect(await screen.findByRole("alert", { name: "There is a problem" })).toHaveTextContent(
      /Embellishment/,
    );
    expect(screen.getByRole("heading", { name: "Embellishment" })).toBeInTheDocument();
  });

  it("5: two rapid initial changes create exactly one design and persist the newest state", async () => {
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    fireEvent.click(screen.getByRole("radio", { name: "Saree" }));
    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(mocks.updateDesignDraft).toHaveBeenLastCalledWith("d1", {
        answers: { garment_type: "saree" },
      }),
    );
    expect(mocks.createDesignDraft).toHaveBeenCalledTimes(1);
  });
});
