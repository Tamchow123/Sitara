// Same-origin API client. All requests use RELATIVE /api/* paths through
// the Next.js rewrite — the browser never needs the Django host, and no
// NEXT_PUBLIC_* backend URL exists.
//
// This module keeps the CSRF-aware unsafe-request flow (register/login/
// logout and, later, design mutations): CSRF tokens live in MEMORY ONLY
// (never localStorage/sessionStorage/IndexedDB); the session itself is an
// HttpOnly cookie the JS cannot read. Safe GETs may go through the generated
// typed client (api/client.ts); both share the transport below.

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
