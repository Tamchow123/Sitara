# 0001 — Production and fast-tier image model selection

- **Status:** accepted
- **Date:** 2026-07-16 (decided; candidates identified 2026-07-13, screening run and blind-scored 2026-07-14/15)
- **Deciders:** project owner (blind human scoring is authoritative)
- **Phase:** Phase 2 (see ../PHASES.md)

> **Decision: `black-forest-labs/flux-1.1-pro`** is Sitara's default MVP
> production image model AND its paid fast/development model. Demo mode
> makes **zero paid model calls** and serves pre-generated private fixtures
> only. Application model selection remains configured through environment
> variables — never hard-coded.

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

Blind visual scoring of the balanced 4 × 12 scoped matrix (locked scoring
sheet SHA-256
`ac0355030709c192f56371e1780628870dfe001a4c72ce1693961c4b9842dec7`;
unblinded via the protected mapping) produced:

| Model | Pooled mean (1–5) | Hard-failure flags |
|---|---|---|
| flux-2-pro | 4.2917 | 1 |
| flux-2-max | 4.2833 | 0 |
| flux-1-1-pro | 4.2667 | 0 |
| klein-4b | 3.9333 | 4 |

- **Default MVP production model:** `black-forest-labs/flux-1.1-pro`
- **Paid fast/development model:** `black-forest-labs/flux-1.1-pro` (one
  model for both roles in the MVP)
- **Demo mode:** no paid model call — pre-generated private fixtures only
- **Retained challenger (not the default):** `black-forest-labs/flux-2-pro`
- **Retained research / potential future premium benchmark:**
  `black-forest-labs/flux-2-max`
- **Rejected for MVP use:** `black-forest-labs/flux-2-klein-4b` (visual
  hard failures — see below)
- **Operationally disqualified:** `black-forest-labs/flux-schnell` (see the
  2026-07-14 section above)
- **Inspiration influence / constrained refinement:** NO CONCLUSION YET —
  those evaluation stages have not been run; the MVP text-only limitation
  stands on its original rationale, not on new evidence.

**Rationale — utility winner, not the absolute numerical visual winner.**
FLUX 1.1 Pro's pooled mean sits 0.0250 below FLUX.2 Pro and 0.0166 below
FLUX.2 Max — differences with no meaning in a single-rater, 12-brief
screening — while it alone combines perfect 12/12 first-attempt reliability,
zero hard failures, ~4.8 s median successful latency (5.2 s max), the lowest
cost of the viable candidates ($0.040/generation observed, $0.480 across the
screening), strong cultural coherence, and strong bridal-occasion
distinctiveness. Compared with FLUX.2 Max it is ~5.9× faster by median
latency and ~6.25× cheaper per screening generation; compared with FLUX.2
Pro it is ~2.4× faster and ~3× cheaper per configured screening attempt
(and FLUX.2 Pro also carried one hard-failure flag and one transient
first-attempt failure recovered on retry-1).

**Klein 4B is NOT the fast model.** Its 0.5-second median-latency advantage
(4.3 s vs 4.8 s) does not justify: the lowest visual score (3.9333), four
hard-failure flags including garment silhouette substitutions, slightly
higher observed screening cost ($0.050 vs $0.040 per cell), and an
anomalous 502.9-second maximum successful latency.

**Configuration, not coupling.** The chosen model is a configuration
default behind the application's provider/model abstraction — the backend
(Phase 3+) must support environment-based override and never hard-code
Replicate model ids in application logic:

```text
DEFAULT_IMAGE_MODEL=black-forest-labs/flux-1.1-pro
FAST_IMAGE_MODEL=black-forest-labs/flux-1.1-pro
DEMO_MODE=true
```

The CANONICAL machine-readable decision is version-controlled alongside
this record at `docs/decisions/0001-image-model.json` (validated on every
checkout by always-running tests; it references the locked score hash and a
repository-relative evidence manifest with expected hashes). The gitignored
run-local file
`experiments/model-eval/outputs/runs/screening-20260714-001/model_decision.json`
is an evidence-run mirror only — when the run is present locally it must
agree with the canonical JSON on every decision-critical field, but it is
never the source of truth on a fresh clone or in CI.

## Limitations (decision is an MVP baseline, revisable)

This screening involved: one visual evaluator; one seed per cell; 12
text-only briefs; base generation only; no direct inspiration-image
(reference-conditioning) evaluation; no refinement evaluation; and no real
bridal customer or cultural-expert panel. The decision is appropriate for
the MVP baseline and must remain revisable as later stages produce
evidence.

## Next evaluation stages

1. FLUX 1.1 Pro prompt-hardening for minimal/moderate embroidery briefs.
2. Rights-approved inspiration influence testing (text-only vs metadata vs
   reference image).
3. One constrained refinement test (fresh regeneration vs image editing).
4. Privacy and image-retention verification against current provider terms.
5. Small human / cultural-expert validation set.
6. Production integration and monitoring thresholds (latency, failure rate,
   spend).

## Consequences

The MVP builds on a single, fast, cheap, operationally reliable model for
both production and paid development use, simplifying budgeting
(~$0.04/generation) and latency expectations (Full generation UX can
honestly promise seconds, not half-minutes). FLUX.2 Pro remains available
as a challenger if 1.1 Pro's visual ceiling proves limiting, and FLUX.2 Max
as a premium benchmark for future tiers. Refinement-continuity and
inspiration-influence promises remain UNDECIDED until their stages run —
nothing in the application may assume them yet.

## Alternatives considered

- **flux-2-pro** — numerically best pooled mean, but one hard-failure flag,
  one transient first-attempt failure, ~2.4× slower and ~3× costlier;
  retained as challenger.
- **flux-2-max** — clean and highest-fidelity, but ~5.9× slower and ~6.25×
  costlier; retained as research/premium benchmark.
- **klein-4b** — rejected for MVP: lowest score, four hard failures
  (including garment silhouette substitutions), latency outlier, no real
  cost advantage.
- **schnell** — operationally disqualified before visual scoring (repeated
  E9828 runtime failures; two cells unresolved after retry-2).

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
- **Experiment commit hash:** results produced across commits up to
  `163a590d40762fc665685bea5bc5f3736557c17e` (the commit current when the
  decision was recorded; each result record carries the exact commit that
  produced it).
- **Total experiment spend (conservative, ledger-accounted):** $7.02 —
  screening-20260713-001 diagnostic $0.80 (overstated by $0.37 by the
  pre-fix 429 misclassification), candidate-smoke-20260714-001 $0.46
  (overstated by $0.37 for the same reason), screening-20260714-001 $5.76
  (including $0.176 retry spend).
- **Output and scoring artefact locations:** run
  `experiments/model-eval/outputs/runs/screening-20260714-001/` — locked
  blind scores `blind-scoped/scoring_sheet_locked.csv` (SHA-256
  `ac0355030709c192f56371e1780628870dfe001a4c72ce1693961c4b9842dec7`),
  protected mapping `candidate_key_scoped.json`, scope
  `review_scope.json` + `review_scope_report.md`, operational
  `reliability_report.md`, evidence-mirror `model_decision.json`. Canonical
  machine-readable decision: `docs/decisions/0001-image-model.json`
  (committed).
