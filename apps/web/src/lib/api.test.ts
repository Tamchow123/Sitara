import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  _resetCsrfTokenForTests,
  apiLogin,
  apiLogout,
  fetchMe,
  fetchReadiness,
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
