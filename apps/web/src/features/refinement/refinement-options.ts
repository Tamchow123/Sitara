// Pure, deterministic data for the refinement panel — the allowlisted DesignSpec
// edit categories a client may REQUEST (mirrors the backend's
// REFINEMENT_CHANGE_TYPES) and the note's bounded length. No provider/model/
// prompt detail here.
//
// TWO tables, not one, because the backend has two constants and they mean
// different things:
//
//   CHANGE_TYPE_LABELS            <- PERSISTED_REFINEMENT_CHANGE_TYPES (eight)
//   REFINEMENT_CHANGE_TYPE_OPTIONS <- REFINEMENT_CHANGE_TYPES (seven)
//
// ADR 0028 retired `styling_details`: every path it could change is unrendered
// by the image prompt builder and it names no canonical selection to fall back
// on, so it is not offered. But a design refined BEFORE the retirement still
// reports it for ever — a persisted lineage row is audit data that is read and
// never rewritten — and the generated `ChangeType` union still admits it,
// because that type comes from the OpenAPI lineage enum, which describes what a
// RESULT may contain. Labelling it is a read-side job and must keep working
// after the request-side list loses it.
//
// `Record<ChangeType, string>` is what keeps the two in step: if the lineage
// enum ever gains or loses a member, this file stops compiling rather than
// quietly rendering a raw machine string at someone.

import type { ChangeType } from "@/lib/api";

export const REFINEMENT_NOTE_MAX_LENGTH = 300;

export const CHANGE_TYPE_LABELS: Record<ChangeType, string> = {
  colour_story: "Colour story",
  fabric_and_texture: "Fabric and texture",
  embellishment: "Embellishment",
  sleeves_and_coverage: "Sleeves and coverage",
  neckline: "Neckline",
  dupatta_or_saree_drape: "Dupatta or saree drape",
  silhouette_detail: "Silhouette detail",
  // Retired by ADR 0028 — never offered, still labelled. See above.
  styling_details: "Styling details",
};

const REQUESTABLE_CHANGE_TYPES = [
  "colour_story",
  "fabric_and_texture",
  "embellishment",
  "sleeves_and_coverage",
  "neckline",
  "dupatta_or_saree_drape",
  "silhouette_detail",
] as const satisfies ReadonlyArray<ChangeType>;

export const REFINEMENT_CHANGE_TYPE_OPTIONS: ReadonlyArray<{
  value: ChangeType;
  label: string;
}> = REQUESTABLE_CHANGE_TYPES.map((value) => ({ value, label: CHANGE_TYPE_LABELS[value] }));

export function changeTypeLabel(changeType: ChangeType): string {
  return CHANGE_TYPE_LABELS[changeType] ?? changeType;
}

export function isNoteWithinLimit(note: string): boolean {
  return note.length <= REFINEMENT_NOTE_MAX_LENGTH;
}
