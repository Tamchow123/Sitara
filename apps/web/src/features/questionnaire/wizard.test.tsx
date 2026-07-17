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
});
