import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  _resetCsrfTokenForTests,
  createDesignDraft,
  updateDesignDraft,
  validateDesignDraft,
} from "./api";

type FetchCall = { url: string; init: RequestInit | undefined };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const CSRF_FAILED = {
  error: { code: "csrf_failed", message: "The security token is missing or invalid." },
};

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

const DETAIL = {
  id: "d1",
  title: "",
  status: "draft",
  questionnaire: null,
  answers: {},
  selected_inspirations: [],
  created_at: "t",
  updated_at: "t",
};

describe("design draft wrappers", () => {
  it("bootstraps CSRF and sends the X-CSRFToken header on create", async () => {
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "tok" });
      return json(DETAIL, 201);
    });
    const result = await createDesignDraft({ title: "x" });
    expect(result.ok).toBe(true);
    const create = calls.find((c) => c.url === "/api/v1/designs/");
    expect(create?.init?.method).toBe("POST");
    expect(
      (create?.init?.headers as Record<string, string>)["X-CSRFToken"],
    ).toBe("tok");
  });

  it("updates via PATCH", async () => {
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "tok" });
      return json(DETAIL);
    });
    const result = await updateDesignDraft("d1", { answers: { garment_type: "lehenga" } });
    expect(result.ok).toBe(true);
    const patch = calls.find((c) => c.url === "/api/v1/designs/d1/");
    expect(patch?.init?.method).toBe("PATCH");
  });

  it("retries a csrf_failed response EXACTLY once and cannot loop", async () => {
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "tok" });
      return json(CSRF_FAILED, 403); // always fails
    });
    const result = await updateDesignDraft("d1", { title: "y" });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("csrf_failed");
    // Two CSRF bootstraps + two PATCH attempts (initial + one retry) only.
    const bootstraps = calls.filter((c) => c.url === "/api/v1/auth/csrf/");
    const patches = calls.filter((c) => c.url === "/api/v1/designs/d1/");
    expect(bootstraps).toHaveLength(2);
    expect(patches).toHaveLength(2);
  });

  it("returns a controlled failure with field errors on a 400", async () => {
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "tok" });
      return json(
        { error: { code: "validation_failed", message: "bad", fields: { garment_type: ["nope"] } } },
        400,
      );
    });
    const result = await createDesignDraft({ answers: { garment_type: "x" } });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe("validation_failed");
      expect(result.fields?.garment_type).toEqual(["nope"]);
    }
  });

  it("turns a network/timeout error into a controlled failure (never a false success)", async () => {
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "tok" });
      throw new TypeError("network down");
    });
    const result = await validateDesignDraft("d1");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("unavailable");
  });
});
