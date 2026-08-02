# Phase — prompt builder v8 (focused, positive-only image prompt)

- **Branch:** `feat/prompt-builder-v8-focused-prompt` (from `main` @ `80af5c5`)
- **Scope:** `PROMPT_BUILDER_VERSION` `7.0.0` → `8.0.0`; ADR 0010 amendment
- **Out of scope:** DesignSpec contract, questionnaire, persistence/immutability,
  provider wiring, cost controls, refinement flow, frontend

## Problem

A live `7.0.0` generation (2026-07-29, `replicate.com/p/z7p4sk3h61rmt0czqvnaktsvrr`)
rendered a design that ignored four validated selections at once: the neckline,
the midriff, the embellishment and the head covering. The DesignSpec was correct
and every one of those requirements was present in the prompt.

Two measured causes, neither of which is missing information:

### 1. Length

`7.0.0` prompts run 2,444–3,993 characters (~600–1,000 tokens). The default image
model's T5-XXL text encoder attends over roughly **512 tokens**. Everything past
that window is weakly conditioned or dropped outright — which is precisely where
`7.0.0` puts colour, fabric, embellishment, drape and the closing coverage
reinforcement.

An A/B comparison on one fixed DesignSpec (2026-07-29) met **2 of 6** stated
requirements at full length and **6 of 6** at roughly a quarter of the words.

### 2. Negation

`7.0.0` states coverage negatively in six places — "not an open, scooped or
sweetheart neckline", "a covered back that is not left open", "with no bare skin
at the waist", "with no hair visible", "without a gharara knee joint", "not
converted into a stitched gown".

Diffusion text conditioning has no representation for negation. "not an open
neckline" conditions on *open neckline*; the qualifier is lost and the mentioned
concept is made **more** likely, not less. Every negated clause in `7.0.0` was
working against the requirement it was meant to defend.

## Decision

`8.0.0` keeps every safety property of `7.0.0` — determinism, purity, bounded
slots, canonical-selection authority, the two safety scans, the snapshot/version
guard, one positive natural-language prompt with no negative prompt, no JSON and
no model identifier — and changes what is said and how much of it.

### D1 — Two bounds, not one

| constant | value | meaning |
| --- | --- | --- |
| `IMAGE_PROMPT_TARGET_CHARS` | 1500 | what the narrative budget aims at |
| `IMAGE_PROMPT_MAX_CHARS` | 2600 | the guaranteed hard bound, never exceeded |

Measured: the eleven reviewed fixtures build to **1,300–1,470**; the adversarial
worst case (64-character machine values in every canonical list, a recognised
garment, every coverage clause firing) measures **2,327**.

`7.0.0` had one 6,000-character bound, and real prompts sat at ~3,900. A single
1,500 bound is not achievable: a schema-valid DesignSpec may carry 64-character
machine values in every canonical list, so its *mandatory* content alone can
exceed 1,500 with no narrative at all. Two bounds state the real situation
honestly — realistic specs land near the target; an adversarial spec drops all
narrative and is still bounded.

### D2 — Positive-only phrasing everywhere

Every clause is rewritten so it names what must be **present**. No clause the
builder authors contains "not", "no", "never" or "without". This applies to the
coverage clauses, the neckline clauses, the per-area clauses, the head-covering
clauses, the garment-construction clauses and the finishing directive.

**Residual, documented not fixed here.** Model-authored narrative may still
phrase a slot negatively — `coverage_and_drape.back_and_midriff` reading "a fully
covered midriff with no bare skin at the waist", for instance. The builder must
not silently rewrite generated meaning; that would be the content rules engine
ADR 0009 rules out. What it does instead is state the canonical requirement
positively **first**, in the directive, and drop the slots where the generated
prose can only ever be negation (see D5's `head_covering`). Phrasing the
DesignSpec's own prose positively belongs to the generation system prompt and
would need a `SPEC_TEMPLATE_VERSION` bump — out of scope for this change.

### D3 — Garment construction is named

A new source-controlled map keyed only on `source_selections.garment_type` states
each garment's construction as **one-piece vs two-piece** in one clause, and folds
in the former garment-integrity cue where one existed. The model gets the
fundamental construction before any detail:

- `lehenga` — two-piece: fitted choli blouse, separate long flared skirt
- `saree` — a single draped length with the pallu falling over a fitted blouse
- `anarkali` — one-piece floor-length flared kurta over narrow trousers
- `gharara` — two-piece: kurti over separate trousers fitted to the knee, flaring
  below the knee
- `sharara` — two-piece: kurti over separate trousers flaring from the waist in
  one continuous line
- `shalwar_kameez` — two-piece: long tunic over separate trousers

The gharara/sharara distinction (CLAUDE.md §12) is preserved and is now stated
positively for both, rather than as a negation on the sharara side only. This
replaces `_GARMENT_INTEGRITY_CUES`; the cue content is not lost, it is merged.

### D4 — Coverage stated once, early

The closing coverage reinforcement is **removed**. It existed because `5.0.0`
judged a single early statement insufficient — but at `7.0.0` lengths the closing
restatement sat ~3,700 characters into the prompt, far outside the encoder window,
so it conditioned nothing while costing ~170 characters. Inside a 1,500-character
prompt the single early statement is within the window by construction.

### D5 — Redundant narrative is dropped

The prompt renders **canonical selections as its skeleton** and a small, fixed set
of bounded narrative slots as supplement — the inverse of `7.0.0`, where
model-authored prose dominated.

Kept (each individually capped): `garment_breakdown.overall_form`,
`colour_story.placement`, `embellishment_plan.placement`,
`embellishment_plan.motifs`, and the `coverage_and_drape` slots for body areas
nothing else has settled.

Dropped from the image prompt (all remain in the persisted DesignSpec and the
user-facing design brief): `title`, `concept_summary`,
`garment_breakdown.silhouette`, `garment_breakdown.drape_or_layering`,
`garment_breakdown.key_proportions`, `garment_breakdown.garment_components`,
`colour_story.palette_summary`, `fabrics_and_texture`,
`embellishment_plan.techniques`, `embellishment_plan.density`,
`embellishment_plan.restraint_notes`, `coverage_and_drape.head_covering`,
`coverage_and_drape.dupatta_or_saree_drape`, `cultural_context.regional_direction`,
`cultural_context.interpretation_notes`, `cultural_context.safeguards`.

Each is dropped for one of two reasons: it restates something the prompt already
carries canonically (`concept_summary`, `garment_breakdown.silhouette`,
`garment_components`, `colour_story.palette_summary`,
`embellishment_plan.techniques`/`density`, `fabrics_and_texture`), or it is
non-visual prose the image model cannot render (`interpretation_notes`,
`safeguards`, `restraint_notes`).

Two slots earned their own reasoning during implementation:

- **`garment_components`** was kept at first. It lost because D3's construction
  clause already names the garment's pieces and D-drape names the dupatta, so
  "Long kameez; Waist-flared sharara trousers; Head dupatta" restates both — and
  at this budget it was beating `embellishment_plan.placement`, which is
  genuinely additive.
- **`coverage_and_drape.head_covering`** is dropped for *every* schema version,
  not only where a canonical answer exists. It has no useful case: when the user
  asked for a covered head the directive states the requirement concretely, and
  when they did not the slot reads "No head covering…" — pure negation, which is
  exactly what D2 removes.

`fabrics_and_texture` has one exception: when `source_selections.fabrics` is empty
(the schema permits it), the entries' `fabric` names render as the design's only
fabric statement — and are **mandatory** for that reason, not narrative, so the
budget cannot drop them.

### D5b — a v1/v2 coverage preference suppresses its own narrative slot

Version 3 already suppressed the narrative for any canonically answered body
area. A version-1/2 `coverage_preferences` value settles its area just as firmly,
so it now suppresses too. `7.0.0` rendered "a closed high neckline covering the
collarbone" in the directive *and* "Neckline: A closed high neckline…" in the
prose: one requirement paid for twice, with a second chance for generated text to
drift from the validated choice.

### D6 — Canonical lists are item-capped

At most 4 colours, 3 fabrics and 4 embellishment styles render. Real questionnaire
answers are far under these; the schema's 8-item ceilings are a hostile-input
backstop, and rendering eight of anything dilutes the prompt. Order is preserved,
so the capped list is always the most important items.

The raw `Coverage preferences: ...` echo line is removed — every value in it is
already rendered as an explicit visual requirement by the coverage directive.

### D7 — The canonical regional selection is rendered, not the model's prose

`7.0.0` rendered `cultural_context.regional_direction`, the model's prose
elaboration, under the framing "Broad regional influence, offered as guidance
rather than a universal rule:".

Kept as a narrative slot, that elaboration is last in priority and the budget
dropped it for **every one of the eleven reviewed fixtures** — the user's
regional choice reached the image prompt in no case at all. That is a product
bug, not a budgeting detail, and it is only visible once the budget is tight
enough to bite.

`8.0.0` therefore renders a short **mandatory** clause built from the canonical
`source_selections.regional_style`: "Broad regional influence: punjabi." It
always renders and costs a fraction as much. The elaboration prose joins
`interpretation_notes` and `safeguards` in the unrendered set, so none of
`cultural_context` reaches the image prompt.

The framing stays non-prescriptive (CLAUDE.md §12) — "broad" and "influence" both
say the direction guides rather than governs. The explicit "rather than a
universal rule" is meta-language an image model cannot use, and the intermediate
wording tried during implementation, "not a rule", reintroduced the one negation
left anywhere in the builder.

### D8 — Budgeting changes shape with the budget

`7.0.0` split the narrative budget proportionally between sections, then
truncated a piece to whatever remained. Both are right at 6,000 characters and
wrong at 1,500:

- proportional shares were too thin to render anything, starving the most
  valuable slots (the garment's overall form, where the colours sit) along with
  the least;
- budget-level truncation produced dangling fragments — "fitted to the knee
  before a.", "Its components are Fitted choli; Trailing.", a bare "Motifs:".

`8.0.0` spends the budget **greedily in priority (reading) order** and selects
**all-or-nothing per piece**: a piece that does not fit is dropped whole, prefix
included. A half-sentence conditions the provider on less than the clean absence
of the detail. Per-slot caps still bound each piece, so all-or-nothing costs
little; a dropped piece frees its whole length, so a later, shorter piece that
still fits is kept rather than wasting the remainder.

Narrative pieces now carry their label as a separate `prefix`, rendered only when
a body survives, so a label can never be orphaned.

The coverage narrative also moves to sit with the coverage directive rather than
after embellishment (ADR 0010's long-standing "coverage outranks decoration").
Ranked below embellishment it lost — and a version-1 spec's neckline narrative is
the *only* neckline information such a spec has, since it carries no canonical
neckline.

## Unchanged

- Purity and determinism; no database, environment, randomness, network or
  provider import.
- One positive natural-language prompt. No negative prompt, no JSON prompt, no
  hard-coded model identifier, no provider call, no reference-image or seed field.
- `construction_caveats`, `image_alt_text`, `styling_notes` and
  `colour_story.rationale` are still never rendered.
- Both safety scans, `ImagePromptBuildError` as the only escaping exception, and
  the guarantee that a rejected string is never echoed.
- The regeneration command's version guard; persisted `image_prompt` /
  `prompt_builder_version` audit data on existing rows is never rewritten.
- Word-boundary truncation stays total (never a partial token).

## Acceptance

1. `PROMPT_BUILDER_VERSION == "8.0.0"`; snapshots and manifest regenerated through
   the guarded command.
2. Every fixture prompt is ≤ `IMAGE_PROMPT_TARGET_CHARS`.
3. No schema-valid DesignSpec, including adversarial near-maximum shapes, exceeds
   `IMAGE_PROMPT_MAX_CHARS`.
4. No negation token appears in any clause the builder authors, asserted both
   statically over the clause maps and end-to-end over every fixture's
   composition, coverage-directive and finishing blocks.
5. Canonical selections, coverage requirements, garment construction and the
   composition directive survive maximum-length pressure.
6. Every `7.0.0` safety and exclusion test still passes, updated only where the
   wording it asserts deliberately changed.
7. ADR 0010 amended.

Provider adherence stays stochastic. Prompt-level correctness is deterministic and
snapshot-guarded; whether the model honours it needs an operator-run before/after
comparison on a fixed DesignSpec, exactly as `5.0.0` required.
