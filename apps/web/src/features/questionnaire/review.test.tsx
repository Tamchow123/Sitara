import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewSummary } from "./ReviewSummary";
import type { QuestionnaireSchema } from "./types";

const mocks = vi.hoisted(() => ({
  fetchDesign: vi.fn(),
  validateDesignDraft: vi.fn(),
  fetchPublicConfig: vi.fn(),
  startDesignGeneration: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("./api", () => ({
  fetchDesign: mocks.fetchDesign,
  validateDesignDraft: mocks.validateDesignDraft,
  fetchPublicConfig: mocks.fetchPublicConfig,
  startDesignGeneration: mocks.startDesignGeneration,
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

// The same schema plus an optional question the user did not answer.
const SCHEMA_WITH_OPTIONAL: QuestionnaireSchema = {
  ...SCHEMA,
  steps: [
    {
      ...SCHEMA.steps[0],
      questions: [
        ...SCHEMA.steps[0].questions,
        {
          id: "regional_style",
          type: "single_choice",
          label: "Regional direction",
          required: false,
          options: [{ value: "punjabi", label: "Punjabi" }],
        },
      ],
    },
  ],
};

function design(overrides: Record<string, unknown> = {}) {
  return {
    id: "d1",
    title: "My concept",
    status: "draft",
    questionnaire: { id: "v1", version: 1, schema: SCHEMA },
    answers: { garment_type: "lehenga" },
    selected_inspirations: [],
    latest_job: null,
    created_at: "t",
    updated_at: "t",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchDesign.mockResolvedValue(design());
  mocks.validateDesignDraft.mockResolvedValue({ ok: true, data: { valid: true } });
  mocks.fetchPublicConfig.mockResolvedValue({
    demo_mode: true,
    generation_enabled: true,
    max_inspiration_images: 3,
    max_refinements: 1,
  });
});

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

describe("ReviewSummary", () => {
  it("calls the server validation endpoint and renders labels resolved from the schema", async () => {
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText("Lehenga")).toBeInTheDocument();
    expect(screen.getByText("Which garment?")).toBeInTheDocument();
    expect(mocks.validateDesignDraft).toHaveBeenCalledWith("d1");
  });

  it("shows an unanswered question as left to Sitara rather than omitting it", async () => {
    mocks.fetchDesign.mockResolvedValue(
      design({ questionnaire: { id: "v1", version: 1, schema: SCHEMA_WITH_OPTIONAL } }),
    );
    render(<ReviewSummary designId="d1" />);

    // The row is present — a silently missing one would read as "never asked".
    expect(await screen.findByText("Regional direction")).toBeInTheDocument();
    expect(screen.getByText("Left to Sitara's imagination")).toBeInTheDocument();
    // ...and it can still be edited from here.
    expect(screen.getByRole("link", { name: "Edit Regional direction" })).toHaveAttribute(
      "href",
      "/design/d1?q=regional_style",
    );
  });

  it("gives each row its own Edit link, deep-linked to that question's screen", async () => {
    render(<ReviewSummary designId="d1" />);
    const edit = await screen.findByRole("link", { name: "Edit Which garment?" });
    expect(edit).toHaveAttribute("href", "/design/d1?q=garment_type");
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

  it("explains that questionnaire answers take priority when an inspiration is selected", async () => {
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
              attribution: "",
              image_url: "/api/v1/inspiration-assets/a/image/",
              thumbnail_url: "/api/v1/inspiration-assets/a/thumbnail/",
            },
          },
        ],
      }),
    );
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText(/answers always take priority/i)).toBeInTheDocument();
  });

  it("omits the priority note when no inspiration is selected", async () => {
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText("No inspiration images selected.")).toBeInTheDocument();
    expect(screen.queryByText(/answers always take priority/i)).not.toBeInTheDocument();
  });

  describe("uploaded photographs", () => {
    function upload(id: string, position: number) {
      return {
        id,
        position,
        width: 900,
        height: 1200,
        rights_acknowledged_at: "2026-07-29T00:00:00Z",
        created_at: "2026-07-29T00:00:00Z",
      };
    }

    it("shows an upload with no preset selected, rather than 'none selected'", async () => {
      // An upload IS an inspiration image. Reporting none while one is about to
      // be sent to the provider would be the review screen lying at the last
      // point the user can still change their mind.
      mocks.fetchDesign.mockResolvedValue(
        design({ inspiration_uploads: [upload("u1", 1)] }),
      );
      render(<ReviewSummary designId="d1" />);

      expect(await screen.findByText("Your own photographs")).toBeInTheDocument();
      expect(screen.queryByText("No inspiration images selected.")).not.toBeInTheDocument();
      expect(screen.getByText(/answers always take priority/i)).toHaveTextContent(
        /sent to the external ai image provider/i,
      );
    });

    it("renders each upload from the ownership-checked endpoint with its own name", async () => {
      mocks.fetchDesign.mockResolvedValue(
        design({ inspiration_uploads: [upload("u1", 1), upload("u2", 2)] }),
      );
      render(<ReviewSummary designId="d1" />);

      const first = await screen.findByAltText(/uploaded inspiration image 1/i);
      expect(first).toHaveAttribute("src", "/api/v1/designs/d1/inspiration-uploads/u1/image/");
      expect(screen.getByAltText(/uploaded inspiration image 2/i)).toHaveAttribute(
        "src",
        "/api/v1/designs/d1/inspiration-uploads/u2/image/",
      );
      // Relative and ownership-checked: never an object-store URL, never the
      // image optimiser (which would cache bytes that need a per-request check).
      for (const image of screen.getAllByRole("img")) {
        expect(image.getAttribute("src")).not.toMatch(/https?:|_next\/image|X-Amz|amazonaws/i);
      }
    });

    it("keeps the section honest for a design with no images at all", async () => {
      mocks.fetchDesign.mockResolvedValue(design({ inspiration_uploads: [] }));
      render(<ReviewSummary designId="d1" />);
      expect(await screen.findByText("No inspiration images selected.")).toBeInTheDocument();
      expect(screen.queryByText("Your own photographs")).not.toBeInTheDocument();
    });
  });

  describe("Generate my concept", () => {
    it("enables the button when valid and generation is enabled", async () => {
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      expect(button).toBeEnabled();
    });

    it("keeps the button disabled with accurate copy when generation is disabled", async () => {
      mocks.fetchPublicConfig.mockResolvedValue({
        demo_mode: true,
        generation_enabled: false,
        max_inspiration_images: 3,
        max_refinements: 1,
      });
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      expect(button).toBeDisabled();
      const note = screen.getByText(/not currently available/i);
      expect(note.textContent).not.toMatch(/demo/i);
      expect(note.textContent).not.toMatch(/key/i);
    });

    it("shows the demo disclosure and associates it with the submit button when demo_mode is true", async () => {
      mocks.fetchPublicConfig.mockResolvedValue({
        demo_mode: true,
        generation_enabled: true,
        generation_mode: "demo",
        max_inspiration_images: 3,
        max_refinements: 1,
      });
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      const disclosure = screen.getByRole("note", { name: /demo disclosure/i });
      expect(disclosure).toHaveTextContent(/deterministic design brief/i);
      expect(disclosure).toHaveTextContent(/curated pack/i);
      expect(button.getAttribute("aria-describedby")).toContain("demo-disclosure");
    });

    it("does not show the demo disclosure when demo_mode is false", async () => {
      mocks.fetchPublicConfig.mockResolvedValue({
        demo_mode: false,
        generation_enabled: true,
        generation_mode: "live",
        max_inspiration_images: 3,
        max_refinements: 1,
      });
      render(<ReviewSummary designId="d1" />);
      await screen.findByRole("button", { name: /Generate my concept/i });
      expect(screen.queryByRole("note", { name: /demo disclosure/i })).not.toBeInTheDocument();
    });

    it("shows a demo-specific unavailable message when the demo asset pack is not ready", async () => {
      mocks.fetchPublicConfig.mockResolvedValue({
        demo_mode: true,
        generation_enabled: false,
        generation_mode: "unavailable",
        max_inspiration_images: 3,
        max_refinements: 1,
      });
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      expect(button).toBeDisabled();
      const note = screen.getByText(/visual library is not ready/i);
      expect(note.textContent).not.toMatch(/key/i);
      expect(note.textContent).not.toMatch(/storage/i);
      expect(note.textContent).not.toMatch(/manifest/i);
    });

    it("keeps the button disabled for an invalid design even when generation is enabled", async () => {
      mocks.validateDesignDraft.mockResolvedValue({
        ok: false,
        status: 400,
        code: "validation_failed",
        message: "bad",
        fields: {},
      });
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      expect(button).toBeDisabled();
    });

    it("does not enable the button when validation is unavailable", async () => {
      mocks.validateDesignDraft.mockResolvedValue({
        ok: false,
        status: 0,
        code: "unavailable",
        message: "unavailable",
      });
      render(<ReviewSummary designId="d1" />);
      await screen.findByText(/Review temporarily unavailable/i);
      expect(screen.queryByRole("button", { name: /Generate my concept/i })).not.toBeInTheDocument();
    });

    it("a double click submits exactly once", async () => {
      let resolveGenerate: (value: unknown) => void = () => {};
      mocks.startDesignGeneration.mockReturnValue(
        new Promise((resolve) => {
          resolveGenerate = resolve;
        }),
      );
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      fireEvent.click(button);
      fireEvent.click(button);
      resolveGenerate({ ok: true, data: { job: { id: "job-1" } } });
      await vi.waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/design/d1/generation/job-1"));
      expect(mocks.startDesignGeneration).toHaveBeenCalledTimes(1);
    });

    it("confirmed success routes to the job", async () => {
      mocks.startDesignGeneration.mockResolvedValue({ ok: true, data: { job: { id: "job-9" } } });
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      fireEvent.click(button);
      await vi.waitFor(() =>
        expect(mocks.replace).toHaveBeenCalledWith("/design/d1/generation/job-9"),
      );
    });

    it("retry after a transport failure reuses the exact same idempotency key", async () => {
      mocks.startDesignGeneration.mockResolvedValueOnce({
        ok: false,
        status: 0,
        code: "unavailable",
        message: "The service could not be reached.",
      });
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      fireEvent.click(button);
      await screen.findByText(/The service could not be reached/i);

      mocks.startDesignGeneration.mockResolvedValueOnce({
        ok: true,
        data: { job: { id: "job-1" } },
      });
      fireEvent.click(screen.getByRole("button", { name: /Try again/i }));
      await vi.waitFor(() => expect(mocks.startDesignGeneration).toHaveBeenCalledTimes(2));

      const firstKey = mocks.startDesignGeneration.mock.calls[0][1];
      const secondKey = mocks.startDesignGeneration.mock.calls[1][1];
      expect(secondKey).toBe(firstKey);
    });

    it.each([
      ["live_generation_disabled", /currently turned off/i],
      ["generation_limit_reached", /reached the limit/i],
      ["live_generation_budget_exhausted", /daily limit/i],
    ])(
      "offers no retry control for %s, which an immediate retry cannot clear",
      async (code, expected) => {
        mocks.startDesignGeneration.mockResolvedValue({
          ok: false,
          status: 429,
          code,
          message: "unused — the page substitutes its own copy",
        });
        render(<ReviewSummary designId="d1" />);
        fireEvent.click(await screen.findByRole("button", { name: /Generate my concept/i }));
        expect(await screen.findByText(expected)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Try again/i })).not.toBeInTheDocument();
      },
    );

    it("still offers a retry for a transient admission outage", async () => {
      mocks.startDesignGeneration.mockResolvedValue({
        ok: false,
        status: 503,
        code: "queue_unavailable",
        message: "unused — the page substitutes its own copy",
      });
      render(<ReviewSummary designId="d1" />);
      fireEvent.click(await screen.findByRole("button", { name: /Generate my concept/i }));
      await screen.findByText(/queue is temporarily unavailable/i);
      expect(screen.getByRole("button", { name: /Try again/i })).toBeInTheDocument();
    });

    it("an in-progress conflict uses latest_job to resume the progress route", async () => {
      mocks.startDesignGeneration.mockResolvedValue({
        ok: false,
        status: 409,
        code: "generation_in_progress",
        message: "A generation job is already in progress for this design.",
      });
      mocks.fetchDesign.mockResolvedValueOnce(design()).mockResolvedValueOnce(
        design({
          status: "generating",
          latest_job: {
            id: "job-resume",
            design_id: "d1",
            design_version_id: null,
            status: "running_text",
            error_code: null,
            created_at: "t",
            updated_at: "t",
            started_at: null,
            completed_at: null,
          },
        }),
      );
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      fireEvent.click(button);
      await vi.waitFor(() =>
        expect(mocks.replace).toHaveBeenCalledWith("/design/d1/generation/job-resume"),
      );
    });

    it("a generated design uses latest_job's version to reach the results route", async () => {
      mocks.startDesignGeneration.mockResolvedValue({
        ok: false,
        status: 409,
        code: "design_already_generated",
        message: "This design has already been generated.",
      });
      mocks.fetchDesign.mockResolvedValueOnce(design()).mockResolvedValueOnce(
        design({
          status: "generated",
          latest_job: {
            id: "job-done",
            design_id: "d1",
            design_version_id: "v-done",
            status: "succeeded",
            error_code: null,
            created_at: "t",
            updated_at: "t",
            started_at: null,
            completed_at: "t",
          },
        }),
      );
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      fireEvent.click(button);
      await vi.waitFor(() =>
        expect(mocks.replace).toHaveBeenCalledWith("/design/d1/result/v-done"),
      );
    });

    it("touches no local/session storage while starting generation", async () => {
      // No IndexedDB assertion alongside these: nothing in this component or
      // its dependency chain (lib/api.ts, TanStack Query's memory-only
      // client) ever references indexedDB, and jsdom has no real IndexedDB
      // implementation to assert against without an added polyfill.
      mocks.startDesignGeneration.mockResolvedValue({ ok: true, data: { job: { id: "job-1" } } });
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      fireEvent.click(button);
      await vi.waitFor(() => expect(mocks.replace).toHaveBeenCalled());
      expect(localStorage.length).toBe(0);
      expect(sessionStorage.length).toBe(0);
    });
  });

  describe("lifecycle redirects", () => {
    it("redirects to the progress route when the design is already generating", async () => {
      mocks.fetchDesign.mockResolvedValue(
        design({
          status: "generating",
          latest_job: {
            id: "job-x",
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
      render(<ReviewSummary designId="d1" />);
      await vi.waitFor(() =>
        expect(mocks.replace).toHaveBeenCalledWith("/design/d1/generation/job-x"),
      );
      expect(screen.queryByRole("button", { name: /Generate my concept/i })).not.toBeInTheDocument();
    });

    it("redirects to the result route when the design is already generated", async () => {
      mocks.fetchDesign.mockResolvedValue(
        design({
          status: "generated",
          latest_job: {
            id: "job-y",
            design_id: "d1",
            design_version_id: "v-y",
            status: "succeeded",
            error_code: null,
            created_at: "t",
            updated_at: "t",
            started_at: null,
            completed_at: "t",
          },
        }),
      );
      render(<ReviewSummary designId="d1" />);
      await vi.waitFor(() =>
        expect(mocks.replace).toHaveBeenCalledWith("/design/d1/result/v-y"),
      );
    });
  });
});
