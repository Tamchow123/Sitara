import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
      id: "detail",
      title: "Detail step",
      questions: [
        {
          id: "silhouette",
          type: "single_choice",
          label: "Silhouette",
          required: true,
          options: [
            { value: "flared_lehenga", label: "Flared" },
            { value: "classic_saree_drape", label: "Draped" },
          ],
        },
        {
          id: "dupatta_style",
          type: "single_choice",
          label: "Dupatta",
          required: false,
          options: [{ value: "head_drape", label: "Head drape" }],
        },
        {
          id: "saree_drape",
          type: "single_choice",
          label: "Saree drape",
          required: false,
          options: [{ value: "nivi_drape", label: "Nivi" }],
        },
      ],
    },
  ],
  rules: [
    {
      id: "saree_shows_drape",
      when: { question_id: "garment_type", operator: "equals", values: ["saree"] },
      then: { action: "show", question_id: "saree_drape" },
    },
    {
      id: "saree_hides_dupatta",
      when: { question_id: "garment_type", operator: "equals", values: ["saree"] },
      then: { action: "hide", question_id: "dupatta_style" },
    },
    {
      id: "non_saree_hides_drape",
      when: { question_id: "garment_type", operator: "not_in", values: ["saree"] },
      then: { action: "hide", question_id: "saree_drape" },
    },
    {
      id: "lehenga_silhouette",
      when: { question_id: "garment_type", operator: "equals", values: ["lehenga"] },
      then: { action: "restrict_options", question_id: "silhouette", values: ["flared_lehenga"] },
    },
    {
      id: "saree_silhouette",
      when: { question_id: "garment_type", operator: "equals", values: ["saree"] },
      then: {
        action: "restrict_options",
        question_id: "silhouette",
        values: ["classic_saree_drape"],
      },
    },
  ],
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
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  mocks.fetchActiveQuestionnaire.mockResolvedValue({ id: "v1", version: 1, schema: SCHEMA });
  mocks.fetchCatalogue.mockResolvedValue({ assets: [] });
  mocks.createDesignDraft.mockResolvedValue({ ok: true, data: detail() });
  mocks.updateDesignDraft.mockResolvedValue({ ok: true, data: detail() });
  mocks.validateDesignDraft.mockResolvedValue({ ok: true, data: { valid: true } });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("QuestionnaireWizard", () => {
  it("renders questions and options from the schema with accessible names", async () => {
    render(<QuestionnaireWizard />);
    expect(await screen.findByRole("heading", { name: "Garment step" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Lehenga" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Saree" })).toBeInTheDocument();
  });

  it("creates the design on the first successful save (partial autosave)", async () => {
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    await waitFor(() => expect(mocks.createDesignDraft).toHaveBeenCalledTimes(1));
    expect(mocks.createDesignDraft).toHaveBeenCalledWith({
      questionnaire_version_id: "v1",
      answers: { garment_type: "lehenga" },
    });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(mocks.replace).toHaveBeenCalledWith("/design/d1");
  });

  it("does not report success when the save fails, and keeps the value", async () => {
    mocks.createDesignDraft.mockResolvedValue({
      ok: false,
      status: 503,
      code: "unavailable",
      message: "Could not save.",
    });
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    await screen.findByText("Could not save.");
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    // The chosen value stays visible/selected.
    expect(screen.getByRole("radio", { name: "Lehenga" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("blocks advancing past a step with a missing required answer and focuses the error summary", async () => {
    render(<QuestionnaireWizard />);
    await screen.findByRole("radio", { name: "Lehenga" });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const summary = await screen.findByRole("alert", { name: "There is a problem" });
    expect(summary).toHaveTextContent(/Please review your answers/i);
    expect(summary).toHaveFocus();
    // Still on the garment step.
    expect(screen.getByRole("heading", { name: "Garment step" })).toBeInTheDocument();
  });

  it("applies show/hide rules and restricts options immediately", async () => {
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Saree" }));
    await screen.findByText("Saved");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    // On the detail step: saree_drape visible, dupatta hidden.
    expect(await screen.findByText("Saree drape")).toBeInTheDocument();
    expect(screen.queryByText("Dupatta")).not.toBeInTheDocument();
    // Silhouette restricted to the saree option only.
    expect(screen.getByRole("radio", { name: "Draped" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Flared" })).not.toBeInTheDocument();
  });

  it("clears stale answers when the controlling answer changes", async () => {
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    await screen.findByText("Saved");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(await screen.findByRole("radio", { name: "Flared" }));
    await waitFor(() => expect(mocks.updateDesignDraft).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    // Switch to saree — the lehenga silhouette must be cleared from the save.
    fireEvent.click(await screen.findByRole("radio", { name: "Saree" }));
    await waitFor(() => {
      const lastCall = mocks.updateDesignDraft.mock.calls.at(-1);
      expect(lastCall?.[1].answers).toEqual({ garment_type: "saree" });
    });
  });

  it("never persists answers to browser storage", async () => {
    render(<QuestionnaireWizard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
    await screen.findByText("Saved");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("reconstructs the wizard from persisted answers on resume", async () => {
    mocks.fetchDesign.mockResolvedValue(
      detail({ answers: { garment_type: "lehenga" } }),
    );
    render(<QuestionnaireWizard initialDesignId="d1" />);
    // Resumes on the first incomplete step (detail) with the saved answer.
    expect(await screen.findByRole("heading", { name: "Detail step" })).toBeInTheDocument();
    expect(mocks.fetchDesign).toHaveBeenCalledWith("d1");
  });

  describe("lifecycle navigation", () => {
    it("renders the wizard for a draft design", async () => {
      mocks.fetchDesign.mockResolvedValue(detail({ status: "draft" }));
      render(<QuestionnaireWizard initialDesignId="d1" />);
      expect(await screen.findByRole("heading", { name: "Garment step" })).toBeInTheDocument();
      expect(mocks.replace).not.toHaveBeenCalledWith(expect.stringContaining("/generation/"));
    });

    it("redirects to the progress route for a generating design with a coherent job", async () => {
      mocks.fetchDesign.mockResolvedValue(
        detail({
          status: "generating",
          latest_job: {
            id: "job-1",
            design_id: "d1",
            design_version_id: null,
            status: "running_image",
            error_code: null,
            created_at: "t",
            updated_at: "t",
            started_at: null,
            completed_at: null,
          },
        }),
      );
      render(<QuestionnaireWizard initialDesignId="d1" />);
      await waitFor(() =>
        expect(mocks.replace).toHaveBeenCalledWith("/design/d1/generation/job-1"),
      );
      expect(screen.queryByRole("heading", { name: "Garment step" })).not.toBeInTheDocument();
    });

    it("redirects to the result route for a generated design", async () => {
      mocks.fetchDesign.mockResolvedValue(
        detail({
          status: "generated",
          latest_job: {
            id: "job-2",
            design_id: "d1",
            design_version_id: "v-2",
            status: "succeeded",
            error_code: null,
            created_at: "t",
            updated_at: "t",
            started_at: null,
            completed_at: "t",
          },
        }),
      );
      render(<QuestionnaireWizard initialDesignId="d1" />);
      await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/design/d1/result/v-2"));
    });

    it("keeps an editable wizard for a failed design with no linked version", async () => {
      mocks.fetchDesign.mockResolvedValue(
        detail({
          status: "generation_failed",
          latest_job: {
            id: "job-3",
            design_id: "d1",
            design_version_id: null,
            status: "failed",
            error_code: "structured_generation_failed",
            created_at: "t",
            updated_at: "t",
            started_at: null,
            completed_at: "t",
          },
        }),
      );
      render(<QuestionnaireWizard initialDesignId="d1" />);
      expect(await screen.findByRole("heading", { name: "Garment step" })).toBeInTheDocument();
      expect(mocks.replace).not.toHaveBeenCalledWith(expect.stringContaining("/generation/"));
    });

    it("redirects to the failed-progress route for a failed design with a linked version", async () => {
      mocks.fetchDesign.mockResolvedValue(
        detail({
          status: "generation_failed",
          latest_job: {
            id: "job-4",
            design_id: "d1",
            design_version_id: "v-4",
            status: "failed",
            error_code: "image_ingest_failed",
            created_at: "t",
            updated_at: "t",
            started_at: null,
            completed_at: "t",
          },
        }),
      );
      render(<QuestionnaireWizard initialDesignId="d1" />);
      await waitFor(() =>
        expect(mocks.replace).toHaveBeenCalledWith("/design/d1/generation/job-4"),
      );
    });

    it("shows a controlled unavailable state for inconsistent lifecycle data, without redirecting", async () => {
      mocks.fetchDesign.mockResolvedValue(detail({ status: "generating", latest_job: null }));
      render(<QuestionnaireWizard initialDesignId="d1" />);
      expect(await screen.findByText(/temporarily unavailable/i)).toBeInTheDocument();
      expect(mocks.replace).not.toHaveBeenCalled();
      expect(mocks.push).not.toHaveBeenCalled();
    });

    it("does not loop: a single redirect call is made for a generating design", async () => {
      mocks.fetchDesign.mockResolvedValue(
        detail({
          status: "generating",
          latest_job: {
            id: "job-5",
            design_id: "d1",
            design_version_id: null,
            status: "queued",
            error_code: null,
            created_at: "t",
            updated_at: "t",
            started_at: null,
            completed_at: null,
          },
        }),
      );
      render(<QuestionnaireWizard initialDesignId="d1" />);
      await waitFor(() => expect(mocks.replace).toHaveBeenCalled());
      expect(mocks.replace).toHaveBeenCalledTimes(1);
    });
  });

  describe("inspiration catalogue", () => {
    async function goToInspirationStep() {
      fireEvent.click(await screen.findByRole("radio", { name: "Lehenga" }));
      await screen.findByText("Saved");
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
      fireEvent.click(await screen.findByRole("radio", { name: "Flared" }));
      await waitFor(() => expect(mocks.updateDesignDraft).toHaveBeenCalled());
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
      await screen.findByRole("heading", { name: "Inspiration images" });
    }

    it("loads and renders the catalogue on the final step", async () => {
      render(<QuestionnaireWizard />);
      await goToInspirationStep();
      expect(mocks.fetchCatalogue).toHaveBeenCalledTimes(1);
      expect(
        await screen.findByText(/No inspiration images are available yet/i),
      ).toBeInTheDocument();
    });

    // Regression test: the loading effect used to depend on catalogue.status,
    // state the SAME effect sets synchronously (idle -> loading). Setting
    // that state always schedules a re-render in which the dependency array
    // has changed, so React tears the effect down (cancelled = true) and
    // re-runs it BEFORE a real (non-instant) fetch has a chance to resolve.
    // The re-run's guard then sees "loading" (not "idle") and bails out
    // without starting a replacement fetch, so when the original fetch
    // finally resolves, its result is discarded by the stale cancelled flag
    // — the catalogue is stuck on "Loading inspiration images…" forever, for
    // any fetch slower than one React render (i.e. every real network call).
    it("does not get stuck loading when the fetch resolves after the effect's own re-render", async () => {
      mocks.fetchCatalogue.mockReset();
      mocks.fetchCatalogue.mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve({ assets: [] }), 20)),
      );
      render(<QuestionnaireWizard />);
      await goToInspirationStep();
      await waitFor(
        () => expect(screen.queryByText(/Loading inspiration images/i)).not.toBeInTheDocument(),
        { timeout: 2000 },
      );
      expect(
        await screen.findByText(/No inspiration images are available yet/i),
      ).toBeInTheDocument();
    });

    it("recovers from a catalogue fetch failure via Try again", async () => {
      mocks.fetchCatalogue.mockReset();
      mocks.fetchCatalogue.mockRejectedValueOnce(new Error("network"));
      mocks.fetchCatalogue.mockResolvedValueOnce({ assets: [] });
      render(<QuestionnaireWizard />);
      await goToInspirationStep();
      expect(await screen.findByText(/temporarily unavailable/i)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      await waitFor(() => expect(mocks.fetchCatalogue).toHaveBeenCalledTimes(2));
      expect(
        await screen.findByText(/No inspiration images are available yet/i),
      ).toBeInTheDocument();
    });
  });
});
