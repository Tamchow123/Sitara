// Questionnaire feature data access.
//
// Reads go through the generated GET-only typed client (api/client.ts) — the
// same same-origin/no-store/5s transport, fully path-typed. Unsafe design
// mutations go through the tested CSRF-aware wrappers in lib/api.ts; they are
// re-exported here so the feature has a single import surface.

import { apiClient } from "@/api/client";

import type { ActiveQuestionnaire, InspirationCatalogue } from "./types";

export {
  createDesignDraft,
  updateDesignDraft,
  validateDesignDraft,
  startDesignGeneration,
  fetchDesign,
  fetchPublicConfig,
} from "@/lib/api";
export type {
  DesignWriteRequest,
  DesignValidationSuccess,
  DraftFailure,
  DraftResult,
  DraftSuccess,
  GenerationResult,
  PublicConfig,
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
