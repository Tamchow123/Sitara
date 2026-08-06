# Prompt fidelity evaluation — run plan

**Status:** ready to run, not started. Branch `feat/prompt-builder-v8-focused-prompt`,
builder `8.2.0` (three commits local, unpushed).

**Goal:** measure how faithfully the generated image reflects the user's
questionnaire answers, across 20 varied designs, and identify **which
requirement classes** fail systematically — not to make any single image good.

## Why this is a baseline-first study, not an iterate-until-happy loop

The obvious approach — tweak the prompt whenever a design disappoints, then move
on — destroys the thing we actually want. The prompt builder is global: changing
it for design 7 changes the prompt for designs 1–6 too, so their earlier results
no longer describe the shipped builder. The final table would mix results from
five different builders and support no pattern claim at all.

So: **freeze the builder, run all 20, then fix.**

| Phase | What | Builder | Images |
| --- | --- | --- | --- |
| 1 | 20 designs, one render each | frozen at `8.2.0` | 20 |
| 2 | Blind review, all 20, fixed rubric | — | 0 |
| 3 | Targeted fixes for the systemic failures only | one bump to `8.3.0` | 0 |
| 4 | Re-render Phase 1 failures | `8.3.0` | ≤ 20 |
| 5 | Regression check on a sample of Phase 1 passes | `8.3.0` | ≤ 6 |

That still honours "up to 3 generations per design" (baseline, fix, verify) while
keeping every comparison valid. Phase 5 exists because a fix that repairs
embellishment while breaking coverage is a net loss, and nothing else would catch
it.

## Stopping rules, decided now

- **Hard cap 50 images.** I stop at 50 whether or not I am satisfied.
- **Budget floor.** Stop if the daily ledger drops below $1.00 remaining.
- **Per-design cap 3 renders.** No exceptions, no "one more try".
- **Unwinnable rule.** If a requirement class still fails in Phase 4 after a
  targeted fix, I stop prompting and record it as a product decision — the
  questionnaire option may be promising something the model cannot deliver. No
  third prompt attempt at the same class.
- **No mid-flight rubric changes.** The rubric below is frozen before the first
  render. If it turns out to be wrong, that is a finding, reported as such.

## Cost and limits (measured, not estimated)

Reservation per full pipeline run, from the live config:

```
ANTHROPIC_MAX_INPUT_TOKENS 8192 @ $3.00/Mtok  = $0.0246
DESIGN_SPEC_MAX_OUTPUT_TOKENS 8192 @ $15.00/Mtok = $0.1229
REPLICATE_MAX_IMAGE_MICRO_USD                 = $0.0400
                                        reserve = $0.1875 per run
```

Reservations settle down to measured actuals via `reconcile_actual`, so expected
real spend is ~$0.06–0.09 per full run and ~$0.04 per image-only render.

| | count | expected |
| --- | --- | --- |
| Phase 1 full runs | 20 | $1.20–1.80 |
| Phase 4 re-renders | ≤20 | ≤$0.80 |
| Phase 5 regression | ≤6 | ≤$0.24 |
| **Total** | **≤46** | **~$2.20–2.80 (~£1.75–2.20)** |

Well under £10. The binding constraint is the **image cap**, deliberately, so
there is no temptation to keep going once the evidence goes flat.

**Config limits that matter:**

- `LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 5000000` ($5.00). Peak *reservations*
  for 20 sequential runs are $3.75 before settlement. Runs are sequential, so
  only one reservation is outstanding at a time — but I check remaining budget
  after every run and stop at the floor.
- `LIVE_GENERATION_DAILY_COUNT_LIMIT = 50`. 46 images fits, with nothing spare.
  If Phase 1 needs re-runs the count is the first thing to bind.
- `LIVE_GENERATION_SESSION_LIMIT = 20` / `IP_LIMIT = 40`. These are **HTTP
  admission** limits. The harness builds each `Design` through the ORM with real
  validated questionnaire answers and calls the real `run_generation_attempt`,
  so it exercises the genuine pipeline (spec → prompt → provider → ingest → budget
  ledger) but does not pass through HTTP admission. Deliberate: it avoids
  fighting a session cap that has nothing to do with what is being measured. The
  budget ledger still applies in full.
- **No inspiration references** on any design — `reference_images.py` signs
  against `http://localhost:9000`, which Replicate cannot reach, so any design
  carrying one fails closed before spending.

## The 20-design matrix

Chosen so every requirement class has enough samples to show a pattern, and so no
single garment or answer dominates. All are questionnaire-v4 / DesignSpec v3.

| # | garment | ceremony | colour(s) | embellishment | neckline | sleeves | midriff | back | head |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | saree | nikah | pistachio | none | high_neck | full | covered | modest | hijab |
| 2 | lehenga | walima | scarlet + antique_gold | heavy zardozi | sweetheart | cap | bare | open | uncovered |
| 3 | gharara | mehndi | #7b1f2b (hex) + ivory | minimal dabka | band_collar | full | covered | modest | veil_style |
| 4 | sharara | baraat | deep_maroon + gold | balanced | v_neck | three_quarter | semi_sheer | modest | dupatta_over_head |
| 5 | anarkali | reception | powder_blue | none | boat_neck | elbow | covered | modest | uncovered |
| 6 | shalwar_kameez | nikah | sage | minimal tilla | classic_crew | full | covered | modest | hijab |
| 7 | saree | pheras | peacock + silver_grey | balanced zari | deep_v_neck | sleeveless | bare | deep_cut | uncovered |
| 8 | lehenga | anand_karaj | mint + pearl | minimal | square_neck | three_quarter | covered | modest | dupatta_over_head |
| 9 | gharara | walima | oxblood + antique_gold | heavy | high_neck | full | covered | modest | hijab |
| 10 | anarkali | mehndi | marigold | balanced gota | curved_scoop | cap | covered | modest | uncovered |
| 11 | saree | reception | ivory + champagne | none | boat_neck | sleeveless | bare | open | uncovered |
| 12 | sharara | nikah | navy + silver_grey | minimal | band_collar | full | covered | modest | veil_style |
| 13 | lehenga | baraat | rani_pink + match_fabric dupatta | heavy | sweetheart | sleeveless | bare | deep_cut | uncovered |
| 14 | shalwar_kameez | walima | lilac | none | classic_crew | three_quarter | covered | modest | dupatta_over_head |
| 15 | saree | mehndi | mehndi_green + marigold | balanced mirror | v_neck | elbow | bare | modest | uncovered |
| 16 | gharara | reception | plum_wine + antique_gold | balanced | square_neck | full | covered | modest | hijab |
| 17 | anarkali | pheras | emerald + gold | heavy | high_neck | full | covered | modest | veil_style |
| 18 | lehenga | nikah | blush + rose | minimal pearl | curved_scoop | cap | semi_sheer | modest | uncovered |
| 19 | shalwar_kameez | baraat | rust | none | boat_neck | full | covered | modest | dupatta_over_head |
| 20 | saree | walima | amethyst + silver_grey | minimal | deep_v_neck | three_quarter | bare | open | uncovered |

Deliberate coverage of the things already known or suspected to be fragile:

- **Object-named colours** (pistachio, sage, peacock, oxblood, mint, marigold,
  plum_wine, rust, amethyst, champagne, pearl, blush) — 12 of 20 designs, so the
  `_COLOUR_DESCRIPTORS` fix gets a real test rather than one lucky sample.
- **`none` embellishment** — 5 designs (1, 5, 11, 14, 19), the known 0-for-3
  failure, across four different garments.
- **Every embellishment density** — none ×5, minimal ×6, balanced ×5, heavy ×4.
- **Less-covered answers** — sleeveless ×4, bare midriff ×6, open/deep-cut back
  ×5, uncovered head ×9. These must be honoured as chosen, not "corrected".
- **All four head-covering answers**, and **all six garments** (saree ×5,
  lehenga ×4, gharara ×3, anarkali ×3, shalwar_kameez ×3, sharara ×2).
- **One bride-supplied hex** (#3) and **one `match_fabric` dupatta** (#13).

## Review rubric — frozen before the first render

Each design is scored on every applicable dimension: **PASS / FAIL / N/A**.
`N/A` means *not observable in a front-facing full-length frame* (the back is
usually N/A) — never "unclear", which is a FAIL.

| # | Dimension | Passes when |
| --- | --- | --- |
| 1 | Garment identity | The rendered garment is the one selected |
| 2 | Construction | One/two-piece correct; gharara knee flare; sharara waist flare; saree draped not stitched |
| 3 | Silhouette | Matches the selected silhouette |
| 4 | Fabric colour | Main fabric is recognisably the chosen hue |
| 5 | Embroidery colour | Surface work is the chosen hue (N/A if unanswered or `none`) |
| 6 | Dupatta colour | Dupatta is the chosen hue, or matches fabric when `match_fabric` |
| 7 | Fabric character | Reads as the chosen fabric (silk sheen, velvet depth, net sheerness) |
| 8 | Embellishment presence | Present when selected; **absent when `none`** |
| 9 | Embellishment density | Visually matches minimal / balanced / heavy |
| 10 | Neckline | Matches the canonical neckline |
| 11 | Sleeves | Matches the chosen length, including less-covered choices |
| 12 | Back | Matches (usually N/A) |
| 13 | Midriff | Matches covered / semi-sheer / bare |
| 14 | Head covering | Matches, including an explicit `uncovered` |
| 15 | Drape | Dupatta styling / saree drape as chosen |
| 16 | Framing | Full head-to-foot, nothing cropped |
| 17 | Single subject | Exactly one adult model |
| 18 | Backdrop | Plain seamless neutral studio |
| 19 | Subject focus | Garment is the subject, not portraiture/jewellery |

**On borderline embellishment:** a woven selvedge or structural pallu border on a
saree is *not* surface embellishment and does not fail #8. Applied motifs,
scattered butis, embroidered borders and metallic thread work do.

## Bias control

I am both the author of the prompts and the judge of the images, which is the
weakest part of this study. Mitigation:

1. The rubric above is frozen before any render exists.
2. Each image is graded **first** by an independent subagent that sees only the
   image, the user's selections and the rubric — not the prompt, not the builder
   version, not my opinion.
3. I grade independently, then reconcile. **Every disagreement is reported in the
   results table**, not silently resolved.
4. Every image is reported, including ones that undermine a fix I authored.

## Deliverables

1. **Per-design table** — 20 rows × 19 dimensions, PASS/FAIL/N/A, with the
   disagreements flagged.
2. **Failure-pattern table** — failure rate per dimension, which is the actual
   answer to "what needs addressing".
3. **Cross-tab** on the interesting cuts: failure rate by garment, by
   embellishment density, by colour-name ambiguity, by covered vs less-covered.
4. **Phase 4/5 before-and-after** for whatever was fixed.
5. A short written verdict per requirement class: fixed / improved / unwinnable at
   prompt level / product decision needed.

## Before starting

- Stack up on `8.2.0` (`docker compose up -d`; verify `api` and `celery` both
  report `8.2.0`).
- Confirm the daily ledger has room; the count resets at UTC midnight.
- Confirm gates: `demo_mode:false`, `generation_enabled:true`.
