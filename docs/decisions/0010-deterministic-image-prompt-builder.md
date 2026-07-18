# 0010 — Deterministic image-prompt builder

- **Status:** accepted
- **Date:** 2026-07-18
- **Deciders:** Sitara maintainers
- **Phase:** Phase 9 (see ../phases/PHASES.md)
- **Related:** ADR 0001 (image model), ADR 0009 (structured DesignSpec
  generation), ADR 0004 (private design ownership)

## Context

Phase 9 turns the validated **DesignSpec** produced in Phase 8 into the single
natural-language prompt string that a later phase will send to the
environment-configured FLUX image model (`black-forest-labs/flux-1.1-pro`, ADR
0001). No image generation, Replicate call, Celery task, generation API
endpoint, image storage, results UI, inspiration influence or refinement is in
scope; those remain deferred. Selected inspiration influence stays deferred to
Phase 13.

## Decision

### DesignSpec remains the only model-authored generation contract

The image prompt is produced entirely by deterministic **application code**
(`sitara/generation/prompt_builder.py`), never by a model. The DesignSpec is
still the sole model-authored artefact; the prompt is a pure, reproducible
projection of it. `build_image_prompt(spec)` performs no database access, no
environment reads, no randomness, no timestamps, no network and imports no
provider SDK — identical validated input always yields identical UTF-8 output,
guarded by committed golden snapshots and a combined-hash manifest.

### Editorial text format, following the Phase 2 evaluated path

Phase 2 screened the selected model with an **editorial** (natural-language,
positive-only) prompt. That model exposes neither a genuine negative-prompt
input nor documented JSON prompting, so Phase 9 produces exactly one positive
editorial prompt string: **no separate negative prompt, no JSON prompt, and no
hard-coded Replicate/model identifier** in the builder. The Phase 2 controlled
exclusion list is deliberately NOT appended, because FLUX 1.1 Pro has no
negative-prompt parameter to receive it.

### Positive-only presentation, no universal coverage suffix

The safeguards are expressed positively through fixed presentation language:
full-length studio fashion photograph with the entire garment visible from head
to hem, a clean uncluttered studio background, an original non-branded textile
and embroidery design, natural anatomy and coherent visible hands, and soft even
lighting showing true fabric colour and embroidery detail. There is **no
universal modesty, sleeve or neckline suffix** — coverage comes only from the
DesignSpec, so a generic suffix can never contradict the user's validated
choices. The prompt promises no photorealistic identity, exact constructibility,
preservation between refinements, designer imitation or historical authenticity
beyond the validated concept.

### Fixed prompt ordering

The visual information renders in one stable, snapshot-tested conceptual order:
garment and ceremony; silhouette and components; drape, layering and
proportions; colour palette and placement; fabrics, texture, finish and
movement; embellishment techniques, density, placement and motifs; coverage,
neckline, sleeves, back, midriff and head covering; dupatta or saree drape;
broad cultural direction and styling cues; and finally the fixed presentation
instructions.

### Garment-integrity cues

A very small, source-controlled set of integrity cues is added only for the
categories with meaningful confusion risk in Phase 2, keyed solely on
`source_selections.garment_type`: gharara (fitted through the upper leg and
knee, flare beginning below the knee), sharara (trousers flaring from the waist
or upper leg, without a gharara knee joint), and saree (visibly draped fabric
with a pallu over a blouse, not converted into a stitched gown). This is not a
broad cultural rules engine and does not duplicate the questionnaire taxonomy.

### Bounded narrative slots and a guaranteed global bound

Every DesignSpec narrative string enters the prompt only through named, bounded
slots. Each slot applies Unicode NFKC normalisation, converts CRLF/CR to LF,
collapses internal whitespace, strips ends, truncates at a word boundary to a
documented per-slot cap, and never inserts HTML, Markdown or control characters.

Per-slot caps alone cannot guarantee `IMAGE_PROMPT_MAX_CHARS` because the schema
permits several eight-item narrative lists and eight fabric entries. Rendering
therefore **reserves space for the mandatory content first** — garment and
ceremony, the canonical silhouette, the garment-integrity cue, the canonical
colour/fabric/embellishment selections, all canonical coverage preferences, the
canonical dupatta/saree drape and the fixed presentation wording — and lets
generated narrative consume only the remaining budget, shared across sections in
fixed order proportionally to each section's natural size. When a section's
narrative exceeds its budget, lower-priority generated details are
deterministically shortened at a word boundary or omitted; canonical selections,
coverage, garment-integrity and presentation content are never removed. As a
result **every DesignSpec valid under the Pydantic schema builds to at most
`IMAGE_PROMPT_MAX_CHARS`** (proved by near-maximum fixtures of several shapes),
and the fully assembled prompt is never sliced.

The generated-content safety scan runs before interpolation and again on the
finished prompt (blocked designer/brand, imitation phrasing, URLs, prompt
leakage, untrusted-section delimiters and control characters). Both scans, the
defensive revalidation and any length overrun surface as a single controlled
`ImagePromptBuildError` — `build_image_prompt` never raises
`GeneratedContentRejected` and never echoes the rejected text.

### Canonical selections are authoritative

Generated narrative may not contradict an explicit machine selection. When
`embellishment_styles` is exactly `["none"]`, the builder renders a clear
unembellished direction and omits the generated techniques, density, placement
and motifs; when `embellishment_density` is `"minimal"`, a small deterministic
word-boundary rule neutralises heavy/dense/opulent/lavish/richly-worked wording
in the generated embellishment narrative. The canonical ordered selections
always remain present, and a non-`none` selection is never silently transformed
into `none`.

### What the prompt never contains

The DesignSpec's `construction_caveats` and `image_alt_text` are not rendered.
No provider metadata, token usage, database identifier, questionnaire label or
schema, inspiration metadata or image reference, raw questionnaire free text,
Anthropic prompt or system instruction can appear — the DesignSpec contract
carries none of those into the builder, and the builder invents none.

### Prompt persistence for reproducibility and audit

`DesignVersion` gains `image_prompt` (TextField) and `prompt_builder_version`
(CharField). Database constraints enforce that the two are all-or-none and that
an image prompt can exist only when a DesignSpec exists; existing Phase 8 rows
(spec only, no prompt) and legacy rows (no spec) remain valid. Both fields are
read-only in Django admin.

`build_and_store_image_prompt(design_version)` persists the prompt atomically:
it locks the DesignVersion row, requires a persisted DesignSpec of the supported
schema version, revalidates the stored JSON, builds the deterministic prompt and
stores it with `PROMPT_BUILDER_VERSION`. The first build populates the fields; a
rerun with the same builder version and identical prompt is idempotent; an
existing different prompt or builder version is never overwritten (a future
builder must create a NEW DesignVersion rather than rewrite historical audit
data). An offline `build_image_prompt` management command builds a version's
prompt with zero provider calls and prints only safe provenance (UUID, builder
version, character count; the prompt itself only with `--show-prompt`).

### Enforced snapshot/version guard

Golden snapshots are regenerated only through the
`regenerate_image_prompt_snapshots` management command, which reads the committed
manifest first and **refuses** to overwrite it when the rendered combined hash
changed while `PROMPT_BUILDER_VERSION` did not — a deliberate version bump is
required. After a bump it rewrites the snapshots and manifest; an unchanged hash
is a no-op. Normal tests run comparison-only and never write files, so silent
wording drift cannot slip past review. `PROMPT_BUILDER_VERSION` is `2.0.0` from
this hardening (bounded rendering and canonical-selection authority changed the
`none`-embellishment output).

## Consequences

- The image prompt is fully reproducible and auditable, decoupled from any paid
  model, and snapshot-guarded against silent wording drift.
- Adding capabilities (negative prompt, JSON prompting) or changing the wording
  requires a deliberate `PROMPT_BUILDER_VERSION` bump and snapshot/manifest
  review, not an incidental edit.
- Inspiration influence and image generation remain out of scope; no provider
  call, no Replicate identifier, no seed and no reference-image field are
  introduced. The Phase 8 paid live checkpoint remains pending.
