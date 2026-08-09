import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  _resetCsrfTokenForTests,
  apiLogin,
  apiLogout,
  clearAnnotations,
  fetchAnnotations,
  fetchMe,
  fetchReadiness,
  saveAnnotations,
  sendRenderToAccount,
} from "./api";

type FetchCall = { url: string; init: RequestInit | undefined };

const CSRF_BODY = { csrf_token: "bootstrap-token" };
const LOGIN_OK_BODY = {
  authenticated: true,
  user: { id: "11111111-1111-4111-8111-111111111111", email: "bride@example.com" },
  csrf_token: "rotated-token",
};
const LOGOUT_BODY = {
  authenticated: false,
  user: null,
  csrf_token: "anonymous-token",
};
const CSRF_FAILED_BODY = {
  error: {
    code: "csrf_failed",
    message: "The security token is missing or invalid. Refresh and try again.",
  },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
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

function defaultHandler(url: string): Response {
  if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
  if (url === "/api/v1/auth/login/") return json(LOGIN_OK_BODY);
  if (url === "/api/v1/auth/logout/") return json(LOGOUT_BODY);
  if (url === "/api/v1/auth/me/") return json({ authenticated: false, user: null });
  return json({ status: "ok", checks: { database: "ok", redis: "ok", storage: "ok" } });
}

beforeEach(() => {
  _resetCsrfTokenForTests();
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("same-origin API client", () => {
  it("uses relative /api/ paths — never an absolute backend origin", async () => {
    const calls = installFetchSpy(defaultHandler);
    await fetchReadiness();
    await fetchMe();
    await apiLogin("bride@example.com", "Correct-Horse-Battery-2026!");
    expect(calls.length).toBeGreaterThanOrEqual(3);
    for (const call of calls) {
      expect(call.url).toMatch(/^\/api\//);
      expect(call.url).not.toMatch(/^https?:\/\//);
    }
  });

  it("sends same-origin credentials on every request", async () => {
    const calls = installFetchSpy(defaultHandler);
    await fetchMe();
    await apiLogin("bride@example.com", "Correct-Horse-Battery-2026!");
    for (const call of calls) {
      expect(call.init?.credentials).toBe("same-origin");
    }
  });

  it("bootstraps a CSRF token before the first unsafe request", async () => {
    const calls = installFetchSpy(defaultHandler);
    await apiLogin("bride@example.com", "Correct-Horse-Battery-2026!");
    expect(calls[0].url).toBe("/api/v1/auth/csrf/");
    expect(calls[1].url).toBe("/api/v1/auth/login/");
    expect(calls[1].init?.method).toBe("POST");
  });

  it("sends the X-CSRFToken header on unsafe requests", async () => {
    const calls = installFetchSpy(defaultHandler);
    await apiLogin("bride@example.com", "Correct-Horse-Battery-2026!");
    const headers = calls[1].init?.headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBe("bootstrap-token");
  });

  it("caches the rotated token after login and reuses it without re-bootstrapping", async () => {
    const calls = installFetchSpy(defaultHandler);
    const result = await apiLogin("bride@example.com", "Correct-Horse-Battery-2026!");
    expect(result.ok).toBe(true);
    await apiLogout();
    // csrf bootstrap, login, logout — no second bootstrap.
    expect(calls.map((c) => c.url)).toEqual([
      "/api/v1/auth/csrf/",
      "/api/v1/auth/login/",
      "/api/v1/auth/logout/",
    ]);
    const logoutHeaders = calls[2].init?.headers as Record<string, string>;
    expect(logoutHeaders["X-CSRFToken"]).toBe("rotated-token");
  });

  it("retries a csrf_failed response exactly once and cannot loop", async () => {
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      return json(CSRF_FAILED_BODY, 403); // login remains rejected every time
    });
    const result = await apiLogin("bride@example.com", "Correct-Horse-Battery-2026!");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("csrf_failed");
    // bootstrap → post → re-bootstrap → post. Exactly four calls, then stop.
    expect(calls.map((c) => c.url)).toEqual([
      "/api/v1/auth/csrf/",
      "/api/v1/auth/login/",
      "/api/v1/auth/csrf/",
      "/api/v1/auth/login/",
    ]);
  });

  it("never persists passwords or tokens in browser storage", async () => {
    installFetchSpy(defaultHandler);
    await apiLogin("bride@example.com", "Correct-Horse-Battery-2026!");
    await apiLogout();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe("apiLogout confirmation requirements", () => {
  function logoutHandler(response: () => Response | Promise<Response>) {
    return installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      if (url === "/api/v1/auth/logout/") return response();
      return json({}, 404);
    });
  }

  it("succeeds only on a confirmed 200 logout body and replaces the token", async () => {
    const calls = logoutHandler(() => json(LOGOUT_BODY));
    const result = await apiLogout();
    expect(result.ok).toBe(true);
    // The in-memory CSRF token is now the fresh anonymous token: the next
    // unsafe request reuses it without a new bootstrap.
    await apiLogout();
    const logoutCalls = calls.filter((c) => c.url === "/api/v1/auth/logout/");
    const headers = logoutCalls[1].init?.headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBe("anonymous-token");
    expect(calls.filter((c) => c.url === "/api/v1/auth/csrf/")).toHaveLength(1);
  });

  it("fails on network errors without claiming success", async () => {
    logoutHandler(() => {
      throw new TypeError("fetch failed");
    });
    const result = await apiLogout();
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("unavailable");
  });

  it("fails on timeout without claiming success", async () => {
    vi.useFakeTimers();
    installFetchSpy((url, init) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject(new DOMException("The operation was aborted.", "AbortError")),
        );
      });
    });
    const pending = apiLogout();
    await vi.advanceTimersByTimeAsync(6000);
    vi.useRealTimers();
    const result = await pending;
    expect(result.ok).toBe(false);
  });

  it("fails on an HTTP 500 JSON response", async () => {
    logoutHandler(() =>
      json({ error: { code: "auth_unavailable", message: "unavailable" } }, 500),
    );
    const result = await apiLogout();
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("auth_unavailable");
  });

  it("fails on a malformed 200 response body", async () => {
    logoutHandler(
      () =>
        new Response("<html>proxy error</html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
    );
    const result = await apiLogout();
    expect(result.ok).toBe(false);
  });

  it("fails on a 200 body that does not prove the session is gone", async () => {
    // authenticated:true or a missing token is NOT a confirmed logout.
    logoutHandler(() =>
      json({ authenticated: true, user: null, csrf_token: "t" }),
    );
    expect((await apiLogout()).ok).toBe(false);
    _resetCsrfTokenForTests();
    logoutHandler(() => json({ authenticated: false, user: null, csrf_token: "" }));
    expect((await apiLogout()).ok).toBe(false);
  });

  it("fails after the single CSRF retry without looping", async () => {
    const calls = logoutHandler(() => json(CSRF_FAILED_BODY, 403));
    const result = await apiLogout();
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("csrf_failed");
    expect(calls.map((c) => c.url)).toEqual([
      "/api/v1/auth/csrf/",
      "/api/v1/auth/logout/",
      "/api/v1/auth/csrf/",
      "/api/v1/auth/logout/",
    ]);
  });
});

describe("fetchMe strict session-state validation", () => {
  function meHandler(response: () => Response | Promise<Response>) {
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/me/") return response();
      return json({}, 404);
    });
  }

  it("resolves a valid authenticated response", async () => {
    meHandler(() =>
      json({ authenticated: true, user: { id: "u1", email: "bride@example.com" } }),
    );
    const me = await fetchMe();
    expect(me.authenticated).toBe(true);
    expect(me.user?.email).toBe("bride@example.com");
  });

  it("resolves a valid anonymous response (incl. a flushed stale session)", async () => {
    // A browser holding a stale sitara_sessionid gets exactly this body —
    // it must land in `anonymous`, never `unavailable`.
    meHandler(() => json({ authenticated: false, user: null }));
    const me = await fetchMe();
    expect(me.authenticated).toBe(false);
    expect(me.user).toBeNull();
  });

  it.each([401, 403, 429, 500, 503])(
    "rejects an HTTP %i response so the provider shows unavailable",
    async (status) => {
      meHandler(() => json({ error: { code: "x", message: "y" } }, status));
      await expect(fetchMe()).rejects.toThrow();
    },
  );

  it("rejects invalid JSON", async () => {
    meHandler(
      () =>
        new Response("<html>bad gateway</html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
    );
    await expect(fetchMe()).rejects.toThrow();
  });

  it("rejects malformed 200 shapes", async () => {
    for (const body of [
      {},
      { authenticated: true, user: null },
      { authenticated: false, user: { id: "u1", email: "x@example.com" } },
      { authenticated: true, user: { id: "", email: "" } },
      { authenticated: "yes", user: null },
    ]) {
      meHandler(() => json(body));
      await expect(fetchMe()).rejects.toThrow();
    }
  });
});

// ---------------------------------------------------------------------------
// Phase 19 — the annotation and render-send client
// ---------------------------------------------------------------------------
//
// Driven here at the fetch boundary rather than through the workspace, because
// the workspace test mocks `@/lib/api` wholesale: every branch below — the 409
// that must become a distinguishable conflict, the CSRF retry that must happen
// exactly once, the absence of any recipient field — is invisible to that suite
// and would survive being deleted.

const DESIGN = "22222222-2222-4222-8222-222222222222";
const VERSION = "33333333-3333-4333-8333-333333333333";
const ANNOTATIONS_PATH = `/api/v1/designs/${DESIGN}/versions/${VERSION}/annotations/`;

const STORED_DOCUMENT = {
  schema_version: 1,
  image_width: 896,
  image_height: 1152,
  items: [
    {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      type: "pin",
      geometry: { point: { x: 0.5, y: 0.25 } },
      note: "Raise the neckline",
      palette: "terracotta",
      created_order: 1,
    },
  ],
  revision: 4,
  updated_at: "2026-08-06T10:00:00Z",
};

const WRITE_BODY = {
  schema_version: 1,
  image_width: 896,
  image_height: 1152,
  items: [],
  expected_revision: 4,
};

describe("annotation document client", () => {
  it("GETs the ownership-scoped path and unwraps the document", async () => {
    const calls = installFetchSpy((url) =>
      url === ANNOTATIONS_PATH ? json({ annotations: STORED_DOCUMENT }) : json({}, 404),
    );

    const document = await fetchAnnotations(DESIGN, VERSION);

    expect(document.revision).toBe(4);
    // The canonical dimensions come from the server, never from the <img>. If
    // this ever stopped being unwrapped the coordinate maths would silently
    // start dividing by undefined.
    expect(document.image_width).toBe(896);
    expect(document.items).toHaveLength(1);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe(ANNOTATIONS_PATH);
    expect(calls[0]!.init?.method ?? "GET").toBe("GET");
  });

  it("percent-encodes both identifiers rather than interpolating them raw", async () => {
    const calls = installFetchSpy(() => json({ annotations: STORED_DOCUMENT }));
    await fetchAnnotations("a/../b", "c?d=e");
    expect(calls[0]!.url).toBe("/api/v1/designs/a%2F..%2Fb/versions/c%3Fd%3De/annotations/");
  });

  it("PUTs with a CSRF header and the caller's expected_revision", async () => {
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      return json({ annotations: { ...STORED_DOCUMENT, revision: 5 } });
    });

    const result = await saveAnnotations(DESIGN, VERSION, WRITE_BODY);

    expect(result).toEqual({ ok: true, document: { ...STORED_DOCUMENT, revision: 5 } });
    const put = calls.find((call) => call.init?.method === "PUT")!;
    expect(put.url).toBe(ANNOTATIONS_PATH);
    expect((put.init?.headers as Record<string, string>)["X-CSRFToken"]).toBe("bootstrap-token");
    expect(JSON.parse(String(put.init?.body))).toMatchObject({ expected_revision: 4 });
    expect(put.init?.credentials).toBe("same-origin");
  });

  it("turns a 409 into a distinguishable conflict carrying the server's revision", async () => {
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      return json(
        {
          error: { code: "annotation_conflict", message: "Changed somewhere else." },
          revision: 9,
        },
        409,
      );
    });

    const result = await saveAnnotations(DESIGN, VERSION, WRITE_BODY);

    // Not `failed`: the UI offers reload-or-discard only for this kind, and a
    // conflict misreported as a failure would wedge into a retry loop against a
    // revision that can never win.
    expect(result).toEqual({
      ok: false,
      kind: "conflict",
      revision: 9,
      message: "Changed somewhere else.",
    });
  });

  it("falls back to revision 0 when a conflict body omits or mistypes it", async () => {
    for (const revision of [undefined, "9", null]) {
      installFetchSpy((url) => {
        if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
        return json({ error: { code: "annotation_conflict" }, revision }, 409);
      });
      _resetCsrfTokenForTests();
      const result = await saveAnnotations(DESIGN, VERSION, WRITE_BODY);
      // 0 forces a full reload rather than letting a malformed body become a
      // revision the client would then send back as authoritative.
      expect(result).toMatchObject({ ok: false, kind: "conflict", revision: 0 });
    }
  });

  it("reports any other rejection as failed, not as a conflict", async () => {
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      return json({ error: { code: "annotation_invalid", message: "Too many marks." } }, 400);
    });

    const result = await saveAnnotations(DESIGN, VERSION, WRITE_BODY);

    expect(result).toEqual({
      ok: false,
      kind: "failed",
      status: 400,
      code: "annotation_invalid",
      message: "Too many marks.",
    });
  });

  it("never reports a save as succeeded when the transport threw", async () => {
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      throw new Error("network down");
    });

    const result = await saveAnnotations(DESIGN, VERSION, WRITE_BODY);

    // status 0 records "we do not know" rather than inventing an HTTP code. The
    // write may have landed; the next attempt's 409 is the recoverable path.
    expect(result).toMatchObject({ ok: false, kind: "failed", status: 0, code: "unavailable" });
  });

  it("clears with a DELETE and treats 204 (no body) as success", async () => {
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      return new Response(null, { status: 204 });
    });

    await expect(clearAnnotations(DESIGN, VERSION)).resolves.toEqual({ ok: true });

    const del = calls.find((call) => call.init?.method === "DELETE")!;
    expect(del.url).toBe(ANNOTATIONS_PATH);
    expect((del.init?.headers as Record<string, string>)["X-CSRFToken"]).toBe("bootstrap-token");
    // A 204 has no body at all. Parsing one unguarded would throw and turn a
    // successful clear into a reported failure.
  });

  it("retries a stale-token DELETE exactly once, then reports the failure", async () => {
    let tokens = 0;
    let deletes = 0;
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") {
        tokens += 1;
        return json({ csrf_token: `token-${tokens}` });
      }
      deletes += 1;
      return json({ error: { code: "csrf_failed", message: "Refresh and try again." } }, 403);
    });

    const result = await clearAnnotations(DESIGN, VERSION);

    expect(deletes).toBe(2);
    expect(tokens).toBe(2);
    expect(result).toEqual({ ok: false, message: "Refresh and try again." });
  });

  it("does not retry a DELETE rejected for a reason other than a stale token", async () => {
    let deletes = 0;
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      deletes += 1;
      return json({ error: { code: "permission_denied", message: "No." } }, 403);
    });

    await clearAnnotations(DESIGN, VERSION);

    expect(deletes).toBe(1);
  });

  it("reports a thrown DELETE as a failure rather than a silent clear", async () => {
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      throw new Error("network down");
    });

    await expect(clearAnnotations(DESIGN, VERSION)).resolves.toMatchObject({ ok: false });
  });
});

describe("sendRenderToAccount", () => {
  function sendHandler(response: () => Response): FetchCall[] {
    return installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      return response();
    });
  }

  it("posts to the plain endpoint with no recipient anywhere in the request", async () => {
    const calls = sendHandler(() => json({ send: { status: "queued" } }, 202));

    await expect(sendRenderToAccount(DESIGN, VERSION, "plain")).resolves.toEqual({ ok: true });

    const post = calls.find((call) => call.init?.method === "POST")!;
    expect(post.url).toBe(`/api/v1/designs/${DESIGN}/versions/${VERSION}/send/`);
    // The whole point of the endpoint's shape: the recipient is read from the
    // signed-in account server-side. An address reaching the wire from here — in
    // the body, the query string or a header — would make this an open relay, so
    // the entire serialised request is checked for an "@" rather than a
    // particular field name that a future refactor could rename.
    const serialised = JSON.stringify({
      url: post.url,
      body: post.init?.body ?? null,
      headers: post.init?.headers ?? null,
    });
    expect(serialised).not.toContain("@");
    expect(JSON.parse(String(post.init?.body))).toEqual({});
    expect(post.init?.credentials).toBe("same-origin");
  });

  it("posts to the annotated endpoint for the flattened render", async () => {
    const calls = sendHandler(() => json({ send: { status: "queued" } }, 202));
    await sendRenderToAccount(DESIGN, VERSION, "annotated");
    const post = calls.find((call) => call.init?.method === "POST")!;
    expect(post.url).toBe(`/api/v1/designs/${DESIGN}/versions/${VERSION}/annotations/send/`);
  });

  it.each([
    ["email_recipient_unavailable", 409, "recipient_unavailable"],
    ["email_delivery_disabled", 503, "disabled"],
    ["design_image_not_ready", 409, "not_ready"],
  ] as const)("maps %s to the %s refusal", async (code, status, kind) => {
    sendHandler(() => json({ error: { code, message: "Not now." } }, status));
    // Each of these is a different situation and only one of them is the user's
    // to fix, so they must not collapse into one generic failure.
    await expect(sendRenderToAccount(DESIGN, VERSION, "plain")).resolves.toEqual({
      ok: false,
      kind,
      message: "Not now.",
    });
  });

  it("maps a 429 to throttled even when the code is unfamiliar", async () => {
    sendHandler(() => json({ error: { code: "send_throttled", message: "Slow down." } }, 429));
    await expect(sendRenderToAccount(DESIGN, VERSION, "plain")).resolves.toEqual({
      ok: false,
      kind: "throttled",
      message: "Slow down.",
    });
  });

  it("falls back to a safe message when the error body is unusable", async () => {
    sendHandler(() => json({}, 500));
    const result = await sendRenderToAccount(DESIGN, VERSION, "plain");
    expect(result).toMatchObject({ ok: false, kind: "failed" });
    // Never a raw status or a server string — and never an address.
    expect(String((result as { message: string }).message)).not.toContain("@");
  });

  it("reports a thrown request as failed and says nothing was sent", async () => {
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json(CSRF_BODY);
      throw new Error("network down");
    });
    const result = await sendRenderToAccount(DESIGN, VERSION, "plain");
    expect(result).toMatchObject({ ok: false, kind: "failed" });
    expect((result as { message: string }).message).toMatch(/nothing was sent/i);
  });

  it("writes nothing to browser storage on the way through", async () => {
    sendHandler(() => json({ send: { status: "queued" } }, 202));
    await sendRenderToAccount(DESIGN, VERSION, "annotated");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});
