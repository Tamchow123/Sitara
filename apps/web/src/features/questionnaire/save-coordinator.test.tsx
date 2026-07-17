import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QuestionnaireWizard } from "./QuestionnaireWizard";
import type { QuestionnaireSchema } from "./types";

const mocks = vi.hoisted(() => ({
  fetchActiveQuestionnaire: vi.fn(),
  fetchCatalogue: vi.fn(),
  fetchDesign: vi.fn(),
  createDesignDraft: vi.fn(),
  updateDesignDraft: vi.fn(),
  validateDesignDraft: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("./api", () => ({
  fetchActiveQuestionnaire: mocks.fetchActiveQuestionnaire,
  fetchCatalogue: mocks.fetchCatalogue,
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
  mocks.fetchCatalogue.mockResolvedValue({ assets: [] });
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
    await flushMicrotasks();

    // Single-flight creation: one POST despite two changes.
    expect(mocks.createDesignDraft).toHaveBeenCalledTimes(1);
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
    const notes = await screen.findByLabelText("Notes");

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
    expect(screen.getByRole("heading", { name: "Garment step" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Notes step" })).not.toBeInTheDocument();
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
    const notes = await screen.findByLabelText("Notes");

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
  const ASSET = {
    id: "a1",
    title: "Look",
    alt_text: "Alt",
    garment_type: "lehenga",
    cultural_context: "",
    attribution: "Studio A",
    image_url: "/api/v1/inspiration-assets/a1/image/",
    thumbnail_url: "/api/v1/inspiration-assets/a1/thumbnail/",
  };

  beforeEach(() => {
    mocks.fetchCatalogue.mockResolvedValue({ assets: [ASSET] });
  });

  it("7: selecting an inspiration then pressing Review waits for the selection save", async () => {
    mocks.fetchDesign.mockResolvedValue(completeDesign());
    const patch = deferred<{ ok: true; data: ReturnType<typeof detail> }>();
    mocks.updateDesignDraft.mockReturnValue(patch.promise);

    render(<QuestionnaireWizard initialDesignId="d1" />);
    fireEvent.click((await screen.findByText("Look")).closest("button") as HTMLElement);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Review" }));
    });
    // The selection save is still in flight → Review has NOT navigated yet.
    expect(mocks.push).not.toHaveBeenCalled();

    await act(async () => {
      patch.resolve({
        ok: true,
        data: completeDesign({
          selected_inspirations: [{ id: "a1", position: 1, available: true, asset: ASSET }],
        }),
      });
    });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/design/d1/review"));
  });

  it("8: retry after a failed inspiration save resends inspiration_asset_ids", async () => {
    mocks.fetchDesign.mockResolvedValue(completeDesign());
    mocks.updateDesignDraft
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        code: "unavailable",
        message: "Could not save.",
      })
      .mockResolvedValueOnce({
        ok: true,
        data: completeDesign({
          selected_inspirations: [{ id: "a1", position: 1, available: true, asset: ASSET }],
        }),
      });

    render(<QuestionnaireWizard initialDesignId="d1" />);
    fireEvent.click((await screen.findByText("Look")).closest("button") as HTMLElement);
    await screen.findByText("Could not save.");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    });
    await waitFor(() => expect(mocks.updateDesignDraft).toHaveBeenCalledTimes(2));
    expect(mocks.updateDesignDraft).toHaveBeenLastCalledWith("d1", {
      inspiration_asset_ids: ["a1"],
    });
  });

  it("15a: a catalogue outage renders an unavailable state with Retry", async () => {
    mocks.fetchDesign.mockResolvedValue(completeDesign());
    // First load fails (outage), the retry succeeds.
    mocks.fetchCatalogue
      .mockRejectedValueOnce(new Error("catalogue_unavailable"))
      .mockResolvedValue({ assets: [ASSET] });
    render(<QuestionnaireWizard initialDesignId="d1" />);
    expect(
      await screen.findByText(/Inspiration images are temporarily unavailable/i),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    expect(await screen.findByText("Look")).toBeInTheDocument();
  });

  it("15b: a legitimately empty catalogue is a valid empty state, not an outage", async () => {
    mocks.fetchDesign.mockResolvedValue(completeDesign());
    mocks.fetchCatalogue.mockResolvedValue({ assets: [] });
    render(<QuestionnaireWizard initialDesignId="d1" />);
    expect(
      await screen.findByText(/No inspiration images are available yet/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Inspiration images are temporarily unavailable/i),
    ).not.toBeInTheDocument();
  });
});
