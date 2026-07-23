# 0018 — Questionnaire feedback, cultural expansion, and visual choice UX (Phase 16B)

- **Status:** accepted
- **Date:** 2026-07-23
- **Deciders:** Sitara maintainers (phase-council review)
- **Phase:** 16B (inserted; see ../phases/PHASES.md and ../phases/phases-16b.md)

## Context

Phase 16 (live-generation security and cost controls, ADR 0017) and the
subsequent composition/coverage-first prompt restructure (ADR 0010 amended,
`PROMPT_BUILDER_VERSION` 5.0.0) were already delivered and merged. The first
substantial round of user feedback asked for concrete taxonomy and UX changes
that cut across the questionnaire schema, the canonical generation inputs, the
deterministic demo engine, the prompt builder and the wizard:

- satin as a fabric distinct from silk;
- a culturally-reviewed Sikh wedding ceremony (Anand Karaj);
- a dedicated, mutually-exclusive neckline question instead of the single
  `high_neckline` coverage tag;
- a much larger curated colour vocabulary without an unusable scrolling list;
- an explicit, reversible "No preference — let Sitara decide" interaction;
- prevention of contradictory coverage / neckline / head-covering / dupatta
  selections;
- rights-controlled, schema-driven visual option cards and a compact grouped
  colour-swatch selector.

Because this changes questionnaire *capabilities* and canonical generation
inputs, the backend contract, the generated TypeScript, the demo engine and the
prompt builder all had to stay aligned. The binding constraints were the
repository non-negotiables: published questionnaire-version immutability
(ADR 0005), the deterministic zero-cost demo guarantee (ADR 0016), the
rights boundary around images (ADR 0006/0014), and the deterministic,
versioned, audit-immutable generation contract (ADR 0009/0010).

## Decision

Deliver Phase 16B as five independently reviewed commits without reopening the
numbered Phase 16. The key decisions:

### Why 16B rather than reopening Phase 16

Phase 16 shipped a distinct concern (live-generation security/cost controls) and
is merged and immutable as delivered history. This work is a separate,
user-feedback-driven slice on top of the merged composition baseline; it is
sequenced before Phase 17's final visual-polish/accessibility pass. Treating it
as an inserted phase keeps each delivered phase's ADR and history coherent.

### Questionnaire version: create v3, never edit v1/v2

v1 is the published, active, fingerprint-locked seed; v2 is a never-activated
draft that the test suite treats as a fixed, known-shape artifact (its exact
rule-set difference from v1 is asserted). No migration or seed activates v2, and
there is no `is_active` field — "active" is the `status` enum guarded by a
partial-unique constraint. Because v2 is relied upon as fixed history by tests,
and per the phase spec's "if there is any evidence v2 is relied upon as
published history, create v3", we introduced **questionnaire v3** as a new draft
(distinct pk, `status="draft"`, `activated_at=null`). v1 and v2 are untouched;
their fingerprints survive v3 activation. Activation stays transactional and
retires the previous active version exactly as the existing service requires.

### Option presentation metadata: `visual_key` / `group`

The strict option shape gains two OPTIONAL fields, `visual_key` and `group`,
each validated as a lower-case machine identifier (`^[a-z][a-z0-9_]{1,63}$`) —
never a URL, path, colour, HTML/CSS or Markdown; the machine-id pattern rejects
all of those. Unknown option keys stay rejected; v1/v2 options without them stay
valid. The nested OpenAPI serializer mirrors the two fields and the committed
schema + generated TypeScript were regenerated deterministically.

### Rights boundary: explanatory visuals vs inspiration assets

Questionnaire explanatory visuals are a **frontend-owned**, source-controlled
manifest (`apps/web/src/features/questionnaire/visuals/`) of project-owned
assets: colour swatches render from project-authored hex values (a hex value is
not third-party imagery) and necklines from original schematic SVGs generated
deterministically with content-hash integrity. These visuals only help a user
understand an option — they are **never** sent to any AI provider and **never**
influence DesignSpec generation, and they are strictly separate from the
rights-controlled inspiration catalogue (which is never reused here). A missing
or unknown visual falls back to plain text. This is a deliberately different,
narrower category from inspiration images (ADR 0006/0014) and does not touch the
private storage, rights-verification or provider-facing paths.

### DesignSpec schema version 2, with historical v1 support

A dedicated neckline changes `source_selections`, so v1 is never mutated in
place. `DesignSpecV2` is a minimal subclass of the v1 model overriding only
`schema_version: Literal[2]` and `source_selections: SourceSelectionsV2` (which
adds `neckline_style: MachineValue | None`); Pydantic preserves the base field
order so v1's committed JSON Schema stays byte-identical, and both inherited
validators still apply. A small explicit registry (`_DESIGN_SPEC_MODELS`) plus
`validate_design_spec` / `design_spec_model_for_version` provide total,
fail-safe version dispatch for the two known versions — never a generic schema
framework. `design_spec_v2.json` is committed and regenerated by the same
management command; validation dispatches on the persisted `schema_version`, and
the actual produced version is persisted (never a module default). Which version
a design targets is decided by questionnaire capability (a `neckline_style`
question ⇒ v2), computed once in the generation context.

### Dedicated neckline semantics

The old multi-select `high_neckline` coverage value is migrated out of
`coverage_preferences`; the authoritative neckline decision is the optional,
single-choice `neckline_style` question. Historical answers carrying
`coverage_preferences=["high_neckline"]` remain valid against their own v1/v2
schema and generate correctly. The prompt builder (`PROMPT_BUILDER_VERSION`
bumped 5.0.0 → 6.0.0) renders the canonical neckline early in the high-priority
coverage directive and restates it in the closing reinforcement, and suppresses
the model-authored neckline narrative when a canonical neckline is chosen so it
can never contradict it. Because `source_selections` is an immutable refinement
root, `neckline_style` is automatically protected across the single-round
refinement (ADR 0015); the refined output's schema version must match the
source's.

### No preference = null/absence

For an optional single-choice question, "No preference" is represented by the
**absence** of the answer key — never a persisted `"no_preference"` option and
never an empty string. The reversible control clears the answer to `""`, which
the wizard's stale-answer clean-up drops (and the derived Zod treats an optional
empty single-choice as valid), so the persisted `answers` object simply omits
the key. The review screen shows "No preference" for a visible optional
single-choice with no answer rather than silently omitting the question. Required
questions never expose the control. Server answer-validation stays authoritative.

### Anand Karaj cultural handling

Anand Karaj is added as a distinct ceremony value, never labelled merely "Sikh
wedding" and never silently mapped to Nikah, Pheras, Baraat, Walima or a generic
reception. The trusted structured-generation system prompt gains guidance to
treat it as the Sikh marriage ceremony without conflating it with other
religious rites (`SPEC_TEMPLATE_VERSION` bumped 2.1.0 → 2.2.0, fingerprint
updated). The generated DesignSpec preserves `ceremony == "anand_karaj"` exactly.
Automated tests assert distinctness, but they do **not** replace human review: a
manual cultural-review checkpoint is recorded, and the production demo pack must
contain an approved, culturally-reviewed Anand Karaj-compatible asset before v3
is activated in production demo mode (enforced fail-closed; see below).

### Coverage / neckline / dupatta consistency

Consistency is expressed with the existing declarative `restrict_options` rule
engine where it can carry the behaviour safely — no general expression language.
v3 adds two rules: a covered-head preference (`head_drape_preferred`) restricts
`dupatta_style` to head-compatible drapes (`head_drape`, `double_dupatta`), and
`full_midriff` restricts `neckline_style` to exclude the plunging `deep_v_neck`.
Server answer-validation applies these as authoritative allow-sets; a bypassed
invalid submission is rejected with a field-safe error. Frontend restriction
alone is never treated as sufficient. The demo selector additionally enforces
fail-closed coverage constraints (below).

### Colour grouping

The curated colours expand into a bounded, source-controlled `group` per option
(neutrals, reds, pinks, yellows/metallics, greens, blues/teals, purples) within
the schema's option limit, keeping stable lower-case machine ids and the
existing maximum of four ordered lead colours. Swatches are curated (never an
unrestricted native colour picker), rendered from the frontend visual manifest's
project-owned hex values; hex is never a canonical answer and never sent to a
provider. Prompt and result text use human-readable colour names derived from
the canonical machine values.

### Demo fail-closed requirements

The demo manifest schema is bumped to version 2 (adds a per-asset `necklines`
tag and the expanded colour/fabric/ceremony vocabulary); the selector is bumped
to 2.0.0 with a neckline scoring dimension and three fail-closed hard
constraints: an Anand Karaj design requires an asset explicitly tagged for it
(never a nearest-neighbour ceremony), a covered-head selection never matches an
uncovered-head asset, and a full-midriff selection never matches an
exposed-midriff asset. The demo spec engine is bumped to 2.0.0 (produces v2 with
the canonical neckline, corrects the head-covering and midriff narrative to
derive from machine values). The pack-wide coverage validator requires every
ceremony — including anand_karaj — to be represented, so a production pack
without an approved Anand Karaj asset fails closed. The development synthetic
pack gains one Anand Karaj asset and is never production-eligible. Necklines are
a soft-scored optional dimension with no pack-wide coverage requirement,
documented in the validator.

### Prompt-builder version bump

`PROMPT_BUILDER_VERSION` 5.0.0 → 6.0.0 (canonical inputs and visual requirements
changed). Version-1 specs render byte-identically; golden snapshots were
regenerated and manually reviewed (v1 snapshots unchanged, two new v2 fixtures
added). Persisted prompts and builder versions remain immutable audit data.

## Consequences

- Demo mode remains strictly zero-cost and deterministic; no provider client is
  ever constructed. Live generation stays disabled by default.
- Activating v3 in production demo mode is an **operator** step gated on an
  approved, culturally-reviewed Anand Karaj production asset (see the operator
  checklist below). Until then v1 stays active in production and v3 is a draft.
- Garment/silhouette/dupatta/saree explanatory visuals ship as text fallback for
  now (only colour swatches and neckline schematics are shipped as project-owned
  visuals) — a documented approved-asset gap, since downloading unlicensed
  imagery to reach numerical coverage is prohibited.
- Revisit if: a later phase supplies an approved illustration pack; if a future
  DesignSpec version is added (extend the registry, never rewrite history); or if
  a reviewed definition is supplied for additional Sikh events (Jaggo, Maiyan),
  which this phase deliberately excludes.

### Operator checklist — Anand Karaj production demo activation

Before activating questionnaire v3 in production demo mode:

1. Supply at least one culturally-reviewed, rights-cleared production demo asset
   tagged `ceremonies: ["anand_karaj"]` (with an appropriate garment,
   neckline, coverage and head-drape tagging) — the synthetic development asset
   is never production-eligible.
2. Install the production pack; `assert_production_content_ready` and
   `validate_manifest_coverage` must pass (every ceremony, including
   anand_karaj, covered; no synthetic-placeholder provenance).
3. Record the human cultural-review sign-off for the Anand Karaj asset.
4. Activate v3 via the transactional activation service; confirm v1 retires and
   exactly one active version remains.

### Deferred

Stylist annotation tools (Phase 19) and optional height/body representation
(Phase 20) remain deferred; Phase 20 will reuse this phase's frontend visual
manifest. This phase adds no annotations, body representation, user-uploaded
visuals, remote image URLs, CMS, unrestricted colours, extra Sikh events,
internationalisation, sharing, image-to-image refinement, a new FLUX model,
reference-image conditioning, or extra refinements.

## Alternatives considered

- **Extend questionnaire v2 in place.** Rejected: v2 is treated as fixed history
  by the test suite; editing it would rewrite that history and weaken the
  immutability story. v3 is non-destructive.
- **Mutate DesignSpec v1 to add the neckline field.** Rejected: v1 is committed,
  persisted history; a version-dispatched v2 preserves all historical specs.
- **A generic versioned-schema framework.** Rejected per ADR 0009; a small
  explicit registry for the two known versions is simpler and safer.
- **A persisted `no_preference` option value.** Rejected: null/absence is the
  honest representation and avoids polluting the canonical answer vocabulary.
- **Colour swatches as downloaded/licensed images or a native colour picker.**
  Rejected: project-owned hex values are rights-clean and accessible; an
  unrestricted picker is explicitly out of scope.
- **Soft-scoring Anand Karaj / covered-head / full-midriff in the demo.**
  Rejected: a culturally-distinct ceremony or a mandatory coverage requirement
  must fail closed rather than show a misleading nearest image.
