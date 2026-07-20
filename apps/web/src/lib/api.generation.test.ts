import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  _resetCsrfTokenForTests,
  fetchGenerationJob,
  GenerationJobMalformedError,
  GenerationJobNotFoundError,
  GenerationJobUnavailableError,
  startDesignGeneration,
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

const JOB = {
  job: {
    id: "j1",
    design_id: "d1",
    design_version_id: null,
    status: "queued",
    error_code: null,
    generation_kind: "initial",
    created_at: "t",
    updated_at: "t",
    started_at: null,
    completed_at: null,
  },
};

describe("startDesignGeneration", () => {
  it("sends CSRF and Idempotency-Key headers on POST generate", async () => {
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "tok" });
      return json(JOB, 202);
    });
    const result = await startDesignGeneration("d1", "11111111-1111-1111-1111-111111111111");
    expect(result.ok).toBe(true);
    const generate = calls.find((c) => c.url === "/api/v1/designs/d1/generate/");
    expect(generate?.init?.method).toBe("POST");
    const headers = generate?.init?.headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBe("tok");
    expect(headers["Idempotency-Key"]).toBe("11111111-1111-1111-1111-111111111111");
    // No job id or token is persisted to browser storage.
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("retries exactly once on a stale CSRF token", async () => {
    let generateCalls = 0;
    const calls = installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "fresh" });
      generateCalls += 1;
      if (generateCalls === 1) return json(CSRF_FAILED, 403);
      return json(JOB, 202);
    });
    const result = await startDesignGeneration("d1", "22222222-2222-2222-2222-222222222222");
    expect(result.ok).toBe(true);
    expect(generateCalls).toBe(2);
    // Two CSRF bootstraps: initial + after the 403 clears the token.
    expect(calls.filter((c) => c.url === "/api/v1/auth/csrf/").length).toBe(2);
  });

  it("maps a controlled error body to a typed failure", async () => {
    installFetchSpy((url) => {
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "tok" });
      return json({ error: { code: "generation_in_progress", message: "busy" } }, 409);
    });
    const result = await startDesignGeneration("d1", "33333333-3333-3333-3333-333333333333");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(409);
      expect(result.code).toBe("generation_in_progress");
    }
  });
});

describe("fetchGenerationJob", () => {
  it("GETs the job endpoint and returns the job", async () => {
    const calls = installFetchSpy(() => json(JOB, 200));
    const job = await fetchGenerationJob("j1");
    expect(job.id).toBe("j1");
    expect(calls[0].url).toBe("/api/v1/jobs/j1/");
    expect(calls[0].init?.method ?? "GET").toBe("GET");
  });

  it("throws GenerationJobNotFoundError on a 404", async () => {
    installFetchSpy(() => json({ error: { code: "not_found", message: "x" } }, 404));
    await expect(fetchGenerationJob("missing")).rejects.toBeInstanceOf(
      GenerationJobNotFoundError,
    );
  });

  it("throws GenerationJobUnavailableError on a 503", async () => {
    installFetchSpy(() => json({ error: { code: "unavailable", message: "x" } }, 503));
    await expect(fetchGenerationJob("j1")).rejects.toBeInstanceOf(GenerationJobUnavailableError);
  });

  it("throws GenerationJobUnavailableError on a network/timeout failure", async () => {
    installFetchSpy(() => {
      throw new TypeError("network down");
    });
    await expect(fetchGenerationJob("j1")).rejects.toBeInstanceOf(GenerationJobUnavailableError);
  });

  it("throws GenerationJobMalformedError on non-JSON output", async () => {
    installFetchSpy(
      () =>
        new Response("<html>proxy error</html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
    );
    await expect(fetchGenerationJob("j1")).rejects.toBeInstanceOf(GenerationJobMalformedError);
  });

  it("throws GenerationJobMalformedError when the job shape is invalid", async () => {
    installFetchSpy(() => json({ job: { id: "j1" } }, 200));
    await expect(fetchGenerationJob("j1")).rejects.toBeInstanceOf(GenerationJobMalformedError);
  });

  it("throws GenerationJobMalformedError when the envelope has no job key", async () => {
    installFetchSpy(() => json({ nope: true }, 200));
    await expect(fetchGenerationJob("j1")).rejects.toBeInstanceOf(GenerationJobMalformedError);
  });

  it("throws GenerationJobMalformedError when status is an unrecognised value", async () => {
    installFetchSpy(() => json({ job: { ...JOB.job, status: "cancelled" } }, 200));
    await expect(fetchGenerationJob("j1")).rejects.toBeInstanceOf(GenerationJobMalformedError);
  });
});
