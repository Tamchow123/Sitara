# 0020 — Private stylist annotation workspace

- **Status:** accepted
- **Date:** 2026-08-08
- **Deciders:** Sitara maintainers
- **Phase:** Phase 19 (see ../phases/PHASES.md). Phase 18 (E2E tests and
  deployment) is deliberately **skipped**; Phase 19 extends Phase 17's Playwright
  suite instead, and carries no deployment-smoke obligation.
- **Related:** ADR 0012 (private design-image storage), ADR 0013 (generation
  progress and results), ADR 0014 (inspiration metadata influence), ADR 0015
  (single-round constrained refinement), ADR 0021 (account render delivery by
  email)

## Context

A user who receives a concept and wants to talk about it — with a tailor, a
family member, or themselves a week later — has nowhere to record *where* on the
garment they mean. ADR 0015 gives them exactly one refinement, spent on a whole
category of change; it is not a place to say "this hem, not that one". The
questionnaire cannot express it either, because the thing being pointed at does
not exist until the concept does.

Three constraints shaped the answer.

**The concept must not change.** ADR 0012 makes `DesignVersion` image provenance
immutable audit data, and ADR 0015 makes refinement a fresh text-to-image
generation that is never sent the original's bytes. A markup feature that edited
the stored render would break both. So annotation had to be *additive* — a
separate private overlay that references a version without touching it.

**Notes are the most personal free text in the product.** A note says what
someone dislikes about a garment they intend to wear. That is not catalogue data
and it is not provider input.

**The overlay cannot be the only representation.** A picture with marks on it is
unreadable to a screen reader and unusable by keyboard. Accessibility is a
product requirement here, not later polish.

## Decision

### An annotation document per `DesignVersion`, stored as validated structured data

One `DesignVersionAnnotation` row per version, holding an ordered list of marks
in a schema-versioned JSON document, validated by
`designs/annotation_schema.py` — which is the sole authority. Four mark types:
`pin`, `arrow`, `rectangle`, `freehand`. Each carries a geometry, a note of at
most 140 characters, a palette name and a `created_order`. At most 100 marks.

The **original image is never modified, re-encoded or re-ingested.** The
annotation row references the version; the version knows nothing about it.
Deleting the annotation document returns the version to exactly its pre-annotated
state.

### Coordinates are normalised against the version's own canonical dimensions

Every stored coordinate is a fraction in `[0, 1]` of the version's recorded
`image_width`/`image_height`, which come from the server's annotation document
and never from the browser's `<img>`. Rectangles additionally satisfy
`x + width <= 1` server-side.

This is what makes a mark mean the same thing at any rendered size, at any zoom
step, and in the server-composited PNG. The consequences are structural rather
than remembered:

- the stage is sized by `aspect-ratio: {w} / {h}` with `object-fit: contain`, and
  **never `cover`** — a cover box crops, and a crop silently invalidates every
  stored coordinate while looking perfectly fine;
- pointer input is converted from `getBoundingClientRect()`, re-measured on every
  press rather than trusted from the cached value a `ResizeObserver` keeps warm;
- zoom and pan transform the image and the overlay **together**, as one wrapper,
  so geometry is never rewritten to fake a viewport change;
- the overlay is independent of the `<img>` `src`, so refreshing a short-lived
  signed URL cannot move a mark.

### Optimistic concurrency by revision, never a silent overwrite

Each save sends the `expected_revision` the client holds. A stale value returns
`409 annotation_conflict` with the server's current revision and leaves the
stored document untouched. The client surfaces that as a distinguishable
conflict, shows both options in plain words — *Reload latest* and *Discard my
changes* — and **both end at the server's copy**. Keeping the local copy and
overwriting the other one is precisely what "nothing is overwritten silently"
rules out; the two buttons are two honest names for the same safe outcome, not a
choice between winning and losing.

A conflict or a failure **suspends** autosave until the user acts. A flaky
connection must not re-fire a doomed write on every tick and re-open the dialog
mid-edit.

### The list is the accessible representation, not a summary of one

Every mark appears as a list row carrying its number and type in the accessible
name, a positional description in words that is always rendered, an editable
note, palette radios with visible text names, delete, and arrow-key nudges.
Selection is bidirectional between the overlay and the list. The toolbar is a
real `role="toolbar"` with a roving tabindex.

Palette is never the only carrier of meaning: each swatch has a visible text
name, and every mark is drawn with a white halo underneath its colour so it
survives both pale silk and a black lehenga. The server's PNG composition applies
the same halo rule, so a mark looks the same in the browser and in the emailed
render.

### The document is memory-only in the browser

No annotation data in `localStorage`, `sessionStorage` or IndexedDB. The
workspace holds it in React state and the server holds it durably; there is no
third copy for someone else on a shared machine to find. Asserted in the
component suite (with a stubbed `indexedDB` whose `open` must never be called)
and again end to end (by enumerating `indexedDB.databases()`).

### Ownership is the same boundary as everywhere else

The annotation endpoints resolve ownership before the object is looked up, so an
inaccessible, nonexistent and foreign version all return the same 404. Nothing
about the notes, the mark count, or whether a document exists is observable
without ownership.

### Note text never reaches an AI provider, and never reaches a log

A note is not provider input under any flag. ADR 0014's provider-facing
inspiration influence is unchanged and reference-image conditioning (ADR 0019)
is untouched by this phase: annotations are not part of any prompt, any
`DesignSpec`, or any refinement payload. Nor are they logged — sensitive-path
logs carry operation names, row UUIDs and exception types only.

## Consequences

**Easier.** A user can say exactly where they mean, and get that back as an
image they can hand to a tailor. The result screen gains a purpose beyond
"look at it once". The stored concept and its audit trail are entirely unaffected,
so nothing about this feature can corrupt Phase 11/12 provenance.

**Harder.** There are now two mutation paths on one document — the debounced PUT
and the DELETE behind *Clear all* — so their ordering matters. Both halves are
handled: a pending debounce is cancelled before a clear, and a save confirmation
whose revision the document has already moved past is ignored rather than
applied. Getting either wrong restores marks the user deleted.

**Deferred.** No shared or collaborative annotation, no server-side annotation
history beyond the current document (undo/redo covers the unsaved session only),
no annotation on a refinement's *parent* from the child's screen, and no
annotation export other than the emailed render in ADR 0021.

**Would trigger revisiting.** A second consumer of the geometry (a print layout,
a PDF, a tailor-facing view) would justify moving `describeGeometry`-style prose
and the mark-drawing rules into a shared module rather than mirroring them in the
browser and the composer. A requirement for more than 100 marks or notes longer
than 140 characters would need the schema version bumped and the composition
performance figures re-measured, since page height scales with note count.

## Alternatives considered

**Edit the stored image in place.** Rejected outright: it breaks ADR 0012's
immutable provenance, destroys the original, and makes "the concept itself never
changes" false.

**A `<canvas>` for the overlay.** Rejected. It would require reading the private
original's pixels into the page to composite, producing a data URL of it — a new
copy of private imagery in the browser for no benefit. An inline SVG whose
`viewBox` *is* the image's pixel space needs no pixel access at all, and its
elements are focusable and labellable, which canvas drawings are not. There is
deliberately no `<canvas>`, `toDataURL`, `getImageData` or `drawImage` anywhere
in the workspace.

**Store coordinates in rendered pixels.** Rejected. They would be meaningless on
any other viewport and would silently drift the moment a container changed width.

**Last-write-wins instead of revisions.** Rejected. Two tabs, or one tab and a
lost response, would silently discard notes with no way to notice.

**A fifth `note` mark type with no geometry.** Rejected. A note without a mark
has nowhere to point; the `N` shortcut instead focuses the note editor of the
selected or most recent mark, and does nothing when there are no marks.

**Free-form colour choice.** Rejected. A user-chosen hex would defeat the
contrast guarantee the halo rule provides and put an untested colour outside the
token set. Three named palette entries, each with a visible label.
