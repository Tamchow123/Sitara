# 0001 — Production and fast-tier image model selection

- **Status:** proposed
- **Date:** 2026-07-13 (candidates identified; evaluation not yet run)
- **Deciders:** project owner (human scoring is authoritative)
- **Phase:** Phase 2 (see ../PHASES.md)

> **No production model has been selected.** This record is a placeholder
> that will be completed only after both evaluation stages have been run and
> blind-scored by a human. Application model selection will be configured
> through environment variables (`REPLICATE_IMAGE_MODEL`, optional
> `REPLICATE_IMAGE_MODEL_FAST`) — never hard-coded.

## Context

Sitara needs a default production text-to-image model, a lower-cost
fast/demo tier, an evidence-based answer on inspiration influence
(text-only vs curated metadata vs actual reference-image conditioning), and
a refinement strategy (fresh regeneration vs image editing). The evaluation
framework lives in `experiments/model-eval/`.

### Candidates identified from the live catalogue (verified 2026-07-13)

Full capability, pricing and terms detail (with sources) lives in
`experiments/model-eval/configs/model_candidates.yaml` and
`experiments/model-eval/TERMS_SNAPSHOT.md`. Official BFL models on
Replicate are versionless; embedded latest-version ids are recorded in the
candidates file for provenance. **These facts are time-sensitive — re-verify
before any live run.**

| Category | Candidate | Replicate ID | ~Cost/gen (2026-07-13) |
|---|---|---|---|
| fast | FLUX Schnell | `black-forest-labs/flux-schnell` | $0.003 |
| fast + reference + editing | FLUX.2 klein 4B | `black-forest-labs/flux-2-klein-4b` | advertised ~$0.001/MP — anomalous, reserved at $0.05 |
| balanced (FLUX.1-era baseline) | FLUX 1.1 Pro | `black-forest-labs/flux-1.1-pro` | $0.04 |
| balanced + reference + editing | FLUX.2 Pro | `black-forest-labs/flux-2-pro` | ~$0.03 |
| highest quality + reference + editing | FLUX.2 Max | `black-forest-labs/flux-2-max` | ~$0.07 |

The two fast-tier candidates (Schnell, klein 4B) are compared head-to-head
in screening; neither replaces the other before scored evidence exists.
klein 4B's advertised price is 15× below its 9B sibling with asymmetric
units, so it runs under a generous conservative reservation and its billing
is flagged for verification against a real billed prediction.

Considered and removed: `flux-kontext-pro` — BFL labels the Kontext family
previous-generation and points editing users at FLUX.2 pro/flex, and FLUX.2
Pro/Max already cover reference conditioning and editing in this evaluation;
it can return later as an editor-only experiment (a candidate with
`text_to_image: false` plus a separately specified base model) if FLUX.2
editing disappoints. Also not selected: `flux-pro` ($0.055), superseded by
`flux-1.1-pro` ($0.04).

## Decision

_To be completed after scoring._

- **Default production model:** NOT YET SELECTED
- **Fast/demo-development model:** NOT YET SELECTED
- **Inspiration influence recommendation** (text-only / metadata /
  reference-image; whether the MVP text-only limitation should remain):
  NOT YET DETERMINED
- **Refinement strategy recommendation** (fresh regeneration /
  image editing): NOT YET DETERMINED
- **Best prompt format per selected model:** NOT YET DETERMINED

## Consequences

_To be completed after scoring._ Must cover: observed refinement drift and
what the refinement UX can honestly promise; cultural hard-failure rates per
model; cost implications at expected MVP volume; any terms issues that
constrain deployment.

## Alternatives considered

_To be completed after scoring — screening losers and why, plus the
runner-up finalist._

## Model-evaluation fields

- **Exact identifiers and versions tested:** see table above and
  `configs/model_candidates.yaml` (embedded latest-version ids recorded per
  candidate; actual served version captured per result record).
- **Pricing verification date:** 2026-07-13 (sources in the candidates file).
- **Provider terms verification date:** 2026-07-13. Unresolved items
  requiring human review before selection: Replicate's prediction
  input/output retention window is unpublished; whether BFL's
  train-and-improve licence covers Replicate-routed traffic; FLUX.2
  per-run + per-MP billing formula unconfirmed against a real bill; JSON
  prompting verified for FLUX.2 generally but not per-variant.
- **API input schema:** per-candidate `capabilities` in
  `configs/model_candidates.yaml`, extracted from each model page's embedded
  OpenAPI schema on 2026-07-13.
- **Experiment commit hash:** _record when the live runs execute (also
  stamped into every result record)._
- **Total experiment spend:** _from `budget_ledger.json` per run — pending._
- **Output and scoring artefact locations:** _run IDs and completed scoring
  sheets — pending._
