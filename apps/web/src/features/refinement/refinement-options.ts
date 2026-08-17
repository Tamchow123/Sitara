// Pure, deterministic data for the refinement panel — the allowlisted DesignSpec
// edit categories a client may REQUEST (mirrors the backend's
// REFINEMENT_CHANGE_TYPES), what each one will actually change, and the note's
// bounded length. No provider/model/prompt detail here.
//
// TWO tables, not one, because the backend has two constants and they mean
// different things:
//
//   CHANGE_TYPE_LABELS             <- PERSISTED_REFINEMENT_CHANGE_TYPES (eight)
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
// quietly rendering a raw machine string at someone. The offered list is then
// DERIVED rather than restated — `CHANGE_TYPE_EFFECTS` is keyed by
// `Exclude<ChangeType, Retired>`, so a missing or retired entry is a compile
// error and the options are built from its keys — which is what stops the two
// drifting apart by hand the way the label list once did.

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

// `Extract` rather than a bare string literal, so this cannot name a category
// the lineage enum does not have. If the enum ever drops `styling_details`,
// `CHANGE_TYPE_LABELS` above is what stops compiling — its explicit key becomes
// an excess property — which is the compile error that forces someone to look.
// `Requestable` would simply widen to the whole (already seven-member) enum and
// `CHANGE_TYPE_EFFECTS` would keep compiling unchanged, so it is not the guard.
type Retired = Extract<ChangeType, "styling_details">;
type Requestable = Exclude<ChangeType, Retired>;

// WHAT each category will change, in the customer's own words — naming the
// questionnaire answer it moves, not just the category (ADR 0028; the project
// owner's decision was "name the field, not just the category"). Before this
// phase a refinement could not alter the image at all, so a promise about what
// would change was one the product could not keep; now it can, and saying which
// answer moves is what makes the choice between seven chips meaningful.
//
// Deliberately version-independent wording. Questionnaire v4 splits coverage
// into sleeves, back and midriff where v1 asked one question, and a v1 concept
// has no neckline answer at all, so this copy names what the customer chose in
// terms true of both rather than claiming a field shape it cannot know from the
// result payload.
export const CHANGE_TYPE_EFFECTS: Record<Requestable, string> = {
  colour_story: "Changes the colours you chose.",
  fabric_and_texture: "Changes the fabrics you chose.",
  embellishment: "Changes the embroidery styles and how much of it you chose.",
  sleeves_and_coverage: "Changes the coverage you chose — sleeves, back and midriff.",
  neckline: "Changes the neckline you chose.",
  dupatta_or_saree_drape: "Changes the dupatta or saree drape you chose.",
  silhouette_detail: "Changes the silhouette you chose.",
};

const REQUESTABLE_CHANGE_TYPES = Object.keys(CHANGE_TYPE_EFFECTS) as Requestable[];

export const REFINEMENT_CHANGE_TYPE_OPTIONS: ReadonlyArray<{
  value: ChangeType;
  label: string;
  effect: string;
}> = REQUESTABLE_CHANGE_TYPES.map((value) => ({
  value,
  label: CHANGE_TYPE_LABELS[value],
  effect: CHANGE_TYPE_EFFECTS[value],
}));

export function changeTypeLabel(changeType: ChangeType): string {
  return CHANGE_TYPE_LABELS[changeType] ?? changeType;
}

export function isNoteWithinLimit(note: string): boolean {
  return note.length <= REFINEMENT_NOTE_MAX_LENGTH;
}

// The remaining-budget phrase, in one place because three separate surfaces
// say it — the panel kicker, the comparison kicker and the note under the
// previous version — and a count that reads "1 refinements left" on the last
// round is the kind of detail a customer notices at the counter.
export function refinementsLeftLabel(remaining: number): string {
  return remaining === 1 ? "1 refinement left" : `${remaining} refinements left`;
}
