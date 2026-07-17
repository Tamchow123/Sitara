import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewSummary } from "./ReviewSummary";
import type { QuestionnaireSchema } from "./types";

const mocks = vi.hoisted(() => ({
  fetchDesign: vi.fn(),
  validateDesignDraft: vi.fn(),
}));

vi.mock("./api", () => ({
  fetchDesign: mocks.fetchDesign,
  validateDesignDraft: mocks.validateDesignDraft,
}));

const SCHEMA: QuestionnaireSchema = {
  schema_version: 1,
  key: "test",
  title: "Test",
  steps: [
    {
      id: "garment",
      title: "Garment",
      questions: [
        {
          id: "garment_type",
          type: "single_choice",
          label: "Which garment?",
          required: true,
          options: [
            { value: "lehenga", label: "Lehenga" },
            { value: "saree", label: "Saree" },
          ],
        },
      ],
    },
  ],
  rules: [],
};

function design(overrides: Record<string, unknown> = {}) {
  return {
    id: "d1",
    title: "My concept",
    status: "draft",
    questionnaire: { id: "v1", version: 1, schema: SCHEMA },
    answers: { garment_type: "lehenga" },
    selected_inspirations: [],
    created_at: "t",
    updated_at: "t",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchDesign.mockResolvedValue(design());
  mocks.validateDesignDraft.mockResolvedValue({ ok: true, data: { valid: true } });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ReviewSummary", () => {
  it("calls the server validation endpoint and renders labels resolved from the schema", async () => {
    render(<ReviewSummary designId="d1" />);
    // The stored value "lehenga" is shown by its schema LABEL, not the raw id.
    expect(await screen.findByText("Lehenga")).toBeInTheDocument();
    expect(screen.getByText("Which garment?")).toBeInTheDocument();
    expect(mocks.validateDesignDraft).toHaveBeenCalledWith("d1");
  });

  it("disables the Generate button", async () => {
    render(<ReviewSummary designId="d1" />);
    const button = await screen.findByRole("button", { name: /Generate my concept/i });
    expect(button).toBeDisabled();
  });

  it("16a: an HTTP 400 routes the user back to complete the incomplete draft", async () => {
    mocks.validateDesignDraft.mockResolvedValue({
      ok: false,
      status: 400,
      code: "validation_failed",
      message: "bad",
      fields: { silhouette: ["This question is required."] },
    });
    render(<ReviewSummary designId="d1" />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/still need attention/i);
    const back = screen.getByRole("link", { name: /return to the questionnaire/i });
    expect(back).toHaveAttribute("href", "/design/d1");
  });

  it("16b: a validation transport failure shows a distinct unavailable state, not incomplete", async () => {
    mocks.validateDesignDraft.mockResolvedValue({
      ok: false,
      status: 0,
      code: "unavailable",
      message: "The service could not be reached.",
    });
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText(/Review temporarily unavailable/i)).toBeInTheDocument();
    // Never tell the user their answers are incomplete when validation never ran.
    expect(screen.queryByText(/still need attention/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Try again/i })).toBeInTheDocument();
  });

  it("16c: a 5xx during validation is unavailable, not incomplete", async () => {
    mocks.validateDesignDraft.mockResolvedValue({
      ok: false,
      status: 503,
      code: "unavailable",
      message: "Temporarily unavailable.",
    });
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText(/Review temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/still need attention/i)).not.toBeInTheDocument();
  });

  it("shows attribution for a selected inspiration", async () => {
    mocks.fetchDesign.mockResolvedValue(
      design({
        selected_inspirations: [
          {
            id: "a",
            position: 1,
            available: true,
            asset: {
              id: "a",
              title: "Emerald look",
              alt_text: "Alt",
              garment_type: "lehenga",
              cultural_context: "",
              attribution: "Photo by Studio A",
              image_url: "/api/v1/inspiration-assets/a/image/",
              thumbnail_url: "/api/v1/inspiration-assets/a/thumbnail/",
            },
          },
        ],
      }),
    );
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText("Photo by Studio A")).toBeInTheDocument();
  });

  it("renders an unavailable selection as a neutral placeholder", async () => {
    mocks.fetchDesign.mockResolvedValue(
      design({
        selected_inspirations: [{ id: "gone", position: 1, available: false, asset: null }],
      }),
    );
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText(/no longer available/i)).toBeInTheDocument();
  });
});
