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

### Authorised scope change: user-uploaded inspiration images

**Added 2026-07-29.** The original 16B scope (and the "Deferred" list below) ruled
out user-supplied imagery entirely. The maintainer's design handoff explicitly
reopened it: a user must be able to attach up to three of **their own** reference
photographs to a design, and — per the same handoff decision recorded in the phase
state file — those bytes will be sent to the image provider once
`ReferenceImagesNotEnabled` is lifted onto `flux-2-max`. That provider-facing half
is a separate slice; this section records the scope change and the storage-side
design that lands first, so code and decision record do not contradict each other
in the interval. *(That slice has since landed — see
[ADR 0019](0019-reference-image-conditioning-and-flux-2-max.md).)*

**Why it is in 16B rather than a new phase.** The upload shares one budget with
curated selections (`MAX_INSPIRATION_IMAGES`), one wizard step, and one
result-page acknowledgement surface. Splitting the two halves of a single
three-slot reference control across phases would leave a half-wired UI in `main`.

**What is introduced.** `DesignInspirationUpload` — a private, per-design row
(`position` 1..3, server-generated `storage_key`, decoded dimensions,
`image_sha256` for per-design deduplication, `rights_acknowledged_at`) with
database constraints for unique position, unique image and position bounds. The
service writes the sanitised object first and the row second, deletes the object
before the row, re-checks the shared cap under `select_for_update()` on the owning
design, and reuses freed positions rather than incrementing monotonically.
Uploaded bytes are decoded by Pillow only, gated on declared size, header pixels
and a wire-level `Content-Length` pre-check, stripped of EXIF/GPS/XMP/ICC, and
re-encoded as one clean WebP; the original is never stored. Endpoints are
anonymous-session-owned, CSRF-protected, throttled per session and per hashed IP,
and return the same indistinguishable 404 for foreign and nonexistent designs. No
storage key, hash or byte size is ever serialised into a response.

**Lifecycle: two cleanup paths, because one is structurally blind.** Writing the
object before the row is the right order — a row pointing at a missing object
would be the worse failure — but it opens a window in which a crash leaves an
object with nothing naming it. The retention purge closes only half of this: it
enumerates upload keys FROM the rows before the `Design` cascade removes them, so
an uploaded image lives at most `DESIGN_RETENTION_DAYS`, but it can never see an
object no row names. A second periodic task,
`maintenance.sweep_orphaned_upload_objects`, is therefore the only thing that
removes one — which makes *reaching* every object as much a requirement as
deleting safely.

Safety: it walks `design-uploads/` alone; considers only objects older than
`USER_UPLOAD_ORPHAN_GRACE_SECONDS`, long enough that an object whose row is
still committing is never a candidate; re-checks row existence per key
immediately before deleting; and before each delete normalises the key and
re-checks containment. Normalisation is the point — a plain `startswith` accepts
`design-uploads/../catalogue/x.webp`, which resolves onto a rights-controlled
catalogue object.

Reachability: a bounded run that always restarted at the same place would leave
the tail of the keyspace permanently unswept, and a private user photograph
would then outlive retention with nothing reporting it. Two things prevent that.
The run scans its directories first, then asks in **one query scoped to those
keys** which of them rows name; a key that is named is skipped for free, so
healthy uploads never consume the orphan budget. Each directory contributes at
most `MAX_INSPIRATION_IMAGES + 8` keys per run and those slices are examined
**round-robin**, so neither a directory full of live uploads nor one that has
piled up crash-window orphans can spend the whole run while a neighbour's lone
orphan survives every pass. (Loading *every* upload key instead would have been
simpler and was rejected: it grows with the platform on every tick regardless of
how little the run then does.)
And the per-design directories are visited from a rotating offset derived from
the clock, advancing a whole window per scheduled run, so while the directory
count is stable the whole prefix is covered in exactly
`ceil(directories / USER_UPLOAD_SWEEP_BATCH_SIZE)` runs — for every combination,
not only where they divide evenly. Under churn that becomes a close
approximation rather than a proof; what churn cannot do is systematically
exclude a directory, which is the property that matters. The rotation is
stateless — nothing to persist, corrupt or reset.

A truncated run reports `truncated: true` rather than reading like a complete
one. One `listdir` of the top-level prefix per run is unavoidable through the
Storage API and is *not* bounded by the batch size; that is documented on the
function rather than glossed. Counts only reach the log; a storage key never
does.

**The upload UI: consent has to come before the file picker.** ADR 0019 accepted
a real, unresolved exposure — a perpetual, irrevocable train-and-improve licence
over inputs, unresolved coverage of Replicate-routed traffic, no published
retention window — and that decision only becomes legitimate at the moment a
user is told about it. So the disclosure is rendered above the affirmation, the
affirmation checkbox is `aria-describedby` the disclosure, and the file input is
disabled until the affirmation is given. The handler re-checks the affirmation
rather than trusting the `disabled` attribute: a consent gate should not rest on
a DOM property. The affirmation's wording names it as the user's own claim
("I have the right to use these images"), never as verified rights, matching the
model boundary below.

Two pieces of existing copy asserted the *opposite* of what now happens — the
picker said image files "are not sent to the AI models in this version" and the
review summary said images are used "through staff-written descriptions, not
direct image matching". Both are corrected, and the picker's test now asserts
the retired claim cannot return in any wording. Saying the opposite of what the
system does is worse than saying nothing.

Uploads and curated presets share one budget because the cap is on *references*,
not on their provenance; the picker owns that arithmetic and hands the upload
control only the remaining count. Multipart cannot use the JSON wrapper (the
browser must set its own boundary), so the upload wrappers re-prove the same
policy independently — same-origin, no-store, in-memory CSRF token, exactly one
retry on a stale token — with the one deliberate difference that a
15 MB body gets a 60s budget instead of the 5s JSON one.

**Relationship to ADR 0006 — deliberately NOT the staff rights model.** A user
upload is *private user content*, not catalogue content. It therefore has no
`RightsRecord`, no verifier identity, no expiry, no approval workflow, no
`publicly_eligible()` participation and no public-display, commercial-use or
derivative-generation flags; it is never listed, never shown to another session,
and can never be promoted into the catalogue. The rights position is a single
per-upload user affirmation (`rights_acknowledged_at`, refused if absent) — a
weaker, honestly-scoped claim that must never be presented, in code or in UI, as
verified rights. The sanitisation pipeline is duplicated rather than imported from
`catalogue/image_processing.py` for exactly that reason (shared primitives stay in
`sitara.image_sanitize`); ADR 0006's staff-only, rights-verified model is
untouched, and none of its prohibitions (user uploads *into the catalogue*, remote
URL imports, scraping, automatic rights verification, public ACLs) are relaxed.

**What ADR 0014 must say once these bytes reach a provider.** ADR 0014's current
absolute — no inspiration image bytes, URLs or storage keys ever reach an AI
provider, `ReferenceImagesNotEnabled` fail-closed — will be *deliberately
overridden*, not quietly reinterpreted. The amending record must state: that the
override is a maintainer decision taken with the provider terms in view (BFL takes
a perpetual, irrevocable licence over Inputs to train and improve its
technologies; coverage of Replicate-routed traffic is unresolved; Replicate
publishes no input retention window); that it covers both user uploads and curated
catalogue presets; that signed reference URLs are short-TTL and minted only inside
the Celery job, never persisted, logged or returned; that the frozen
`InspirationContextSnapshot` audit trail is unchanged; and that demo mode remains
strictly zero-cost with no reference upload path. Until that record exists,
`ReferenceImagesNotEnabled` stays in force — nothing in this slice sends an
uploaded byte anywhere.

> **Superseded 2026-07-29 — that record now exists: [ADR 0019](0019-reference-image-conditioning-and-flux-2-max.md).**
> It says everything the paragraph above required, and the override is
> implemented. `ReferenceImagesNotEnabled` no longer exists anywhere in the
> codebase: `ImageGenerationRequest` now raises `ReferenceImagesRejected`, a
> bounds check (count ceiling, https only, length ceiling), not a blanket
> refusal. The bytes of the references a user selected — uploads and curated
> presets alike — are sent to the image provider as short-TTL signed URLs minted
> inside the Celery job by `generation/reference_images.py`. Demo mode still
> signs nothing. Read that ADR and those two modules, not this paragraph, for
> what actually happens today.

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

### DesignSpec schema version 3, with historical v1/v2 support

Questionnaire v4 replaces `colour_palette` with a colour per garment role
(`fabric_colour` / `embroidery_colour` / `dupatta_colour`, each either a
canonical option value or a bride-supplied six-digit lower-case hex, plus the
bounded `custom_colours` palette) and replaces `coverage_preferences` with one
question per body area (`sleeves` / `back_coverage` / `midriff` /
`head_covering`). That is a different `source_selections` contract, so it gets
its own version rather than mutating v1 or v2.

`SourceSelectionsV3` is deliberately **not** a subclass of `SourceSelections`:
version 3 does not carry `colour_palette` or `coverage_preferences` at all, and
`extra="forbid"` must reject them so a v1/v2 payload can never be mis-persisted
as v3. `DesignSpecV3` overrides only `schema_version: Literal[3]` and
`source_selections`; the narrative structure is unchanged, because
`coverage_and_drape` already has a slot per body area and `colour_story` already
describes placement. `_DESIGN_SPEC_MODELS` gains `3` — still a small explicit
registry for known versions, never a generic schema framework — and
`design_spec_v3.json` is committed by the same management command, with v1's and
v2's files byte-identical. Which version a design targets stays a questionnaire
capability check computed once in the generation context: an explicit ladder
where v3 requires the **whole** v4 replacement question set, so a
partially-migrated schema falls back to v2 and fails the contract check loudly
rather than producing half a version-3 brief.

Every replacement field is optional in the questionnaire, so every one is
nullable in the contract — "no preference" stays a first-class answer here too.

### `selection_semantics.py` — one adapter, not per-consumer version branches

Three consumers — the deterministic image-prompt builder, the demo DesignSpec
engine and the demo asset selector — need the same few questions answered for
every version: which colours were chosen and in what order, and must the head or
the midriff be covered. `sitara/generation/selection_semantics.py` is the single
sanctioned place that answers them. It is a short list of named accessors over
KNOWN versions (never a generic mapping framework), pure, and total over both a
validated model instance and the equivalent plain mapping, because the demo
engine builds its spec from the context's `source_selections` dict before any
model exists. A future DesignSpec version extends this module rather than adding
a version branch inside each consumer.

`explicit_head_covering_decision()` is deliberately **tri-state**. Version 3 asks
the head-covering question directly, so its answer is authoritative in both
directions — an explicit `uncovered` beats any inference from the dupatta
styling, exactly as `neckline_style` beats the retired `high_neckline`. Version
1/2's multi-select can only express the positive case, so its absence returns
`None` and each caller's own dupatta inference still applies, which is what keeps
historical designs rendering and selecting unchanged. The prompt builder and the
demo selector share that one rule, so a selected demo asset can never ask for the
opposite of what the prompt asks for.

### Demo engine and manifest under version 3

`DEMO_SPEC_TEMPLATE_VERSION` 2.0.0 → 3.0.0 (it now produces v3 and renders each
body area's own answer). `demo/phrases.py` gains v4's silhouettes, colours,
drapes and the four per-area coverage maps **additively** — an older design must
keep rendering its own vocabulary, so values are never removed. The demo
manifest's tagging vocabulary is likewise widened (v4 silhouettes, colours,
`trail_dupatta`, `lehenga_drape`) with **no** manifest schema-version bump:
widening an allowlist changes no persisted structure and every committed manifest
stays valid. `manifest.coverage_tags_for_selections()` projects v4's body-area
answers onto the manifest's own pre-existing coverage tags for asset matching
only — deliberately partial and slightly lossy (a cap sleeve takes the nearest
short-sleeve tag; an open back, a bare or semi-sheer midriff and an uncovered
head project to nothing rather than to a tag meaning something else). It is never
user-facing wording and never feeds a DesignSpec.

`DEMO_SELECTOR_VERSION` is deliberately **not** bumped: the selector now reads
colour and coverage through the adapter, but for a version-1/2 spec those
accessors return exactly the fields they replaced, so every score, tie-break and
selection for an existing design is unchanged. Bumping would silently re-seed
historical demo selections to no purpose.

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
the key. Required questions never expose the control. Server answer-validation
stays authoritative.

**Amended 2026-07-29 (wizard restructure, below):** the review screen originally
showed "No preference" for a visible optional single-choice with no answer, and
omitted every other unanswered question. It now gives EVERY visible question a
row, and an unanswered one — of any type, optional or not — reads "Left to
Sitara's imagination", the handoff's own phrasing. The persistence rule above is
unchanged; only what the review screen says about an absent answer changed. A
silently missing row reads as "I never asked", which is the opposite of what that
screen is for.

### Wizard navigation: one question per screen, steps become categories

**Added 2026-07-29.** The handoff asks a bride one thing at a time: 16
single-question screens grouped into 9 progress categories. The wizard until now
rendered one schema STEP per screen. Rather than reshape the schema — steps are
its persistence and grouping unit, and published versions are immutable (ADR
0005) — the mapping is derived in the frontend, in one module
(`apps/web/src/features/questionnaire/screens.ts`):

- each schema step becomes a progress CATEGORY; each of its visible questions
  becomes its own SCREEN. The plan is derived from schema + current answers, so a
  question the rules hide loses its screen immediately and nothing extra is
  persisted — the wizard holds a screen index, answers remain the only state.
- **One grouping exception:** a step's `colour_choice` and `colour_list`
  questions share a single screen, because they are answered against one shared
  swatch grid and splitting them would ask the same question three times with the
  same controls. The rule is keyed on question TYPE, never on an id, so a further
  colour role needs no code change.
- A step whose every question is hidden contributes no screens and therefore no
  category: an unreachable pill would make "Step n of m" a lie. When that happens
  behind the user, the screen index and the furthest-reached marker are clamped
  to the nearest surviving screen rather than jumping to the start.
- Resume is screen-based: a returning user lands on the exact unanswered required
  question, not the top of its category.
- **Continue is disabled until the current screen's required questions have
  values**, with the reason named through `aria-describedby` rather than left to
  be inferred from a greyed-out button. The derived Zod resolver remains the
  second line of defence: a screen can be answered and still invalid (a bounded
  multi-choice below `min_items`), and that path still raises the error summary.
- **Skip** appears only on a screen with nothing required. It advances without
  validating and is deliberately NON-destructive — it never clears an answer.
  Clearing is what the reversible "No preference" control above is for; a Skip
  that discarded a just-made choice would be a data-loss trap.
- The handoff's "locked until the ceremony and garment are answered" rule needs
  no separate state: the furthest-reached marker only advances through Continue,
  and Continue is unavailable while a required question in front of the user is
  unanswered — so nothing beyond the opening category can have been reached until
  they are answered.
- The review screen's per-row Edit links deep-link to a single question
  (`?q=<question id>`), clamped to the furthest screen reached, so an Edit link
  can never skip a user ahead. An unknown or now-hidden question id resolves to
  nothing and the wizard resumes normally rather than guessing.

`ProgressNav` was not changed: the wizard supplies the current screen's category
index and each category's first screen as its jump target.

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

### Prompt-builder version bumps

`PROMPT_BUILDER_VERSION` 5.0.0 → 6.0.0 (canonical inputs and visual requirements
changed). Version-1 specs render byte-identically; golden snapshots were
regenerated and manually reviewed (v1 snapshots unchanged, two new v2 fixtures
added). Persisted prompts and builder versions remain immutable audit data.

`PROMPT_BUILDER_VERSION` 6.0.0 → 7.0.0 for DesignSpec v3: colour is named per
garment role (including a bride-supplied hex, rendered as a literal colour code,
and `match_fabric` rendered as the relationship it is), and each answered body
area becomes an explicit visual requirement whose model-authored narrative is
suppressed so generated prose can never contradict a validated choice. Because
each version-3 body area is a single explicit answer, a deliberately
less-covered one is rendered too — it contradicts nothing, it states what the
user chose — and that directive's heading drops the word "modesty" accordingly;
version 1/2's multi-select keeps its original coverage-increasing-values-only
treatment, because there an absent value meant nothing had been asked for. The
bride's saved `custom_colours` palette is never rendered as a requirement: it is
the set a role may be answered FROM, not a selection. All eight pre-existing
golden snapshots stayed byte-identical through the bump; three new v3 fixtures
and snapshots were added and reviewed.

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
*questionnaire option visuals*, remote image URLs, CMS, unrestricted colours,
extra Sikh events, internationalisation, sharing, image-to-image refinement, or
extra refinements.

**Superseded 2026-07-29 by the authorised scope change above:** user-uploaded
*inspiration images* are now in scope (private per-design references, sanitised
and rights-affirmed), and with them the switch to `flux-2-max` and the lifting of
`ReferenceImagesNotEnabled` for reference-image conditioning. Those three items
were listed as non-goals when this ADR was accepted; they are now deliberate,
maintainer-authorised scope, recorded here and in `../phases/phases-16b.md` so no
reader takes the original list as still binding. Everything else in the list
stands.

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
