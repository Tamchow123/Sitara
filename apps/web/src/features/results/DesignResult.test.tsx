import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DesignResult } from "./DesignResult";
import type { DesignImages, DesignResult as DesignResultType } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  fetchDesignResult: vi.fn(),
  fetchDesignImageUrls: vi.fn(),
  // Captures every useQuery(...) options object this test file's render
  // passes through, keyed by call order, so assertions can find the image
  // query specifically by its queryKey.
  capturedQueries: [] as Record<string, unknown>[],
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchDesignResult: mocks.fetchDesignResult,
    fetchDesignImageUrls: mocks.fetchDesignImageUrls,
  };
});

vi.mock("@tanstack/react-query", async () => {
  const actual =
    await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query");
  return {
    ...actual,
    useQuery: (options: Record<string, unknown>, ...rest: unknown[]) => {
      mocks.capturedQueries.push(options);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return (actual.useQuery as any)(options, ...rest);
    },
  };
});

function findCapturedQuery(keyPrefix: string) {
  return mocks.capturedQueries
    .filter((o) => Array.isArray(o.queryKey) && (o.queryKey as unknown[])[0] === keyPrefix)
    .at(-1);
}

function result(overrides: Partial<DesignResultType> = {}): DesignResultType {
  return {
    design_id: "d1",
    design_version_id: "v1",
    version_number: 1,
    title: "Ivory and gold flared lehenga",
    concept_summary: "A concept summary describing the overall look.",
    garment_breakdown: {
      overall_form: "A fitted choli with a full flared skirt.",
      garment_components: ["Choli", "Lehenga skirt", "Dupatta"],
      silhouette: "Fitted bodice, wide flare.",
      drape_or_layering: "Dupatta over the head.",
      key_proportions: "Close bodice, defined waist.",
    },
    colour_story: {
      palette_summary: "Ivory with gold accents.",
      placement: "Ivory base, gold embroidery.",
      rationale: "Calm and bridal.",
    },
    fabrics_and_texture: [
      { fabric: "Silk", placement: "Skirt", finish_and_movement: "Smooth drape." },
      { fabric: "Organza", placement: "Dupatta", finish_and_movement: "Sheer float." },
    ],
    embellishment_plan: {
      techniques: ["Zardozi", "Dabka"],
      density: "Balanced.",
      placement: ["Hem", "Bodice"],
      motifs: ["Floral"],
      restraint_notes: "Open ground between motifs.",
    },
    coverage_and_drape: {
      sleeves: "Full length.",
      neckline: "Modest higher neckline.",
      back_and_midriff: "Covered.",
      head_covering: "Dupatta worn over the head.",
      dupatta_or_saree_drape: "Draped over the head.",
    },
    cultural_context: {
      regional_direction: "Pakistani",
      interpretation_notes: ["Treated as one broad regional direction."],
      safeguards: ["No specific community presented as universal."],
    },
    styling_notes: ["Keep jewellery warm-toned.", "Centre-parted hairstyle."],
    construction_caveats: [
      "This is a concept visualisation, not a sewing pattern.",
      "It does not guarantee the garment can be constructed exactly as shown.",
    ],
    image_alt_text: "A model in an ivory flared lehenga with gold embroidery.",
    created_at: "2026-07-19T12:00:00Z",
    ...overrides,
  };
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

function renderResult(designId = "d1", versionId = "v1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <DesignResult designId={designId} versionId={versionId} />
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.capturedQueries = [];
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("DesignResult — result rendering", () => {
  it("renders every documented section from a representative fixture", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();

    expect(await screen.findByRole("heading", { name: /Ivory and gold flared lehenga/i })).toBeInTheDocument();
    expect(screen.getByText(/concept summary describing/i)).toBeInTheDocument();
    expect(screen.getByText("Choli")).toBeInTheDocument();
    expect(screen.getByText(/Ivory with gold accents/i)).toBeInTheDocument();
    expect(screen.getByText("Zardozi")).toBeInTheDocument();
    expect(screen.getByText(/Full length\./)).toBeInTheDocument();
    expect(screen.getByText("Pakistani", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/Keep jewellery warm-toned/i)).toBeInTheDocument();
    expect(screen.getAllByText(/not a sewing pattern/i).length).toBeGreaterThan(0);
  });

  it("renders multiple fabric entries", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    expect(screen.getByText(/Silk/)).toBeInTheDocument();
    expect(screen.getByText(/Organza/)).toBeInTheDocument();
  });

  it("handles a null regional direction gracefully", async () => {
    mocks.fetchDesignResult.mockResolvedValue({
      ok: true,
      result: result({
        cultural_context: {
          regional_direction: null,
          interpretation_notes: ["A blended interpretation."],
          safeguards: ["No single tradition claimed."],
        },
      }),
    });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    expect(screen.queryByText(/Regional direction:/i)).not.toBeInTheDocument();
    expect(screen.getByText(/A blended interpretation/i)).toBeInTheDocument();
  });

  it("renders cultural safeguards and construction caveats", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    expect(screen.getByText(/No specific community presented as universal/i)).toBeInTheDocument();
    expect(
      screen.getByText(/does not guarantee the garment can be constructed exactly as shown/i),
    ).toBeInTheDocument();
  });

  it("places the generic disclaimer near the heading, before the detailed brief", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { container } = renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    const disclaimer = container.querySelector(".result-disclaimer");
    const summarySection = document.getElementById("brief-summary");
    expect(disclaimer).not.toBeNull();
    expect(disclaimer?.textContent).toMatch(/AI-assisted visual concept/i);
    expect(disclaimer?.textContent).toMatch(/not a photograph/i);
    expect(disclaimer?.textContent).toMatch(/not a sewing pattern/i);
    expect(disclaimer?.textContent).toMatch(/does not guarantee/i);
    // Disclaimer precedes the detailed brief in document order.
    expect(
      disclaimer!.compareDocumentPosition(summarySection!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("uses the exact image_alt_text for the image alt attribute", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    expect(
      await screen.findByRole("img", { name: "A model in an ivory flared lehenga with gold embroidery." }),
    ).toBeInTheDocument();
  });

  it("never renders source_selections or internal provenance", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { container } = renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    const raw = container.innerHTML;
    expect(raw).not.toMatch(/source_selections/i);
    expect(raw).not.toMatch(/prompt_builder_version/i);
    expect(raw).not.toMatch(/design_spec_provider/i);
    expect(raw).not.toContain("d1");
  });

  it("renders malicious-looking text as plain text, never as HTML", async () => {
    mocks.fetchDesignResult.mockResolvedValue({
      ok: true,
      result: result({
        concept_summary: "<img src=x onerror=alert(1)> a concept summary with markup-looking text.",
      }),
    });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { container } = renderResult();
    await screen.findByRole("img");
    expect(screen.getByText(/onerror=alert\(1\)/)).toBeInTheDocument();
    // Only the ONE legitimate result image exists — the malicious markup was
    // never parsed as HTML into a second <img>.
    expect(container.querySelectorAll("img")).toHaveLength(1);
  });
});

describe("DesignResult — result error states", () => {
  it("shows a loading state before the result resolves", () => {
    mocks.fetchDesignResult.mockReturnValue(new Promise(() => {}));
    renderResult();
    expect(screen.getByRole("status")).toHaveTextContent(/loading your result/i);
  });

  it("shows an indistinguishable not-found state on 404, with no retry action", async () => {
    mocks.fetchDesignResult.mockResolvedValue({
      ok: false,
      status: 404,
      code: "not_found",
      message: "Not found.",
    });
    renderResult();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/not found/i);
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });

  it("shows 'result still being prepared' on a 409", async () => {
    mocks.fetchDesignResult.mockResolvedValue({
      ok: false,
      status: 409,
      code: "design_result_not_ready",
      message: "not ready",
    });
    renderResult();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/still being prepared/i);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("shows a controlled retryable error on a 503", async () => {
    mocks.fetchDesignResult.mockResolvedValue({
      ok: false,
      status: 503,
      code: "design_result_unavailable",
      message: "unavailable",
    });
    renderResult();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/temporarily unavailable/i);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("shows a controlled state for a malformed response", async () => {
    mocks.fetchDesignResult.mockResolvedValue({
      ok: false,
      status: 200,
      code: "invalid_response",
      message: "malformed",
    });
    renderResult();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/unexpected response/i);
  });

  it("shows a controlled retryable error when the result service cannot be reached at all", async () => {
    mocks.fetchDesignResult.mockResolvedValue({
      ok: false,
      status: 0,
      code: "unavailable",
      message: "The service could not be reached.",
    });
    renderResult();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not be reached/i);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

describe("DesignResult — signed URL handling", () => {
  it("does not start the image query until the result succeeds", async () => {
    let resolveResult: (value: unknown) => void = () => {};
    mocks.fetchDesignResult.mockReturnValue(
      new Promise((resolve) => {
        resolveResult = resolve;
      }),
    );
    renderResult();
    expect(mocks.fetchDesignImageUrls).not.toHaveBeenCalled();
    resolveResult({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    expect(mocks.fetchDesignImageUrls).toHaveBeenCalledWith("d1", "v1");
  });

  it("disables background polling for the image query", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    const imageQueryOptions = findCapturedQuery("design-image");
    expect(imageQueryOptions?.refetchIntervalInBackground).toBe(false);
  });

  it("declares refetchOnWindowFocus:false on the image query itself, so the near-expiry effect is the only focus-refresh mechanism", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    const imageQueryOptions = findCapturedQuery("design-image");
    expect(imageQueryOptions?.refetchOnWindowFocus).toBe(false);
  });

  it("removes signed URLs from the query cache after unmount (gcTime: 0)", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { unmount, client } = renderResult();
    await screen.findByRole("img");
    unmount();
    // gcTime: 0 schedules removal via a zero-delay timer rather than
    // removing synchronously on unmount; flush that one macrotask.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(client.getQueryData(["design-image", "d1", "v1"])).toBeUndefined();
    expect(client.getQueryData(["design-result", "d1", "v1"])).toBeUndefined();
  });

  it("rejects a malformed (non-future) expiry rather than rendering an unsafe URL", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({
      ok: true,
      images: images({ expires_at: "not-a-real-date" }),
    });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    // The image query retries once (transient-failure tolerance) before
    // settling into its error state, so allow for that real backoff delay.
    expect(await screen.findByRole("alert", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("keeps the result brief visible when image delivery fails", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({
      ok: false,
      status: 503,
      code: "design_image_delivery_unavailable",
      message: "Design images are temporarily unavailable.",
    });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    expect(screen.getByText(/concept summary describing/i)).toBeInTheDocument();
    // The image query retries once before settling into its error state.
    expect(await screen.findByText(/temporarily unavailable/i, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText(/concept summary describing/i)).toBeInTheDocument();
  });

  it("touches no browser storage while loading the result and image", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("refreshes the image before the signed URL expires, at ~80% of the remaining lifetime", async () => {
    vi.useFakeTimers();
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    const shortLived = images({ expires_at: new Date(Date.now() + 2000).toISOString() });
    const refreshed = images({ expires_at: new Date(Date.now() + 300_000).toISOString() });
    mocks.fetchDesignImageUrls.mockResolvedValueOnce({ ok: true, images: shortLived });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: refreshed });

    renderResult();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.fetchDesignImageUrls).toHaveBeenCalledTimes(1);

    // Before 80% of the 2s lifetime (1600ms): no refresh yet.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(mocks.fetchDesignImageUrls).toHaveBeenCalledTimes(1);

    // At/after 80%: a refresh has fired.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(mocks.fetchDesignImageUrls).toHaveBeenCalledTimes(2);
  });

  it("stops refreshing after unmount", async () => {
    vi.useFakeTimers();
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({
      ok: true,
      images: images({ expires_at: new Date(Date.now() + 2000).toISOString() }),
    });
    const { unmount } = renderResult();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const callsAtUnmount = mocks.fetchDesignImageUrls.mock.calls.length;
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(mocks.fetchDesignImageUrls.mock.calls.length).toBe(callsAtUnmount);
  });

  it("triggers an immediate refresh on window focus when the URL is near expiry", async () => {
    // Fake only Date (not setTimeout/MessageChannel) so React's real
    // scheduler still flushes normally and findByRole/waitFor can settle
    // the chained result -> image queries with real timers; only the
    // near-expiry math needs a controllable clock.
    vi.useFakeTimers({ toFake: ["Date"] });
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValueOnce({
      ok: true,
      // Near-expiry (well under the 15s threshold), but far enough out that
      // the proactive 80%-lifetime refetchInterval has not fired yet.
      images: images({ expires_at: new Date(Date.now() + 10_000).toISOString() }),
    });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    await screen.findByRole("img");
    expect(mocks.fetchDesignImageUrls).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(mocks.fetchDesignImageUrls).toHaveBeenCalledTimes(2));
  });

  it("does not refetch on focus when the URL is not near expiry", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({
      ok: true,
      images: images({ expires_at: new Date(Date.now() + 300_000).toISOString() }),
    });
    renderResult();
    await screen.findByRole("img");
    expect(mocks.fetchDesignImageUrls).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(mocks.fetchDesignImageUrls).toHaveBeenCalledTimes(1);
  });
});

describe("DesignResult — copy and download actions", () => {
  it("announces copy success", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    fireEvent.click(screen.getByRole("button", { name: /copy brief/i }));
    expect(await screen.findByText(/copied to clipboard/i)).toBeInTheDocument();
  });

  it("announces copy failure", async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    fireEvent.click(screen.getByRole("button", { name: /copy brief/i }));
    expect(await screen.findByText(/could not copy/i)).toBeInTheDocument();
  });

  it("downloads the brief with the fixed filename and revokes the object URL", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const createObjectURL = vi.fn().mockReturnValue("blob:fake-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    let downloadedFilename: string | null = null;
    const originalClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function click(this: HTMLAnchorElement) {
      downloadedFilename = this.download;
    };
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    fireEvent.click(screen.getByRole("button", { name: /download brief/i }));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(downloadedFilename).toBe("sitara-design-brief.txt");
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fake-url");
    HTMLAnchorElement.prototype.click = originalClick;
  });

  it("still revokes the object URL if the download click throws", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const createObjectURL = vi.fn().mockReturnValue("blob:fake-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const originalClick = HTMLAnchorElement.prototype.click;
    // React reports a synchronous event-handler throw via a window "error"
    // event rather than re-throwing out of fireEvent.click; swallow it here
    // so the test asserts only on the try/finally's revoke guarantee.
    const onWindowError = (event: ErrorEvent) => event.preventDefault();
    window.addEventListener("error", onWindowError);
    HTMLAnchorElement.prototype.click = function click() {
      throw new Error("blocked by a hostile extension");
    };
    renderResult();
    await screen.findByRole("heading", { name: /Ivory and gold/i });
    fireEvent.click(screen.getByRole("button", { name: /download brief/i }));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fake-url");
    window.removeEventListener("error", onWindowError);
    HTMLAnchorElement.prototype.click = originalClick;
  });
});
