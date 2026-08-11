# Sitara Phase 23 — A refinement that actually refines, and options that explain themselves

**Status:** specification only. Nothing here is implemented. Written to be picked
up on its own branch. The first half is a defect report with a root cause traced
to specific lines; the second is a smaller content defect that shares a theme —
in both cases, something the user chose does not reach them. Owner statements are
marked **Owner decision**. All six open questions were answered on 2026-08-11 —
the answers, and what each changed, are recorded at the end.

**This phase is a hard prerequisite of Phase 24**, which charges a token per
refinement. A refinement must change the concept before it is charged for.

## Main objective

1. **Refinement changes nothing, and it cannot.** This is not a tuning problem or
   a model problem. For **three of the eight** allowlisted refinement categories
   the refined design *provably cannot alter a single character of the image
   prompt*, and for two more it can only alter a slot unrelated to what was
   asked. §1 shows why, line by line. §3 fixes it.
2. **The materials question has one info button.** Satin explains itself; the
   other eleven fabrics do not. The same gap exists across three more questions.
   §7 fixes it, and finds that "all or none" is the right instinct for a reason
   that goes past tidiness.

> **Owner decision:** "the ammendments don't make any changes in all tests I've
> run to the design at all" and "on the materials only one of them has the info
> button either none of them should or all of them should."

---

## Part A — Why a refinement changes nothing

### 1. The root cause

Two systems drifted apart, each correct on its own terms.

**Refinement (Phase 14, ADR 0015)** was designed against prompt builder 3.0.0, a
6,000-character prompt in which model-authored narrative carried most of the
design. Its allowlist therefore names narrative fields — `styling_notes`,
`concept_summary`, `construction_caveats`, `coverage_and_drape.neckline` — and it
freezes the one thing that must never drift:

```python
REFINEMENT_IMMUTABLE_ROOTS = frozenset({"schema_version", "source_selections"})
```

**The prompt builder is now at 8.3.0**, and 8.0.0 deliberately inverted the
balance. From its own module docstring:

> renders CANONICAL SELECTIONS as the skeleton and a small fixed set of bounded
> narrative slots as supplement, instead of letting model-authored prose
> dominate

`IMAGE_PROMPT_TARGET_CHARS` fell from 6,000 to **1,500**. Whole narrative fields
were dropped outright — `title`, `concept_summary`, `styling_notes`,
`construction_caveats`, `image_alt_text`, `garment_components`,
`drape_or_layering`, `cultural_context.*`, `colour_story.palette_summary`,
`colour_story.rationale`, `embellishment_plan.techniques`, `.density`,
`.restraint_notes`, and `fabrics_and_texture` whenever canonical fabrics exist —
each for a good, recorded reason.

The two changes are individually right and jointly fatal. **Refinement may change
only narrative. The prompt is built almost entirely from canonical selections.
Refinement may not change canonical selections.** The intersection is nearly
empty.

### 2. The intersection, category by category

Under questionnaire v4 / DesignSpec v3 — the current shape — exactly four
generated fields still reach the prompt, all of them bounded, low-priority and
droppable by the narrative budget:

| Field | Cap | Priority |
| --- | --- | --- |
| `garment_breakdown.overall_form` | 240 (`_FORM_CAP`) | after the garment's canonical identity |
| `colour_story.placement` | 200 (`_PLACEMENT_CAP`) | after the canonical colours |
| `embellishment_plan.placement` | 3 × 90 | after canonical density and styles |
| `embellishment_plan.motifs` | 3 × 90 | last but one |

The coverage narrative slots (`coverage_and_drape.sleeves`, `.neckline`,
`.back_and_midriff`) are **suppressed whenever the area has a canonical answer**,
which under questionnaire v4 is always — `_coverage_narrative` exists to fill
gaps a v1/v2 spec leaves, and v3 leaves none.

Crossing that against `REFINEMENT_ALLOWED_PATHS`:

| `change_type` | What it may change | What reaches the prompt | Effect on the image |
| --- | --- | --- | --- |
| `dupatta_or_saree_drape` | head covering, drape narrative, `drape_or_layering`, styling notes, caveats | **nothing** — `head_covering` is dropped for every version, the drape narrative is replaced by the canonical clause, `drape_or_layering` is unrendered | **none, provably** |
| `styling_details` | styling notes, interpretation notes, title, summary, alt text | **nothing** — not one of these is rendered | **none, provably** |
| `sleeves_and_coverage` | `coverage_and_drape.sleeves`, `.back_and_midriff`, `garment_components`, styling notes, caveats | **nothing** — both slots suppressed by the canonical answers, the rest unrendered | **none, provably** |
| `neckline` | `coverage_and_drape.neckline`, `garment_components`, `embellishment_plan.placement`, caveats | only embellishment placement — which is not a neckline | none *on the neckline* |
| `fabric_and_texture` | `fabrics_and_texture`, `colour_story`, styling notes, caveats | `fabrics_and_texture` is unrendered whenever `source_selections.fabrics` is non-empty, i.e. normally; only `colour_story.placement` survives | none *on the fabric* |
| `colour_story` | `colour_story`, `fabrics_and_texture`, styling notes, title, summary | `colour_story.placement` — 200 characters saying *where* colour sits, never *which* | the canonical `Colours:` line is unchanged, so the colour does not change |
| `silhouette_detail` | `garment_breakdown` (root), `fabrics_and_texture`, caveats | `garment_breakdown.overall_form`, 240 chars | the canonical `The silhouette is X` line is unchanged |
| `embellishment` | `embellishment_plan`, `fabrics_and_texture`, styling notes, caveats | `embellishment_plan.placement` + `.motifs` | placement and motifs can move; **density and style cannot** |

So: three categories can change nothing at all, two can change only something the
user did not ask about, and three can move one bounded narrative slot that the
budget may drop anyway. That is the whole of the reported symptom.

### 3. Three further amplifiers, all working the same direction

Even the narrative that survives is fighting three forces that pull the refined
image back onto the original:

- **Seed reuse.** `_find_source_attempt_seed` copies the initial attempt's seed
  verbatim. With a near-identical prompt, an identical seed produces a
  near-identical image *by design* — that is what seed reuse is for. It is not a
  bug; it is a continuity aid doing its job on an input that did not change.
- **The same reference images.** `pipeline.py` calls
  `reference_image_urls(version.design)` for every live attempt, refinements
  included. flux-2-max is conditioned on the same photographs it saw the first
  time, which anchors the output further.
- **Demo mode reproduces the bug exactly.** `demo/refinement_engine.py`'s
  docstring states it *"never touches `source_selections`"*, and
  `select_demo_asset` scores against exactly those canonical fields. Identical
  canonical input, identical manifest, deterministic tie-break → **the same
  fixture image, every time**. If the owner tested in demo mode, the refined
  concept was not similar to the original; it was byte-identical to it.

None of the three is wrong. All three become correct the moment the prompt
genuinely differs, which is what §4 fixes.

---

## Part B — The fix

### 4. A refinement may change its own canonical selection

**The premise to overturn.** ADR 0015 froze `source_selections` because a
DesignSpec's selections are the user's own answers echoed back, and a model must
never quietly rewrite what someone said. That reasoning is sound and must
survive. But it answers the wrong question here. A refinement *is* the user
changing one answer — deliberately, through a named category, on a screen that
exists for that purpose. Refusing to record the change is not protecting their
answers; it is discarding them.

So `REFINEMENT_IMMUTABLE_ROOTS` narrows, and each category gains the canonical
field it is named after:

| `change_type` | Canonical paths it may change (v3) | v1/v2 equivalent |
| --- | --- | --- |
| `colour_story` | `source_selections.fabric_colour`, `.embroidery_colour`, `.dupatta_colour` | `.colour_palette` |
| `fabric_and_texture` | `source_selections.fabrics` | same |
| `embellishment` | `source_selections.embellishment_styles`, `.embellishment_density` | same |
| `sleeves_and_coverage` | `source_selections.sleeves`, `.back_coverage`, `.midriff` | `.coverage_preferences` |
| `neckline` | `source_selections.neckline_style` | *(v1 has none — see below)* |
| `dupatta_or_saree_drape` | `source_selections.dupatta_style`, `.saree_drape`, `.head_covering` | `.dupatta_style`, `.saree_drape` |
| `silhouette_detail` | `source_selections.silhouette` | same |
| `styling_details` | **none** — see §6 | none |

Permanently immutable, for every category:

```text
schema_version      a version is not a design choice
garment_type        changing the garment is a new design, not a refinement
ceremony            likewise
regional_style      a cultural direction is not a styling tweak (§12)
custom_colours      the palette a colour may be chosen FROM, not a selection
```

`REFINEMENT_ALLOWED_PATHS` keeps every narrative path it already has. The
canonical paths are **added**, not substituted — the refined narrative should
still agree with the refined selection, and the existing exact-diff validator
still rejects anything outside the category.

**Version dispatch is mandatory, not optional.** A v1 spec has no
`neckline_style` attribute at all — `_canonical_neckline` already uses `getattr`
for exactly that reason. The allowlist must resolve per `schema_version` through
the existing registry, and a `neckline` refinement of a v1 spec must fail with a
controlled, documented code rather than an `AttributeError`. `selection_semantics.py`
is the single version-independent reader (§ Phase 16B) and is where this belongs.

### 5. A refined selection is only valid if the questionnaire says so

This is the guardrail that makes §4 safe, and it is not optional.

Today `source_selections` is trustworthy because it is verified as an exact echo
of validated questionnaire answers. Once a refinement may change it, that
guarantee has to be replaced with an equally strong one, or the model can invent
`"fabrics": ["unobtainium"]` and the builder will render it.

Every changed canonical value must pass, in this order, **before the candidate
spec is accepted and before anything is persisted**:

1. **In the category's allowlist** — the existing exact-diff check, unchanged.
2. **A real option of that question, under the design's own pinned
   questionnaire version.** Not the active version — the pinned one, because
   `Design.questionnaire_version` is assign-once and a design pinned to a retired
   version must stay refinable (Phase 9's follow-up proved that path).
3. **Consistent with that version's compatibility rules.** Questionnaire v4 ships
   `restrict_options` rules tying coverage, neckline, head covering and dupatta
   together (ADR 0018). A refinement that sets a neckline the current coverage
   answers forbid must be rejected, not rendered. Reuse
   `sitara.questionnaire.rules` — do not write a second rule evaluator.
4. **Cardinality and shape** — a multi-select stays a list within its bounds, a
   single-choice stays one value, an optional question may become absent.

A candidate failing any of these is refused exactly as an out-of-allowlist diff
is refused today: no persistence, bounded retries
(`MAX_REFINEMENT_PROVIDER_REQUESTS = 2`), then a controlled terminal error. Never
a partial accept.

**Never trust the model's own claim about what it changed** (§7). The diff is
computed from the two specs and checked field by field, as now.

### 6. `styling_details` has nothing left to change

Every path in its allowlist — `styling_notes`, `cultural_context.interpretation_notes`,
`title`, `concept_summary`, `image_alt_text` — is deliberately unrendered, and
8.0.0's reasoning for dropping `styling_notes` is explicit: *"advisory beauty/
jewellery prose pulls the provider toward portraiture and can contradict the
coverage requirements."* That reasoning still holds. Rendering it back is not on
the table.

So the category has three honest options:

- **Retire it.** Eight becomes seven. The refinement panel stops offering a
  choice that cannot do anything.
- **Redefine it** as a category over something canonical that is currently
  unreachable — but there is nothing suitable left; every canonical field is
  claimed by another category.
- **Keep it, and say what it does.** It genuinely changes the written brief the
  customer reads on the result screen and in the downloaded text, which is not
  nothing — it is just not the image. The panel would have to say so plainly.

**Recommendation: retire it**, because a control whose effect is invisible in the
thing the user is looking at is worse than an absent one.

**Decided: retired.** Eight categories become seven.

### 7. Seed and references, now that they matter

Both become live decisions the moment the prompt differs:

- **Keep seed reuse.** With a genuinely changed prompt, an identical seed is
  precisely the continuity aid it was documented as — the requested thing
  changes, the rest holds. Its documentation stays as it is (§7: *"a continuity
  aid only, never a guarantee"*), and this phase must not upgrade that wording.
- **References are the harder call.** Re-sending the customer's photographs
  anchors the refinement to the inspiration they chose, which is usually right.
  For `colour_story` it is usually wrong: a reference image is a far stronger
  colour signal than a text clause, so "make it green" against a red reference
  will lose. Options: send them always (status quo), suppress for the colour
  category, or suppress for every refinement. **Decided: suppress for
  `colour_story` only** — the narrowest change that addresses the observed
  failure. Every other category keeps its references.

### 8. Demo mode has to change too

`build_demo_refined_spec` must apply the same canonical edit under the same
validation, so that:

- `select_demo_asset` reruns against genuinely different input and may resolve to
  a different asset;
- and when it resolves to **the same** asset — a perfectly legitimate outcome
  when the pack has nothing closer — the UI **says so**. ADR 0016's honesty rule
  is that demo output is never presented as something it is not, and "your
  refinement produced this identical image" is exactly the case that needs a
  sentence. Silently returning the same picture is what produced this bug report.

The demo path stays deterministic, zero-cost, and free of any provider or
reference URL.

### 9. Version bumps

Work out from what actually changed:

| Constant | Bump? | Why |
| --- | --- | --- |
| `PROMPT_BUILDER_VERSION` | **No** | `build_image_prompt` is a pure function of the spec and is not edited. A different prompt comes from a different spec, which is the point. Golden snapshots must stay byte-identical — **prove it**, do not assume it. |
| `DESIGN_SPEC_SCHEMA_VERSION` | **No** | No field added, removed or retyped. |
| `REFINEMENT_REQUEST_SCHEMA_VERSION` | **No** | The request is still one `change_type` plus a bounded note. Persisted rows stay valid. |
| `REFINEMENT_TEMPLATE_VERSION` | **Yes** | The system prompt currently instructs the model to preserve `source_selections`. That instruction is now wrong. |
| `DEMO_REFINEMENT_TEMPLATE_VERSION` | **Yes** | Same reason. |
| `LIVE_GENERATION_PRICING_PROFILE` | **No** | No model change, no per-call cost change. |

That `PROMPT_BUILDER_VERSION` does not move is a useful property and worth
stating in the ADR: this phase fixes refinement without touching the prompt
contract, so every persisted `image_prompt` and its recorded builder version stay
exactly as immutable audit data should.

---

## Part C — Options that explain themselves

### 10. What is actually wrong

`ChoiceOptionCard.tsx` mounts the info trigger only when the option carries a
description:

```tsx
{option.description && onShowInfo ? (
```

with the comment *"a card with no description never gets a trigger."* The
component is correct. The **content** is uneven. Measured against
`questionnaire_v4.json`:

| Question | Options | With a description |
| --- | ---: | ---: |
| `ceremony` | 7 | 7 |
| `garment_type` | 6 | 6 |
| `silhouette` | 23 | 23 |
| `neckline_style` | 9 | 9 |
| `sleeves` | 5 | 5 |
| `back_coverage` | 3 | 3 |
| `midriff` | 3 | 3 |
| `head_covering` | 4 | 4 |
| `dupatta_style` | 5 | 5 |
| `saree_drape` | 4 | 4 |
| **`fabrics`** | 12 | **1** — satin only |
| **`embellishment_styles`** | 13 | **0** |
| **`embellishment_density`** | 3 | **0** |
| **`regional_style`** | 9 | **0** |
| `fabric_colour` / `embroidery_colour` | 28 | 0 |
| `dupatta_colour` | 29 | **1** — `match_fabric` only |

The reported symptom is `fabrics`, and its cause is historical: satin was added
in Phase 16B (ADR 0018) *with* a description, alongside eleven options inherited
from v1 that never had one. The `dupatta_colour` case looks identical but may not
be — colour questions are `colour_choice` type and are rendered by
`ColourSwatchGrid`, not `ChoiceOptionCard`. **Verify which renderer handles it
before assuming the same defect**; a lone info button on a swatch grid is a
different problem from a lone info button on a card grid.

### 11. Which gaps to fill, and which are not gaps

**Fill: `fabrics` (11 missing) and `embellishment_styles` (13 missing).** These
are the two most culturally specific vocabularies in the product. The difference
between zardozi, dabka, nakshi, gota patti and chikankari is not decoration — it
is regional craft tradition, and a customer choosing between them without being
told what they are is exactly the flattening §2 forbids. This is a product
requirement, not polish.

**Fill: `embellishment_density` (3 missing).** Three short lines; "minimal and
airy" versus "rich and heavily worked" reads clearly enough that a description is
mostly reassurance, but three of three is cheap and consistency is the ask.

**Do not fill: `regional_style` (9 missing). Decided — leave it undescribed.**
This one was never a copywriting task. §12 requires regional influences to be
*"optional and non-prescriptive"*, and a sentence explaining what "Rajasthani
bridal influences" means can very easily become prescriptive, or wrong, or both.
Zero of nine is an internally consistent "none" for that question and satisfies
the all-or-none instruction on its own terms.

**An implementer must not add these later without the owner's cultural review.**
Say so in the fixture, next to the question, so that a future contributor filling
in "the missing ones" for tidiness does not quietly ship nine claims about
regional bridal tradition that nobody reviewed.

**Not a gap: the colour swatches.** A swatch *is* the description — it shows the
colour. Twenty-eight one-line restatements ("Pistachio: a pale green") add noise,
and the product already keeps an unambiguous hue vocabulary in source control for
exactly these values (`prompt_builder._COLOUR_DESCRIPTORS`) where it is needed —
in the prompt, where there is no swatch to look at. `match_fabric` is the one
option in a colour list that is *not* a colour, so its description is a
legitimate exception rather than an inconsistency. The fix there is presentation:
make it read as the exception it is.

**Decided:** the instruction was "either none of them should or all of them
should", and the answer taken is *all of them on the card-rendered questions*,
*none on the swatches because the swatch is the explanation* — with
`regional_style` deliberately staying at zero of nine, which is itself an
internally consistent "none" for that question.

### 12. This needs questionnaire v5

Published versions are immutable — ADR 0005, enforced in
`QuestionnaireVersion.save()`, and reaffirmed by the Phase 9 follow-up commit
that restored `questionnaire_v1.json` byte-for-byte after it was edited in place.
An option description is presentation metadata, but it lives inside the frozen
`schema` JSON, so the rule applies whole.

Therefore:

- `questionnaire_v5.json` ships as a **new draft** (fixed UUID, `version=5`,
  `status=draft`), identical to v4 except for the added descriptions.
- v4 is left untouched, whatever its status is in any environment.
- Activation is an operator step through `activate_questionnaire_version`, never
  a fixture load. `loaddata` never activates anything.
- Designs pinned to v4 keep working, keep their historical semantics, and stay
  refinable — the Phase 9 follow-up already established and tested that path;
  reuse its test shape.

No DesignSpec version, prompt-builder version or demo-manifest change follows: a
description is never read by the validator, never enters `source_selections`,
never reaches the prompt builder, and never reaches a provider.

---

## Read first

`CLAUDE.md` in full (§7, §12, §16 especially), `docs/phases/PHASES.md`, and:

| Source | Why |
| --- | --- |
| `docs/decisions/0015-*` | Single-round refinement. Amended by Part B. |
| `docs/decisions/0010-*` | The prompt builder, including the 5.0.0 and 8.x restructures. |
| `docs/decisions/0005-*` | Questionnaire immutability — why Part C needs v5. |
| `docs/decisions/0018-*` | Questionnaire v3/v4, option presentation metadata, the compatibility rules §5 must honour. |
| `apps/api/sitara/generation/prompt_builder.py` | What is and is not rendered. Read the whole file. |
| `apps/api/sitara/generation/refinement.py` | `REFINEMENT_ALLOWED_PATHS`, `REFINEMENT_IMMUTABLE_ROOTS`, the diff. |
| `apps/api/sitara/generation/refinement_service.py` | The retry-bounded accept loop and atomic persistence. |
| `apps/api/sitara/generation/selection_semantics.py` | The single version-independent reader for canonical selections. |
| `apps/api/sitara/generation/demo/refinement_engine.py`, `selector.py` | Why demo returns the same asset. |
| `apps/api/sitara/questionnaire/rules.py` | The rule evaluator §5 must reuse. |
| `apps/web/src/features/questionnaire/ChoiceOptionCard.tsx` | Line 104 — the info trigger. |
| `apps/api/sitara/questionnaire/fixtures/questionnaire_v4.json` | The source for v5. |

### Findings already established — verify, do not re-derive

- `PROMPT_BUILDER_VERSION` is `8.3.0`; `IMAGE_PROMPT_TARGET_CHARS` is 1,500 and
  `IMAGE_PROMPT_MAX_CHARS` 2,600.
- `REFINEMENT_IMMUTABLE_ROOTS` is exactly `{"schema_version", "source_selections"}`.
- `_coverage_narrative` suppresses every slot whose area has a canonical answer;
  `coverage_and_drape.head_covering` is dropped for **every** version.
- `_fabrics` renders `fabrics_and_texture` only when
  `source_selections.fabrics` is empty.
- `_drape` and `_regional` render canonical values only.
- `MAX_REFINEMENTS = 1`, so a design has at most two versions. Nothing here
  changes that.
- `demo/refinement_engine.py` states it never touches `source_selections`.
- `questionnaire_v4.json` ships `status: draft`; v1 is the active fixture.
- `satin` is the only fabric with a description; it was added by ADR 0018.

---

## Required commit boundaries

One reviewable concern each, in dependency order. Commit 1 is a characterisation
test that **fails on `main`** and passes after commit 2 — it is the evidence the
defect existed, and it must land first so the fix cannot be reviewed on trust.

1. `test(refinement): prove a refinement cannot change the image prompt`
2. `feat(refinement): let a category change its own canonical selection`
3. `feat(refinement): validate a refined selection against the questionnaire`
4. `feat(demo): refine canonical selections and disclose an unchanged asset`
5. `feat(frontend): describe what a refinement will and will not change`
6. `feat(questionnaire): describe every material and embellishment option`
7. `docs(phase-23): record why a refinement changed nothing`

---

## Automated tests

Beyond the tests named inline:

**The characterisation test (commit 1)** — for each of the eight categories,
build a source spec, apply a maximal legal narrative-only refinement, build both
prompts, and assert the diff. On `main` this documents that three categories
produce a zero-character diff; after commit 2 the same test asserts the intended
non-empty diff for each. This test is the phase's spine — write it first and do
not weaken it later to make a fix pass.

**Canonical refinement** — each category changes its own canonical field and
nothing else; a category may not change another category's canonical field; the
permanently-immutable roots are rejected for every category, including
`garment_type` and `ceremony`; a v1 spec refused a `neckline` refinement gets a
controlled code, never an `AttributeError`; the exact-diff validator still
rejects an out-of-allowlist narrative path.

**Questionnaire validation of the refined value** — an invented option value is
rejected pre-persistence; a value valid in the *active* version but not in the
design's *pinned* version is rejected; a value that breaks a v4 `restrict_options`
consistency rule is rejected; a multi-select refined to a bare string is
rejected; an optional single-choice refined to absent is accepted. Each rejection
persists nothing and leaves the source version untouched.

**Prompt immutability** — every committed golden snapshot is byte-identical after
this phase, and `PROMPT_BUILDER_VERSION` is unchanged. Assert both.

**Demo** — the same questionnaire input plus the same refinement selects the same
asset deterministically; a refinement whose canonical change has a better match
in the manifest selects the different asset; a refinement that resolves to the
same asset is *labelled* as such in the result payload; zero provider wrapper
invocations, zero client construction, zero network egress throughout.

**Seed and references** — seed reuse still copies only a prior attempt's own
persisted seed; a `colour_story` refinement is handed **no** reference URLs and
every other category is handed the same set as its source attempt — both pinned
by asserting what `ImageGenerationRequest` was constructed with, not by asserting
an intermediate call.

**Descriptions** — v1 keeps its canonical fingerprint; v4 is byte-identical
after the phase; v5 loads as a draft and activating it retires v4 atomically; a
design pinned to v4 still validates, still generates and still refines; every
option of `fabrics`, `embellishment_styles` and `embellishment_density` carries a
description in v5; descriptions never appear in `source_selections`, in a
DesignSpec, in an image prompt or in any provider payload (assert the negative).

**Frontend** — an option with a description gets a trigger and an option without
one does not (unchanged behaviour, now with content that satisfies it); the
info drawer's ARIA disclosure state is unchanged; jest-axe passes on the
materials and embellishment screens.

---

## Commands and validation

Per CLAUDE.md §20 in full, plus:

```powershell
docker compose exec api python manage.py loaddata questionnaire_v5   # loads as a DRAFT
docker compose exec api pytest -k "refinement or prompt_builder or questionnaire"
```

Prompt-snapshot regeneration must **not** run; if the snapshot guard demands a
`PROMPT_BUILDER_VERSION` bump, something rendered differently and the cause needs
finding before the phase proceeds. The local `.env` may have live generation on,
so backend runs need the safety overrides explicit.

## Manual checkpoint

In `DEMO_MODE=true` with no provider keys: generate a concept, then refine it
once in each of the categories that survive §6, and confirm the refined image
differs in the requested way and only in the requested way. Then repeat one
category and confirm the review, the DesignSpec, the image prompt and the result
brief all agree on the *refined* selection rather than the original.

The **live** refinement checkpoint — proving the changed prompt actually moves
flux-2-max — is operator-run, budgeted, and not part of the PR. Until it is run,
the honest claim is "the refined prompt now differs, and differs in the requested
field", not "refinement visibly changes the image".

## Non-goals

- More than one refinement. `MAX_REFINEMENTS` stays 1.
- Image-to-image editing. Refinement stays a fresh text-to-image generation.
- Changing `garment_type`, `ceremony` or `regional_style` through a refinement.
- Re-rendering narrative fields that 8.0.0 deliberately dropped.
- Any `PROMPT_BUILDER_VERSION` bump, snapshot regeneration or model change.
- Editing questionnaire v1–v4 in place.
- Descriptions on individual colour swatches.
- Trusting a model's own statement about what it changed.

## Documentation and decision record

- **ADR 0028 — a refinement changes its own selection.** Amends ADR 0015. Must
  state the root cause in the terms of §1 — that two individually-correct
  decisions produced an empty intersection — record the narrowed immutable set
  and why `garment_type`/`ceremony`/`regional_style` stay frozen, record the
  questionnaire-validation guardrail as the replacement for the exact-echo
  guarantee, and record that `PROMPT_BUILDER_VERSION` deliberately does not move.
- **An addendum to ADR 0018 — every option explains itself.** Decided as an
  addendum rather than a new record, since it continues 0018's option-presentation
  metadata. Must record questionnaire v5, why v4 could not be edited in place, the
  swatch-is-the-description decision, and that `regional_style` stays undescribed
  on purpose so that no prescriptive cultural wording is invented.
- Update `CLAUDE.md` §7 where it describes refinement as changing only narrative
  fields, and §12 if the regional descriptions land.
- Update `docs/phases/PHASES.md`.

## Decisions taken by the project owner

All six questions were answered on 2026-08-11. Nothing in this phase is waiting
on the owner.

| # | Question | Decision |
| --- | --- | --- |
| 1 | Retire `styling_details`? | **Retired.** Eight refinement categories become **seven**. A control that cannot change the image is removed rather than left offering a promise it cannot keep. |
| 2 | Should a colour refinement still send the reference photographs? | **Suppressed for `colour_story` only.** Every other category keeps them. A reference photo outweighs a text clause on colour, so "make it green" against a red reference would keep losing — which is the reported bug. |
| 3 | "All or none" — all on the cards, none on the swatches? | **Yes.** A swatch is its own explanation; `match_fabric` is the one non-colour option in a colour list and keeps its description as the legitimate exception. |
| 4 | Who writes the nine `regional_style` descriptions? | **Nobody — leave that question without descriptions.** Internally consistent (zero of nine), avoids inventing prescriptive cultural wording, and honours §12's requirement that regional influences stay non-prescriptive. |
| 5 | New ADR or an addendum to ADR 0018? | **Addendum to ADR 0018.** Only ADR 0028 is a new record. |
| 6 | Should the refinement panel say what each category will change? | **Yes.** Name the field, not just the category — "this will change the fabrics you chose". |

### What these answers changed

- **`styling_details` is deleted, not deprecated.** `REFINEMENT_CHANGE_TYPES`
  loses a member, which is an API contract change: regenerate the OpenAPI schema
  and the TypeScript client, and prove drift-free. Any **persisted** refinement
  row naming it stays valid and readable — a historical row is audit data and is
  never rewritten (§14's discipline applied to a different table). A *new*
  request naming it is a controlled 400, not a 500.
- **`regional_style` descriptions are out of scope**, so §11's four-question list
  becomes three: `fabrics` (11 missing), `embellishment_styles` (13 missing),
  `embellishment_density` (3 missing). Questionnaire **v5** is still required —
  the immutability rule does not care how many strings changed.
- **Colour refinements gain a reference-suppression branch**, which needs its own
  test asserting what `ImageGenerationRequest` was handed, per category. This is
  the one place in this phase that touches the live provider path, and it can
  only ever *remove* a reference URL, never add one.
- **Commit 5 grows** to cover both the panel copy and the removal of the retired
  category from the UI.

### Consequence worth stating before implementation starts

Retiring `styling_details` and fixing the other seven means **every refinement
category will, for the first time, change something the customer can see.** That
is the goal — and it also means the first live run after this phase is the first
time refinement has ever genuinely exercised the provider twice on different
prompts. The budgeted live checkpoint in §Manual checkpoint is therefore not a
formality; it is the first real test of the feature.
