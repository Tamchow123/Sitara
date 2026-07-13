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
model supports. Nothing auto-selects a winner — human scoring is
authoritative, and hard cultural failures (e.g. a gharara rendered as a
sharara, ignored coverage requirements) disqualify regardless of prettiness.

## Planning and dry runs (zero cost, zero network)

```bash
python -m model_eval.cli inspect                       # show candidates + facts
python -m model_eval.cli plan --config configs/screening.yaml --budget-usd 10
python -m model_eval.cli run --config configs/screening.yaml --dry-run --budget-usd 10
```

`plan` and `--dry-run` print the planned request count, models, prompt
formats, inspiration modes, refinement experiments, visible skips, and the
**conservative maximum spend** — a deliberately pessimistic ceiling built
from each candidate's `max_cost_per_generation_usd`. Real spend is usually
much lower. Dry runs make zero network calls and create no artefacts.

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

Completed and skipped requests are detected from their result records and
never re-sent (spend is not double-counted); failed ones are retried. If a
crash left a stale `budget_ledger.json.lock`, delete the lock file after
confirming no other run is active.

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

Sheets label images with anonymised candidate codes; the code→model mapping
is written separately to `candidate_key.json` — don't open it until scoring
is done (`--reveal` exists for after). The scoring CSV has 1–5 rubric
columns (garment accuracy, cultural coherence, fabric realism, embroidery,
dupatta styling, anatomy, prompt adherence, modesty/coverage, reference
influence, refinement consistency, overall quality), yes/no hard-failure
columns, and a reviewer-notes column. There is no automated aesthetic
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
prompts/    briefs.yaml — the garment/ceremony brief matrix
references/ rights-controlled reference manifest (+ local/, gitignored)
src/        model_eval package (config, matrix, formats, budget, runner, ...)
tests/      pytest suite — fully mocked, proves the zero-spend gates
outputs/    run artefacts (gitignored): results, images, ledgers, sheets
```
