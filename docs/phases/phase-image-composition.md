# Phase — Generated-image composition and catalogue framing

Implement this as a focused phase **after Phase 16 is merged**. It is independent of Phase 16’s live-generation security and cost-control work and must not absorb Phase 17 UI-polish tasks.

## Context

The first real paid generation on 2026-07-22 confirmed that the live pipeline works end to end. However, its composition does not match the approved Phase 2 model-evaluation outputs in:

```text
experiments/model-eval/outputs/runs/screening-20260714-001/
```

The Phase 2 references consistently present:

- Exactly one model.
- A full-body, head-to-foot view.
- The complete garment and trailing fabric inside the frame.
- A centred standing catalogue pose.
- A plain neutral studio backdrop.
- Soft, even studio lighting.
- The garment as the primary visual subject.

The current live output instead resembles a cropped bridal beauty/editorial portrait: only part of the garment is visible, the model and fabric are shown more closely, and an ornate interior and floral setting compete with the clothing.

The current working diagnosis is a prompt-prioritisation problem:

- `build_image_prompt` is in `generation/prompt_builder.py`.
- The current `PROMPT_BUILDER_VERSION` is `3.0.0`.
- Approximately 600 words of garment, drape and embellishment detail precede the composition instruction.
- The full-length studio instruction appears only at the end of the prompt.
- `aspect_ratio="3:4"` already matches the evaluation references.
- `prompt_upsampling=False`.
- Phase 2 demonstrated that `black-forest-labs/flux-1.1-pro` can produce the required composition.

Before editing, verify from repository and persisted-generation evidence that the live request actually used the expected prompt-builder version, final prompt and provider parameters. Do not reopen model selection unless repository evidence contradicts the diagnosis.

## Objective

Make the deterministic image prompt reliably prioritise a South Asian bridalwear **catalogue composition**:

- Exactly one adult model.
- Standing upright and primarily facing the camera.
- Centred in the frame.
- Top of head, both feet, complete outfit, garment hem, dupatta fall and any train or trailing fabric visible.
- Visible breathing room around the complete subject.
- Seamless plain neutral studio backdrop.
- Soft, even, shadow-controlled studio lighting.
- Garment construction, drape, colour and embellishment as the primary visual subject rather than the face, jewellery or environment.

Preserve the canonical garment semantics already encoded by the builder.

## Required investigation

Before implementation:

1. Read the relevant parts of:

   - `CLAUDE.md`
   - ADR 0010
   - ADR 0014
   - `generation/prompt_builder.py`
   - Prompt-builder tests and golden-fixture tooling
   - The production generation path that persists and submits `image_prompt`
   - The Phase 2 model-evaluation documentation

2. Report:

   - The current prompt section ordering.
   - Representative prompt character and word counts.
   - Where the composition instruction currently appears.
   - The confirmed builder version and provider parameters used by the live generation.
   - Any prompt wording that encourages portrait, editorial, environmental or face-focused composition.

3. Produce a concise implementation plan before modifying files.

Do not modify anything under the frozen Phase 2 output directory.

## Implementation requirements

### 1. Lead with a stable composition directive

Restructure `build_image_prompt` so that the first non-whitespace content is a concise composition directive equivalent to:

> Full-length South Asian bridalwear catalogue photograph of exactly one adult model standing upright and primarily facing the camera, centred in frame. Position the camera far enough away for the top of the head, both feet, the complete garment, hem, dupatta fall and any trailing fabric to remain fully visible with clear margin around the subject. Use a seamless plain neutral studio backdrop and soft, even studio lighting. Keep the garment as the primary visual subject.

The final wording may be improved, but it must remain:

- Concise.
- Positive natural language.
- Garment-focused.
- Applicable across sarees, lehengas, shararas/ghararas, anarkalis and kurta-style outfits.
- The first and highest-priority section of the prompt.

Consider extracting it into a clearly named constant so its position and intentional wording are easy to test and review.

### 2. Use an explicit prompt hierarchy

Use this approximate priority order:

1. Composition, framing and studio presentation.
2. Garment type and silhouette.
3. Construction, layers and drape.
4. Coverage and modesty requirements.
5. Colour and fabric.
6. Embroidery and surface decoration.
7. Dupatta or veil treatment.
8. Restrained finishing and photographic-quality details.

Composition must never be displaced or truncated by lower-priority detail.

### 3. Reduce redundancy without losing canonical semantics

The current prompt is likely too long and front-loads many similarly weighted details.

Refactor repeated or verbose wording into concise deterministic clauses. Do not arbitrarily remove supported design fields merely to shorten the prompt.

If the existing maximum-length behaviour can truncate content:

- Composition must always be retained.
- Use a documented deterministic priority order.
- Retain safety, coverage and core garment-construction semantics ahead of decorative secondary details.
- Do not add model-dependent or random summarisation.

Record representative before-and-after prompt lengths in the completion report.

### 4. Remove accidental editorial cues

Inspect the generated wording for terms that may encourage:

- Bridal portraiture.
- Beauty photography.
- Close framing.
- Cinematic venue photography.
- Ornate environmental scenes.
- Face or jewellery emphasis.

Replace such wording with positive catalogue and garment-documentation language where appropriate.

Do not create a separate negative prompt.

### 5. Version and audit behaviour

- Bump `PROMPT_BUILDER_VERSION`.
- Preserve existing immutability rules.
- Existing persisted `image_prompt` and `prompt_builder_version` values must never be rewritten.
- A changed builder must only affect newly created `DesignVersion` records.
- Regenerate the golden snapshot or manifest using the documented command.
- Review and commit the resulting intentional diff.

### 6. Update ADR 0010

Amend ADR 0010 to record:

- Composition-first prompt ordering.
- The catalogue-framing requirements.
- Why composition is treated as the highest-priority section.
- The prompt-length and redundancy decision.
- The version bump.
- Why this remains a deterministic positive natural-language prompt.

## Hard constraints

Do not weaken any of the following:

- One positive natural-language image prompt only.
- No separate negative prompt.
- No JSON image prompt.
- No hard-coded model identifier inside the builder.
- No provider call from the builder.
- No raw questionnaire free text in the prompt.
- No prompt content derived from inspiration-image bytes or provider metadata.
- No reference-image or image-to-image conditioning.
- Do not change `black-forest-labs/flux-1.1-pro`.
- Do not modify, regenerate, delete, stage or reformat anything under:

```text
experiments/model-eval/outputs/
```

- Do not modify Phase 16’s operator gates, cost ceilings, Redis controls, privacy rules or live-generation security behaviour.
- Do not trigger paid provider calls while implementing or testing this phase.

## Automated tests

Add or update tests covering:

### 1. Composition comes first

- The composition section is the first non-whitespace content.
- It cannot drift behind garment-detail sections.

### 2. Required framing semantics

The prompt must express:

- Exactly one model.
- Full-body framing.
- Complete garment and trailing fabric visible.
- Neutral studio backdrop.
- Even studio lighting.
- Garment-focused catalogue presentation.

### 3. Determinism

- The same canonical specification produces exactly the same prompt.

### 4. Golden fixtures

- The reviewed fixture manifest and combined hash match the new builder version.

### 5. Length handling

- The existing maximum-character bound remains enforced.
- The composition section survives every deterministic length-management path.

### 6. Existing invariants

- No negative-prompt field.
- No provider or model identifier.
- No raw free text.
- No provider metadata.
- All existing prompt-builder tests continue to pass.

Prefer testing the ordering through a shared composition constant or section boundary rather than duplicating a long, fragile literal throughout the test suite. The golden fixtures should remain the exact-output regression protection.

## Commands and checks

Run from the appropriate repository directories:

```bash
pytest sitara/generation/tests/test_prompt_builder.py
```

Run the broader generation test suite and any tests covering persisted prompt/version behaviour.

Also run:

```bash
ruff check .
ruff format --check .
python manage.py check
```

Run the documented golden-manifest regeneration command and inspect the diff before accepting it.

The frontend should remain unchanged. Run `npm run build` only if this phase legitimately changes user-facing result copy.

## Operator-run live validation

Claude Code must **not** initiate paid generations.

Prepare a documented, operator-run validation matrix using non-user, non-private test specifications. It should cover at least:

- Saree.
- Lehenga.
- Sharara or gharara.
- Anarkali or kurta-style outfit.
- More than one coverage level.
- More than one dupatta treatment.

Before any paid run, report:

- Proposed number of images.
- Estimated provider cost.
- Applicable daily and per-user cost ceilings.
- Exact operator command or UI flow.
- Where validation results should be recorded.

Use the following rubric for every output:

| Criterion | Pass condition |
|---|---|
| Subject count | Exactly one model |
| Full-body framing | Top of head and both feet visible |
| Complete garment | Hem, lower garment, dupatta and trailing fabric remain inside the frame |
| Pose | Upright, centred and primarily front-facing |
| Background | Plain neutral studio backdrop without an environmental scene |
| Lighting | Soft and even enough to inspect garment details |
| Visual priority | Garment is more prominent than face, jewellery or setting |
| Semantic fidelity | Garment type, construction, coverage, colour and key embellishment remain faithful to the specification |

Recommend a small bounded run, such as six outputs, but do not spend anything without explicit operator approval.

Record the result of each criterion rather than relying only on a general visual impression. Document any garment-type-specific failure.

## Privacy and artefact handling

Generated designs are private by default.

- Do not commit live generated images to the repository.
- Do not upload private user designs to a public PR.
- Use synthetic, non-user test specifications for validation.
- Add approved private artefact links or manually attached screenshots to the PR only when repository visibility and image rights permit it.
- Otherwise, include a results table and placeholders for operator-supplied before/after evidence.

The Phase 2 images remain read-only visual references.

## Acceptance criteria

The phase is complete when:

- The production prompt begins with the reviewed composition directive.
- Garment-detail sections follow the documented priority order.
- Prompt wording has been made materially more concise without losing canonical design requirements.
- The builder version has been bumped.
- Golden fixtures have been deliberately regenerated and reviewed.
- All required tests and checks pass.
- ADR 0010 has been amended.
- No frozen evaluation files have changed.
- No Phase 16 cost or security controls have changed.
- No paid generation has been triggered by Claude Code.
- An operator-run validation plan and scoring rubric are included.
- The PR clearly separates deterministic code acceptance from stochastic live-image observations.

## Deliverable

Create a focused branch and PR containing only:

- The restructured deterministic prompt builder.
- The `PROMPT_BUILDER_VERSION` bump.
- Reviewed golden snapshot or manifest changes.
- New or updated tests.
- The ADR 0010 amendment.
- Operator-run validation instructions.
- A PR results table for approved before/after evidence.

In the completion report include:

- Files changed.
- Exact builder version change.
- Before-and-after prompt ordering.
- Representative before-and-after prompt lengths.
- Tests and commands run.
- Golden-manifest diff summary.
- Confirmation that frozen experiment outputs were untouched.
- Confirmation that no paid generation occurred.
- Remaining risks or observations requiring operator validation.