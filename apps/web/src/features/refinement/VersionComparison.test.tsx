import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VersionComparison } from "./VersionComparison";
import type { DesignImages, DesignResult as DesignResultType } from "@/lib/api";
import { axeViolations } from "@/test-utils/axe";

const mocks = vi.hoisted(() => ({
  fetchDesignResult: vi.fn(),
  fetchDesignImageUrls: vi.fn(),
  // Both cards now carry a Send to account control, so the send client is
  // reached from this suite. Stubbed rather than left to the real module: an
  // unstubbed `fetchRenderSendState` would go to the transport, and the
  // allowance sentence and the naming prompt would both be asserted against
  // whatever a failed read leaves behind — which is to say, vacuously.
  fetchRenderSendState: vi.fn(),
  sendRenderToAccount: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchDesignResult: mocks.fetchDesignResult,
    fetchDesignImageUrls: mocks.fetchDesignImageUrls,
    fetchRenderSendState: mocks.fetchRenderSendState,
    sendRenderToAccount: mocks.sendRenderToAccount,
  };
});

// Signed in by default. `useAuth`'s context default is an anonymous user, so
// without this every send control on this screen would render its signed-out
// branch and every assertion about the real one would pass by never reaching it.
const auth = vi.hoisted(() => ({ user: null as unknown }));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));

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
    refinements_remaining: 3,
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
      refinement: { change_type: "colour_story", demo_asset_unchanged: false },
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

function renderComparison(
  refinedOverrides: Partial<DesignResultType> = {},
  // The refined SIDE, not its result: the disclosure's placement depends on
  // whether each card's image is deliverable, so a test has to be able to fail
  // one card's image without touching the other's.
  sideOverrides: Partial<Omit<Parameters<typeof VersionComparison>[0]["refined"], "result">> = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const refined = refinedResult(refinedOverrides);
  const utils = render(
    <QueryClientProvider client={client}>
      <VersionComparison
        designId="d1"
        parentVersionId="v1"
        refinementsRemaining={2}
        refined={{
          result: refined,
          images: images(),
          imagesPending: false,
          imagesFetching: false,
          imagesError: null,
          onRetryImages: vi.fn(),
          ...sideOverrides,
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
  auth.user = { id: "u1", email: "stylist@example.com" };
  mocks.fetchRenderSendState.mockResolvedValue({
    used: 0,
    limit: 3,
    suggestedFilename: "Ivory lehenga",
  });
  mocks.sendRenderToAccount.mockResolvedValue({ ok: true });
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
    expect(await screen.findByRole("heading", { name: /previous concept/i })).toBeInTheDocument();
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
    await screen.findByRole("heading", { name: /previous concept/i });
    const summaries = screen.getAllByText(/view complete brief/i);
    expect(summaries).toHaveLength(2);
    // Both details/summary controls actually expose the underlying brief.
    expect(screen.getAllByText(/Garment breakdown/i).length).toBe(2);
  });

  it("puts the current (refined) concept first, with the original under Previous design", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderComparison();
    // Wait for BOTH cards to be mounted before reading order — the original
    // side loads asynchronously, so a premature query could otherwise catch
    // a transient single-card DOM.
    await screen.findByRole("heading", { name: /previous concept/i });
    await screen.findByRole("heading", { name: /refined concept/i });
    // DesignBrief also renders its own h3s (e.g. "Interpretation notes")
    // inside each card's collapsed detailed brief — scope to the card's own
    // "<label> — version N" heading specifically.
    // Phase 17 applies the handoff's History hierarchy: the current design
    // leads and earlier ones sit under "Previous design". On a narrow screen
    // the same order stacks, so what a user meets first is the concept they
    // just generated rather than the one they replaced.
    const headings = screen.getAllByRole("heading", { level: 2, name: /— version \d/ });
    expect(headings[0]).toHaveTextContent(/refined concept/i);
    expect(headings[1]).toHaveTextContent(/previous concept/i);
    expect(headings[0]).toHaveTextContent(/current/i);
    expect(screen.getByRole("heading", { name: /previous design/i })).toBeInTheDocument();
  });

  it("labels each version from its own persisted is_demo, never inferred together", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result({ is_demo: true }) });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    renderComparison({ is_demo: false });
    await screen.findByRole("heading", { name: /previous concept/i });
    // Refined (live) leads; the original (demo) follows it.
    const headings = screen.getAllByRole("heading", { level: 2, name: /— version \d/ });
    expect(headings[0]).toHaveTextContent(/live/i);
    expect(headings[1]).toHaveTextContent(/demo/i);
  });

  it("displays the selected refinement category in human-readable form", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { container } = renderComparison({
      lineage: {
        kind: "refinement",
        parent_version_id: "v1",
        refinement: { change_type: "dupatta_or_saree_drape", demo_asset_unchanged: false },
      },
    });
    await screen.findByRole("heading", { name: /previous concept/i });
    const disclosure = container.querySelector(".comparison-disclosure");
    expect(disclosure?.textContent).toMatch(/requested change:\s*dupatta or saree drape/i);
  });

  it("still labels a concept refined through the RETIRED styling_details category", async () => {
    // ADR 0028 retired the category, so it is no longer offered. A design
    // refined before that still carries it in its persisted lineage for ever,
    // and the read side must keep labelling it — a raw `styling_details` on
    // screen would be this phase's own regression, not an old design's fault.
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { container } = renderComparison({
      lineage: {
        kind: "refinement",
        parent_version_id: "v1",
        refinement: { change_type: "styling_details", demo_asset_unchanged: false },
      },
    });
    await screen.findByRole("heading", { name: /previous concept/i });
    const disclosure = container.querySelector(".comparison-disclosure");
    expect(disclosure?.textContent).toMatch(/requested change:\s*styling details/i);
    expect(disclosure?.textContent).not.toMatch(/styling_details/);
  });

  it("never renders a raw refinement note (not part of the fetched type at all)", async () => {
    mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
    mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
    const { container } = renderComparison();
    await screen.findByRole("heading", { name: /previous concept/i });
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
    await screen.findByRole("heading", { name: /previous concept/i });
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
    await screen.findByRole("heading", { name: /previous concept/i });
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
    await screen.findByRole("heading", { name: /previous concept/i });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("shows a loading state for the previous concept before it resolves", () => {
    mocks.fetchDesignResult.mockReturnValue(new Promise(() => {}));
    renderComparison();
    expect(screen.getByText(/loading your previous concept/i)).toBeInTheDocument();
  });

  it("shows a retryable error state if the previous concept cannot be loaded", async () => {
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

  describe("both concepts carry their own actions", () => {
    // Until ADR 0029 a refinement was a one-off, and this screen was a farewell
    // look at a finished pair — ResultImage gated Annotate and Send behind
    // optional ids the comparison never passed, with a comment calling the two
    // renders "read-only". It was not read-only then either, and now the screen
    // is where a customer stands between rounds: the concept she is looking at
    // has to be the one she can annotate and email.

    it("offers Annotate and Send to account on BOTH cards, each pointing at its own version", async () => {
      mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
      mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
      renderComparison();
      // Wait for the PREVIOUS card's image, not just its heading: that card's
      // image query is `enabled` only once its result query has succeeded, and
      // the actions live inside ResultImage's ready branch. Waiting on the
      // heading alone reads the DOM a tick early and finds one action set.
      await screen.findByRole("img", { name: "Original image alt text." });

      const annotate = screen.getAllByRole("link", { name: /annotate/i });
      expect(annotate).toHaveLength(2);
      expect(annotate.map((link) => link.getAttribute("href")).sort()).toEqual([
        "/design/d1/result/v1/annotate",
        "/design/d1/result/v2/annotate",
      ]);
      expect(screen.getAllByRole("button", { name: /send to account/i })).toHaveLength(2);
    });

    it("distinguishes the two sets by version, for a flat screen-reader list", async () => {
      // A rotor lists links and buttons flat and does not show the <article>
      // that groups them, so two bare "Annotate"s name the same thing twice.
      mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
      mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
      renderComparison();
      // Wait for the PREVIOUS card's image, not just its heading: that card's
      // image query is `enabled` only once its result query has succeeded, and
      // the actions live inside ResultImage's ready branch. Waiting on the
      // heading alone reads the DOM a tick early and finds one action set.
      await screen.findByRole("img", { name: "Original image alt text." });

      expect(
        screen.getByRole("link", { name: /annotate — refined concept, version 2/i }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: /annotate — previous concept, version 1/i }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /send to account — refined concept, version 2/i }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /send to account — previous concept, version 1/i }),
      ).toBeInTheDocument();
    });

    it("states the accepted exposure exactly once, however many send controls there are", async () => {
      // ADR 0021's disclosure is a statement about what sending does, not a
      // caption on a button. Printed twice it reads as two different exposures;
      // printed under only one of two identical controls it reads as applying to
      // that one alone.
      mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
      mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
      renderComparison();
      // Wait for the PREVIOUS card's image, not just its heading: that card's
      // image query is `enabled` only once its result query has succeeded, and
      // the actions live inside ResultImage's ready branch. Waiting on the
      // heading alone reads the DOM a tick early and finds one action set.
      await screen.findByRole("img", { name: "Original image alt text." });
      expect(screen.getAllByText(/may keep a copy outside/i)).toHaveLength(1);
    });

    it("keeps the disclosure when the CURRENT card's image failed and the other's did not", async () => {
      // The hole this closes: tying the sentence to one nominated card drops it
      // altogether whenever THAT card's image fails, while the sibling card goes
      // on offering Send with nothing said about what sending does. The screen
      // asks both cards, so the sentence follows the controls rather than a card.
      mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
      mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
      renderComparison(
        {},
        {
          images: undefined,
          imagesPending: false,
          imagesError: new Error("image unavailable"),
        },
      );
      await screen.findByRole("img", { name: "Original image alt text." });

      // Exactly one send control — the refined card is showing its error state.
      expect(screen.getAllByRole("button", { name: /send to account/i })).toHaveLength(1);
      expect(screen.getAllByText(/may keep a copy outside/i)).toHaveLength(1);
    });

    it("says nothing about sending when neither card can offer it", async () => {
      // The reason the sentence is not simply printed unconditionally: with no
      // send control anywhere, an accepted-exposure statement describes nothing
      // that is on the screen.
      mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
      mocks.fetchDesignImageUrls.mockResolvedValue({
        ok: false,
        status: 409,
        code: "design_image_not_ready",
        message: "not ready",
      });
      renderComparison(
        {},
        {
          images: undefined,
          imagesPending: false,
          imagesError: new Error("image unavailable"),
        },
      );
      // Wait for BOTH image queries to have RESOLVED — the refined card's error
      // branch and the previous card's — before asserting an absence. Waiting on
      // the heading instead would let every assertion below pass simply because
      // nothing had rendered yet, which is the same hollow shape as asserting a
      // send control is missing one tick before it mounts.
      // The generous timeout is not decoration: the previous card's image query
      // is configured `retry: 1`, so it only reaches its terminal error state
      // after that retry, and the default 1s window expires first.
      await waitFor(
        () => {
          expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(2);
        },
        { timeout: 5000 },
      );
      expect(screen.queryByRole("button", { name: /send to account/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: /annotate/i })).not.toBeInTheDocument();
      expect(screen.queryByText(/may keep a copy outside/i)).not.toBeInTheDocument();
    });

    it("gives the two briefs disjoint DOM ids", async () => {
      // BriefSection wires each id into aria-controls and aria-labelledby. Two
      // briefs minting the same ids means an assistive technology following
      // either reference lands on whichever copy is first in the document —
      // which is the other concept's card.
      mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
      mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
      const { container } = renderComparison();
      await screen.findByRole("heading", { name: /previous concept/i });

      const ids = Array.from(container.querySelectorAll("[id]")).map((el) => el.id);
      expect(new Set(ids).size).toBe(ids.length);
      expect(ids).toEqual(expect.arrayContaining(["brief-refined-garment-heading"]));
      expect(ids).toEqual(expect.arrayContaining(["brief-previous-garment-heading"]));
    });

    it("lends the send dialog to one card at a time, and gives the other its reason", async () => {
      // ModalDialog's containment is a Tab cycle plus a scrim: browse mode walks
      // past both and can open the second dialog on top of the first, whose focus
      // restore then targets an element inside it. A disabled control is out of
      // the browse-mode action set as well as the tab order.
      mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
      mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
      renderComparison();
      // Wait for the PREVIOUS card's image, not just its heading: that card's
      // image query is `enabled` only once its result query has succeeded, and
      // the actions live inside ResultImage's ready branch. Waiting on the
      // heading alone reads the DOM a tick early and finds one action set.
      await screen.findByRole("img", { name: "Original image alt text." });

      const refinedSend = screen.getByRole("button", {
        name: /send to account — refined concept, version 2/i,
      });
      const previousSend = screen.getByRole("button", {
        name: /send to account — previous concept, version 1/i,
      });
      expect(previousSend).toBeEnabled();

      await act(async () => {
        refinedSend.click();
      });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(previousSend).toBeDisabled();
      expect(screen.getByText(/finish or close the other email first/i)).toBeInTheDocument();

      await act(async () => {
        screen.getByRole("button", { name: /^cancel$/i }).click();
      });
      expect(screen.getByRole("button", {
        name: /send to account — previous concept, version 1/i,
      })).toBeEnabled();
    });

    it("calls the version's own render-send state, not the page's", async () => {
      mocks.fetchDesignResult.mockResolvedValue({ ok: true, result: result() });
      mocks.fetchDesignImageUrls.mockResolvedValue({ ok: true, images: images() });
      renderComparison();
      // Wait for the PREVIOUS card's image, not just its heading: that card's
      // image query is `enabled` only once its result query has succeeded, and
      // the actions live inside ResultImage's ready branch. Waiting on the
      // heading alone reads the DOM a tick early and finds one action set.
      await screen.findByRole("img", { name: "Original image alt text." });
      expect(mocks.fetchRenderSendState).toHaveBeenCalledWith("d1", "v2", "plain");
      expect(mocks.fetchRenderSendState).toHaveBeenCalledWith("d1", "v1", "plain");
    });
  });

  describe("accessibility", () => {
    it("has no axe violations with both versions present", async () => {
      const { container } = renderComparison();
      await screen.findByRole("heading", { name: /previous design/i });
      expect(await axeViolations(container)).toHaveNoViolations();
    });

    it("has no axe violations when the original's brief cannot be loaded", async () => {
      // The asymmetric state: one side is a full concept, the other an alert.
      // Worth its own run — a heading level or label can only go wrong here.
      mocks.fetchDesignResult.mockResolvedValue({
        ok: false,
        status: 503,
        code: "design_result_unavailable",
        message: "unavailable",
      });
      const { container } = renderComparison();
      await screen.findByRole("alert");
      expect(await axeViolations(container)).toHaveNoViolations();
    });
  });
});
