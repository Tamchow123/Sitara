// Questionnaire feature data access.
//
// Reads go through the generated GET-only typed client (api/client.ts) — the
// same same-origin/no-store/5s transport, fully path-typed. Unsafe design
// mutations go through the tested CSRF-aware wrappers in lib/api.ts; they are
// re-exported here so the feature has a single import surface.

import { apiClient } from "@/api/client";

import type {
  ActiveQuestionnaire,
  DesignDraft,
  InspirationCatalogue,
} from "./types";

export {
  createDesignDraft,
  updateDesignDraft,
  validateDesignDraft,
} from "@/lib/api";
export type {
  DesignWriteRequest,
  DesignValidationSuccess,
  DraftFailure,
  DraftResult,
  DraftSuccess,
} from "@/lib/api";

export async function fetchActiveQuestionnaire(): Promise<ActiveQuestionnaire> {
  const { data } = await apiClient.GET("/api/v1/questionnaire/active/");
  if (!data) throw new Error("questionnaire_unavailable");
  return data;
}

export async function fetchCatalogue(): Promise<InspirationCatalogue> {
  const { data } = await apiClient.GET("/api/v1/inspiration-assets/");
  if (!data) throw new Error("catalogue_unavailable");
  return data;
}

// GET a single design (ownership enforced by the session cookie server-side;
// a foreign/nonexistent design is an indistinguishable 404 → thrown here).
export async function fetchDesign(designId: string): Promise<DesignDraft> {
  const { data } = await apiClient.GET("/api/v1/designs/{design_id}/", {
    params: { path: { design_id: designId } },
  });
  if (!data) throw new Error("not_found");
  return data;
}
