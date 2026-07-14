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

Execution-safety note: the framework's resume is crash-safe after a
provider prediction id has been persisted locally; around the
provider-acceptance boundary itself, duplicate prevention is best-effort
rather than exactly-once, because Replicate exposes no idempotency
mechanism for prediction creation. Budgets are enforced conservatively
(reserve-before-call; unresolved billing formulas account the full
reservation), so run ledgers may overstate — never understate — spend.
Actual charges must be confirmed on the Replicate billing page.

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

### Diagnostic run 2026-07-13 (NOT usable for selection)

`experiments/model-eval/outputs/runs/screening-20260713-001` is an
**incomplete diagnostic run** and **must not be used to select finalists**:
the account ran out of Replicate credit mid-run (32 of 42 attempted requests
failed with HTTP 402 "Insufficient credit"; only flux-1.1-pro and flux-2-pro
produced 5 outputs each before credit ran out, and 18 of 60 planned requests
were never attempted), and the run then crashed on a Windows
`PermissionError` while persisting the budget ledger. The run is preserved
as evidence; the framework has since been hardened (402/401 run-level halts,
per-model circuit breaking, Windows ledger recovery, genuinely blind
artefacts, and an incomplete-run review refusal that rejects exactly this
run). Screening must be restarted from scratch — after the candidate schema
smoke (`configs/candidate_smoke.yaml`, 5 requests, $0.51 ceiling) passes
5/5 — with sufficient account credit (the screening ceiling is $6.12).

### Diagnostic run 2026-07-14 (candidate smoke — diagnostic only)

`experiments/model-eval/outputs/runs/candidate-smoke-20260714-001` is
**diagnostic only**. Three of five candidates succeeded (schnell, klein-4b,
flux-1.1-pro — confirming their input schemas are accepted); flux-2-pro and
flux-2-max received HTTP 429 throttles ("rate limit … reduced to 6 requests
per minute with a burst of 1 … while you have less than $5.0 in credit")
**before any prediction was created**. Those two 429s were produced before
the rate-limit fix and were conservatively misclassified as
`ambiguous_provider_failure`, so the ledger assumed their full reservations
($0.12 + $0.25) as spend even though Replicate accepted nothing — its spend
totals overstate reality by $0.37. The run is preserved as evidence. The
framework now classifies creation-throttles as pre-acceptance, retries them
with the provider's hint, and halts/resumes safely when they persist.
Re-run the candidate smoke (ideally with more than $5 account credit, which
avoids the reduced low-balance limits) before the full screening.

### Screening run 2026-07-14 (screening-20260714-001) and retry policy

The full 60-request screening completed without halting: **55 first-attempt
successes and 5 accepted predictions that died inside Replicate's
infrastructure** — Schnell 4/12 first-attempt failures (`Director:
unexpected error handling prediction (E9828)`), FLUX.2 Pro 1/12
(`Prediction interrupted; please retry (code: PA)`). These first-attempt
failure rates are retained as reliability evidence and feed the model
decision alongside visual scores: the original failure records and their
conservatively settled ledger entries are never modified. Targeted retries
(allowlisted transient errors only, identical inputs, separate auditable
attempt records and ledger keys) exist solely to recover the missing
comparison images so every logical cell can be scored; a recovered retry
does not erase the original failure. Blind reviewers never see retry
status — visual scoring and operational reliability are assessed
separately, via the blind artefacts and the non-blind `reliability-report`
respectively (the latter examined only after visual scoring).

### Schnell operationally disqualified (2026-07-14, before blind visual scoring)

FLUX Schnell reached the screening but is **operationally disqualified from
the blind visual-selection stage** — a reliability decision, not a visual
one. Across its 12 planned logical cells it produced 8 first-attempt
successes and 4 first-attempt failures (66.7% first-attempt success rate),
all failures being Replicate-side `Director: unexpected error handling
prediction (E9828)` runtime faults. Targeted retries recovered only 2 of
the 4 cells (retry-1: 4 attempts, 1 success; retry-2: 3 attempts, 1
success), leaving `scr-gharara-nikah-heavy` and `scr-sharara-mehndi-moderate`
unresolved after the screening's stop-after-retry-2 limit — 10/12 cells
with output from 19 provider attempts (10 successful, 9 failed). Its very
low generation cost ($0.003/image) does not compensate for an operational
reliability that cannot dependably produce a complete evaluation set, let
alone serve production traffic. No conclusion about Schnell's visual
quality is drawn, and the evaluation record honestly retains that it was
planned, run and disqualified. The remaining blind visual screening
compares FOUR candidates on a balanced 4 x 12 = 48-image matrix: klein-4b,
flux-1.1-pro, flux-2-pro, flux-2-max — **Klein 4B remains the fast-model
candidate.** The formal scope and disposition live in the run's
`review_scope.json` / `review_scope_report.md`.

## Decision

_To be completed after scoring._

- **Default production model:** NOT YET SELECTED
- **Fast/demo-development model:** NOT YET SELECTED (Klein 4B is the
  remaining fast-tier candidate after Schnell's operational
  disqualification)
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
