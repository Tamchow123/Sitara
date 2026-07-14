# Sitara Phase 2 — image-model feasibility evaluation

A standalone, budget-controlled experiment that decides which FLUX models
Sitara should use — **before** any application code is built. It is not part
of the future Django/Next.js application and imports none of it.

## What this evaluation decides

1. The default production text-to-image model.
2. The lower-cost fast/demo-development model.
3. Whether inspiration should influence generation via text-only briefs,
   curated metadata, or actual reference-image conditioning.
4. Whether refinement should use prompt-modification + fresh regeneration or
   image-editing / conditioned refinement.
5. Which prompt format (editorial, sectioned, JSON where officially
   supported) works best per candidate model.
6. Whether the models represent South Asian bridal garments, modesty
   requirements and ceremony-specific styling accurately.

**No model has been selected yet.** The candidates in
`configs/model_candidates.yaml` were verified against live official pages on
2026-07-13 and carry that date; selection happens only after human scoring,
and is recorded in `../../docs/decisions/0001-image-model.md`. The
application will read its model choice from environment variables — nothing
here hard-codes a production model.

> **Time-sensitive facts.** Provider pricing, capabilities and terms change.
> Re-check the official Replicate/Black Forest Labs pages (URLs are in the
> candidates file and `TERMS_SNAPSHOT.md`) immediately before any live run,
> and update `checked_on` / `verified_on` dates when you do.

## Setup

```bash
cd experiments/model-eval
py -3.12 -m venv .venv          # or: python3.12 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows
# .venv/bin/python -m pip install -e ".[dev]"     # POSIX
```

Use `.venv/Scripts/python` (Windows) or `.venv/bin/python` (POSIX) for every
command below, or activate the venv first.

## Verifying the implementation costs nothing

**No paid call is ever required to verify this framework.** The test suite
mocks every provider interaction and proves the gates hold:

```bash
python -m pytest
```

## The two-stage process

**Stage A — screening** (`configs/screening.yaml`): every candidate model
runs the 12 screening briefs once (one seed, editorial prompt, text-only).
Purpose: cheaply eliminate clearly unsuitable models. Human review of the
blind contact sheet picks the top two.

**Stage B — finalists** (`configs/finalists.yaml`): edit `models:` to the
two screening winners, then run the full 30-brief matrix with the three
inspiration modes, both refinement strategies, and every prompt format each
model supports. A companion round, `configs/seed_stability.yaml`, runs three
seeds over a reduced representative brief subset to measure per-seed
variance (finalists.yaml itself is single-seed by design). Nothing
auto-selects a winner — human scoring is authoritative, and hard cultural
failures (e.g. a gharara rendered as a sharara, ignored coverage
requirements) disqualify regardless of prettiness. Briefs whose cultural
framing needs owner review carry a `cultural_review` note in
`prompts/briefs.yaml`; treat their scores as provisional until reviewed.

## Planning and dry runs (zero cost, zero network)

```bash
python -m model_eval.cli inspect                       # show candidates + facts
python -m model_eval.cli plan --config configs/screening.yaml --budget-usd 10
python -m model_eval.cli run --config configs/screening.yaml --dry-run --budget-usd 10
```

`plan` and `--dry-run` print the planned request count, models, prompt
formats, inspiration modes, refinement experiments, visible skips (including
editing-only model rejections and unverified-reference exclusions, which are
validated against the manifest at plan time and excluded from counts and
spend), preflight warnings, and the **conservative maximum spend** — a
deliberately pessimistic ceiling built from each candidate's
`max_cost_per_generation_usd`. Dry runs make zero network calls and create
no artefacts.

**Cost accounting is deliberately conservative.** Where a billing formula is
verified (`formula_verified: true`, flat per-image models), successful runs
reconcile to an input-aware calculated cost. Where it is unresolved (the
FLUX.2 per-run + per-MP formula, klein-4b's anomalous advertised price),
successful runs are accounted at the FULL reserved amount — the ledger may
overcount but can never undercount. Each result records its `cost_basis`
(`calculated` or `reserved_conservative`); nothing is presented as a
provider-reported charge, because Replicate does not report one per
prediction.

## Deliberately enabling live calls

A live run refuses to start unless **all four** gates are present:

```bash
ALLOW_PROVIDER_CALLS=true \
REPLICATE_API_TOKEN=... \
python -m model_eval.cli run \
  --config configs/screening.yaml \
  --budget-usd 10 \
  --confirm-live
```

(On Windows PowerShell: `$env:ALLOW_PROVIDER_CALLS="true"; $env:REPLICATE_API_TOKEN="..."` first.)

Missing any of the four → the provider adapter is never constructed. Review
the current official provider terms before your first live run; see
`TERMS_SNAPSHOT.md` for what was recorded and what remains unresolved.

## The smoke test (run this before the full screening)

`configs/smoke.yaml` plans exactly **one** provider request (flux-2-pro,
one modest nikah brief, seed 11, editorial prompt, text-only, 3:4) with a
conservative ceiling of **$0.12**. Dry-run it first (PowerShell):

```powershell
.venv\Scripts\python -m model_eval.cli run `
  --config configs/smoke.yaml `
  --dry-run `
  --budget-usd 0.12
```

Confirm it reports exactly 1 planned request, 0 skips, and a 0.12 USD
conservative maximum. Then run it live, deliberately:

```powershell
$env:ALLOW_PROVIDER_CALLS="true"
$env:REPLICATE_API_TOKEN="<your token>"

.venv\Scripts\python -m model_eval.cli run `
  --config configs/smoke.yaml `
  --budget-usd 0.12 `
  --confirm-live `
  --run-id smoke-flux2-pro-001
```

Afterwards, check the ledger and clear the environment:

```powershell
.venv\Scripts\python -m model_eval.cli budget-status `
  --run-id smoke-flux2-pro-001

Remove-Item Env:REPLICATE_API_TOKEN
Remove-Item Env:ALLOW_PROVIDER_CALLS
```

Because flux-2-pro's billing formula is unresolved, the ledger will
conservatively account the **full $0.12 reservation** even though the real
charge is expected to be ~$0.03. **Check the actual Replicate billing page
manually after the smoke test** — comparing the real charge against the
recorded reservation is also how the FLUX.2 billing formula gets verified
(and `formula_verified` can then be flipped in the candidates file).

### Candidate schema smoke (required before the full screening)

After the single-request smoke, `configs/candidate_smoke.yaml` runs ONE
representative brief across **all five** screening candidates — exactly 5
requests, $0.51 conservative ceiling — to verify every candidate's input
configuration, credit and auth before committing to the 60-request round:

```powershell
.venv\Scripts\python -m model_eval.cli run `
  --config configs/candidate_smoke.yaml `
  --dry-run `
  --budget-usd 0.51
```

**The candidate smoke must pass (5/5 succeeded) before restarting the full
screening.** A deterministic rejection here identifies a broken model
configuration at 1/12th of the screening cost.

## Failure handling and recovery

- **402 insufficient credit / 401 authentication:** the run HALTS
  immediately after the first such rejection — no further provider calls,
  the reservation is released (the request was rejected before acceptance),
  a failed record with `provider_insufficient_credit` /
  `provider_authentication_failed` is saved, and the CLI prints a concise
  halt message (no traceback). Fix credit/token, then rerun with the SAME
  run id: completed requests are never re-sent or re-charged, the halted
  request itself is retried automatically (it is safe — nothing was
  accepted and nothing was spent), and the run continues from where it
  stopped. On success the superseded failure record is replaced, so the
  final run summary and completeness check reflect the true final state.
- **Deterministic rejections (400/404/422):** the same invalid model
  configuration would fail on every brief, so after the first such
  rejection the model is DISABLED for the rest of the run: its remaining
  requests become visible `model_disabled_after_provider_rejection` skips,
  other models keep running, and the disablement is reported in the run
  summary. Fix the candidate's input configuration, then rerun.
- **Windows ledger persistence:** every ledger write goes to a `.tmp` file
  (flushed + fsynced) and is promoted with bounded retries for transient
  access-denied/sharing-violation errors (antivirus/indexers). If all
  retries fail, the run stops with a clear message and the **valid `.tmp`
  is preserved** — on the next open of that run's ledger it is validated
  (same budget, strict forward progression of the main state) and recovered
  automatically; malformed or regressive temp files are quarantined with a
  report and never promoted. No double-spend, no resurrected reservations.

## Incomplete runs are not reviewable

`contact-sheet` and `scoring-sheet` REFUSE runs that are incomplete: any
failed requests, fewer successes than planned, unresolved reservations, an
early halt, or a missing run summary (interruption). This prevents a partial
run from quietly becoming the basis for model selection. For debugging only,
`--allow-partial` generates artefacts that are prominently watermarked
**PARTIAL / NOT VALID FOR MODEL SELECTION** (and remain fully blind).

## How the budget ledger works

Per run, `outputs/runs/<run-id>/budget_ledger.json` enforces
reserve-before-spend:

1. Before each provider call, the candidate's conservative max cost is
   atomically reserved; if the reservation would exceed `--budget-usd`, the
   run halts and the call never happens.
2. After success the reservation is reconciled down to the best available
   cost estimate; failures rejected before provider acceptance release it;
   ambiguous failures conservatively count the full reservation as spent.
3. Every mutation is persisted immediately (atomic file replace + process
   lock), so a crashed run cannot restart with a reset budget.

Check it any time: `python -m model_eval.cli budget-status --run-id <run-id>`.

This is an experiment-level control; the production application will use an
atomic Redis mechanism instead (docs/PHASES.md, Phase 16).

## Resuming an interrupted run

Re-run the same command with the same run id and budget:

```bash
python -m model_eval.cli run --config configs/screening.yaml \
  --budget-usd 10 --confirm-live --run-id screening-20260713T120000Z
```

Resume is crash-safe **after the prediction id has been persisted**: an
attempt record is written before each submission and the provider prediction
id immediately after acceptance, so a request whose id is on disk is resumed
by POLLING that prediction rather than submitting a duplicate.
Already-downloaded outputs are reused, completed/skipped requests are never
re-sent, and settled ledger entries are never charged twice.

Around the provider-acceptance boundary itself, duplicate prevention is
**best-effort, not a strict exactly-once guarantee**: there is an
unavoidable small window between Replicate accepting a request and the
prediction id being written locally, and Replicate provides no idempotency
mechanism this implementation could use to close it. A crash inside that
window can produce one duplicate submission on resume; the budget stays
conservative regardless.

Failed requests are final for their run (their spend is already accounted),
with one exception: **402 insufficient-credit and 401 authentication
failures are retried automatically** when you rerun with the same run id,
because they were rejected before provider acceptance — their reservation
was released, no prediction exists, and no spend occurred. Every other
failure category stays final; to retry one deliberately, delete its result
record and use a new run id. A stale lock file left by a crashed process is
reclaimed automatically when its PID is no longer alive; if the PID cannot
be read, delete `budget_ledger.json.lock` manually after confirming no
other run is active.

## Reference images (rights-controlled)

See `references/README.md`. In short: list every reference in
`references/manifest.yaml` with verified rights; the runner rejects anything
whose `rights_status` is not `verified` (recorded as a skip, no spend, no
call). Reference-mode requests appear in the plan but are skipped at run
time until their manifest entries are verified. Never scrape images; never
commit an image whose licence does not explicitly allow it.

## Contact sheets and scoring

```bash
python -m model_eval.cli contact-sheet --run-id <run-id>            # blind, by model
python -m model_eval.cli contact-sheet --run-id <run-id> --by mode  # or format / refinement
python -m model_eval.cli scoring-sheet --run-id <run-id>
```

Blind artefacts are genuinely blind: every successful output is copied into
`blind/` under an anonymised name (`image-001.webp`, hash-ordered so the
numbering leaks nothing), and the blind HTML and CSV contain **no** model
keys, Replicate IDs, request IDs or original filenames anywhere — captions
show only Candidate A/B/... codes and `item-NNN` ids. The reverse mapping
lives solely in `candidate_key.json`; don't open it until scoring is done
(`--reveal` regenerates a non-blind sheet for afterwards).

The scoring CSV has 1–5 rubric columns (garment accuracy, cultural
coherence, fabric realism, embroidery, dupatta styling, anatomy, prompt
adherence, modesty/coverage, **bridal-occasion distinctiveness**, reference
influence, refinement consistency, overall quality), yes/no hard-failure
columns, and a reviewer-notes column. Bridal distinctiveness matters:
Sitara generates bridalwear, so a structurally coherent, beautiful outfit
that reads as ordinary formalwear scores low — and in the extreme fails
`hf_reads_as_non_bridal_everydaywear`. There is no automated aesthetic
scoring and no LLM judge.

## Filling in the decision record

After scoring both stages, complete `../../docs/decisions/0001-image-model.md`:
chosen default + fast models (exact identifiers and versions tested),
pricing/terms verification dates, total spend (from the budget ledgers),
refinement-drift findings, inspiration-mode findings, scoring artefact
locations, and the experiment git commit hash (recorded in every result
record). Application model selection then goes into environment variables —
never into code.

## Layout

```
configs/    model candidates (verified facts) + stage configs
            (smoke / screening / finalists / seed_stability)
prompts/    briefs.yaml — the garment/ceremony brief matrix
references/ rights-controlled reference manifest (+ local/, gitignored)
src/        model_eval package (config, matrix, formats, costs, budget,
            runner, ...)
tests/      pytest suite — fully mocked, proves the zero-spend gates
outputs/    run artefacts (gitignored): results, images, ledgers, sheets
```

Security note: output images are downloaded with a separate, UNAUTHENTICATED
HTTP client — the Replicate API token is only ever sent to the Replicate API
itself, never to output-hosting domains, and downloads must stay on HTTPS
through any redirect.
