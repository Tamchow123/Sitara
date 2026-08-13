# 0028 — A refinement changes its own selection

- **Status:** accepted
- **Date:** 2026-08-13
- **Deciders:** Sitara project owner
- **Phase:** Phase 23 (see ../phases/PHASES.md)
- **Related:** **amends ADR 0015** (single-round constrained refinement);
  ADR 0010 (the deterministic image-prompt builder, whose 8.0.0 restructure is
  half the root cause); ADR 0005 (a design's questionnaire version is pinned and
  immutable); ADR 0009 (structured design-spec generation); ADR 0016
  (deterministic demo mode); ADR 0019 (reference-image conditioning)

## Context

### Two correct decisions produced an empty intersection

Refinement (Phase 14, ADR 0015) was designed against prompt builder 3.0.0 — a
6,000-character prompt in which model-authored narrative carried most of the
design. Its per-category allowlist therefore names **narrative** DesignSpec
fields (`styling_notes`, `concept_summary`, `construction_caveats`,
`coverage_and_drape.neckline`), and it froze the one thing that must never
drift:

```python
REFINEMENT_IMMUTABLE_ROOTS = frozenset({"schema_version", "source_selections"})
```

Freezing `source_selections` was right at the time. It was the exact echo of
already-validated questionnaire answers, and letting a model rewrite the user's
own answers under the name "refinement" would have been indefensible.

Prompt builder **8.0.0** then deliberately inverted the balance, for its own
good and recorded reasons: canonical selections became the skeleton, narrative
became a small set of bounded supplementary slots, and
`IMAGE_PROMPT_TARGET_CHARS` fell from 6,000 to 1,500. Whole narrative fields
stopped being rendered at all — `title`, `concept_summary`, `styling_notes`,
`construction_caveats`, `image_alt_text`, `garment_components`,
`cultural_context.*`, `colour_story.palette_summary`, `colour_story.rationale`,
`embellishment_plan.techniques`, `.density`, `.restraint_notes`, and
`fabrics_and_texture` whenever canonical fabrics exist.

Neither decision was wrong. Together they left refinement changing only fields
the image prompt no longer reads, while the fields it does read were frozen. For
three of the eight categories the refined design **provably could not alter a
single character of the image prompt**; for two more it could only move a slot
unrelated to what was asked.

> **Owner report:** "the ammendments don't make any changes in all tests I've run
> to the design at all."

That is not a tuning problem or a model problem. It is an empty intersection.

### Why it had to be fixed now

Phase 24 charges a token per refinement. A refinement must change the concept
before the product charges for it.

## Decision

### 1. Each category may change the one canonical selection it is named after

`source_selections` stops being frozen whole. A `colour_story` refinement may
change the colour answers; a `fabric_and_texture` refinement may change
`fabrics`; and so on. **Which** field that is depends on the DesignSpec version —
versions 1 and 2 record colour as one ordered `colour_palette` and coverage as
one `coverage_preferences` multi-select, version 3 records colour per garment
role and coverage per body area — so the mapping is version-dispatched
(`selection_semantics.py`: a short explicit table over known versions, never a
schema-mapping framework, per ADR 0009).

An **empty** entry is meaningful and is not the same as a missing version key: a
version-1 spec has no `neckline_style` because the questionnaire that produced it
had no neckline question. A neckline refinement of such a concept could only ever
return an unchanged design, so it is refused with a controlled
`refinement_category_unavailable` rather than accepted as a no-op.

### 2. Four selections stay frozen, each for its own reason

`REFINEMENT_IMMUTABLE_SELECTION_FIELDS` is `garment_type`, `ceremony`,
`regional_style`, `custom_colours`. These are checked **explicitly**, not left to
the allowlist's silence, so a future allowlist entry cannot grant one by accident.

- **`garment_type` / `ceremony`** — changing either is a new design, not a
  refinement of this one.
- **`regional_style`** — a cultural direction is not a styling tweak. CLAUDE.md
  §12 requires regional influences to stay optional and non-prescriptive, and a
  model deciding a concept is now Rajasthani is exactly the prescriptive move
  that rule exists to prevent.
- **`custom_colours`** — the bride's saved palette is the set a colour question
  may be answered *from*, not a selection of its own. There is nothing here for
  a refinement to mean.

`schema_version` remains an immutable root.

### 3. The replacement guarantee: revalidation against the PINNED questionnaire

`source_selections` was trustworthy for exactly one reason — it was an exact echo
of validated answers and nothing could touch it. Once a refinement may change it
that guarantee is gone, and **nothing else replaces it**: a model can answer
`"fabrics": ["unobtainium"]` and the deterministic prompt builder will render it
faithfully, because rendering what it is given is the builder's job.

The replacement is deliberately as strong as what it replaces. Every changed
value is revalidated as **a set of answers the design's own questionnaire would
have accepted from the user in the first place**. Three things are load-bearing:

- **The design's pinned version, never the active one.**
  `Design.questionnaire_version` is assign-once, and a design pinned to a
  RETIRED version must stay refinable. Validating against whatever is active
  today would reject a good refinement of an older concept — or worse, accept a
  value that version never offered.
- **No second rule evaluator.** `validate_questionnaire_answers` (and through it
  `questionnaire.rules`) stays the single authority for declared-option
  membership, `restrict_options` consistency, exclusivity, item counts, answer
  shape and colour-palette membership. §12 forbids duplicating those rules and a
  second copy would drift.
- **Selections alone are a complete answer set.** Every required question in
  every committed questionnaire version is a `source_selections` field, so
  validating the selections on their own with `require_complete=True` is exact
  rather than approximate. A structural test pins that invariant; if a future
  version adds a required question outside the selection set, refinement fails
  **closed** and that test says why.

Failure is the controlled `RefinedSelectionsInvalid`, carrying only the question
ids that failed — never the rejected value, never the questionnaire contents,
never a raw validator message.

The model's own claim about what it changed is still never trusted: the diff is
computed field by field and checked against the category's exact allowlist,
exactly as ADR 0015 required.

### 4. `styling_details` is retired — eight categories become seven

Every narrative path `styling_details` could change is unrendered by builder
8.0.0, and it names no canonical selection to fall back on. A control whose
effect is invisible in the thing the user is looking at is worse than an absent
one, so it is **removed** rather than left offering a promise it cannot keep.

It stays *named* in `RETIRED_REFINEMENT_CHANGE_TYPES` because a persisted
`DesignVersion.refinement_request` row may carry it, and a historical row is
audit data that is read and never rewritten (CLAUDE.md §14's discipline applied
to a different table). So there are two constants and they mean different things:
`REFINEMENT_CHANGE_TYPES` (seven — what a client may request today) and
`PERSISTED_REFINEMENT_CHANGE_TYPES` (eight — what may appear in a stored row or a
result payload), the second derived from the first so they cannot drift.

### 5. A colour refinement is sent no reference images

Reference-image conditioning (ADR 0019) is kept for every category except
`colour_story`. A reference photograph outweighs a text clause about colour, so
"make it green" against a red reference kept losing — which is the reported bug.
Suppression is per request and decided from the request itself; nothing else
about ADR 0019 changes, and the demo path never builds a reference URL at all.

### 6. Demo mode applies the same canonical edit, and says when the asset did not move

Demo mode stays strictly zero-cost and deterministic (ADR 0016). It now applies
the same canonical selection change as a live refinement, choosing from
**candidate values computed by the same validator that judges the result** —
substituting each declared option and keeping the ones the design's own
questionnaire accepts — rather than from a phrase vocabulary that is a superset
of any one questionnaire.

The demo asset pack is small, so a changed brief will often select the same
image. That is disclosed rather than hidden: the result carries
`demo_asset_unchanged`, and the UI says the refinement did change the design
brief but the pack had no closer image, and that live generation would produce a
new one. Demo output is never presented as a fresh provider render.

### 7. `PROMPT_BUILDER_VERSION` deliberately does not move

The builder is unchanged. This phase changes what it is *given*, not how it
renders. Bumping 8.3.0 would demand a reviewed snapshot/manifest regeneration for
a builder that produces byte-identical output for byte-identical input, and would
break the comparability of persisted `prompt_builder_version` audit data. Two
versions that do move: `REFINEMENT_TEMPLATE_VERSION` and
`DEMO_REFINEMENT_TEMPLATE_VERSION`, both to `2.0.0`, because the instructions
given to the structured-generation stage genuinely changed.

## Consequences

- A refinement now changes the image prompt, and changes it in the field the
  customer asked about. **This is the honest claim: the refined prompt differs,
  and differs in the requested field.** Whether the provider's image visibly
  changes is an operator-run live checkpoint, not something this phase can or
  does assert — nothing in it has made a paid provider call.
- `source_selections` is no longer an exact echo of the questionnaire answers
  for a refined version. It is now "a set of answers that questionnaire would
  have accepted", which is weaker and is the deliberate trade. Anything reading
  a refined version's selections as a record of what the user personally typed
  is wrong; the parent version still holds that.
- `REFINEMENT_CHANGE_TYPES` losing a member is an API contract change: the
  OpenAPI schema and the generated TypeScript client are regenerated and proved
  drift-free. A stored `styling_details` row stays valid and readable for ever.
- Two new controlled failures a caller can see: `refinement_category_unavailable`
  (the category owns nothing on this spec version) and the invalid-selection path
  (the model produced an answer the design's own questionnaire would refuse).
  Both fail closed and neither leaks the rejected value.
- The single-round limit is untouched: `MAX_REFINEMENTS = 1`, still enforced via
  `DesignVersion.parent_version`. Refinement is still a fresh text-to-image
  generation through the same deterministic builder and the same selected model —
  never image-to-image, never sent the original image's bytes, URL or storage key.
- Deferred: the seven categories still map to fixed field groups. A refinement
  that should move two groups at once ("make it lighter and less embroidered")
  still needs two rounds, and only one is allowed.

**Revisit this if** a future prompt-builder restructure changes which fields are
rendered again — the failure mode this ADR fixes is not a bug in either system
but a drift between two, and the same drift can recur. The structural test that
pins "every required question is a selection field" is the tripwire.

## Alternatives considered

- **Widen the narrative allowlist and leave `source_selections` frozen.**
  Rejected: it fixes nothing. The narrative fields are unrendered — that is the
  root cause, not an incidental detail — so a wider allowlist over them still
  produces a byte-identical prompt.
- **Make the prompt builder render narrative again.** Rejected: it undoes
  8.0.0's recorded reasons (advisory prose pulls the provider toward portraiture
  and can contradict coverage requirements), requires a `PROMPT_BUILDER_VERSION`
  bump and a snapshot regeneration, and degrades every future generation to fix
  one round of refinement.
- **Let a refinement change any selection, including `garment_type`.**
  Rejected: that is a new design. The user has a single-round refinement, and
  spending it on a garment change would silently discard the concept they were
  looking at.
- **Trust the model's stated change instead of diffing.** Rejected outright, and
  it was already prohibited by ADR 0015. A model's claim about its own output is
  not evidence.
- **Deprecate `styling_details` with a warning rather than removing it.**
  Rejected: a control that cannot affect the image is not improved by a label
  saying so. Removing it from the request side while keeping it readable on the
  result side gives the honest behaviour without rewriting history.
- **Suppress reference images for every category.** Rejected: for six of the
  seven the reference is doing exactly what ADR 0019 intended. Only colour is in
  direct conflict with it.
- **Regenerate the demo pack so every refinement yields a different image.**
  Rejected: it would spend money, and it treats "the demo looks convincing" as
  more important than "the demo is honest". Disclosure is cheaper and truer.
