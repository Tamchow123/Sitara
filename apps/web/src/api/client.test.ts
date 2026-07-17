import { afterEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/api/client";
import type { components, paths } from "@/api/schema";
import { REQUEST_TIMEOUT_MS } from "@/lib/transport";

// ---------------------------------------------------------------------------
// Compile-time contract assertions (checked by `tsc --noEmit`). These are
// exported so they are never flagged as unused; their VALUE is irrelevant —
// the point is that the types resolve (or, for the negative cases, that they
// do NOT). If the generated schema stops matching, typecheck fails.
// ---------------------------------------------------------------------------

// 1 + 2: the generated `paths` type imports and known operations compile.
export type CsrfOperation = paths["/api/v1/auth/csrf/"]["get"];
export type DesignsListOperation = paths["/api/v1/designs/"]["get"];
export type QuestionnaireOperation = paths["/api/v1/questionnaire/active/"]["get"];

// 3: a path that does not exist in the contract must not type-check.
// @ts-expect-error unknown paths are absent from the generated contract.
export type UnknownOperation = paths["/api/v1/does-not-exist/"]["get"];

// 4: the questionnaire response is structurally typed (steps/questions/rules).
export type QuestionnaireSteps = components["schemas"]["QuestionnaireSchema"]["steps"];
export type StepQuestions = components["schemas"]["StepSchema"]["questions"];
export type QuestionnaireRules = components["schemas"]["QuestionnaireSchema"]["rules"];
export type QuestionOptions = components["schemas"]["QuestionSchema"]["options"];

// 5: the public catalogue asset exposes exactly the public fields.
type PublicAsset = components["schemas"]["PublicInspirationAsset"];
export type PublicAssetKeys = keyof PublicAsset;
const _publicAssetShape: Record<PublicAssetKeys, true> = {
  id: true,
  title: true,
  alt_text: true,
  garment_type: true,
  cultural_context: true,
  attribution: true,
  image_url: true,
  thumbnail_url: true,
};
void _publicAssetShape;

// 6: private storage/rights fields are NOT part of any generated public type.
// @ts-expect-error storage keys are never exposed on the public asset type.
export type NoStorageKey = PublicAsset["image_storage_key"];
// @ts-expect-error rights evidence is never exposed on the public asset type.
export type NoRightsEvidence = PublicAsset["evidence_reference"];

// 7: the exported client is GET-ONLY. GET is available and path-typed; the
// unsafe methods are absent so a typed mutation cannot be written here (they
// go through lib/api.ts's CSRF-aware flow).
export type GetIsAvailable = typeof apiClient.GET;
// @ts-expect-error POST is not exposed — unsafe mutations go through lib/api.ts.
export type PostUnavailable = typeof apiClient.POST;
// @ts-expect-error PATCH is not exposed — unsafe mutations go through lib/api.ts.
export type PatchUnavailable = typeof apiClient.PATCH;
// @ts-expect-error PUT is not exposed — unsafe mutations go through lib/api.ts.
export type PutUnavailable = typeof apiClient.PUT;
// @ts-expect-error DELETE is not exposed — unsafe mutations go through lib/api.ts.
export type DeleteUnavailable = typeof apiClient.DELETE;

// ---------------------------------------------------------------------------
// Runtime transport behaviour.
// ---------------------------------------------------------------------------

type FetchCall = { input: RequestInfo | URL; init: RequestInit | undefined };

function installFetchSpy(): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ input, init });
      return new Response(
        JSON.stringify({
          status: "ok",
          checks: { database: "ok", redis: "ok", auth_cache: "ok", storage: "ok" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("generated typed client transport", () => {
  it("targets the SAME origin as the page with an /api/v1/ path — no backend host", async () => {
    const calls = installFetchSpy();
    await apiClient.GET("/api/v1/health/ready");
    expect(calls).toHaveLength(1);
    const request = calls[0].input as Request;
    const url = new URL(request.url);
    expect(url.origin).toBe(window.location.origin);
    expect(url.pathname).toBe("/api/v1/health/ready");
  });

  it("sends same-origin credentials", async () => {
    const calls = installFetchSpy();
    await apiClient.GET("/api/v1/health/ready");
    expect(calls[0].init?.credentials).toBe("same-origin");
  });

  it("never caches responses", async () => {
    const calls = installFetchSpy();
    await apiClient.GET("/api/v1/health/ready");
    expect(calls[0].init?.cache).toBe("no-store");
  });

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
    const pending = apiClient.GET("/api/v1/health/ready").catch(() => undefined);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 100);
    expect(captured?.aborted).toBe(true);
    vi.useRealTimers();
    await pending;
  });

  it("returns typed data for a known GET operation", async () => {
    installFetchSpy();
    const { data } = await apiClient.GET("/api/v1/health/ready");
    expect(data?.status).toBe("ok");
    expect(data?.checks.database).toBe("ok");
  });
});
