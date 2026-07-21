import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VersionComparison } from "./VersionComparison";
import type { DesignImages, DesignResult as DesignResultType } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  fetchDesignResult: vi.fn(),
  fetchDesignImageUrls: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchDesignResult: mocks.fetchDesignResult,
    fetchDesignImageUrls: mocks.fetchDesignImageUrls,
  };
});

function result(overrides: Partial<DesignResultType> = {}): DesignResultType {
  return {
    design_id: "d1",
    design_version_id: "v1",
    version_number: 1,
    title: "Original concept",
    concept_summary: "The original concept summary.",
    garment_breakdown: {
      overall_form: "form",
      garment_components: ["choli"],
      silhouette: "fitted",
      drape_or_layering: "layered",
      key_proportions: "balanced",
    },
    colour_story: { palette_summary: "ivory and gold", placement: "all over", rationale: "calm" },
    fabrics_and_texture: [{ fabric: "silk", placement: "skirt", finish_and_movement: "smooth" }],
    embellishment_plan: {
      techniques: ["zardozi"],
      density: "balanced",
      placement: ["hem"],
      motifs: ["floral"],
      restraint_notes: "restrained",
    },
    coverage_and_drape: {
      sleeves: "full",
      neckline: "modest",
      back_and_midriff: "covered",
      head_covering: "dupatta",
      dupatta_or_saree_drape: "over the head",
    },
    cultural_context: {
      regional_direction: "Pakistani",
      interpretation_notes: ["one broad direction"],
      safeguards: ["no single community claimed"],
    },
    styling_notes: ["warm jewellery"],
    construction_caveats: ["This is a concept visualisation, not a sewing pattern."],
    image_alt_text: "Original image alt text.",
    created_at: "2026-07-19T12:00:00Z",
    inspiration_acknowledgements: [],
    lineage: { kind: "initial", parent_version_id: null, refinement: null },
    is_demo: false,
    ...overrides,
  };
}

function refinedResult(overrides: Partial<DesignResultType> = {}): DesignResultType {
  return result({
    design_version_id: "v2",
    version_number: 2,
    title: "Refined concept",
    concept_summary: "The refined concept summary.",
    image_alt_text: "Refined image alt text.",
    lineage: {
      kind: "refinement",
      parent_version_id: "v1",
      refinement: { change_type: "colour_story" },
    },
    ...overrides,
  });
}

function images(overrides: Partial<DesignImages> = {}): DesignImages {
  return {
    original: {
      url: "https://minio.local/signed-original",
      download_url: "https://minio.local/signed-original-download",
      width: 1536,
      height: 2048,
    },
    thumbnail: { url: "https://minio.local/signed-thumbnail", width: 384, height: 512 },
    expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
    ...overrides,
  };
}

function renderComparison(refinedOverrides: Partial<DesignResultType> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const refined = refinedResult(refinedOverrides);
  const utils = render(
    <QueryClientProvider client={client}>
      <VersionComparison
        designId="d1"
        parentVersionId="v1"
        refined={{
          result: refined,
          images: images(),
          imagesPending: false,
          imagesFetching: false,
          imagesError: null,
          onRetryImages: vi.fn(),
        }}
      />
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("VersionComparison", () => {
  it("loads version 1 via lineage.parent_version_id and renders both headings", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderComparison();
    expect(mocks.fetchDesignResult).toHaveBeenCalledWith("d1", "v1");
    expect(await screen.findByRole("heading", { name: /original concept/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /refined concept/i })).toBeInTheDocument();
  });

  it("renders the correct image alt text for each version", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderComparison();
    expect(await screen.findByRole("img", { name: "Original image alt text." })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Refined image alt text." })).toBeInTheDocument();
  });

  it("makes the complete brief available for both versions via a disclosure control", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderComparison();
    await screen.findByRole("heading", { name: /original concept/i });
    const summaries = screen.getAllByText(/view complete brief/i);
    expect(summaries).toHaveLength(2);
    // Both details/summary controls actually expose the underlying brief.
    expect(screen.getAllByText(/Garment breakdown/i).length).toBe(2);
  });

  it("keeps the original then refined DOM order for consistent mobile stacking", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderComparison();
    // Wait for BOTH cards to be mounted before reading order — the original
    // side loads asynchronously, so a premature query could otherwise catch
    // a transient single-card DOM.
    await screen.findByRole("heading", { name: /original concept/i });
    await screen.findByRole("heading", { name: /refined concept/i });
    // DesignBrief also renders its own h3s (e.g. "Interpretation notes")
    // inside each card's collapsed detailed brief — scope to the card's own
    // "<label> — version N" heading specifically.
    const headings = screen.getAllByRole("heading", { level: 2, name: /— version \d/ });
    expect(headings[0]).toHaveTextContent(/original concept/i);
    expect(headings[1]).toHaveTextContent(/refined concept/i);
  });

  it("labels each version from its own persisted is_demo, never inferred together", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result({ is_demo: true }) });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderComparison({ is_demo: false });
    await screen.findByRole("heading", { name: /original concept/i });
    const headings = screen.getAllByRole("heading", { level: 2, name: /— version \d/ });
    expect(headings[0]).toHaveTextContent(/demo/i);
    expect(headings[1]).toHaveTextContent(/live/i);
  });

  it("displays the selected refinement category in human-readable form", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { container } = renderComparison({
      lineage: {
        kind: "refinement",
        parent_version_id: "v1",
        refinement: { change_type: "dupatta_or_saree_drape" },
      },
    });
    await screen.findByRole("heading", { name: /original concept/i });
    const disclosure = container.querySelector(".comparison-disclosure");
    expect(disclosure?.textContent).toMatch(/requested change:\s*dupatta or saree drape/i);
  });

  it("never renders a raw refinement note (not part of the fetched type at all)", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { container } = renderComparison();
    await screen.findByRole("heading", { name: /original concept/i });
    expect(container.innerHTML).not.toMatch(/refinement_request/i);
  });

  it("shows the drift disclosure near the comparison heading", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderComparison();
    expect(await screen.findByText(/new generation/i)).toBeInTheDocument();
    expect(screen.getByText(/visual drift is expected|drift/i)).toBeInTheDocument();
    expect(screen.getByText(/does not guarantee the same pose/i)).toBeInTheDocument();
  });

  it("one side's image-delivery failure does not hide the other side's brief", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({
      ok: false,
      status: 503,
      code: "design_image_delivery_unavailable",
      message: "Design images are temporarily unavailable.",
    });
    renderComparison();
    await screen.findByRole("heading", { name: /original concept/i });
    // Version 1's image failed, but its brief (and version 2's) still show.
    expect(await screen.findByText(/temporarily unavailable/i, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByText(/The original concept summary\./i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/The refined concept summary\./i).length).toBeGreaterThan(0);
    expect(screen.getByRole("img", { name: "Refined image alt text." })).toBeInTheDocument();
  });

  it("refreshes the parent version's signed URL independently of the refined side", async () => {
    vi.useFakeTimers();
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    const shortLived = images({ expires_at: new Date(Date.now() + 2000).toISOString() });
    const refreshed = images({ expires_at: new Date(Date.now() + 300_000).toISOString() });
    mocks.fetchDesignImageUrls.mockResolvedValueOnce({ ok: true, images: shortLived });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: refreshed });

    renderComparison();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.fetchDesignImageUrls).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(mocks.fetchDesignImageUrls).toHaveBeenCalledTimes(2);
  });

  it("clears both queries from the cache on unmount (gcTime: 0)", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { unmount, client } = renderComparison();
    await screen.findByRole("heading", { name: /original concept/i });
    unmount();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(client.getQueryData(["design-result", "d1", "v1"])).toBeUndefined();
    expect(client.getQueryData(["design-image", "d1", "v1"])).toBeUndefined();
  });

  it("touches no browser storage", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderComparison();
    await screen.findByRole("heading", { name: /original concept/i });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("shows a loading state for the original concept before it resolves", () => {
    mocks.fetchDesignResult.mockReturnValue(new Promise(() => {}));
    renderComparison();
    expect(screen.getByText(/loading your original concept/i)).toBeInTheDocument();
  });

  it("shows a retryable error state if the original concept cannot be loaded", async () => {
    mocks.fetchDesignResult.mockResolvedValue({
      ok: false,
      status: 503,
      code: "design_result_unavailable",
      message: "unavailable",
    });
    renderComparison();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/temporarily unavailable/i);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
