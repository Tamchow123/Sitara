// Pure, deterministic data for the refinement panel — the eight allowlisted
// DesignSpec edit categories (mirrors the backend's REFINEMENT_ALLOWED_PATHS
// keys) and the note's bounded length. No provider/model/prompt detail here.

import type { ChangeType } from "@/lib/api";

export const REFINEMENT_NOTE_MAX_LENGTH = 300;

export const REFINEMENT_CHANGE_TYPE_OPTIONS: ReadonlyArray<{
  value: ChangeType;
  label: string;
}> = [
  { value: "colour_story", label: "Colour story" },
  { value: "fabric_and_texture", label: "Fabric and texture" },
  { value: "embellishment", label: "Embellishment" },
  { value: "sleeves_and_coverage", label: "Sleeves and coverage" },
  { value: "neckline", label: "Neckline" },
  { value: "dupatta_or_saree_drape", label: "Dupatta or saree drape" },
  { value: "silhouette_detail", label: "Silhouette detail" },
  { value: "styling_details", label: "Styling details" },
];

export function changeTypeLabel(changeType: ChangeType): string {
  return (
    REFINEMENT_CHANGE_TYPE_OPTIONS.find((option) => option.value === changeType)?.label ??
    changeType
  );
}

export function isNoteWithinLimit(note: string): boolean {
  return note.length <= REFINEMENT_NOTE_MAX_LENGTH;
}
