// Same-origin API client. All requests use RELATIVE /api/* paths through
// the Next.js rewrite — the browser never needs the Django host, and no
// NEXT_PUBLIC_* backend URL exists.
//
// This module keeps the CSRF-aware unsafe-request flow (register/login/
// logout and, later, design mutations): CSRF tokens live in MEMORY ONLY
// (never localStorage/sessionStorage/IndexedDB); the session itself is an
// HttpOnly cookie the JS cannot read. Safe GETs may go through the generated
// typed client (api/client.ts); both share the transport below.

import { apiClient } from "@/api/client";
import type { components } from "@/api/schema";
import { REQUEST_TIMEOUT_MS, fetchWithTimeout } from "@/lib/transport";

export { REQUEST_TIMEOUT_MS };

let csrfToken: string | null = null;

export function _resetCsrfTokenForTests(): void {
  csrfToken = null;
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetchWithTimeout(path);
  // Readiness intentionally returns 503 with a JSON body when a dependency
  // is down — still displayable state, not a thrown error.
  return (await response.json()) as T;
}

async function ensureCsrfToken(): Promise<string> {
  if (csrfToken) return csrfToken;
  const data = await getJson<{ csrf_token: string }>("/api/v1/auth/csrf/");
  csrfToken = data.csrf_token;
  return csrfToken;
}

export type ApiEnvelope<T> = {
  ok: boolean;
  status: number;
  data: T;
};

type ErrorBody = {
  error?: { code?: string; message?: string; fields?: Record<string, string[]> };
};

// One CSRF-aware unsafe request for BOTH POST and PATCH. The token lives in
// memory only; on a stale-token 403 it clears, re-bootstraps and retries
// EXACTLY once. Same-origin/no-store/5s-timeout all come from the shared
// transport. Malformed JSON or a network/timeout error throws (the caller
// turns that into a controlled failure — never a false success).
async function sendJson<T>(
  method: "POST" | "PATCH",
  path: string,
  body: unknown,
  hasRetried = false,
): Promise<ApiEnvelope<T>> {
  const token = await ensureCsrfToken();
  const response = await fetchWithTimeout(path, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRFToken": token },
    body: JSON.stringify(body),
  });
  const data = (await response.json()) as T & ErrorBody;
  if (
    response.status === 403 &&
    data?.error?.code === "csrf_failed" &&
    !hasRetried
  ) {
    // Stale token: clear, bootstrap a fresh one, retry EXACTLY once.
    csrfToken = null;
    return sendJson<T>(method, path, body, true);
  }
  return { ok: response.ok, status: response.status, data };
}

function postJson<T>(path: string, body: unknown): Promise<ApiEnvelope<T>> {
  return sendJson<T>("POST", path, body);
}

// ---------------------------------------------------------------------------
// Platform status (unchanged behaviour, now same-origin)
// ---------------------------------------------------------------------------

// Server wire types are ALIASES of the generated OpenAPI schema — never
// hand-maintained duplicates (Phase 6). Change the backend serializer and
// regenerate; these follow automatically.
export type ReadyChecks = components["schemas"]["ReadyChecks"];
export type ReadyResponse = components["schemas"]["ReadyResponse"];
export type PublicConfig = components["schemas"]["PublicConfig"];

export function fetchReadiness(): Promise<ReadyResponse> {
  return getJson<ReadyResponse>("/api/v1/health/ready");
}

export function fetchPublicConfig(): Promise<PublicConfig> {
  return getJson<PublicConfig>("/api/v1/config/public");
}

// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------

export type AuthUser = components["schemas"]["AuthUser"];
export type MeResponse = components["schemas"]["MeResponse"];

export type AuthFailure = {
  ok: false;
  code: string;
  message: string;
  fields?: Record<string, string[]>;
};
export type AuthSuccess = { ok: true; user: AuthUser };
export type AuthResult = AuthSuccess | AuthFailure;

type AuthBody = {
  authenticated?: boolean;
  user?: AuthUser | null;
  csrf_token?: string;
} & ErrorBody;

function toFailure(status: number, body: ErrorBody): AuthFailure {
  return {
    ok: false,
    code: body.error?.code ?? (status >= 500 ? "unavailable" : "unknown_error"),
    message:
      body.error?.message ?? "Something went wrong. Please try again shortly.",
    fields: body.error?.fields,
  };
}

function isAuthUser(value: unknown): value is AuthUser {
  if (typeof value !== "object" || value === null) return false;
  const user = value as Record<string, unknown>;
  return (
    typeof user.id === "string" &&
    user.id.length > 0 &&
    typeof user.email === "string" &&
    user.email.length > 0
  );
}

function isMeResponse(value: unknown): value is MeResponse {
  if (typeof value !== "object" || value === null) return false;
  const body = value as Record<string, unknown>;
  if (body.authenticated === false) return body.user === null;
  if (body.authenticated === true) return isAuthUser(body.user);
  return false;
}

// Session-state bootstrap must be STRICT: anything other than a valid 200
// me-response (401/403/429/5xx, timeout, invalid JSON, malformed shape)
// rejects, so the auth provider shows "unavailable" instead of silently
// treating a broken backend as a signed-out user. A genuine HTTP 200
// anonymous body still resolves to anonymous. (Readiness intentionally
// stays status-agnostic — its 503 body is displayable state.)
export async function fetchMe(): Promise<MeResponse> {
  const response = await fetchWithTimeout("/api/v1/auth/me/");
  if (response.status !== 200) {
    throw new Error("session state unavailable");
  }
  const body: unknown = await response.json();
  if (!isMeResponse(body)) {
    throw new Error("session state unavailable");
  }
  return body;
}

export async function apiLogin(email: string, password: string): Promise<AuthResult> {
  const { ok, status, data } = await postJson<AuthBody>("/api/v1/auth/login/", {
    email,
    password,
  });
  if (ok && data.user && data.csrf_token) {
    csrfToken = data.csrf_token; // rotated by Django on login
    return { ok: true, user: data.user };
  }
  return toFailure(status, data);
}

export async function apiRegister(
  email: string,
  password: string,
  passwordConfirm: string,
): Promise<AuthResult> {
  const { ok, status, data } = await postJson<AuthBody>("/api/v1/auth/register/", {
    email,
    password,
    password_confirm: passwordConfirm,
  });
  if (ok && data.user && data.csrf_token) {
    csrfToken = data.csrf_token; // rotated by Django on login
    return { ok: true, user: data.user };
  }
  return toFailure(status, data);
}

export type LogoutResult = { ok: true } | AuthFailure;

// Logout succeeds ONLY on a confirmed server response: HTTP 200 with a
// valid body proving the session is gone (authenticated exactly false,
// user null, non-empty rotated anonymous token). A timeout, network error,
// malformed body, 5xx or repeated CSRF failure returns a typed failure and
// must never be reported as a successful sign-out — the Django session may
// still be active. The cached CSRF token is left alone on failure (postJson
// already refreshed it if the failure was CSRF-related).
export async function apiLogout(): Promise<LogoutResult> {
  let envelope: ApiEnvelope<AuthBody>;
  try {
    envelope = await postJson<AuthBody>("/api/v1/auth/logout/", {});
  } catch {
    // Timeout, network failure or invalid JSON — no confirmation exists.
    return {
      ok: false,
      code: "unavailable",
      message: "The service could not be reached.",
    };
  }
  const { status, data } = envelope;
  if (
    status === 200 &&
    data.authenticated === false &&
    data.user === null &&
    typeof data.csrf_token === "string" &&
    data.csrf_token.length > 0
  ) {
    // Django flushed the session and issued a fresh anonymous token.
    csrfToken = data.csrf_token;
    return { ok: true };
  }
  return toFailure(status, data);
}

// ---------------------------------------------------------------------------
// Design drafts (Phase 7) — CSRF-aware unsafe operations
// ---------------------------------------------------------------------------
//
// Explicit typed wrappers (NOT a generic exported POST/PATCH client): the
// generated api/client.ts stays GET-only, and every unsafe design mutation
// goes through the tested in-memory-CSRF, retry-once flow above. Request and
// response types are the generated OpenAPI components, so they cannot drift.

export type DesignDraft = components["schemas"]["DesignDetailResponse"];
export type DesignWriteRequest = components["schemas"]["DesignWriteRequest"];
export type DesignValidationSuccess = components["schemas"]["DesignValidationSuccess"];
export type SelectedInspiration = components["schemas"]["SelectedInspiration"];
export type DesignQuestionnaire = components["schemas"]["DesignQuestionnaire"];

export type DraftFailure = {
  ok: false;
  status: number;
  code: string;
  message: string;
  fields?: Record<string, string[]>;
};
export type DraftSuccess<T> = { ok: true; data: T };
export type DraftResult<T> = DraftSuccess<T> | DraftFailure;

function toDraftFailure(status: number, body: ErrorBody): DraftFailure {
  return {
    ok: false,
    status,
    code: body.error?.code ?? (status >= 500 ? "unavailable" : "unknown_error"),
    message:
      body.error?.message ?? "Something went wrong. Please try again shortly.",
    fields: body.error?.fields,
  };
}

// A thrown transport error (timeout/network/invalid JSON) must become a
// controlled failure — the caller never reports a save as succeeded unless
// the server confirmed it.
const UNREACHABLE: DraftFailure = {
  ok: false,
  status: 0,
  code: "unavailable",
  message: "The service could not be reached. Your changes were not saved.",
};

async function draftRequest<T>(
  method: "POST" | "PATCH",
  path: string,
  body: unknown,
): Promise<DraftResult<T>> {
  let envelope: ApiEnvelope<T & ErrorBody>;
  try {
    envelope = await sendJson<T & ErrorBody>(method, path, body);
  } catch {
    return UNREACHABLE;
  }
  const { ok, status, data } = envelope;
  if (ok) return { ok: true, data: data as T };
  return toDraftFailure(status, data);
}

export function createDesignDraft(
  body: DesignWriteRequest,
): Promise<DraftResult<DesignDraft>> {
  return draftRequest<DesignDraft>("POST", "/api/v1/designs/", body);
}

export function updateDesignDraft(
  designId: string,
  body: DesignWriteRequest,
): Promise<DraftResult<DesignDraft>> {
  return draftRequest<DesignDraft>(
    "PATCH",
    `/api/v1/designs/${encodeURIComponent(designId)}/`,
    body,
  );
}

export function validateDesignDraft(
  designId: string,
): Promise<DraftResult<DesignValidationSuccess>> {
  return draftRequest<DesignValidationSuccess>(
    "POST",
    `/api/v1/designs/${encodeURIComponent(designId)}/validate/`,
    {},
  );
}

// GET a single design through the generated typed client (ownership enforced
// by the session cookie server-side; a foreign/nonexistent design is an
// indistinguishable 404 → thrown here). A generic single-design read, so it
// lives here alongside the other design wrappers rather than inside any one
// feature that happens to consume it (questionnaire/, results/).
export async function fetchDesign(designId: string): Promise<DesignDraft> {
  const { data } = await apiClient.GET("/api/v1/designs/{design_id}/", {
    params: { path: { design_id: designId } },
  });
  if (!data) throw new Error("not_found");
  return data;
}

// ---------------------------------------------------------------------------
// Generation jobs (Phase 10) — CSRF-aware start + GET-only poll
// ---------------------------------------------------------------------------
//
// Two narrow explicit wrappers (NOT a generic arbitrary-header client): the
// generated api/client.ts stays GET-only, and starting generation goes through
// the same in-memory-CSRF, retry-once, no-store, 5s-timeout transport as every
// other unsafe design mutation. The Idempotency-Key header is sent WITHOUT
// exposing a general header API. Wire types are generated OpenAPI components,
// so they cannot drift. No polling UI, TanStack Query or results route here.

export type GenerationJob = components["schemas"]["GenerationJob"];
export type GenerationJobResponse = components["schemas"]["GenerationJobResponse"];

export type GenerationResult = DraftResult<GenerationJobResponse>;

// The unsafe start-generation request reuses the shared CSRF flow but must also
// carry an Idempotency-Key header. This is the ONLY place that header is added;
// there is deliberately no generic header parameter on the transport.
async function sendGenerate(
  path: string,
  idempotencyKey: string,
  hasRetried = false,
): Promise<ApiEnvelope<GenerationJobResponse & ErrorBody>> {
  const token = await ensureCsrfToken();
  const response = await fetchWithTimeout(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": token,
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({}),
  });
  const data = (await response.json()) as GenerationJobResponse & ErrorBody;
  if (response.status === 403 && data?.error?.code === "csrf_failed" && !hasRetried) {
    csrfToken = null;
    return sendGenerate(path, idempotencyKey, true);
  }
  return { ok: response.ok, status: response.status, data };
}

export async function startDesignGeneration(
  designId: string,
  idempotencyKey: string,
): Promise<GenerationResult> {
  let envelope: ApiEnvelope<GenerationJobResponse & ErrorBody>;
  try {
    envelope = await sendGenerate(
      `/api/v1/designs/${encodeURIComponent(designId)}/generate/`,
      idempotencyKey,
    );
  } catch {
    return UNREACHABLE;
  }
  const { ok, status, data } = envelope;
  if (ok) return { ok: true, data: data as GenerationJobResponse };
  return toDraftFailure(status, data);
}

// ---------------------------------------------------------------------------
// Refinement (Phase 14) — CSRF-aware start, reusing the exact same transport
// discipline as startDesignGeneration but with a real JSON body (source
// version + one allowlisted category + an optional bounded note).
// ---------------------------------------------------------------------------

export type ChangeType = components["schemas"]["ChangeTypeEnum"];
export type RefinementRequestBody = components["schemas"]["RefinementWriteRequest"];

async function sendRefine(
  path: string,
  body: RefinementRequestBody,
  idempotencyKey: string,
  hasRetried = false,
): Promise<ApiEnvelope<GenerationJobResponse & ErrorBody>> {
  const token = await ensureCsrfToken();
  const response = await fetchWithTimeout(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": token,
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(body),
  });
  const data = (await response.json()) as GenerationJobResponse & ErrorBody;
  if (response.status === 403 && data?.error?.code === "csrf_failed" && !hasRetried) {
    csrfToken = null;
    return sendRefine(path, body, idempotencyKey, true);
  }
  return { ok: response.ok, status: response.status, data };
}

export async function startDesignRefinement(
  designId: string,
  request: RefinementRequestBody,
  idempotencyKey: string,
): Promise<GenerationResult> {
  let envelope: ApiEnvelope<GenerationJobResponse & ErrorBody>;
  try {
    envelope = await sendRefine(
      `/api/v1/designs/${encodeURIComponent(designId)}/refine/`,
      request,
      idempotencyKey,
    );
  } catch {
    return UNREACHABLE;
  }
  const { ok, status, data } = envelope;
  if (ok) return { ok: true, data: data as GenerationJobResponse };
  return toDraftFailure(status, data);
}

// Three distinct, narrow error types so a caller (the progress polling hook)
// can tell an owned-but-missing job apart from a transient outage and from a
// malformed response, instead of one generic thrown Error.
export class GenerationJobNotFoundError extends Error {
  constructor() {
    super("generation job not found");
    this.name = "GenerationJobNotFoundError";
  }
}
export class GenerationJobUnavailableError extends Error {
  constructor() {
    super("generation job temporarily unavailable");
    this.name = "GenerationJobUnavailableError";
  }
}
export class GenerationJobMalformedError extends Error {
  constructor() {
    super("generation job response was malformed");
    this.name = "GenerationJobMalformedError";
  }
}

// The complete, known GenerationJob.status enum (mirrors the generated
// StatusEnum). A status outside this set is treated as malformed rather than
// silently accepted — an unrecognised status must never be guessed at by the
// progress UI (which relies on exactly these five values to decide what to
// render and when to stop polling).
const KNOWN_JOB_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "running_text",
  "running_image",
  "succeeded",
  "failed",
]);

// Since Phase 14: which pipeline branch a job runs. A value outside this set
// is treated as malformed, matching KNOWN_JOB_STATUSES's discipline.
const KNOWN_GENERATION_KINDS: ReadonlySet<string> = new Set(["initial", "refinement"]);

function isGenerationJob(value: unknown): value is GenerationJob {
  if (typeof value !== "object" || value === null) return false;
  const job = value as Record<string, unknown>;
  return (
    typeof job.id === "string" &&
    job.id.length > 0 &&
    typeof job.design_id === "string" &&
    job.design_id.length > 0 &&
    (job.design_version_id === null || typeof job.design_version_id === "string") &&
    typeof job.status === "string" &&
    KNOWN_JOB_STATUSES.has(job.status) &&
    (job.error_code === null || typeof job.error_code === "string") &&
    typeof job.generation_kind === "string" &&
    KNOWN_GENERATION_KINDS.has(job.generation_kind) &&
    typeof job.created_at === "string" &&
    typeof job.updated_at === "string" &&
    (job.started_at === null || typeof job.started_at === "string") &&
    (job.completed_at === null || typeof job.completed_at === "string")
  );
}

// Validates the runtime response shape rather than casting arbitrary JSON.
// Throws one of the three typed errors above; never exposes a raw response
// body or backend exception message to the caller.
export async function fetchGenerationJob(jobId: string): Promise<GenerationJob> {
  let response: Response;
  try {
    response = await fetchWithTimeout(`/api/v1/jobs/${encodeURIComponent(jobId)}/`);
  } catch {
    throw new GenerationJobUnavailableError();
  }
  if (response.status === 404) {
    throw new GenerationJobNotFoundError();
  }
  if (response.status !== 200) {
    throw new GenerationJobUnavailableError();
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new GenerationJobMalformedError();
  }
  const job = (body as { job?: unknown } | null)?.job;
  if (!isGenerationJob(job)) {
    throw new GenerationJobMalformedError();
  }
  return job;
}

// ---------------------------------------------------------------------------
// Design images (Phase 11) — short-lived signed URL retrieval
// ---------------------------------------------------------------------------
//
// One narrow GET wrapper. The returned URLs are TEMPORARY BEARER URLS: anyone
// holding one can use it until it expires, so they are requested fresh on
// every call and NEVER cached in module state, localStorage, sessionStorage
// or IndexedDB. Phase 12 owns refresh while a results page stays open; there
// is deliberately no polling or automatic refresh here.

export type DesignImage = components["schemas"]["DesignImage"];
export type DesignOriginalImage = components["schemas"]["DesignOriginalImage"];
export type DesignImages = components["schemas"]["DesignImages"];

// Failures reuse the file's canonical DraftFailure shape (and its shared
// status/body -> code/message mapping) so this endpoint can never drift from
// the rest of the API client's error-handling contract.
export type DesignImageUrlFailure = DraftFailure;
export type DesignImageUrlSuccess = { ok: true; images: DesignImages };
export type DesignImageUrlResult = DesignImageUrlSuccess | DesignImageUrlFailure;

function isDesignImage(value: unknown): value is DesignImage {
  if (typeof value !== "object" || value === null) return false;
  const image = value as Record<string, unknown>;
  return (
    typeof image.url === "string" &&
    image.url.length > 0 &&
    typeof image.width === "number" &&
    typeof image.height === "number"
  );
}

function isDesignOriginalImage(value: unknown): value is DesignOriginalImage {
  if (!isDesignImage(value)) return false;
  const image = value as Record<string, unknown>;
  return typeof image.download_url === "string" && image.download_url.length > 0;
}

function isDesignImages(value: unknown): value is DesignImages {
  if (typeof value !== "object" || value === null) return false;
  const images = value as Record<string, unknown>;
  return (
    isDesignOriginalImage(images.original) &&
    isDesignImage(images.thumbnail) &&
    typeof images.expires_at === "string" &&
    images.expires_at.length > 0
  );
}

// Strict result mapping: a timeout/network error, invalid JSON, or a 200 with
// a malformed body all become typed failures — never a false success and
// never an exception the caller must remember to catch.
export async function fetchDesignImageUrls(
  designId: string,
  designVersionId: string,
): Promise<DesignImageUrlResult> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `/api/v1/designs/${encodeURIComponent(designId)}/versions/${encodeURIComponent(
        designVersionId,
      )}/images/`,
    );
  } catch {
    return {
      ok: false,
      status: 0,
      code: "unavailable",
      message: "The service could not be reached.",
    };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return {
      ok: false,
      status: response.status,
      code: "invalid_response",
      message: "The service returned an unexpected response.",
    };
  }
  if (response.status === 200) {
    const images = (body as { images?: unknown }).images;
    if (isDesignImages(images)) {
      return { ok: true, images };
    }
    return {
      ok: false,
      status: 200,
      code: "invalid_response",
      message: "The service returned an unexpected response.",
    };
  }
  return toDraftFailure(response.status, body as ErrorBody);
}

// ---------------------------------------------------------------------------
// Design result (Phase 12) — the curated, private concept result
// ---------------------------------------------------------------------------
//
// One narrow GET wrapper, strictly shape-validated. Never issues or expects a
// signed image URL — Phase 11's image endpoint above is the only issuer.

export type DesignResult = components["schemas"]["DesignResult"];

export type DesignResultFailure = DraftFailure;
export type DesignResultSuccess = { ok: true; result: DesignResult };
export type DesignResultOutcome = DesignResultSuccess | DesignResultFailure;

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isGarmentBreakdown(value: unknown): value is DesignResult["garment_breakdown"] {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.overall_form === "string" &&
    isStringArray(v.garment_components) &&
    typeof v.silhouette === "string" &&
    typeof v.drape_or_layering === "string" &&
    typeof v.key_proportions === "string"
  );
}

function isColourStory(value: unknown): value is DesignResult["colour_story"] {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.palette_summary === "string" &&
    typeof v.placement === "string" &&
    typeof v.rationale === "string"
  );
}

function isFabricEntry(value: unknown): value is DesignResult["fabrics_and_texture"][number] {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.fabric === "string" &&
    typeof v.placement === "string" &&
    typeof v.finish_and_movement === "string"
  );
}

function isEmbellishmentPlan(value: unknown): value is DesignResult["embellishment_plan"] {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    isStringArray(v.techniques) &&
    typeof v.density === "string" &&
    isStringArray(v.placement) &&
    isStringArray(v.motifs) &&
    typeof v.restraint_notes === "string"
  );
}

function isCoverageAndDrape(value: unknown): value is DesignResult["coverage_and_drape"] {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.sleeves === "string" &&
    typeof v.neckline === "string" &&
    typeof v.back_and_midriff === "string" &&
    typeof v.head_covering === "string" &&
    typeof v.dupatta_or_saree_drape === "string"
  );
}

function isCulturalContext(value: unknown): value is DesignResult["cultural_context"] {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    (v.regional_direction === null || typeof v.regional_direction === "string") &&
    isStringArray(v.interpretation_notes) &&
    isStringArray(v.safeguards)
  );
}

function isInspirationAcknowledgement(
  value: unknown,
): value is DesignResult["inspiration_acknowledgements"][number] {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.position === "number" &&
    typeof v.title === "string" &&
    typeof v.attribution === "string"
  );
}

const KNOWN_CHANGE_TYPES: ReadonlySet<string> = new Set([
  "colour_story",
  "fabric_and_texture",
  "embellishment",
  "sleeves_and_coverage",
  "neckline",
  "dupatta_or_saree_drape",
  "silhouette_detail",
  "styling_details",
]);

function isRefinementLineage(
  value: unknown,
): value is NonNullable<DesignResult["lineage"]["refinement"]> {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.change_type === "string" && KNOWN_CHANGE_TYPES.has(v.change_type);
}

// Since Phase 14: additive parent-child lineage. Never validates/exposes the
// raw note, refinement-request hash, its schema version, a seed or the
// source attempt — the backend payload structurally excludes them.
function isLineage(value: unknown): value is DesignResult["lineage"] {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (v.kind !== "initial" && v.kind !== "refinement") return false;
  if (v.parent_version_id !== null && typeof v.parent_version_id !== "string") return false;
  if (v.refinement === null) return true;
  return isRefinementLineage(v.refinement);
}

function isDesignResult(value: unknown): value is DesignResult {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.design_id === "string" &&
    v.design_id.length > 0 &&
    typeof v.design_version_id === "string" &&
    v.design_version_id.length > 0 &&
    typeof v.version_number === "number" &&
    typeof v.title === "string" &&
    typeof v.concept_summary === "string" &&
    isGarmentBreakdown(v.garment_breakdown) &&
    isColourStory(v.colour_story) &&
    Array.isArray(v.fabrics_and_texture) &&
    v.fabrics_and_texture.every(isFabricEntry) &&
    isEmbellishmentPlan(v.embellishment_plan) &&
    isCoverageAndDrape(v.coverage_and_drape) &&
    isCulturalContext(v.cultural_context) &&
    isStringArray(v.styling_notes) &&
    isStringArray(v.construction_caveats) &&
    typeof v.image_alt_text === "string" &&
    typeof v.created_at === "string" &&
    Array.isArray(v.inspiration_acknowledgements) &&
    v.inspiration_acknowledgements.every(isInspirationAcknowledgement) &&
    isLineage(v.lineage)
  );
}

// Strict result mapping using the generated OpenAPI types and runtime shape
// validation — a timeout/network error, invalid JSON, or a 200 with a
// malformed body all become typed failures, never a false success.
export async function fetchDesignResult(
  designId: string,
  designVersionId: string,
): Promise<DesignResultOutcome> {
  let response: Response;
  try {
    response = await fetchWithTimeout(
      `/api/v1/designs/${encodeURIComponent(designId)}/versions/${encodeURIComponent(
        designVersionId,
      )}/result/`,
    );
  } catch {
    return {
      ok: false,
      status: 0,
      code: "unavailable",
      message: "The service could not be reached.",
    };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return {
      ok: false,
      status: response.status,
      code: "invalid_response",
      message: "The service returned an unexpected response.",
    };
  }
  if (response.status === 200) {
    const result = (body as { result?: unknown }).result;
    if (isDesignResult(result)) {
      return { ok: true, result };
    }
    return {
      ok: false,
      status: 200,
      code: "invalid_response",
      message: "The service returned an unexpected response.",
    };
  }
  return toDraftFailure(response.status, body as ErrorBody);
}
