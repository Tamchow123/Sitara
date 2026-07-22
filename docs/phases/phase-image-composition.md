# Phase — Generated-image composition and framing

Proposed as its own phase, to run AFTER Phase 16 (live-generation security and
cost controls) is merged. It is a self-contained image-prompt-builder tuning
phase, independent of Phase 16's security/cost work and of Phase 17 (UI polish).

## Why

The first real live generation (2026-07-22) proved the full paid pipeline works
end to end, but the FLUX-1.1-pro output is a cropped, 3/4 "beauty editorial"
image on a busy/ornate background — not the intended **full-length, head-to-toe,
single model standing, plain neutral studio background** catalogue framing shown
in the Phase 2 model-eval references
(`experiments/model-eval/outputs/runs/screening-20260714-001/`).

Diagnosis (already done — not a bug, not a model limitation):
* `build_image_prompt` (`generation/prompt_builder.py`, `PROMPT_BUILDER_VERSION 3.0.0`)
  front-loads ~600 words of garment/drape/embellishment detail and places the
  composition directive ("Present the concept as a full-length studio fashion
  photograph … clean, uncluttered studio background …") as the **last** line, so
  the model underweights it.
* Ruled out: `aspect_ratio="3:4"` is correct (references are the same ratio);
  `prompt_upsampling=False` (FLUX is not rewriting the prompt). The eval proves
  the model can produce full-length / plain-background output. It is purely a
  prompt structure/emphasis problem.

## Objective

Make the deterministic image prompt reliably yield the intended catalogue
framing — full-length, entire garment head to hem, one model standing facing the
camera, plain neutral studio background, soft even lighting — without changing
the garment semantics the prompt already encodes.

## Scope

* Restructure `build_image_prompt` so the composition/framing directive **leads**
  the prompt (e.g. opens with "Full-length studio fashion photograph, one model
  standing facing the camera, entire garment visible head to hem, plain
  uncluttered neutral studio background, soft even lighting."), then the garment
  detail; likely trim the prompt's overall length for emphasis.
* Bump `PROMPT_BUILDER_VERSION` and regenerate + review the golden
  snapshot/manifest of prompt fixtures (the prompt-builder fixture test asserts a
  combined hash — it must be deliberately updated with the reviewed change).
* Validate against the eval references with a small number of budgeted live
  generations across a few garment types (saree, lehenga, sharara/gharara,
  kurta-style) and coverage levels; compare framing/background/pose to the
  references.
* Update ADR 0010 (deterministic image-prompt builder) with the framing decision
  and the version bump rationale.

## Non-goals (hard constraints — do not weaken)

* No negative prompt, no JSON prompt, no hard-coded model id, no provider call
  from the builder — one positive natural-language prompt only (CLAUDE.md §7,
  ADR 0010). Persisted `image_prompt`/`prompt_builder_version` stay immutable
  audit data; a builder change produces NEW `DesignVersion`s, never rewrites old.
* Do NOT change the selected image model (`black-forest-labs/flux-1.1-pro`)
  without a scoped, documented evaluation (CLAUDE.md §7).
* Do NOT introduce reference-image / image-to-image conditioning — it stays
  fail-closed and is a separately approved phase (ADR 0014).
* Do NOT modify, regenerate, delete, stage or reformat anything under
  `experiments/model-eval/outputs/` — Phase 2 evidence is frozen (CLAUDE.md §21).
  The eval images are read-only visual references only.
* No prompt content derived from raw questionnaire free text, inspiration image
  bytes, or provider metadata (the existing builder invariants hold).

## Commands / checks

* Inside `api`: `pytest sitara/generation/tests/test_prompt_builder.py` and the
  broader generation suite; `ruff check .` / `ruff format --check .`;
  `manage.py check`. Regenerate the golden manifest via the documented
  builder/manifest command and commit the reviewed diff.
* Frontend: unaffected, but run `npm run build` if any result-view copy changes.

## Automated tests

* Prompt-builder determinism + golden-manifest hash match at the new version.
* A test asserting the composition/framing directive is present AND positioned at
  the START of the built prompt (guards against it drifting back to the end).
* Existing invariants still hold: max-chars bound, no negative prompt, no
  model id, deterministic (same spec → same prompt).

## Manual checkpoint (budgeted, operator-run)

With live generation enabled and a small daily budget, generate a handful of
concepts across garment types and compare to the eval references: full-length,
entire garment visible, single standing model, plain neutral background, even
lighting, faithful garment construction and coverage. Record before/after
examples. Keep spend within the configured ceiling.

## Deliverable

A focused branch/PR: restructured `build_image_prompt`, `PROMPT_BUILDER_VERSION`
bump, reviewed golden snapshot/manifest, new/updated tests, and an ADR 0010
amendment — with before/after sample images in the PR body. Live generation stays
operator-gated exactly as Phase 16 left it.
