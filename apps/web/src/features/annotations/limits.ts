// The bounds the server enforces, restated for the editor.
//
// These are NOT the authority — `annotation_schema.py` is, and Django rejects
// anything outside them regardless of what happens here. They exist so the UI
// can refuse politely at the point of the gesture instead of letting a user
// place a 101st mark and discover on save that it was never going to persist.
// If the two ever disagree the server wins and the save fails loudly; that is
// the intended failure mode, not something to paper over locally.

import type { AnnotationItemType, Palette } from "@/lib/api";

export const MAX_ITEMS = 100;
export const MAX_NOTE_LENGTH = 140;

/** Freehand strokes are simplified before they are sent. */
export const MIN_FREEHAND_POINTS = 2;
export const MAX_FREEHAND_POINTS = 500;

/**
 * An arrow shorter than this points at nothing. Measured as `hypot(dx, dy)` in
 * normalised space — the straight-line distance, never either axis alone, which
 * is the same reading the server applies.
 */
export const MIN_ARROW_LENGTH = 0.005;

/** A rectangle needs both edges; a zero-area drag is a mis-click, not a mark. */
export const MIN_RECT_EDGE = 0.005;

// The two enums below are the contract's, not ours. They are aliased from the
// generated types rather than retyped, because a hand-written mirror of a
// generated union is a drift waiting to happen: adding a palette server-side
// would leave the editor silently unable to offer it, with nothing failing.
//
// `satisfies` covers the other direction — an entry here that the contract does
// not accept is a typecheck error rather than a 400 at save time. And the
// `Record<…, string>` label maps below force a NEW contract value to be given a
// visible name before it will compile, which is the whole point of §12's rule
// that colour is never the only label.
export const PALETTES = ["terracotta", "sage", "ink"] as const satisfies readonly Palette[];
export type PaletteName = Palette;

export const DEFAULT_PALETTE: PaletteName = "terracotta";

/** Visible text for every swatch — colour is never the only label (§12). */
export const PALETTE_LABELS: Record<PaletteName, string> = {
  terracotta: "Terracotta",
  sage: "Sage",
  ink: "Ink",
};

export type ItemType = AnnotationItemType;

export const TYPE_LABELS: Record<ItemType, string> = {
  pin: "Pin",
  arrow: "Arrow",
  rectangle: "Rectangle",
  freehand: "Freehand",
};

/** Nudge steps, as fractions of the image's SMALLER edge (§13). */
export const NUDGE_STEP = 0.005;
export const NUDGE_STEP_COARSE = 0.02;

export const ZOOM_MIN = 0.6;
export const ZOOM_MAX = 3;
export const ZOOM_IN_FACTOR = 1.25;
export const ZOOM_OUT_FACTOR = 0.8;

/** Idle delay before an automatic save (§14). */
export const AUTOSAVE_DEBOUNCE_MS = 800;

/** How long the send button holds its confirmation before reverting (§14). */
export const SEND_FLASH_MS = 2200;
