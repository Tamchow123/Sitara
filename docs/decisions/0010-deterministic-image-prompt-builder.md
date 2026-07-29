# 0010 — Deterministic image-prompt builder

- **Status:** accepted
- **Date:** 2026-07-18 (amended 2026-07-22 for Phase image-composition:
  composition-first ordering and catalogue framing, `PROMPT_BUILDER_VERSION`
  `4.0.0`; further amended 2026-07-22 for the coverage-first follow-up after live
  evidence, `PROMPT_BUILDER_VERSION` `5.0.0`; further amended 2026-07-23 for
  Phase 16B — the dedicated canonical neckline (DesignSpec v2) is rendered early
  beside coverage and restated at the close, and the model-authored neckline
  narrative is suppressed when a canonical neckline is chosen, `PROMPT_BUILDER_VERSION`
  `6.0.0`, with v1 golden snapshots byte-identical and two new v2 fixtures; see
  ADR 0018)
- **Deciders:** Sitara maintainers
- **Phase:** Phase 9 (see ../phases/PHASES.md); amended by Phase
  image-composition (see ../phases/phase-image-composition.md) and Phase 16B
  (see 0018-questionnaire-feedback-and-visual-choice-ux.md)
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

### Composition-first ordering and catalogue framing (Phase image-composition, `4.0.0`)

The first real paid generation (2026-07-22) proved the pipeline end to end but
produced a cropped, editorial, environmentally-busy portrait rather than the
full-length plain-studio catalogue framing shown in the frozen Phase 2
references (`experiments/model-eval/outputs/runs/screening-20260714-001/`). The
diagnosis was one of prompt *priority*: ~600 words of garment, drape and
embellishment detail preceded the composition instruction, which appeared only
at the very end, so the model treated framing as an afterthought.

The prompt therefore now **leads** with a fixed, garment-agnostic
catalogue-composition directive (`_COMPOSITION`) as its first and
highest-priority section: exactly one adult model, standing upright and
primarily facing the camera, centred in frame; the camera placed far enough back
that the top of the head, both feet, the complete outfit, the garment hem, the
dupatta fall and any train or trailing fabric stay fully in frame with clear
breathing room; a seamless plain neutral studio backdrop; soft, even,
shadow-controlled studio lighting; and the garment's construction, drape, colour
and embellishment (not the face, jewellery or setting) as the primary subject.
The directive is a source-controlled constant so its position and intentional
wording are easy to test and review, and it applies unchanged across sarees,
lehengas, shararas/ghararas, anarkalis and kurta-style outfits.

**Composition is treated as the highest-priority section** because it is the one
instruction the reference evaluation showed the model most readily drops when it
is buried behind detail — and because framing failures (cropping, environmental
scenes, face/jewellery emphasis) waste an entire paid render regardless of how
faithful the garment semantics are. It is a **mandatory** piece rendered first,
so the bounded-rendering budget never truncates or displaces it.

The visual information then renders in one stable, snapshot-tested priority
order: the leading composition directive; garment and ceremony; silhouette and
components; drape, layering and proportions; **coverage, neckline, sleeves,
back, midriff and head covering** (moved ahead of colour/fabric so coverage and
modesty outrank decoration); colour palette and placement; fabrics, texture,
finish and movement; embellishment techniques, density, placement and motifs;
dupatta or saree drape; broad cultural direction and styling cues; and finally a
short photographic-**finishing** directive.

### Positive-only composition and finishing, no universal coverage suffix

The safeguards are still expressed positively and split across the two fixed
directives. `_COMPOSITION` (leading) owns all framing, backdrop and lighting.
`_FINISHING` (trailing, the renamed former presentation block) now carries only
the design-integrity safeguards — an original, non-branded textile and embroidery
design, natural anatomy and coherent naturally-posed hands, and colour-faithful
even lighting — and no longer repeats the framing/backdrop/lighting language, so
the two directives never duplicate each other. There is **no universal modesty,
sleeve or neckline suffix** — coverage comes only from the DesignSpec, so a
generic suffix can never contradict the user's validated choices. The prompt
promises no photorealistic identity, exact constructibility, preservation
between refinements, designer imitation or historical authenticity beyond the
validated concept. As before, an unembellished (`["none"]`) design uses a
finishing variant that asks for plain textile, colour, texture, drape and
garment detail rather than embroidery detail.

### Prompt length and redundancy

The change is deliberately **not** a length increase for its own sake. Framing
is now stated **once** in the leading composition directive instead of being
scattered and repeated again at the end of the prompt, and `concept_summary` — a
model-authored whole-design prose restatement that overlaps every structured
section — has its slot cap tightened from 700 to 400 characters to bound the
single most redundant slot on verbose specs. No supported design field was
removed and no canonical machine selection, coverage, garment-integrity or
composition content is ever shortened. The small reviewed golden fixtures are
already lean (well under the summary cap), so they grow only by the fixed,
high-value composition directive; a genuinely verbose spec is materially bounded
by the tightened cap and the de-duplicated framing. When the global length
budget is under pressure, the documented deterministic priority order shortens
or omits only lower-priority *generated* narrative — composition, canonical
selections, coverage, garment-integrity and finishing survive intact.

This remains a **single deterministic positive natural-language prompt**: the
composition directive is fixed positive prose, there is still no negative prompt,
no JSON prompt, no hard-coded model identifier and no provider call, and
identical validated input still yields byte-identical output guarded by the
golden snapshots and combined-hash manifest.

### Coverage-first rendering and portrait-cue removal (Phase image-composition follow-up, `5.0.0`)

The first true live `4.0.0` generation (2026-07-22) confirmed composition-first
**fixed the framing** — a full-length, head-to-foot, plain-studio catalogue image
— but exposed a second failure: FLUX rendered an **open blouse neckline and a
bare head** even though the DesignSpec explicitly specified a closed high neckline
and a head-covering pallu, and the coverage instructions were present and correct
in the prompt. Two causes: the coverage instructions sat in mid-prompt prose
(FLUX weights the opening most), and the persisted `styling_notes` rendered
advisory beauty text ("jewellery minimal at the neckline… a maang tikka or head
ornament") that actively pulled toward an exposed neckline and a visible head.

`5.0.0` therefore renders **coverage as an explicit, high-priority visual
directive** rather than trusting mid-prompt narrative:

- A concise, garment-neutral **coverage directive is the second section**,
  immediately after composition, built deterministically from the canonical
  `coverage_preferences` (and the head-covering signal). It states the
  coverage-critical selections as concrete visual requirements — e.g. "a fully
  closed high blouse neckline covering the collarbone and upper chest, not an
  open, scooped or sweetheart neckline"; "full-length sleeves… with both arms
  fully covered"; and, when a covered head was requested, "the [pallu/dupatta]
  pulled up and over the head like a veil, completely covering the hair with no
  hair visible". The clause set is a small source-controlled map keyed only on
  canonical machine values (like the garment-integrity cues), **not** a broad
  rules engine.
- It is **strictly conditional**: only coverage-**increasing** selections get a
  clause (sleeveless / short / elbow / three-quarter sleeves get none, so the
  directive never contradicts a validated less-covered choice), and the
  head-covering clause appears only when `head_drape_preferred` is chosen or the
  dupatta is styled `head_drape`.
- It is **garment-neutral**: the head covering references the saree **pallu** for
  a saree, the **dupatta** for a non-saree, and never invents a dupatta for a
  saree with `dupatta_style=None`.
- The critical coverage requirements are **briefly restated last** (a short
  positive reinforcement after the finishing directive), because the live
  evidence shows a single early statement is insufficient. This is still one
  positive natural-language prompt — no negative prompt.
- **Advisory `styling_notes` and non-visual `colour_story.rationale` are no
  longer rendered** into the image prompt. They are beauty/styling and
  explanatory prose that pull toward portraiture and can contradict coverage;
  they remain in the persisted DesignSpec brief, only the image prompt omits them
  (which also trims length).

Coverage correctness in the prompt is now deterministic and snapshot-guarded; the
provider's adherence remains stochastic (a closed high neckline and especially a
fully covered head fight strong FLUX priors), so acceptance requires an
operator-run before/after comparison on a fixed DesignSpec, not wording
inspection alone.

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
therefore **reserves space for the mandatory content first** — the leading
composition directive, garment and ceremony, the canonical silhouette, the
garment-integrity cue, the canonical colour/fabric/embellishment selections, all
canonical coverage preferences, the canonical dupatta/saree drape and the fixed
finishing wording — and lets
generated narrative consume only the remaining budget, shared across sections in
fixed order proportionally to each section's natural size. When a section's
narrative exceeds its budget, lower-priority generated details are
deterministically shortened at a word boundary or omitted; the leading
composition directive, canonical selections, coverage, garment-integrity and
finishing content are never removed. As a
result **every DesignSpec valid under the Pydantic schema builds to at most
`IMAGE_PROMPT_MAX_CHARS`** (proved by near-maximum fixtures of several shapes),
and the fully assembled prompt is never sliced.

Word-boundary truncation is TOTAL: it never emits a partial token. When a
narrative field's first token alone exceeds the available limit there is no safe
boundary, so the whole (non-mandatory) piece is omitted rather than cut
mid-word. Mandatory canonical machine values are bounded by the schema and are
never routed through truncation.

The generated-content safety scan runs before interpolation and again on the
finished prompt: blocked designer/brand, imitation phrasing, URLs, prompt
leakage, untrusted-section delimiters, control characters, and **raw HTML tags
or Markdown formatting** (`<tag>`, `**bold**`, `__bold__`, `[label](url)`,
`# headings`, fenced/inline code). Markup is rejected rather than silently
stripped, so DesignSpec generation can fall back on its existing single retry
instead of changing model-authored meaning; a bare `<`/`>`, single
hyphen/underscore and ordinary parenthetical prose remain accepted. Both scans,
the defensive revalidation and any length overrun surface as a single controlled
`ImagePromptBuildError` — `build_image_prompt` never raises
`GeneratedContentRejected` and never echoes the rejected text.

### Canonical selections are authoritative

Generated narrative may not contradict an explicit machine selection. When
`embellishment_styles` is exactly `["none"]`, `"none"` is authoritative: the
builder echoes the selection, renders ONE clear unembellished instruction, and
omits **both** the generated embellishment-plan content (techniques, density,
placement, motifs) **and** the `embellishment_density` line — even when a stale
persisted density is `minimal`, `balanced` or `heavy`. The finishing wording
also switches to an unembellished variant that describes the plain textile,
fabric colour, texture, drape and garment detail rather than asking for
embroidery detail, so the fixed wording can never contradict a `none` choice
(non-`none` designs keep the embroidery-aware finishing). When
`embellishment_density` is `"minimal"`, a small deterministic word-boundary rule
neutralises heavy/dense/opulent/lavish/richly-worked wording in the generated
embellishment narrative. The canonical ordered selections always remain present,
and a non-`none` selection is never silently transformed into `none`.

The questionnaire enforces the same authority at source: a schema-derived
compatibility rule (`none_hides_embellishment_density`, `equals ["none"] → hide`)
hides `embellishment_density` when `"none"` is chosen, so the existing Phase 7
rule machinery clears a stale density on the frontend and Django rejects a
supplied hidden `embellishment_density` answer. The rule is data in the
questionnaire schema, interpreted generically by both languages — never
hard-coded — and is exercised by the shared cross-language contract.

Because published questionnaire schemas are immutable (ADR 0005), this rule does
NOT edit the published, active `questionnaire_v1`; it ships as a new DRAFT
`questionnaire_v2` (a distinct UUID, `version=2`, `status=draft`) whose schema is
v1 plus exactly this one rule. v1 is restored byte-for-byte and guarded by a
canonical-schema fingerprint test; v2 is loaded with `loaddata questionnaire_v2`
and activated only through `activate_questionnaire_version`. Loading a draft
never activates or retires anything, existing designs keep validating against
their pinned historical schema, and v2 is not treated as active until an explicit
activation checkpoint is performed.

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
wording drift cannot slip past review. `PROMPT_BUILDER_VERSION` is `5.0.0`:
`2.0.0` introduced bounded rendering and canonical-selection authority; `3.0.0`
finalised the no-embellishment rules (dropping the density line and switching to
the unembellished finishing wording), made truncation total and added HTML/Markdown
rejection; `4.0.0` (Phase image-composition) moved the fixed composition
directive to the front as the highest-priority section, reordered the
garment-detail hierarchy (coverage ahead of colour/fabric), split the trailing
block into finishing-only wording and tightened the `concept_summary` cap
(700→400); `5.0.0` (Phase image-composition follow-up) added the high-priority
conditional coverage directive plus a closing reinforcement, and stopped
rendering `styling_notes` and `colour_story.rationale` — each changing every
fixture snapshot and requiring the version bump.
Each bump rewrote snapshots deliberately through the regeneration command's
version guard; persisted `image_prompt`/`prompt_builder_version` audit data on
existing `DesignVersion` rows is never rewritten, so a builder change only
affects newly created versions.

## Consequences

- The image prompt is fully reproducible and auditable, decoupled from any paid
  model, and snapshot-guarded against silent wording drift.
- Adding capabilities (negative prompt, JSON prompting) or changing the wording
  requires a deliberate `PROMPT_BUILDER_VERSION` bump and snapshot/manifest
  review, not an incidental edit.
- Inspiration influence and image generation remain out of scope; no provider
  call, no Replicate identifier, no seed and no reference-image field are
  introduced. The Phase 8 paid live checkpoint remains pending.

**Phase 14 note (ADR 0015):** refinement introduces no second prompt-builder
path. `build_image_prompt(spec)` is called identically for an initial and a
refined `DesignVersion` — it is a pure projection of whatever validated
DesignSpec it is given, with no branch on `generation_kind`. A refined
version's image prompt therefore carries the current `PROMPT_BUILDER_VERSION`
(now `4.0.0`) and the same reproducibility/snapshot guarantees as any initial
version's, and the composition-first ordering applies identically to refined
generations.

**Phase image-composition note:** the composition-first restructure (`4.0.0`) and
the coverage-first follow-up (`5.0.0`) changed prompt wording and ordering only;
they added no provider call, negative prompt, JSON prompt, model identifier,
reference-image conditioning or seed, and did not alter the DesignSpec contract,
the persistence/immutability model or any Phase 16 cost/security control. Live
evidence drove the sequence: `4.0.0` (composition first) fixed the framing but a
real generation then showed the provider ignoring an explicit high neckline and
head covering, so `5.0.0` renders coverage as a high-priority conditional visual
directive with a closing reinforcement and stops rendering advisory
styling/rationale prose. Each bumped `PROMPT_BUILDER_VERSION` and regenerated the
golden snapshots and manifest. Prompt-level coverage correctness is deterministic
and snapshot-guarded; provider adherence stays stochastic and needs an
operator-run before/after comparison.
