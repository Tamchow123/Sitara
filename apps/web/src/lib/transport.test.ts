import { afterEach, describe, expect, it, vi } from "vitest";

import { REQUEST_TIMEOUT_MS, fetchWithTimeout } from "@/lib/transport";

type FetchCall = { input: RequestInfo | URL; init: RequestInit | undefined };

function installFetchSpy(): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ input, init });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

function headersOf(call: FetchCall): Record<string, string> {
  return (call.init?.headers ?? {}) as Record<string, string>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchWithTimeout header handling", () => {
  it("preserves headers already present on an incoming Request", async () => {
    const calls = installFetchSpy();
    const request = new Request("http://localhost/api/v1/health/ready", {
      headers: { "X-Custom": "kept" },
    });
    await fetchWithTimeout(request);
    // Request header names are normalised to lower case.
    expect(headersOf(calls[0])["x-custom"]).toBe("kept");
  });

  it("merges init.headers over an incoming Request's headers", async () => {
    const calls = installFetchSpy();
    const request = new Request("http://localhost/api/v1/health/ready", {
      headers: { "X-Custom": "from-request" },
    });
    await fetchWithTimeout(request, { headers: { "x-custom": "from-init" } });
    expect(headersOf(calls[0])["x-custom"]).toBe("from-init");
  });

  it("adds Accept: application/json only when absent", async () => {
    const calls = installFetchSpy();
    await fetchWithTimeout("/api/v1/health/ready");
    expect(headersOf(calls[0]).Accept).toBe("application/json");
  });

  it("preserves a caller-supplied Accept header (no duplicate/override)", async () => {
    const calls = installFetchSpy();
    await fetchWithTimeout("/api/v1/health/ready", { headers: { Accept: "text/html" } });
    const headers = headersOf(calls[0]);
    expect(headers.Accept).toBe("text/html");
    // No second lower-cased default was injected alongside it.
    expect(headers.accept).toBeUndefined();
  });

  it("preserves original casing of a caller's plain-object headers", async () => {
    const calls = installFetchSpy();
    await fetchWithTimeout("/api/v1/auth/login/", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": "tok" },
    });
    const headers = headersOf(calls[0]);
    expect(headers["X-CSRFToken"]).toBe("tok");
    expect(headers["Content-Type"]).toBe("application/json");
  });
});

describe("fetchWithTimeout non-negotiable policy", () => {
  it("forces same-origin credentials even when init tries to override", async () => {
    const calls = installFetchSpy();
    await fetchWithTimeout("/api/v1/health/ready", { credentials: "include" });
    expect(calls[0].init?.credentials).toBe("same-origin");
  });

  it("forces no-store caching even when init tries to override", async () => {
    const calls = installFetchSpy();
    await fetchWithTimeout("/api/v1/health/ready", { cache: "force-cache" });
    expect(calls[0].init?.cache).toBe("no-store");
  });
});

describe("fetchWithTimeout timeout", () => {
  it("aborts the request after the shared timeout", async () => {
    vi.useFakeTimers();
    let captured: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            captured = init?.signal ?? undefined;
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("The operation was aborted.", "AbortError")),
            );
          }),
      ),
    );
    const pending = fetchWithTimeout("/api/v1/health/ready").catch(() => undefined);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 100);
    expect(captured?.aborted).toBe(true);
    vi.useRealTimers();
    await pending;
  });
});
