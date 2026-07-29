// Multipart inspiration-upload wrappers (Phase 16B).
//
// These cannot go through sendJson — the browser must set its own multipart
// boundary — so the shared policy has to be re-proven here rather than assumed:
// same-origin, no-store, CSRF header, exactly one retry on a stale token, and
// no Content-Type of our own.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  _resetCsrfTokenForTests,
  inspirationUploadImageUrl,
  removeInspirationUpload,
  uploadInspirationImage,
} from "./api";
import { REQUEST_TIMEOUT_MS, UPLOAD_TIMEOUT_MS } from "./transport";

type FetchCall = { url: string; init: RequestInit | undefined };

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

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

const UPLOAD = {
  id: "u1",
  position: 1,
  width: 900,
  height: 1200,
  rights_acknowledged_at: "2026-07-29T00:00:00Z",
  created_at: "2026-07-29T00:00:00Z",
};

const CSRF_FAILED = {
  error: { code: "csrf_failed", message: "The security token is missing or invalid." },
};

const POST_URL = "/api/v1/designs/d1/inspiration-uploads/";
const DELETE_URL = "/api/v1/designs/d1/inspiration-uploads/u1/";

function image(name = "dress.jpg"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "image/jpeg" });
}

beforeEach(() => {
  _resetCsrfTokenForTests();
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("uploadInspirationImage", () => {
  it("posts multipart with the CSRF header and no Content-Type of ours", async () => {
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "tok" });
      return json({ upload: UPLOAD }, 201);
    });

    const result = await uploadInspirationImage("d1", image(), true);

    expect(result).toEqual({ ok: true, upload: UPLOAD });
    const post = calls.find((call) => call.url === POST_URL);
    expect(post?.init?.method).toBe("POST");
    const headers = post?.init?.headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBe("tok");
    // A Content-Type we chose would replace the browser's boundary and corrupt
    // the body — the request must carry none.
    expect(
      Object.keys(headers).some((key) => key.toLowerCase() === "content-type"),
    ).toBe(false);
    expect(post?.init?.body).toBeInstanceOf(FormData);
    expect(post?.init?.credentials).toBe("same-origin");
    expect(post?.init?.cache).toBe("no-store");
  });

  it("sends the file and the affirmation as form fields", async () => {
    const calls = installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/" ? json({ csrf_token: "tok" }) : json({ upload: UPLOAD }, 201),
    );

    await uploadInspirationImage("d1", image("lehenga.jpg"), true);

    const body = calls.find((call) => call.url === POST_URL)?.init?.body as FormData;
    expect((body.get("image") as File).name).toBe("lehenga.jpg");
    expect(body.get("rights_acknowledged")).toBe("true");
  });

  it("passes a withheld affirmation through rather than deciding locally", async () => {
    // The server owns this rule; the client must not quietly send `true`.
    const calls = installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/"
        ? json({ csrf_token: "tok" })
        : json({ error: { code: "rights_not_acknowledged" } }, 400),
    );

    const result = await uploadInspirationImage("d1", image(), false);

    const body = calls.find((call) => call.url === POST_URL)?.init?.body as FormData;
    expect(body.get("rights_acknowledged")).toBe("false");
    expect(result.ok).toBe(false);
  });

  it("uses the upload timeout, not the 5s JSON one", async () => {
    // A 15 MB body on a slow link cannot fit the JSON budget; a request that
    // aborts mid-upload would look to the user like a broken feature.
    const seen: number[] = [];
    const original = globalThis.setTimeout;
    vi.stubGlobal("setTimeout", ((handler: TimerHandler, ms?: number, ...rest: unknown[]) => {
      if (typeof ms === "number") seen.push(ms);
      return (original as typeof globalThis.setTimeout)(handler, ms, ...rest);
    }) as typeof globalThis.setTimeout);
    installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/" ? json({ csrf_token: "tok" }) : json({ upload: UPLOAD }, 201),
    );

    await uploadInspirationImage("d1", image(), true);

    // The CSRF preflight is a small JSON GET and legitimately keeps the 5s
    // budget; it is the LAST deadline armed — the one guarding the multipart
    // body — that must be the upload budget. Asserting only that the list
    // contains UPLOAD_TIMEOUT_MS would still pass if the POST itself reverted
    // to 5s while some other timer happened to use 60s.
    expect(seen).toEqual([REQUEST_TIMEOUT_MS, UPLOAD_TIMEOUT_MS]);
  });

  it("retries EXACTLY once on a stale CSRF token", async () => {
    let posts = 0;
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: `tok-${posts}` });
      posts += 1;
      if (posts === 1) return json(CSRF_FAILED, 403);
      return json({ upload: UPLOAD }, 201);
    });

    const result = await uploadInspirationImage("d1", image(), true);

    expect(result.ok).toBe(true);
    expect(calls.filter((call) => call.url === POST_URL)).toHaveLength(2);
  });

  it("gives up after one retry rather than looping", async () => {
    const calls = installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/" ? json({ csrf_token: "tok" }) : json(CSRF_FAILED, 403),
    );

    const result = await uploadInspirationImage("d1", image(), true);

    expect(result.ok).toBe(false);
    expect(calls.filter((call) => call.url === POST_URL)).toHaveLength(2);
  });

  it.each([
    ["invalid_image", 400, /JPEG, PNG or single-frame WebP/i],
    ["image_too_large", 413, /too large/i],
    ["duplicate_image", 409, /already added this image/i],
    ["inspiration_limit_reached", 409, /Remove one to add another/i],
    ["upload_throttled", 429, /wait a moment/i],
    ["upload_throttle_unavailable", 503, /temporarily unavailable/i],
    ["storage_unavailable", 503, /could not be stored/i],
    ["rights_not_acknowledged", 400, /Confirm you have the right/i],
  ])("maps %s to text a person can act on", async (code, status, expected) => {
    installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/" ? json({ csrf_token: "tok" }) : json({ error: { code } }, status),
    );

    const result = await uploadInspirationImage("d1", image(), true);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe(code);
      expect(result.message).toMatch(expected);
    }
  });

  it("survives a proxy 413 that is not JSON at all", async () => {
    installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/"
        ? json({ csrf_token: "tok" })
        : new Response("<html>Request Entity Too Large</html>", { status: 413 }),
    );

    const result = await uploadInspirationImage("d1", image(), true);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toBeTruthy();
  });

  it("carries Retry-After through when the server sends one", async () => {
    installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/"
        ? json({ csrf_token: "tok" })
        : json({ error: { code: "upload_throttled" } }, 429, { "Retry-After": "45" }),
    );

    const result = await uploadInspirationImage("d1", image(), true);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.retryAfterSeconds).toBe(45);
  });

  it("refuses a 201 whose body is not a recognisable upload", async () => {
    // Never a false success: a malformed body must not become a preview the
    // user believes is stored.
    installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/" ? json({ csrf_token: "tok" }) : json({ upload: {} }, 201),
    );

    const result = await uploadInspirationImage("d1", image(), true);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("invalid_response");
  });

  it("refuses a 201 that is missing a declared field", async () => {
    // A half-record must not become a preview either: an upload without
    // dimensions renders as a collapsed box the user cannot explain.
    const partial: Record<string, unknown> = { ...UPLOAD };
    delete partial.rights_acknowledged_at;
    installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/"
        ? json({ csrf_token: "tok" })
        : json({ upload: partial }, 201),
    );

    const result = await uploadInspirationImage("d1", image(), true);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("invalid_response");
  });

  it("never exposes a storage key, hash or filename from the response", async () => {
    // The contract exposes none of these; if one ever appeared, it must not be
    // carried into client state. The wrapper projects the six declared fields
    // rather than passing the parsed body through, so this holds even when the
    // server sends more than it should.
    installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/"
        ? json({ csrf_token: "tok" })
        : json(
            {
              upload: {
                ...UPLOAD,
                storage_key: "design-uploads/2026/07/29/secret.webp",
                sha256: "a".repeat(64),
                original_filename: "my-wedding-photo.jpg",
              },
            },
            201,
          ),
    );

    const result = await uploadInspirationImage("d1", image(), true);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(Object.keys(result.upload).sort()).toEqual([
        "created_at",
        "height",
        "id",
        "position",
        "rights_acknowledged_at",
        "width",
      ]);
      expect(JSON.stringify(result.upload)).not.toMatch(/design-uploads|secret|wedding-photo/);
    }
  });
});

describe("removeInspirationUpload", () => {
  it("sends DELETE with the CSRF header", async () => {
    const calls = installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/"
        ? json({ csrf_token: "tok" })
        : new Response(null, { status: 204 }),
    );

    const result = await removeInspirationUpload("d1", "u1");

    expect(result).toEqual({ ok: true });
    const call = calls.find((entry) => entry.url === DELETE_URL);
    expect(call?.init?.method).toBe("DELETE");
    expect((call?.init?.headers as Record<string, string>)["X-CSRFToken"]).toBe("tok");
  });

  it("treats an already-removed upload as removed", async () => {
    // The user asked for it to be gone, and it is gone. Reporting a failure
    // would leave a preview on screen for something that no longer exists.
    installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/"
        ? json({ csrf_token: "tok" })
        : json({ error: { code: "not_found" } }, 404),
    );

    expect(await removeInspirationUpload("d1", "u1")).toEqual({ ok: true });
  });

  it("reports a genuine failure rather than claiming success", async () => {
    installFetchSpy((url) =>
      url === "/api/v1/auth/csrf/"
        ? json({ csrf_token: "tok" })
        : json({ error: { code: "storage_unavailable" } }, 503),
    );

    const result = await removeInspirationUpload("d1", "u1");

    expect(result.ok).toBe(false);
  });

  it("retries exactly once on a stale CSRF token", async () => {
    let attempts = 0;
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "tok" });
      attempts += 1;
      if (attempts === 1) return json(CSRF_FAILED, 403);
      return new Response(null, { status: 204 });
    });

    expect(await removeInspirationUpload("d1", "u1")).toEqual({ ok: true });
    expect(calls.filter((call) => call.url === DELETE_URL)).toHaveLength(2);
  });
});

describe("inspirationUploadImageUrl", () => {
  it("points at the ownership-checked endpoint, relative and same-origin", () => {
    expect(inspirationUploadImageUrl("d1", "u1")).toBe(
      "/api/v1/designs/d1/inspiration-uploads/u1/image/",
    );
  });

  it("never builds an absolute or storage URL", () => {
    const url = inspirationUploadImageUrl("d1", "u1");
    expect(url.startsWith("/api/")).toBe(true);
    expect(url).not.toMatch(/https?:/);
    expect(url).not.toMatch(/amazonaws|minio|X-Amz/i);
  });
});
