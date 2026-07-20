import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetCsrfTokenForTests, fetchDesignResult } from "./api";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type FetchCall = { url: string; init: RequestInit | undefined };

function installFetchSpy(
  handler: (url: string, init?: RequestInit) => Response | Promise<Response>,
): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      return handler(url, init);
    }),
  );
  return calls;
}

beforeEach(() => {
  _resetCsrfTokenForTests();
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const RESULT = {
  result: {
    design_id: "d1",
    design_version_id: "v1",
    version_number: 1,
    title: "Ivory lehenga",
    concept_summary: "A concept summary.",
    garment_breakdown: {
      overall_form: "form",
      garment_components: ["choli", "skirt"],
      silhouette: "fitted",
      drape_or_layering: "layered",
      key_proportions: "balanced",
    },
    colour_story: { palette_summary: "ivory and gold", placement: "all over", rationale: "calm" },
    fabrics_and_texture: [
      { fabric: "silk", placement: "skirt", finish_and_movement: "smooth" },
    ],
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
    image_alt_text: "A model in an ivory lehenga.",
    created_at: "2026-07-19T12:00:00Z",
    inspiration_acknowledgements: [],
    lineage: { kind: "initial", parent_version_id: null, refinement: null },
  },
};

describe("fetchDesignResult", () => {
  it("returns the result on a valid 200", async () => {
    const calls = installFetchSpy(() => json(RESULT));
    const outcome = await fetchDesignResult("design-1", "version-1");
    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      expect(outcome.result.title).toBe("Ivory lehenga");
      expect(outcome.result.fabrics_and_texture).toHaveLength(1);
    }
    expect(calls[0].url).toBe("/api/v1/designs/design-1/versions/version-1/result/");
    expect(calls[0].init?.credentials).toBe("same-origin");
    expect(calls[0].init?.cache).toBe("no-store");
  });

  it("encodes the design and version ids into the path", async () => {
    const calls = installFetchSpy(() => json(RESULT));
    await fetchDesignResult("a/b", "c d");
    expect(calls[0].url).toBe("/api/v1/designs/a%2Fb/versions/c%20d/result/");
  });

  it("maps the controlled 404 to not_found", async () => {
    installFetchSpy(() => json({ error: { code: "not_found", message: "Not found." } }, 404));
    const outcome = await fetchDesignResult("d", "v");
    expect(outcome).toMatchObject({ ok: false, status: 404, code: "not_found" });
  });

  it("maps the controlled 409 to design_result_not_ready", async () => {
    installFetchSpy(() =>
      json(
        {
          error: {
            code: "design_result_not_ready",
            message: "This design version has no complete result yet.",
          },
        },
        409,
      ),
    );
    const outcome = await fetchDesignResult("d", "v");
    expect(outcome).toMatchObject({ ok: false, status: 409, code: "design_result_not_ready" });
  });

  it("maps the controlled 503 to design_result_unavailable", async () => {
    installFetchSpy(() =>
      json(
        { error: { code: "design_result_unavailable", message: "Unavailable." } },
        503,
      ),
    );
    const outcome = await fetchDesignResult("d", "v");
    expect(outcome).toMatchObject({ ok: false, status: 503, code: "design_result_unavailable" });
  });

  it("rejects a 200 with a malformed body as invalid_response", async () => {
    installFetchSpy(() => json({ result: { title: "only a title" } }));
    const outcome = await fetchDesignResult("d", "v");
    expect(outcome).toMatchObject({ ok: false, status: 200, code: "invalid_response" });
  });

  it("rejects non-JSON output as invalid_response", async () => {
    installFetchSpy(
      () =>
        new Response("<html>proxy error</html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
    );
    const outcome = await fetchDesignResult("d", "v");
    expect(outcome).toMatchObject({ ok: false, code: "invalid_response" });
  });

  it("returns a typed failure when the network/timeout throws", async () => {
    installFetchSpy(() => {
      throw new TypeError("network down");
    });
    const outcome = await fetchDesignResult("d", "v");
    expect(outcome).toMatchObject({ ok: false, status: 0, code: "unavailable" });
  });

  it("never stores the result in browser storage", async () => {
    installFetchSpy(() => json(RESULT));
    await fetchDesignResult("d", "v");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("never issues a signed URL — no http(s) URL appears anywhere in the result", async () => {
    installFetchSpy(() => json(RESULT));
    const outcome = await fetchDesignResult("d", "v");
    expect(outcome.ok).toBe(true);
    const raw = JSON.stringify(outcome);
    expect(raw).not.toMatch(/https?:\/\//);
  });

  it("accepts a populated inspiration_acknowledgements array", async () => {
    const withInspiration = {
      result: {
        ...RESULT.result,
        inspiration_acknowledgements: [
          { position: 1, title: "Emerald look", attribution: "Studio A" },
        ],
      },
    };
    installFetchSpy(() => json(withInspiration));
    const outcome = await fetchDesignResult("d", "v");
    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      expect(outcome.result.inspiration_acknowledgements).toEqual([
        { position: 1, title: "Emerald look", attribution: "Studio A" },
      ]);
    }
  });

  it("rejects a malformed inspiration_acknowledgements entry as invalid_response", async () => {
    const malformed = {
      result: {
        ...RESULT.result,
        inspiration_acknowledgements: [{ position: "1", title: "x", attribution: "" }],
      },
    };
    installFetchSpy(() => json(malformed));
    const outcome = await fetchDesignResult("d", "v");
    expect(outcome).toMatchObject({ ok: false, status: 200, code: "invalid_response" });
  });
});
