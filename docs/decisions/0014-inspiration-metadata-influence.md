# 0014 — Rights-safe inspiration metadata influence

- **Status:** accepted
- **Date:** 2026-07-20
- **Deciders:** Sitara maintainers
- **Phase:** Phase 13 (see ../phases/PHASES.md)
- **Related:** ADR 0001 (image model), ADR 0006 (rights-controlled inspiration
  catalogue), ADR 0009 (structured DesignSpec generation), ADR 0010
  (deterministic image-prompt builder), ADR 0013 (generation progress and
  results)

## Context

Phase 5B's catalogue lets a user select up to three rights-approved
inspiration images, but Phase 8's `GenerationContext` deliberately sent none
of that selection to Anthropic — deferred to this phase. Phase 2's model
evaluation never ran a comparison between metadata-only and direct
image-conditioning influence, so no evidence exists that either mechanism
produces better results; this phase does not make that claim either way.
Direct image conditioning also carries unresolved rights, pricing, and
provider-terms questions that a scoped Phase 2-style evaluation would need to
settle before any image bytes could be sent to a provider. Given that, Phase
13 implements the smallest mechanism that lets a selected inspiration
meaningfully influence a concept without ever sending image bytes, a URL, or
a storage key anywhere, and without weakening any existing rights, privacy,
or cost-control invariant.

## Decision

### Metadata-only influence, not reference-image conditioning

Selected inspiration image **bytes are never sent to Anthropic or Replicate**.
The only provider-facing signal is a curated cue built from catalogue fields
that are already frozen once an asset is approved: `garment_type`, `alt_text`
(exposed to the provider as `visual_description`) and `cultural_context`. No
new catalogue metadata field is introduced — Phase 13 reuses exactly what
Phase 5B already collects and freezes. `ReferenceImagesNotEnabled` (Phase 10)
remains a fail-closed guard with no implementation to enable: generation
always constructs an empty `reference_image_urls` tuple, and a non-empty
tuple is rejected at `ImageGenerationRequest` construction — before any
provider client exists — regardless of live-gate state.

### A versioned, hashed snapshot is the single source of truth

`apps/api/sitara/generation/inspiration_context.py` defines strict Pydantic
v2 models (`extra="forbid"`) for one `InspirationContextSnapshot`:
`schema_version` (fixed at `INSPIRATION_CONTEXT_SCHEMA_VERSION = 1`) and up to
three `InspirationContextItem`s, each carrying an audit-only `asset_id` and
`position`, a `provider_cues` object (`garment_type` machine-id-or-null,
`visual_description`, `cultural_context`-or-null) and an `acknowledgement`
object (`title`, `attribution`) that is **never** sent to a provider.
`provider_inspiration_cues()` and `inspiration_acknowledgements()` derive two
disjoint, minimal projections from one snapshot — the only two shapes any
downstream code may ever see. Canonical text normalisation (Unicode NFKC,
CRLF/CR folded to LF, whitespace collapsed) feeds a deterministic, sorted-key,
compact-separator JSON representation whose SHA-256 is the snapshot's audit
hash. `DesignVersion` gains `inspiration_context` (nullable JSON),
`inspiration_context_schema_version` (nullable, DB-pinned to `1` when
present) and `inspiration_context_sha256` (a 64-lowercase-hex CharField, DB
shape-checked) with an all-or-none constraint and a "requires a DesignSpec"
constraint mirroring the existing `design_spec`/`image_prompt` provenance
pattern. A Phase-13-generated `DesignVersion` always records a snapshot, even
an empty one when no inspiration was selected — the absence of the three
fields is reserved exclusively for legacy, pre-Phase-13 rows.

**Phase 14 note (ADR 0015):** a refined `DesignVersion`'s
`inspiration_context`/`_schema_version`/`_sha256` are copied byte-for-byte
from its parent version at persistence time, never rebuilt from a fresh live
catalogue query. Refinement never re-selects inspirations and never
re-validates them against `publicly_eligible()` — it inherits exactly the
influence (or lack of it) its source version already had, consistent with
this section's "immutable audit data" invariant: a later asset retirement or
rights change still cannot alter what an already-generated version's
acknowledgement displays, whether that version is an initial generation or a
refinement of one.

### Every selected inspiration is re-validated, twice, before it can influence a design

Before any provider is selected, `build_inspiration_context_snapshot`
re-confirms every selected asset is still returned by
`InspirationAsset.objects.publicly_eligible()` and runs the existing
generated-content safety scan (`scan_generated_text`, now living in a new
dependency-free `sitara/content_safety.py` leaf module so the catalogue app's
own approval-time defence can reuse it without reversing the
catalogue-below-generation dependency direction) over `alt_text` and any
non-empty `cultural_context`. Rejection surfaces as one of two generic,
never-echoing exceptions — `InspirationAssetIneligible` /
`InspirationMetadataUnavailable` — translated by `build_generation_context`
into `DesignNotReady` (`inspiration_unavailable` /
`inspiration_metadata_unavailable`) before any provider client is
constructed. `approve_inspiration_asset` independently runs the same safety
scan at approval time as defence in depth; this never mutates or backfills
already-approved assets, so a legacy unsafe asset is still caught by the
selection-time recheck.

### Rights, selection and metadata are locked and re-verified again, right before persistence

The generation input fingerprint (`_input_snapshot`) now also carries the
exact `InspirationContextSnapshot` object and its hash, bundled with the
existing questionnaire/answers/inspiration-id tuple into one `_InputSnapshot`
dataclass. Inside the same short, already-existing `_finalise_atomic`
transaction that locks the `Design` row after the provider call returns,
three more row groups are now locked in one documented order:
`DesignInspiration` by position, `InspirationAsset` by UUID, `UsageRights`
by UUID. The snapshot is then rebuilt fresh under those locks and compared
by exact content and hash against the pre-provider snapshot. Any mismatch (a
selection change, asset retirement, rights revocation or expiry, or a
metadata/attribution mutation) raises the existing
`DesignChangedDuringGeneration`, persists nothing, and **never retries the
provider** — closing a race the plain eligibility recheck
alone could not catch, since a title or attribution edit does not change
eligibility. No lock is ever held across the provider network call itself.

### Curated cues reach the trusted JSON; the system prompt keeps selections authoritative

`build_user_message`'s trusted JSON gains a `curated_inspiration_cues` array
(position/garment_type/visual_description/cultural_context only — never
inside the untrusted free-text delimiters, since these are staff-curated,
validated data, not user free text). The system prompt gains explicit rules:
questionnaire selections are always authoritative; a cue is only used when
compatible with the selected garment, ceremony, colours, fabrics,
embellishment level, coverage and drape; a conflicting cue is ignored outright
(never changes the garment type, never weakens a coverage preference, never
increases embellishment beyond the selection); no regional or religious claim
may be invented from a cue; any compatible influence must be expressed in
abstract design vocabulary, never by copying a person, pose, background,
composition, logo, text or signature motif; and the output must never mention
an inspiration's title, id or attribution. Because this changes both the
trusted system prompt and the trusted JSON shape, `SPEC_TEMPLATE_VERSION`
deliberately bumps `1.0.0` → `2.0.0`, with the deterministic
`prompt_template_fingerprint()` material list extended and its recorded
`PROMPT_TEMPLATE_HASH` recomputed — proved by tests that the pre-Phase-13
fingerprint no longer matches, the new one matches exactly, and an
undocumented future prompt edit breaks the guard. `DESIGN_SPEC_SCHEMA_VERSION`
stays `1` (no DesignSpec field was added or removed) and
`PROMPT_BUILDER_VERSION` stays `3.0.0` — metadata reaches FLUX only
indirectly, through the unchanged deterministic image-prompt builder, and
prompt snapshots remain byte-identical.

### A generated concept must never surface what it was never given

A new post-output check, `_assert_no_inspiration_leakage`, rejects any
generated output whose flattened text contains a selected inspiration's title
or attribution — data the model was never sent, so its appearance means it
was guessed or fabricated. Matching reuses the codebase's existing
token-boundary `contains_phrase` (never a raw substring test) and only
considers a title/attribution of at least two words as a candidate match, so
a short, ordinary catalogue word (nothing requires a title to be distinctive)
can never spuriously collide with unrelated generated prose. A match raises
the existing `GeneratedContentRejected` (a new `RejectionCategory
.INSPIRATION_LEAKAGE`), which the existing retry loop already handles
identically to any other content-safety rejection.

### Acknowledgements are audit-only, historical, and never reconstructed live

The result API's curated payload additively gains
`inspiration_acknowledgements` (position/title/attribution), built **only**
from the persisted `DesignVersion.inspiration_context` snapshot — revalidated
through the strict Pydantic model and hash-verified before use, never by
re-querying the live catalogue. A legacy pre-Phase-13 version returns an empty
list; this is never a result-readiness requirement. Corrupt or
hash-mismatched persisted content is a controlled `503
design_result_unavailable`, never a raw exception, and the offending content
is never logged or exposed. Because the snapshot is immutable audit data, a
generated design keeps displaying its historical acknowledgement even after
the source asset is later retired, expires, or is otherwise made ineligible —
future generation is blocked by current eligibility, but an existing private
result remains reproducible and its attribution intact. Whether a later
rights revocation should ever require retroactively deleting or redacting an
already-generated design is an unresolved policy question left for Phase
16/operator review; this phase makes no such change.

### Frontend disclosure is honest and non-technical

The questionnaire inspiration picker and review-summary pages state plainly
that selection is optional, that Sitara uses each image's staff-written
description as a secondary visual cue, that questionnaire answers stay
authoritative, that the image files themselves are not sent to the AI models
in this version, and that the result will not be an exact copy — without
naming a provider or implying reference-image conditioning. The results page
renders an "Inspiration acknowledgements" section only when the array is
non-empty, using the same plain-React-escaping, no-`dangerouslySetInnerHTML`,
no-link discipline as the rest of the results page; the copy/download brief
formatter includes the same acknowledgements and limitation note. No asset
UUID, provider cue, or catalogue image is ever read by the frontend for this
feature.

## Consequences

- Phase 16 (live-generation rate limits and cost ceilings) inherits this
  phase's provider-facing cue shape unchanged; nothing here needs to change
  when `LIVE_GENERATION_ENABLED` is eventually turned on.
- A future direct-image-conditioning phase would need its own scoped model
  evaluation (current pricing/terms/capability verification, rights review,
  prompt and provider-contract review) and a separately approved phase — this
  ADR records the MVP boundary, not a permanent architectural ceiling.
- The unresolved policy question of whether a post-generation rights
  revocation should ever force deletion or redaction of an already-generated
  design's historical acknowledgement remains open for Phase 16/operator
  review.
- `apps/api/sitara/generation/input_safety.py` is now a thin re-export shim
  over the new `sitara/content_safety.py` leaf module; every existing
  `from .input_safety import ...` call site keeps working unchanged, but any
  future generation-only safety helper should be added to
  `content_safety.py` directly if catalogue (or another lower-layer app) is
  ever expected to need it too.

## Alternatives considered

- **Direct reference-image conditioning** — rejected for this phase: Phase 2
  never ran the comparison, the Replicate wrapper's reference-image path
  stays deliberately unimplemented (`ReferenceImagesNotEnabled` remains
  fail-closed with nothing to enable), and sending image bytes to a provider
  raises rights, pricing, and provider-terms questions a scoped, separately
  authorised evaluation would need to answer first.
- **A generic tags/ontology or arbitrary-metadata engine** — rejected: the
  phase reuses exactly the three already-frozen, already-curated catalogue
  fields; no new catalogue metadata field, free-form tag system, or automated
  cultural classification was introduced.
- **Sending titles or attribution to the provider as extra context** —
  rejected: titles and attribution are catalogue presentation text, not
  curated design description, and sending them would risk the model echoing
  a source's identifying text into generated output; they remain audit-only
  and are actively checked for leakage in generated output instead.
- **Reusing `generation/input_safety.py` directly from `catalogue/services.py`**
  — implemented first, then replaced: this would have created a
  catalogue-below-generation-below-catalogue import cycle at the package
  level (only avoided today by `input_safety.py` happening to have no
  `sitara` imports of its own). The safety-scan primitives were extracted
  into a new, genuinely dependency-free `sitara/content_safety.py` leaf
  module instead, with `generation/input_safety.py` reduced to a re-export
  shim, restoring a strictly acyclic dependency graph.
- **A single generic "content changed" check instead of exact snapshot
  content-and-hash comparison** — rejected: the phase explicitly requires
  catching a metadata or attribution mutation that leaves eligibility
  unchanged, which only an exact content comparison (not an eligibility-only
  recheck) can detect.
