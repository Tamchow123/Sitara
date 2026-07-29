# 0015 — Single-round constrained refinement

- **Status:** accepted
- **Amended by ADR 0018 (Phase 16B, 2026-07-23):** refinement is version-aware —
  a v1 or v2 source is validated by dispatch and the refined output must keep the
  source's `schema_version`. The dedicated canonical `neckline_style` lives in
  `source_selections`, already an immutable refinement root, so it can never be
  changed by any refinement category, and the prompt builder renders the
  canonical neckline authoritatively so coverage-refinement prose cannot
  contradict it.
- **Date:** 2026-07-20
- **Deciders:** Sitara maintainers
- **Phase:** Phase 14 (see ../phases/PHASES.md)
- **Related:** ADR 0001 (image model), ADR 0009 (structured DesignSpec
  generation), ADR 0010 (deterministic image-prompt builder), ADR 0011
  (asynchronous generation pipeline), ADR 0012 (private design-image
  storage), ADR 0013 (generation progress and results), ADR 0014
  (inspiration metadata influence)

## Context

Phase 12 delivers one durable generation attempt that produces exactly one
private `DesignVersion` per `Design`. Users who receive a concept they mostly
like but want one specific detail changed currently have no path but
re-answering the questionnaire from scratch. Phase 2's model evaluation never
ran a comparison between fresh regeneration and true image-to-image editing
for a partial-change workflow, so no evidence exists that editing would
preserve more of the original than a fresh generation would, and image
editing raises its own unresolved provider-capability, pricing, and
provider-terms questions. Given that, this phase implements the smallest
mechanism that lets a user request exactly one bounded change and receive a
second, honestly-labelled concept, without ever claiming visual continuity
that has not been demonstrated.

## Decision

### Exactly one refinement, enforced in the database and the pipeline

A `Design` may be refined at most once. `MAX_DESIGN_VERSIONS` (default `2`)
and the fixed `MAX_REFINEMENTS = 1` bound this at the application layer, and
`DesignVersion.parent_version`/`refined_versions` plus
`enqueue_design_refinement`'s check (`source_version.refined_versions
.exists()`) enforce it operationally: once a refinement has actually
succeeded and produced a version 2, no further refinement of that `Design` is
accepted. A refinement attempt that failed cleanly (no staged image, no
unresolved provider spend) does not permanently consume the one allowed
round — the user may retry from the same source version — but an attempt
with ambiguous provider spend blocks further attempts the same way a stuck
initial generation already does, never silently retrying a provider call
whose outcome is unknown.

### A strict, versioned refinement request contract

`apps/api/sitara/generation/refinement.py` defines a strict Pydantic v2
`RefinementRequest` (`extra="forbid"`): a fixed `schema_version` (currently
`REFINEMENT_REQUEST_SCHEMA_VERSION = 1`), exactly one `change_type` drawn
from eight allowlisted categories (`colour_story`, `fabric_and_texture`,
`embellishment`, `sleeves_and_coverage`, `neckline`,
`dupatta_or_saree_drape`, `silhouette_detail`, `styling_details`), and an
optional bounded `note` (`REFINEMENT_NOTE_MAX_LENGTH = 300` characters,
`str_strip_whitespace=True`). The note is untrusted user free text: it is
safety-scanned (`scan_user_text`, reusing Phase 13's `content_safety.py`
primitives, now additionally hardened against Unicode format-character
phrase-splitting bypasses) before it ever reaches a provider, and it is
rejected outright if it contains measurement units/marks or sewing-pattern
language — the note may express a preference, never a specification. The
same canonical-JSON-plus-SHA-256 discipline Phase 13 established for
`InspirationContextSnapshot` applies here: `refinement_request_canonical_json`
and `refinement_request_sha256` give the request one deterministic,
reproducible audit hash.

### Exact DesignSpec diff allowlists, checked field-by-field

`REFINEMENT_ALLOWED_PATHS` maps each `change_type` to the exact dotted
DesignSpec paths it may touch (for example `colour_story` may only touch
`colour_story.*`; `sleeves_and_coverage` may only touch
`coverage_and_drape.sleeves`/`coverage_and_drape.back_and_midriff`).
`REFINEMENT_IMMUTABLE_ROOTS` (`schema_version`, `source_selections`) can
never change regardless of category. After every provider attempt,
`diff_design_spec_paths` computes the exact set of changed paths between the
source and candidate DesignSpec and `path_is_allowed` rejects the candidate
outright if any changed path falls outside the requested category's
allowlist or touches an immutable root — this is a structural check on the
diff itself, not a trust placed in the model's own claim about what it
changed.

### The same DesignSpec schema, a separate refinement prompt

`DESIGN_SPEC_SCHEMA_VERSION` stays `1` — a refined DesignSpec is validated by
the exact same schema as an initial one, so every existing DesignSpec
consumer (the prompt builder, the result API, the copy/download brief)
handles both without a version branch. The refinement request/response
prompt itself is a new, separate structured-output prompt
(`apps/api/sitara/generation/refinement_prompting.py`,
`REFINEMENT_TEMPLATE_VERSION = "1.0.0"`, its own
`refinement_prompt_template_fingerprint()`/`REFINEMENT_PROMPT_TEMPLATE_HASH`
guard) — it is not a variant of Phase 9's initial-generation system prompt,
since its job (apply exactly one allowlisted change to an existing,
already-valid DesignSpec) is different from Phase 9's (author a DesignSpec
from questionnaire answers). The DesignSpec's own recorded template version
on a refined version is `REFINEMENT_DESIGN_SPEC_TEMPLATE_VERSION =
f"refinement-{REFINEMENT_TEMPLATE_VERSION}"` (currently
`"refinement-1.0.0"`), so an audit reader can always tell from a persisted
`DesignVersion` alone whether it came from the initial-generation prompt or
the refinement prompt. `PROMPT_BUILDER_VERSION` (image-prompt builder) stays
`3.0.0` — unchanged, per the next section.

### Parent-child DesignVersion lineage

`DesignVersion` gains `parent_version` (self-FK, `on_delete=PROTECT`,
`related_name="refined_versions"`), `refinement_request` (the validated,
canonical request JSON), `refinement_request_schema_version`, and
`refinement_request_sha256`, with CheckConstraints enforcing: all four
refinement fields present together or none of them; a version-1 row never
has a parent or a refinement request; a version-2-or-later row always has
both; a row can never be its own parent. `GenerationAttempt` gains a
parallel `generation_kind` (`initial`/`refinement`), `source_design_version`,
and its own copy of the refinement request fields — the async task only
receives the attempt id, not the not-yet-created child `DesignVersion`, so
the attempt needs its own durable copy to survive a worker restart.

### The historical inspiration snapshot is copied forward, not rebuilt

A refined version's `inspiration_context`/`_schema_version`/`_sha256` are
copied byte-for-byte from the source version at persistence time
(`_finalise_refinement_atomic`) rather than rebuilt from a fresh live
catalogue query. This matches ADR 0014's own invariant that a persisted
snapshot is immutable historical audit data: refinement never re-selects
inspirations, never re-validates them against `publicly_eligible()`, and
never lets a later asset retirement or rights change alter what a refined
version's acknowledgement displays — the refined version inherits exactly
the influence (or lack of it) its parent already had.

### One durable pipeline, branched by generation kind

`apps/api/sitara/generation/pipeline.py`'s existing resumable, advisory-lock
protected, crash-recoverable state machine handles both generation kinds —
there is no second pipeline. `enqueue_design_refinement` mirrors
`enqueue_design_generation`'s precondition ordering (idempotent replay check,
availability check, in-progress-attempt check, `Design` status check, source
availability, staged/unresolved-spend checks) with refinement-specific
substitutions: the `Design` must be `GENERATED` (not `DRAFT`) to start a
refinement, and the source `DesignVersion` is independently re-validated
(`validate_source_version`) rather than assumed valid from a prior read.
`_execute()` branches on `attempt.generation_kind` only for the text stage
(`_run_refinement_text_stage` vs the existing `_run_text_stage`); every other
stage — image submission, polling, staging, permanent ingest, the exact
same spend-resolved/ambiguous error taxonomy, the exact same deterministic
Celery task id (the attempt UUID) — is unmodified and shared.

### Fresh text-to-image generation, seed reuse as a continuity aid only

A refinement is a **complete new text-to-image generation**, never
image-to-image editing: the refined DesignSpec is rebuilt into a fresh
prompt through the exact same deterministic `build_image_prompt` Phase 10
already uses for initial generation (no branch, no refinement-specific
prompt-builder path — `PROMPT_BUILDER_VERSION` stays `3.0.0` because the
builder itself did not change, only the DesignSpec it is given). The
original image's bytes, URL, or storage key are never sent to any provider
— refinement never has network access to the original image at all, only to
the source DesignVersion's structured data. When the source generation
attempt recorded a seed, the pipeline reuses it (`_find_source_attempt_seed`)
as one input to the provider request; this is documented everywhere it
appears (backend audit fields, frontend copy) as a continuity **aid**, not a
guarantee — FLUX's own non-determinism, plus the changed prompt text from
the refined DesignSpec, mean pose, composition, framing, and unstated details
can still differ substantially between version 1 and version 2.

### Version 1 remains readable through and after a refinement failure

Starting a refinement never deletes, hides, or invalidates version 1: the
result endpoint continues to serve it, the frontend result page keeps it
mounted, and a failed refinement attempt never creates a partial or
placeholder `DesignVersion`. `GenerationAttempt.generation_kind` is exposed
on the public job payload precisely so the frontend can render honest,
kind-aware progress copy and, on failure, help the user find their way back
to the still-private, still-complete original — without ever exposing the
source version id itself as a public/job-payload field (that plumbing is a
frontend-only navigation convenience, carried through an optional query
parameter set by the page that already knows the id, never trusted for
anything security-sensitive).

### Side-by-side comparison with honest drift disclosure

When a design has been refined, the frontend fetches both versions through
two independent pairs of queries (result + signed image, per version) and
renders them side by side (stacked, original-then-refined, on narrow
viewports), each with its own complete brief available on demand. The
comparison view states plainly, near its heading, that the refined image is
a new generation, that visual drift is expected, that only the DesignSpec
edit itself was constrained, and that seed reuse does not guarantee the same
pose, composition, or garment details — the same disclosure appears before
submission, as a checkbox the user must acknowledge before a refinement can
be requested at all.

### The raw refinement note is never exposed in a result payload

`DesignResult`'s `lineage.refinement` carries only `change_type` — never the
free-text note, never the request's hash, never a seed, never provider or
storage detail. The note exists to steer one generation attempt and is
audited (hashed, versioned) on the backend for accountability; it is not
product-facing history, and copying it into a public-ish result payload
would risk the same kind of unintended disclosure ADR 0014 already avoids for
inspiration titles/attribution.

## Consequences

- Phase 2's refinement evaluation (a scoped comparison of fresh regeneration
  against true image-to-image editing) still has not run. This phase does
  not claim refinement preserves visual continuity better than a second
  independent generation would — it only claims the requested DesignSpec
  edit is constrained and auditable. Any future claim of measured continuity
  requires that evaluation and a documented decision update.
- A later image-editing phase (true image-to-image or inpainting-based
  refinement) needs its own scoped model evaluation, rights/pricing/
  provider-terms review, and a separately approved phase — this ADR records
  the fresh-generation-only MVP boundary, not a permanent architectural
  ceiling.
- Phase 16 (live-generation rate limits and cost ceilings) inherits the
  refinement pipeline's shared resume/idempotency/spend-taxonomy guarantees
  unchanged; a refinement attempt is bounded by
  `MAX_REFINEMENT_PROVIDER_REQUESTS = 2` the same way initial generation is
  bounded by its own retry ceiling, so no new unbounded-spend surface is
  introduced.
- A future third `generation_kind` would extend
  `GenerationAttempt.GenerationKind`, add its own `_run_<kind>_text_stage`
  branch in `pipeline.py`, and its own frontend progress copy colocated with
  the existing kind-aware copy in `features/generation/` — the branching
  points this phase establishes are the intended extension seams.

## Alternatives considered

- **Image-to-image editing (ControlNet, inpainting, or similar)** — rejected
  for this phase: no scoped model evaluation has run, and it raises
  unresolved provider-capability, pricing, and provider-terms questions a
  separately authorised evaluation would need to answer first. Fresh
  text-to-image generation with optional seed reuse was chosen as the
  smallest mechanism that ships a genuinely useful refinement without those
  open questions.
- **Multi-round refinement** — rejected: each additional round would compound
  drift, cost, and audit complexity without evidence that users need more
  than one bounded change; `MAX_REFINEMENTS = 1` keeps the feature's scope,
  cost, and UX promise small and honest.
- **A free-text refinement instruction (no category allowlist)** — rejected:
  an unconstrained instruction could not be diffed against a fixed allowlist
  of DesignSpec paths, reopening exactly the "the model claims it changed
  only X" trust problem the allowlist-plus-diff-check mechanism exists to
  avoid. The optional note stays a bounded, safety-scanned preference
  attached to one fixed category, not a substitute for it.
- **Reusing Phase 9's initial-generation prompt for refinement** — rejected:
  the tasks are different (author from scratch vs. edit exactly one
  category of an existing, already-valid DesignSpec), and sharing one prompt
  would make the two responsibilities harder to reason about and version
  independently. A separate `refinement_prompting.py` module with its own
  template version keeps both audit-traceable on their own terms.
- **Rebuilding the inspiration snapshot for a refined version** — rejected:
  refinement never re-selects inspirations, and rebuilding from the live
  catalogue would let a later asset retirement or rights change silently
  alter what a previously-generated version's acknowledgement displays,
  contradicting ADR 0014's immutable-audit-data invariant.
