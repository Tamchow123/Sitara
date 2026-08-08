// The editing session: the marks, the selection, and undo/redo.
//
// A reducer rather than a pile of useState calls because almost every action
// here has to do two things at once — mutate the marks AND push the previous
// marks onto the undo stack — and a single place that does both is the only way
// that stays true as actions are added.
//
// Undo/redo covers the CURRENT UNSAVED SESSION, as §12 specifies. It is not a
// server-side history: it does not reach across a reload, and undoing past the
// last save simply produces marks that differ from the stored document, which
// the autosave then writes like any other edit.
//
// `baseline` is what the server last confirmed. Everything the UI calls "unsaved
// changes" is derived from comparing `items` to it, never from a manual dirty
// flag that a forgotten action could leave stale.

import { MAX_ITEMS, MAX_NOTE_LENGTH, type ItemType, type PaletteName } from "./limits";
import { translateGeometry, type Point } from "./geometry";
import type { AnnotationDocument, AnnotationGeometry, AnnotationItem } from "@/lib/api";

export type EditorState = {
  items: AnnotationItem[];
  selectedId: string | null;
  /** Snapshots of `items`, most recent last. */
  past: AnnotationItem[][];
  future: AnnotationItem[][];
  /** The marks the server last confirmed, and the revision it confirmed them at. */
  baseline: AnnotationItem[];
  revision: number;
  updatedAt: string | null;
  /**
   * Which generation of the document this is. Bumped whenever the document is
   * REPLACED wholesale — a load, a post-conflict reload, or a clear.
   *
   * A save confirmation carries the epoch it was sent under, so one that arrives
   * after a replacement can be told apart from a current one. The revision number
   * cannot do this job: a never-saved document sits at revision 0, so a first-ever
   * save sent at 0 and a clear that leaves it at 0 compare equal, and the stale
   * confirmation was let through — restoring the marks the user had just cleared.
   */
  epoch: number;
};

export type EditorAction =
  | { type: "hydrate"; document: AnnotationDocument }
  | { type: "add"; item: AnnotationItem }
  | { type: "setNote"; id: string; note: string }
  | { type: "setPalette"; id: string; palette: PaletteName }
  | { type: "setGeometry"; id: string; geometry: AnnotationGeometry }
  | { type: "nudge"; id: string; delta: Point }
  | { type: "remove"; id: string }
  | { type: "clearAll" }
  | { type: "select"; id: string | null }
  | { type: "undo" }
  | { type: "redo" }
  | {
      type: "saved";
      sent: AnnotationItem[];
      revision: number;
      updatedAt: string | null;
      /** The document generation the request was sent under. */
      sentAtEpoch: number;
    };

export function initialEditorState(): EditorState {
  return {
    items: [],
    selectedId: null,
    past: [],
    future: [],
    baseline: [],
    revision: 0,
    updatedAt: null,
    epoch: 0,
  };
}

/**
 * Are these the same marks?
 *
 * Compared field by field in `created_order` sequence rather than by JSON
 * string, so a key-order difference coming back from the server cannot read as
 * an edit and leave the pill permanently claiming unsaved changes.
 */
export function itemsEqual(a: AnnotationItem[], b: AnnotationItem[]): boolean {
  if (a.length !== b.length) return false;
  for (let index = 0; index < a.length; index += 1) {
    const left = a[index]!;
    const right = b[index]!;
    if (
      left.id !== right.id ||
      left.type !== right.type ||
      left.note !== right.note ||
      left.palette !== right.palette ||
      left.created_order !== right.created_order ||
      JSON.stringify(left.geometry) !== JSON.stringify(right.geometry)
    ) {
      return false;
    }
  }
  return true;
}

export function isDirty(state: EditorState): boolean {
  return !itemsEqual(state.items, state.baseline);
}

export function canUndo(state: EditorState): boolean {
  return state.past.length > 0;
}

export function canRedo(state: EditorState): boolean {
  return state.future.length > 0;
}

export function selectedItem(state: EditorState): AnnotationItem | null {
  if (!state.selectedId) return null;
  return state.items.find((item) => item.id === state.selectedId) ?? null;
}

export function byCreatedOrder(items: AnnotationItem[]): AnnotationItem[] {
  return [...items].sort((a, b) => a.created_order - b.created_order);
}

export function nextCreatedOrder(items: AnnotationItem[]): number {
  return items.reduce((highest, item) => Math.max(highest, item.created_order), 0) + 1;
}

/**
 * Compact `created_order` to 1..n after a deletion.
 *
 * Relative order is preserved; only the gap closes. Without this, deleting the
 * second of three marks leaves badges reading 1 and 3 under a heading that says
 * there are two — the numbers are the user's only handle for referring to a
 * mark, so they have to match what the list shows.
 */
function renumber(items: AnnotationItem[]): AnnotationItem[] {
  return byCreatedOrder(items).map((item, index) => ({
    ...item,
    created_order: index + 1,
  }));
}

/**
 * Apply a note to one mark, truncated to the stored limit the server also enforces.
 *
 * Private. It was briefly exported so `commitEdit` could compute the post-edit
 * marks for an immediate save, but predicting a transition is not the same as
 * asking for one: the caller now runs `editorReducer` itself, which cannot drift
 * from what `dispatch` produces because it IS what `dispatch` produces.
 */
function applyNote(items: AnnotationItem[], id: string, note: string): AnnotationItem[] {
  const trimmed = note.slice(0, MAX_NOTE_LENGTH);
  return items.map((item) => (item.id === id ? { ...item, note: trimmed } : item));
}

/** Push the current marks onto the undo stack and drop any redo future. */
function record(state: EditorState, items: AnnotationItem[]): EditorState {
  return {
    ...state,
    items,
    past: [...state.past, state.items],
    future: [],
  };
}

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "hydrate": {
      // A load or a post-conflict reload. The stacks are cleared deliberately:
      // an undo across a reload would restore marks the server has never heard
      // of, on top of a document that has moved on.
      const items = byCreatedOrder(action.document.items);
      return {
        items,
        selectedId: null,
        past: [],
        future: [],
        baseline: items,
        revision: action.document.revision,
        updatedAt: action.document.updated_at,
        epoch: state.epoch + 1,
      };
    }

    case "add": {
      if (state.items.length >= MAX_ITEMS) return state;
      const next = record(state, [...state.items, action.item]);
      return { ...next, selectedId: action.item.id };
    }

    case "setNote": {
      const target = state.items.find((item) => item.id === action.id);
      if (!target || target.note === action.note.slice(0, MAX_NOTE_LENGTH)) return state;
      return record(state, applyNote(state.items, action.id, action.note));
    }

    case "setPalette": {
      const target = state.items.find((item) => item.id === action.id);
      if (!target || target.palette === action.palette) return state;
      return record(
        state,
        state.items.map((item) =>
          item.id === action.id ? { ...item, palette: action.palette } : item,
        ),
      );
    }

    case "setGeometry": {
      const target = state.items.find((item) => item.id === action.id);
      if (!target) return state;
      return record(
        state,
        state.items.map((item) =>
          item.id === action.id ? { ...item, geometry: action.geometry } : item,
        ),
      );
    }

    case "nudge": {
      const target = state.items.find((item) => item.id === action.id);
      if (!target) return state;
      const geometry = translateGeometry(target.geometry, action.delta);
      if (JSON.stringify(geometry) === JSON.stringify(target.geometry)) {
        // Already against the edge. Recording this would fill the undo stack
        // with steps that visibly do nothing.
        return state;
      }
      return record(
        state,
        state.items.map((item) => (item.id === action.id ? { ...item, geometry } : item)),
      );
    }

    case "remove": {
      if (!state.items.some((item) => item.id === action.id)) return state;
      const remaining = renumber(state.items.filter((item) => item.id !== action.id));
      const next = record(state, remaining);
      return {
        ...next,
        selectedId: state.selectedId === action.id ? null : state.selectedId,
      };
    }

    case "clearAll": {
      if (state.items.length === 0) return state;
      // The epoch bump is what stops a save that was already in flight from
      // putting these marks back when it lands. Undo still works — `record` keeps
      // the stack — it is only the SERVER's confirmations that are fenced off.
      return { ...record(state, []), selectedId: null, epoch: state.epoch + 1 };
    }

    case "select": {
      if (state.selectedId === action.id) return state;
      // Selection is view state, not an edit: it is not undoable and does not
      // make the document dirty.
      return { ...state, selectedId: action.id };
    }

    case "undo": {
      const previous = state.past[state.past.length - 1];
      if (!previous) return state;
      return {
        ...state,
        items: previous,
        past: state.past.slice(0, -1),
        future: [state.items, ...state.future],
        selectedId: previous.some((item) => item.id === state.selectedId)
          ? state.selectedId
          : null,
      };
    }

    case "redo": {
      const [next, ...rest] = state.future;
      if (!next) return state;
      return {
        ...state,
        items: next,
        past: [...state.past, state.items],
        future: rest,
        selectedId: next.some((item) => item.id === state.selectedId)
          ? state.selectedId
          : null,
      };
    }

    case "saved": {
      if (state.epoch !== action.sentAtEpoch) {
        // The document was REPLACED while this request was in flight — a clear, or
        // a post-conflict reload. The whole confirmation is discarded, revision
        // included.
        //
        // Adopting the reported revision looks tempting and is wrong. After a
        // DELETE the server holds no row, and `replace_annotation_document`
        // accepts only `expected_revision: 0` in that state — so adopting the
        // stale PUT's revision would make the next legitimate write 409 against a
        // document nobody else has touched, and since both recovery actions end at
        // the server's copy, the mark the user had just drawn would be thrown away.
        // The clear path already synthesises the correct 0.
        //
        // There is no reachable case that wants adoption: `reloadLatest` only runs
        // once a save has resolved AS a conflict, so it cannot race an open
        // request, and the local-only clear racing a save is exactly what
        // `hasEverSent()` makes impossible.
        return state;
      }
      // The baseline becomes what was actually SENT, not what is on screen now.
      // If the user typed while the request was in flight, `items` still differs
      // from the new baseline, so the document stays dirty and the coalesced
      // follow-up write fires — which is exactly the behaviour §14 asks for.
      return {
        ...state,
        baseline: action.sent,
        revision: action.revision,
        updatedAt: action.updatedAt,
      };
    }

    default:
      return state;
  }
}

/**
 * A new mark, ready to add.
 *
 * The id is a client-generated UUID because the mark must be referable — by the
 * list, the overlay and the undo stack — from the moment it is drawn, long
 * before any server round trip could hand one back.
 */
export function createItem(
  type: ItemType,
  geometry: AnnotationGeometry,
  palette: PaletteName,
  items: AnnotationItem[],
): AnnotationItem {
  return {
    id: crypto.randomUUID(),
    type,
    geometry,
    note: "",
    palette,
    created_order: nextCreatedOrder(items),
  };
}
