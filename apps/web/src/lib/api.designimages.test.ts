import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetCsrfTokenForTests, fetchDesignImageUrls } from "./api";

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

const IMAGES = {
  images: {
    original: {
      url: "https://minio.local/signed-original",
      download_url: "https://minio.local/signed-original-download",
      width: 1536,
      height: 2048,
    },
    thumbnail: { url: "https://minio.local/signed-thumbnail", width: 384, height: 512 },
    expires_at: "2026-07-19T12:05:00Z",
  },
};

describe("fetchDesignImageUrls", () => {
  it("returns the images on a valid 200", async () => {
    const calls = installFetchSpy(() => json(IMAGES));
    const result = await fetchDesignImageUrls("design-1", "version-1");
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.images.original.url).toBe("https://minio.local/signed-original");
      expect(result.images.original.download_url).toBe(
        "https://minio.local/signed-original-download",
      );
      expect(result.images.thumbnail.width).toBe(384);
      expect(result.images.expires_at).toBe("2026-07-19T12:05:00Z");
    }
    // Same-origin relative path with encoded ids; a plain GET.
    expect(calls[0].url).toBe("/api/v1/designs/design-1/versions/version-1/images/");
    expect(calls[0].init?.credentials).toBe("same-origin");
    expect(calls[0].init?.cache).toBe("no-store");
  });

  it("rejects a 200 body missing the original download_url as invalid_response", async () => {
    installFetchSpy(() =>
      json({
        images: {
          original: { url: "https://minio.local/signed-original", width: 1536, height: 2048 },
          thumbnail: { url: "https://minio.local/signed-thumbnail", width: 384, height: 512 },
          expires_at: "2026-07-19T12:05:00Z",
        },
      }),
    );
    const result = await fetchDesignImageUrls("d", "v");
    expect(result).toMatchObject({ ok: false, status: 200, code: "invalid_response" });
  });

  it("encodes the design and version ids into the path", async () => {
    const calls = installFetchSpy(() => json(IMAGES));
    await fetchDesignImageUrls("a/b", "c d");
    expect(calls[0].url).toBe("/api/v1/designs/a%2Fb/versions/c%20d/images/");
  });

  it("maps the controlled 409 to its stable code", async () => {
    installFetchSpy(() =>
      json(
        {
          error: {
            code: "design_image_not_ready",
            message: "This design version has no viewable image yet.",
          },
        },
        409,
      ),
    );
    const result = await fetchDesignImageUrls("d", "v");
    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "design_image_not_ready",
    });
  });

  it("maps the controlled 503 to its stable code", async () => {
    installFetchSpy(() =>
      json(
        { error: { code: "design_image_delivery_unavailable", message: "Unavailable." } },
        503,
      ),
    );
    const result = await fetchDesignImageUrls("d", "v");
    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "design_image_delivery_unavailable",
    });
  });

  it("maps a 404 without a body code to unknown_error", async () => {
    installFetchSpy(() => json({}, 404));
    const result = await fetchDesignImageUrls("d", "v");
    expect(result).toMatchObject({ ok: false, status: 404, code: "unknown_error" });
  });

  it("rejects a 200 with a malformed body as invalid_response", async () => {
    installFetchSpy(() =>
      json({ images: { original: { url: "", width: 1, height: 1 } } }),
    );
    const result = await fetchDesignImageUrls("d", "v");
    expect(result).toMatchObject({ ok: false, status: 200, code: "invalid_response" });
  });

  it("rejects non-JSON output as invalid_response", async () => {
    installFetchSpy(
      () =>
        new Response("<html>proxy error</html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
    );
    const result = await fetchDesignImageUrls("d", "v");
    expect(result).toMatchObject({ ok: false, code: "invalid_response" });
  });

  it("returns a typed failure when the network/timeout throws", async () => {
    installFetchSpy(() => {
      throw new TypeError("network down");
    });
    const result = await fetchDesignImageUrls("d", "v");
    expect(result).toMatchObject({ ok: false, status: 0, code: "unavailable" });
  });

  it("never stores the signed URLs in browser storage", async () => {
    installFetchSpy(() => json(IMAGES));
    await fetchDesignImageUrls("d", "v");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("does not cache URLs in module state: every call fetches fresh", async () => {
    const calls = installFetchSpy(() => json(IMAGES));
    await fetchDesignImageUrls("d", "v");
    await fetchDesignImageUrls("d", "v");
    expect(calls.length).toBe(2);
  });
});
