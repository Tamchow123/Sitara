import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewSummary } from "./ReviewSummary";
import type { QuestionnaireSchema } from "./types";
import { axeViolations } from "@/test-utils/axe";

const mocks = vi.hoisted(() => ({
  fetchDesign: vi.fn(),
  validateDesignDraft: vi.fn(),
  fetchPublicConfig: vi.fn(),
  startDesignGeneration: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  refreshUser: vi.fn(),
  // Phase 21: the screen's final control depends on whether there is an account,
  // so every test now has an authentication status. It defaults to authenticated
  // in beforeEach so the pre-existing generate tests describe the same journey
  // they always did.
  authStatus: { current: "authenticated" as string },
  pathname: { current: "/design/d1/review" as string | null },
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
  usePathname: () => mocks.pathname.current,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ status: mocks.authStatus.current, refreshUser: mocks.refreshUser }),
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
  mocks.authStatus.current = "authenticated";
  mocks.pathname.current = "/design/d1/review";
  mocks.fetchDesign.mockResolvedValue(design());
  mocks.validateDesignDraft.mockResolvedValue({ ok: true, data: { valid: true } });
  // The exact payload the running stack returns under DEMO_MODE=true. The
  // earlier fixture paired demo_mode: true with generation_enabled: true,
  // which the backend never produces — and that impossible combination is
  // what hid the disabled-in-demo-mode bug from every test in this file.
  mocks.fetchPublicConfig.mockResolvedValue({
    demo_mode: true,
    generation_enabled: false,
    generation_mode: "demo",
    max_inspiration_images: 3,
    max_refinements: 3,
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

  it("renders a historical curated selection as a neutral placeholder", async () => {
    // ADR 0025 retired the catalogue: the payload carries no asset object, no
    // title and no attribution, so the only honest thing to render for an old
    // design that still holds a selection is that it has gone.
    mocks.fetchDesign.mockResolvedValue(
      design({ selected_inspirations: [{ id: "gone", position: 1, available: false }] }),
    );
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText(/no longer available/i)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("explains that questionnaire answers take priority when a reference is attached", async () => {
    mocks.fetchDesign.mockResolvedValue(
      design({
        inspiration_uploads: [
          {
            id: "u1",
            position: 1,
            width: 900,
            height: 1200,
            rights_acknowledged_at: "2026-07-29T00:00:00Z",
            created_at: "2026-07-29T00:00:00Z",
          },
        ],
      }),
    );
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText(/answers always take priority/i)).toBeInTheDocument();
  });

  it("omits the priority note when no inspiration is selected", async () => {
    render(<ReviewSummary designId="d1" />);
    expect(await screen.findByText("No photographs added.")).toBeInTheDocument();
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
      expect(screen.queryByText("No photographs added.")).not.toBeInTheDocument();
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
      expect(await screen.findByText("No photographs added.")).toBeInTheDocument();
      expect(screen.queryByText("Your own photographs")).not.toBeInTheDocument();
    });
  });

  describe("Generate my concept", () => {
    it("enables the button in demo mode, which the backend admits without the live flag", async () => {
      // Regression guard for the bug this fixture change exposed: demo mode
      // reports generation_enabled: false (it means the LIVE capability), so
      // reading that flag directly disabled demo generation for everyone.
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      expect(button).toBeEnabled();
      expect(screen.getByText(/ready to generate your concept/i)).toBeInTheDocument();
    });

    it("enables the button for live generation when the operator has turned it on", async () => {
      mocks.fetchPublicConfig.mockResolvedValue({
        demo_mode: false,
        generation_enabled: true,
        generation_mode: "live",
        max_inspiration_images: 3,
        max_refinements: 3,
      });
      render(<ReviewSummary designId="d1" />);
      expect(await screen.findByRole("button", { name: /Generate my concept/i })).toBeEnabled();
    });

    it("keeps the button disabled with accurate copy when generation is disabled", async () => {
      // Live mode with the capability flag off: the one case where
      // generation_enabled: false genuinely means "do not offer it".
      mocks.fetchPublicConfig.mockResolvedValue({
        demo_mode: false,
        generation_enabled: false,
        generation_mode: "unavailable",
        max_inspiration_images: 3,
        max_refinements: 3,
      });
      render(<ReviewSummary designId="d1" />);
      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      expect(button).toBeDisabled();
      const note = screen.getByText(/not currently available/i);
      expect(note.textContent).not.toMatch(/demo/i);
      expect(note.textContent).not.toMatch(/key/i);
    });

    it("shows the demo disclosure and associates it with the submit button when demo_mode is true", async () => {
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
        max_refinements: 3,
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
        max_refinements: 3,
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

  // Phase 21 / ADR 0023. The whole questionnaire stays open to an anonymous
  // visitor; the one action that produces a concept does not.
  describe("an account is required to generate", () => {
    it("offers sign-in instead of the generate button when there is no account", async () => {
      mocks.authStatus.current = "anonymous";
      render(<ReviewSummary designId="d1" />);

      const signIn = await screen.findByRole("link", { name: /sign in to generate/i });
      expect(signIn).toHaveAttribute("href", "/login?next=%2Fdesign%2Fd1%2Freview");
      expect(screen.queryByRole("button", { name: /Generate my concept/i })).not.toBeInTheDocument();
    });

    it("offers registration to the same destination", async () => {
      mocks.authStatus.current = "anonymous";
      render(<ReviewSummary designId="d1" />);

      const create = await screen.findByRole("link", { name: /create an account/i });
      expect(create).toHaveAttribute("href", "/register?next=%2Fdesign%2Fd1%2Freview");
    });

    it("promises the answers are kept, and still shows every one of them", async () => {
      // The reassurance is only worth printing if it is true on screen: the
      // review is fully rendered behind the sign-in prompt, not replaced by it.
      mocks.authStatus.current = "anonymous";
      render(<ReviewSummary designId="d1" />);

      expect(await screen.findByText(/nothing you have entered is lost/i)).toBeInTheDocument();
      expect(screen.getByText("Lehenga")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Edit Which garment?" })).toBeInTheDocument();
    });

    it("never starts a generation for an anonymous visitor", async () => {
      mocks.authStatus.current = "anonymous";
      render(<ReviewSummary designId="d1" />);
      await screen.findByRole("link", { name: /sign in to generate/i });
      expect(mocks.startDesignGeneration).not.toHaveBeenCalled();
    });

    it("sends the visitor back to a safe path even if the route is hostile", async () => {
      // pathname is not user input today, but it is the value that becomes a
      // ?next= this application prints itself — so it goes through safeNextPath
      // on the way OUT as well as on the way back in.
      mocks.authStatus.current = "anonymous";
      mocks.pathname.current = "//evil.example/phish";
      render(<ReviewSummary designId="d1" />);

      expect(await screen.findByRole("link", { name: /sign in to generate/i })).toHaveAttribute(
        "href",
        "/login?next=%2Faccount",
      );
    });

    it("falls back to this design's own review route when there is no pathname", async () => {
      mocks.authStatus.current = "anonymous";
      mocks.pathname.current = null;
      render(<ReviewSummary designId="d1" />);

      expect(await screen.findByRole("link", { name: /sign in to generate/i })).toHaveAttribute(
        "href",
        "/login?next=%2Fdesign%2Fd1%2Freview",
      );
    });

    it("waits for a deliberate press while the account status is still unknown", async () => {
      // "loading" is not "anonymous": showing the sign-in link here would send a
      // signed-in user to a login page they do not need.
      mocks.authStatus.current = "loading";
      render(<ReviewSummary designId="d1" />);

      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      expect(button).toBeDisabled();
      expect(screen.getByText(/checking your account/i)).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: /sign in to generate/i })).not.toBeInTheDocument();
    });

    it("still offers generation when the account check itself failed", async () => {
      // A failed /auth/me read is not evidence of being signed out, and it must
      // not stand between a signed-in user and their concept. The endpoint is the
      // authority and will refuse if they really are anonymous.
      mocks.authStatus.current = "unavailable";
      mocks.startDesignGeneration.mockResolvedValue({ ok: true, data: { job: { id: "job-1" } } });
      render(<ReviewSummary designId="d1" />);

      fireEvent.click(await screen.findByRole("button", { name: /Generate my concept/i }));
      await vi.waitFor(() =>
        expect(mocks.replace).toHaveBeenCalledWith("/design/d1/generation/job-1"),
      );
    });

    it("turns a server refusal into the same sign-in routing, not a failure", async () => {
      // The session expired while the review was open. The client believed it was
      // signed in, so the up-front check cannot catch this — only the response can.
      mocks.startDesignGeneration.mockResolvedValue({
        ok: false,
        status: 401,
        code: "authentication_required",
        message: "unused — the page substitutes its own copy",
      });
      render(<ReviewSummary designId="d1" />);
      fireEvent.click(await screen.findByRole("button", { name: /Generate my concept/i }));

      const signIn = await screen.findByRole("link", { name: /sign in to generate/i });
      expect(signIn).toHaveAttribute("href", "/login?next=%2Fdesign%2Fd1%2Freview");
      // Not an apology, and not a "Try again" that could only fail again.
      expect(screen.queryByText(/could not start your generation/i)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Try again/i })).not.toBeInTheDocument();
      // Announced politely, and it says the answers survived — the one thing a
      // person whose session just expired mid-journey needs to be told.
      const announcement = screen.getByRole("status");
      expect(announcement).toHaveTextContent(/your answers are saved/i);
      expect(announcement).toHaveTextContent(/sign in or create an account/i);
    });

    it("re-reads the account after a server refusal so the shell stops showing a stale one", async () => {
      mocks.startDesignGeneration.mockResolvedValue({
        ok: false,
        status: 401,
        code: "authentication_required",
        message: "Sign in or create an account to generate your concept.",
      });
      render(<ReviewSummary designId="d1" />);
      fireEvent.click(await screen.findByRole("button", { name: /Generate my concept/i }));

      // Settle on the rendered outcome first, then assert the side effect — the
      // other way round leaves React re-rendering after the assertion.
      await screen.findByRole("link", { name: /sign in to generate/i });
      expect(mocks.refreshUser).toHaveBeenCalled();
    });

    it("shows an enabled button and waits, on a fresh mount with an account", async () => {
      // Coming back from sign-in is a fresh mount of this screen with an account
      // present. It must offer the button and WAIT for a press (assumption Q4).
      // Deliberately named for what it renders rather than for the journey it
      // stands in for: starting from "authenticated" cannot by itself prove that
      // nothing fires on an authentication CHANGE — the test below does that.
      mocks.authStatus.current = "authenticated";
      render(<ReviewSummary designId="d1" />);

      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      expect(button).toBeEnabled();
      expect(mocks.startDesignGeneration).not.toHaveBeenCalled();

      mocks.startDesignGeneration.mockResolvedValue({ ok: true, data: { job: { id: "job-2" } } });
      fireEvent.click(button);
      await vi.waitFor(() => expect(mocks.startDesignGeneration).toHaveBeenCalledTimes(1));
    });

    it("does not fire a generation when an account appears while the screen is open", async () => {
      // The case a fresh authenticated mount cannot catch: signing in elsewhere
      // (another tab, or a client-side navigation that keeps this tree mounted)
      // flips the status under a screen that is already showing. If anyone ever
      // adds an effect keyed on authStatus, this is the test that fails.
      mocks.authStatus.current = "anonymous";
      const { rerender } = render(<ReviewSummary designId="d1" />);
      await screen.findByRole("link", { name: /sign in to generate/i });

      mocks.authStatus.current = "authenticated";
      rerender(<ReviewSummary designId="d1" />);

      const button = await screen.findByRole("button", { name: /Generate my concept/i });
      expect(button).toBeEnabled();
      expect(mocks.startDesignGeneration).not.toHaveBeenCalled();
    });

    it("has no axe violations on the anonymous review", async () => {
      mocks.authStatus.current = "anonymous";
      const { container } = render(<ReviewSummary designId="d1" />);
      await screen.findByRole("link", { name: /sign in to generate/i });
      expect(await axeViolations(container)).toHaveNoViolations();
    });

    it("writes nothing to browser storage while routing to sign-in", async () => {
      mocks.authStatus.current = "anonymous";
      render(<ReviewSummary designId="d1" />);
      const signIn = await screen.findByRole("link", { name: /sign in to generate/i });
      // The press as well as the render: stashing the design or the return path
      // in storage on the way out is exactly the shortcut this asserts against.
      fireEvent.click(signIn);
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

  describe("accessibility", () => {
    it("has no axe violations on the ready-to-generate review", async () => {
      const { container } = render(<ReviewSummary designId="d1" />);
      await screen.findByRole("button", { name: /Generate my concept/i });
      expect(await axeViolations(container)).toHaveNoViolations();
    });

    it("has no axe violations when the draft is incomplete and the error summary is shown", async () => {
      mocks.validateDesignDraft.mockResolvedValue({
        ok: false,
        status: 400,
        code: "validation_failed",
        message: "bad",
        fields: { garment_type: ["This question needs an answer."] },
      });
      const { container } = render(<ReviewSummary designId="d1" />);
      await screen.findByText(/still need attention/i);
      expect(await axeViolations(container)).toHaveNoViolations();
    });
  });
});
