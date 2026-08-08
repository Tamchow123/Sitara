import { describe, expect, it } from "vitest";

import {
  byCreatedOrder,
  canRedo,
  canUndo,
  createItem,
  editorReducer,
  initialEditorState,
  isDirty,
  itemsEqual,
  nextCreatedOrder,
  selectedItem,
  type EditorAction,
  type EditorState,
} from "./reducer";
import { MAX_ITEMS } from "./limits";
import type { AnnotationDocument, AnnotationItem } from "@/lib/api";

function pin(order: number, overrides: Partial<AnnotationItem> = {}): AnnotationItem {
  return {
    id: `id-${order}`,
    type: "pin",
    geometry: { point: { x: 0.5, y: 0.5 } },
    note: "",
    palette: "terracotta",
    created_order: order,
    ...overrides,
  } as AnnotationItem;
}

function doc(items: AnnotationItem[], revision = 1): AnnotationDocument {
  return {
    schema_version: 1,
    image_width: 1024,
    image_height: 1536,
    items,
    revision,
    updated_at: "2026-08-08T10:00:00Z",
  };
}

function run(state: EditorState, ...actions: EditorAction[]): EditorState {
  return actions.reduce(editorReducer, state);
}

const loaded = (items: AnnotationItem[], revision = 1) =>
  editorReducer(initialEditorState(), { type: "hydrate", document: doc(items, revision) });

describe("hydrating", () => {
  it("adopts the server document as the baseline, so nothing reads as unsaved", () => {
    const state = loaded([pin(1)]);
    expect(isDirty(state)).toBe(false);
    expect(state.revision).toBe(1);
  });

  it("treats revision 0 with no marks as an ordinary starting point", () => {
    // Nothing has ever been saved for this version. That is not an error state
    // and must not be special-cased into one.
    const state = editorReducer(initialEditorState(), {
      type: "hydrate",
      document: { ...doc([], 0), updated_at: null },
    });
    expect(state.items).toEqual([]);
    expect(state.revision).toBe(0);
    expect(isDirty(state)).toBe(false);
  });

  it("clears the undo stacks, so undo cannot restore marks the server never saw", () => {
    const edited = run(loaded([pin(1)]), { type: "remove", id: "id-1" });
    expect(canUndo(edited)).toBe(true);

    const reloaded = editorReducer(edited, { type: "hydrate", document: doc([pin(1)], 4) });
    expect(canUndo(reloaded)).toBe(false);
    expect(canRedo(reloaded)).toBe(false);
  });

  it("orders marks by created_order regardless of the order they arrive in", () => {
    const state = loaded([pin(3), pin(1), pin(2)]);
    expect(state.items.map((item) => item.created_order)).toEqual([1, 2, 3]);
  });
});

describe("adding a mark", () => {
  it("numbers it after the highest existing order and selects it", () => {
    const state = run(loaded([pin(1), pin(2)]), {
      type: "add",
      item: pin(nextCreatedOrder([pin(1), pin(2)]), { id: "new" }),
    });
    expect(state.items.at(-1)?.created_order).toBe(3);
    expect(state.selectedId).toBe("new");
    expect(isDirty(state)).toBe(true);
  });

  it("refuses to exceed the document ceiling", () => {
    // The server enforces this too; refusing here means the user finds out at
    // the gesture rather than on save.
    const full = Array.from({ length: MAX_ITEMS }, (_, index) => pin(index + 1));
    const state = run(loaded(full), { type: "add", item: pin(MAX_ITEMS + 1, { id: "over" }) });
    expect(state.items).toHaveLength(MAX_ITEMS);
  });
});

describe("editing", () => {
  it("truncates a note to the schema's ceiling instead of sending an invalid one", () => {
    const state = run(loaded([pin(1)]), {
      type: "setNote",
      id: "id-1",
      note: "x".repeat(200),
    });
    expect(state.items[0]!.note).toHaveLength(140);
  });

  it("ignores a note change that changes nothing, so the undo stack stays useful", () => {
    const start = loaded([pin(1, { note: "same" })]);
    const state = editorReducer(start, { type: "setNote", id: "id-1", note: "same" });
    expect(state).toBe(start);
    expect(canUndo(state)).toBe(false);
  });

  it("changes a palette", () => {
    const state = run(loaded([pin(1)]), { type: "setPalette", id: "id-1", palette: "ink" });
    expect(state.items[0]!.palette).toBe("ink");
  });

  it("does not record a nudge that is already against the edge", () => {
    // Otherwise holding an arrow key at the boundary fills the undo stack with
    // steps that visibly do nothing.
    const start = loaded([pin(1, { geometry: { point: { x: 1, y: 0.5 } } })]);
    const state = editorReducer(start, { type: "nudge", id: "id-1", delta: { x: 0.1, y: 0 } });
    expect(state).toBe(start);
  });
});

describe("deleting", () => {
  it("closes the numbering gap so badges match what the list shows", () => {
    // Deleting the second of three must not leave badges reading 1 and 3 under a
    // heading that says there are two — the numbers are the user's only handle
    // for referring to a mark.
    const state = run(loaded([pin(1), pin(2), pin(3)]), { type: "remove", id: "id-2" });
    expect(state.items.map((item) => item.created_order)).toEqual([1, 2]);
    expect(state.items.map((item) => item.id)).toEqual(["id-1", "id-3"]);
  });

  it("drops the selection when the selected mark goes", () => {
    const state = run(
      loaded([pin(1), pin(2)]),
      { type: "select", id: "id-2" },
      { type: "remove", id: "id-2" },
    );
    expect(state.selectedId).toBeNull();
  });

  it("keeps the selection when a different mark goes", () => {
    const state = run(
      loaded([pin(1), pin(2)]),
      { type: "select", id: "id-2" },
      { type: "remove", id: "id-1" },
    );
    expect(state.selectedId).toBe("id-2");
  });

  it("clearAll empties the document and is undoable", () => {
    const state = run(loaded([pin(1), pin(2)]), { type: "clearAll" });
    expect(state.items).toEqual([]);
    expect(canUndo(state)).toBe(true);
    expect(editorReducer(state, { type: "undo" }).items).toHaveLength(2);
  });
});

describe("undo and redo", () => {
  it("walks back and forward through the session", () => {
    const state = run(
      loaded([]),
      { type: "add", item: pin(1) },
      { type: "add", item: pin(2, { id: "id-2" }) },
    );
    expect(state.items).toHaveLength(2);

    const undone = run(state, { type: "undo" }, { type: "undo" });
    expect(undone.items).toHaveLength(0);
    expect(canRedo(undone)).toBe(true);

    const redone = run(undone, { type: "redo" }, { type: "redo" });
    expect(redone.items).toHaveLength(2);
  });

  it("discards the redo future once a new edit lands", () => {
    const state = run(
      loaded([]),
      { type: "add", item: pin(1) },
      { type: "undo" },
      { type: "add", item: pin(1, { id: "different" }) },
    );
    expect(canRedo(state)).toBe(false);
  });

  it("drops a selection that undo removed from the document", () => {
    // Leaving it would point the list and the overlay at a mark that no longer
    // exists.
    const state = run(
      loaded([]),
      { type: "add", item: pin(1, { id: "gone" }) },
      { type: "undo" },
    );
    expect(state.selectedId).toBeNull();
  });

  it("does nothing at the ends of the stacks", () => {
    const start = loaded([pin(1)]);
    expect(editorReducer(start, { type: "undo" })).toBe(start);
    expect(editorReducer(start, { type: "redo" })).toBe(start);
  });

  it("is not affected by selection, which is view state and not an edit", () => {
    const state = run(loaded([pin(1)]), { type: "select", id: "id-1" });
    expect(canUndo(state)).toBe(false);
    expect(isDirty(state)).toBe(false);
  });
});

describe("dirtiness and the save baseline", () => {
  it("becomes clean when the saved snapshot matches what is on screen", () => {
    const edited = run(loaded([pin(1)]), { type: "setNote", id: "id-1", note: "hem" });
    expect(isDirty(edited)).toBe(true);

    const saved = editorReducer(edited, {
      type: "saved",
      sent: edited.items,
      revision: 2,
      updatedAt: "2026-08-08T10:05:00Z",
      sentAtEpoch: 1,
    });
    expect(isDirty(saved)).toBe(false);
    expect(saved.revision).toBe(2);
  });

  it("stays dirty when an edit landed while the save was in flight", () => {
    // This is what makes the coalesced follow-up write fire. The baseline is
    // what was SENT, not what is on screen now.
    const first = run(loaded([pin(1)]), { type: "setNote", id: "id-1", note: "first" });
    const sent = first.items;
    const later = editorReducer(first, { type: "setNote", id: "id-1", note: "second" });

    const saved = editorReducer(later, {
      type: "saved",
      sent,
      revision: 2,
      updatedAt: null,
      sentAtEpoch: 1,
    });
    expect(isDirty(saved)).toBe(true);
    expect(saved.items[0]!.note).toBe("second");
  });

  it("ignores a confirmation for a document generation that has been replaced", () => {
    // A post-conflict reload landed while the PUT was open. Applying its
    // confirmation would restore the marks the user had just discarded.
    const edited = run(loaded([pin(1)], 4), { type: "setNote", id: "id-1", note: "hem" });
    const sent = edited.items;
    const reloaded = editorReducer(edited, { type: "hydrate", document: doc([], 9) });

    const late = editorReducer(reloaded, {
      type: "saved",
      sent,
      revision: 5,
      updatedAt: null,
      sentAtEpoch: edited.epoch,
    });

    expect(late.items).toHaveLength(0);
    expect(late.baseline).toHaveLength(0);
    // 5 is BEHIND the reloaded 9, so it is not adopted — a late confirmation must
    // never drag the client backwards.
    expect(late.revision).toBe(9);
  });

  it("fences off a stale confirmation on a never-saved document, where revisions cannot", () => {
    // The case a revision comparison could not see. A first-ever save is sent at
    // revision 0; the user clears before it lands; `clearAll` leaves the revision
    // at 0 too, so `0 === 0` let the confirmation through and the cleared marks
    // came back as the baseline.
    const drawn = run(loaded([], 0), { type: "add", item: pin(1) });
    const sent = drawn.items;
    const cleared = editorReducer(drawn, { type: "clearAll" });
    expect(cleared.revision).toBe(0);
    expect(cleared.epoch).not.toBe(drawn.epoch);

    const late = editorReducer(cleared, {
      type: "saved",
      sent,
      revision: 1,
      updatedAt: null,
      sentAtEpoch: drawn.epoch,
    });

    // The marks stay gone, the baseline is not resurrected, and the revision the
    // stale request reported is NOT adopted.
    expect(late.items).toHaveLength(0);
    expect(late.baseline).toHaveLength(0);
    expect(late.revision).toBe(0);
    expect(isDirty(late)).toBe(false);
  });

  it("does not adopt the revision a stale request reports after a delete", () => {
    // The backend accepts only `expected_revision: 0` once the row is gone
    // (`replace_annotation_document`'s `stored is None` branch). Clearing
    // synthesises exactly that 0. Adopting the in-flight PUT's revision instead
    // would make the very next write 409 against a document nobody else has
    // touched — and since both recovery actions end at the server's copy, whatever
    // the user drew next would be discarded.
    const edited = run(loaded([pin(1)], 4), { type: "setNote", id: "id-1", note: "hem" });
    const sent = edited.items;
    // What doClearAll's DELETE branch does on success.
    const clearedOnServer = editorReducer(edited, { type: "hydrate", document: doc([], 0) });

    const late = editorReducer(clearedOnServer, {
      type: "saved",
      sent,
      revision: 5,
      updatedAt: null,
      sentAtEpoch: edited.epoch,
    });

    expect(late.revision).toBe(0);
    expect(late.items).toHaveLength(0);
  });

  it("still applies a confirmation sent under the current generation", () => {
    const edited = run(loaded([pin(1)], 4), { type: "setNote", id: "id-1", note: "hem" });
    const applied = editorReducer(edited, {
      type: "saved",
      sent: edited.items,
      revision: 5,
      updatedAt: null,
      sentAtEpoch: edited.epoch,
    });
    expect(applied.revision).toBe(5);
    expect(isDirty(applied)).toBe(false);
  });

  it("compares field by field, so a re-serialised document does not read as an edit", () => {
    const a = [pin(1, { note: "same" })];
    const b = [{ ...pin(1, { note: "same" }) }];
    expect(itemsEqual(a, b)).toBe(true);
  });

  it("notices a geometry-only change", () => {
    const a = [pin(1)];
    const b = [pin(1, { geometry: { point: { x: 0.4, y: 0.5 } } })];
    expect(itemsEqual(a, b)).toBe(false);
  });
});

describe("helpers", () => {
  it("selectedItem resolves the current selection, or null", () => {
    const state = run(loaded([pin(1)]), { type: "select", id: "id-1" });
    expect(selectedItem(state)?.id).toBe("id-1");
    expect(selectedItem(loaded([pin(1)]))).toBeNull();
  });

  it("byCreatedOrder does not mutate its input", () => {
    const items = [pin(2), pin(1)];
    const sorted = byCreatedOrder(items);
    expect(sorted).not.toBe(items);
    expect(items.map((item) => item.created_order)).toEqual([2, 1]);
  });

  it("createItem generates a fresh id and the next order", () => {
    const item = createItem("pin", { point: { x: 0.1, y: 0.2 } }, "sage", [pin(1)]);
    expect(item.created_order).toBe(2);
    expect(item.id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(item.note).toBe("");
    expect(item.palette).toBe("sage");
  });
});
