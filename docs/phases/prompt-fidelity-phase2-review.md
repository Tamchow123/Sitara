# Prompt-fidelity evaluation — Phase 2 review

Blind rubric review of the 20 Phase 1 renders (`prompt-fidelity-phase1-results.json`),
builder frozen at `8.2.0`, per the frozen rubric in
`prompt-fidelity-evaluation-plan.md`. Methodology: each image was graded first by
an independent subagent that saw only the image, that row's actual selections and
the rubric — never the prompt, builder version or my own observations — then I
graded independently and reconciled.

**Methodology caveats, stated up front rather than buried:**

- I saw two subagent verdicts (rows 2 and 3) via automatic completion notifications
  before I independently viewed those two images myself, which weakens
  independence for those two specifically. Every other row I viewed blind to the
  subagent's verdict.
- On reconciliation I found and corrected one real disagreement in my own favour
  of the subagent (row 4 — confirmed on a third, careful look to be a genuine
  back-facing render, not the front-facing shot I first assumed) and reversed one
  of my own snap judgements to agree with the subagent (row 1 dimension 8 — a
  plain woven gold border, not applied metallic embroidery, correctly passes
  under the rubric's own carve-out). No other disagreements survived reconciliation.
- Dimension 6 (dupatta colour) was graded inconsistently across designs that
  never specified an explicit dupatta colour: some graders correctly scored N/A,
  one (row 4) instead compared the rendered dupatta against the *fabric* colour
  and failed it. That is a measurement-reliability gap, not a confirmed product
  finding — dimension 6 has too few clean samples (effectively 1: row 13's
  `match_fabric` case, which passed) to draw a conclusion either way.
- The rubric has no dimension for **shot orientation** (front- vs back-facing).
  Row 4 rendered back-facing; dimension 16 (framing) still passed it because the
  figure is complete head-to-foot with nothing cropped — framing and orientation
  are different properties, and only one of them has a rubric line. This is a
  rubric gap, not a scored dimension, and it silently invalidated 2 of that row's
  19 dimensions (neckline, midriff — both went N/A rather than gradable).

## Per-design results (fails only; everything else in that row is PASS, or N/A where noted)

| # | garment / ceremony | fails | notes |
|---|---|---|---|
| 1 | saree / nikah | 10 | neckline unobservable under hijab drape (correctly FAIL per rubric: unclear ≠ N/A) |
| 2 | lehenga / walima | — | clean |
| 3 | gharara / mehndi | 1,2,3,4,7,9,15 | reads as a generic fitted-bodice ballgown, not a gharara at all |
| 4 | sharara / baraat | 4,6,12,14,15 | **rendered back-facing**, not front-facing (10,13 N/A as a result); head-covering also failed |
| 5 | anarkali / reception | 10,15 | shoulder wrap obscures neckline/one-shoulder drape |
| 6 | shalwar kameez / nikah | 3,4,9,10 | sage rendered as cream/beige; silhouette and neckline obscured by dupatta |
| 7 | saree / pheras | 3,7,13,15 | net rendered opaque; midriff covered instead of bare; generic nivi drape instead of Bengali atpoure |
| 8 | lehenga / anand karaj | 9,10,13,15 | minimal read as heavy; square neck read as sweetheart; midriff sheer not covered |
| 9 | gharara / walima | — | clean — genuine knee-flare construction visible |
| 10 | anarkali / mehndi | 11 | cap sleeve rendered as a wider elbow-cuff band |
| 11 | saree / reception | 8,10,15→13 | **embellishment rendered despite "none" selected**; one-shoulder asymmetry read as FAIL on boat-neck; midriff covered not bare |
| 12 | sharara / nikah | 7,9 | jamawar read as plain embroidered fabric, not self-patterned weave; minimal read as heavy |
| 13 | lehenga / baraat | 4,7 | rani pink rendered as dusty rose/champagne; velvet read as sheer net/satin |
| 14 | shalwar kameez / walima | 8,11,15 | **embellishment rendered despite "none" selected**; sleeves obscured; head-drape read as a hijab wrap |
| 15 | saree / mehndi | 3,13 | half-saree read as a standard full nivi drape; midriff covered not bare |
| 16 | gharara / reception | 2,9,10 | reads as a continuous gown, no knee-flare; balanced read as heavy; square neck read as sweetheart |
| 17 | anarkali / pheras | 1,2,3,7,10,13 | **reads as a two-piece lehenga with bare midriff, not a one-piece anarkali at all** |
| 18 | lehenga / nikah | 3,9,13 | straight cut rendered as a flared/pooling skirt; minimal read as heavy; semi-sheer midriff rendered fully bare |
| 19 | shalwar kameez / baraat | 2,8,15 | bottom read as a lehenga skirt, not shalwar trousers; **embellishment rendered despite "none" selected**; single dupatta not double |
| 20 | saree / walima | 9,13 | minimal read as heavy; midriff covered not bare |

## Failure-rate table (all 20, N/A excluded from the denominator)

| # | dimension | fails / gradable | rate |
|---|---|---|---|
| 1 | Garment identity | 2/20 | 10% |
| 2 | Construction | 4/20 | 20% |
| 3 | Silhouette | 6/20 | 30% |
| 4 | Fabric colour | 4/20 | 20% |
| 5 | Embroidery colour | 0/13 | 0% |
| 6 | Dupatta colour | 1/2 | *(too few samples — see caveat above)* |
| 7 | Fabric character | 5/20 | 25% |
| 8 | Embellishment presence | 3/20 | 15% (**60% within the "none" subgroup — see below**) |
| 9 | Embellishment density | 7/15 | 47% |
| 10 | Neckline | 7/18 | 39% |
| 11 | Sleeves | 2/20 | 10% |
| 12 | Back | 1/1 | *(only one observable sample — the row 4 orientation failure)* |
| 13 | Midriff | 7/19 | 37% |
| 14 | Head covering | 1/20 | 5% |
| 15 | Drape | 7/20 | 35% |
| 16 | Framing | 0/20 | 0% |
| 17 | Single subject | 0/20 | 0% |
| 18 | Backdrop | 0/20 | 0% |
| 19 | Subject focus | 0/20 | 0% |

## Cross-tabs

**By garment** (avg fails/design, designs with ≥1 fail):

| garment | n | avg fails | ≥1 fail |
|---|---|---|---|
| saree | 5 | 2.4 | 5/5 |
| lehenga | 4 | 2.25 | 3/4 |
| gharara | 3 | 3.33 | 2/3 (but catastrophic when it fails — full garment-identity collapse) |
| sharara | 2 | 3.5 | 2/2 |
| anarkali | 3 | 3.0 | 3/3 |
| shalwar kameez | 3 | 3.33 | 3/3 |

Saree never lost garment identity outright; gharara and anarkali each lost it
outright once (rows 3 and 17), in **opposite directions** — gharara (should be
two-piece) rendered as one continuous piece; anarkali (should be one-piece)
rendered as a two-piece lehenga with a bare midriff gap. The model does not
reliably hold the one-piece/two-piece distinction in either direction.

**By requested embellishment density** — the single clearest finding in this study:

| density | n | dimension-9 fails | rate |
|---|---|---|---|
| none | 5 | 3 (dimension 8, presence) | **60%** |
| minimal | 6 | 5 | **83%** |
| balanced | 5 | 1 | 20% |
| heavy | 4 | 0 | **0%** |

Every failure in this table is in the same direction: **more decoration than
requested, never less.** The pipeline has a strong prior toward embellished
bridalwear that "none" and "minimal" selections mostly fail to overcome.

**By requested midriff coverage:**

| midriff | n (gradable) | dimension-13 fails | rate |
|---|---|---|---|
| bare / semi-sheer | 6 | 5 | **83%** |
| covered | 12 | 2 | 17% |

Same shape as the embellishment finding, same direction: the pipeline defaults
toward **more coverage than requested**, not less. Combined, these two cross-tabs
describe one underlying bias, not two unrelated ones — the model regresses
toward "conventional heavily-embellished, modestly-covered bridalwear" whenever
the user asked for something more restrained or less covered, in either density
or midriff.

**By colour-naming style** (object-named hues — pistachio, oxblood, marigold,
etc. — vs standard colour words), fabric colour (dimension 4) only:

| naming | n | fails | rate |
|---|---|---|---|
| object-named | 12 | 2 (sage→cream, rani pink→dusty rose) | 17% |
| standard/other | 8 | 2 (custom hex, deep maroon on the back-facing row 4) | 25% |

This is a genuinely good result, and worth stating plainly: object-named colours
are **no longer** the primary colour-fidelity risk in this sample. The `8.1.0`
`_COLOUR_DESCRIPTORS` fix (documented in `phase-prompt-builder-v8.md`, made after
the `pistachio→pink` failure on the first live `8.0.0` render) appears to be
holding up across a real 12-design sample, not just the one case it was written
to fix. The two real colour misses here are a custom hex value and a standard
descriptive name, not an object-noun colour — the failure mode this fix targeted
has moved from "worst problem" to "no longer distinguishable from baseline."

## Verdict per requirement class

- **Embellishment restraint (`none` and `minimal` density) — unwinnable at this
  prompt-builder layer, needs a product decision.** 60–83% failure with the
  *same directional bias* every time is not noise; it is the model's own prior
  overriding the prompt. `8.2.0`'s "describe an unembellished fabric by what it
  is" fix (the fabric-only phrasing for `none`) did not fix this — 3 of 5 `none`
  designs still show embellishment. A fourth prompt attempt at the same class is
  exactly what the plan's stopping rule says not to make. This is a finding to
  report, not a bug to keep chasing.
- **Midriff/coverage restraint — same verdict, same reasoning.** 83% failure on
  bare/semi-sheer requests, in the direction of *more* coverage. Paired with the
  embellishment finding, this reads as one systemic bias, not two.
- **Colour fidelity — fixed, validated at scale.** The `8.1.0` object-colour fix
  holds across 12 real samples. No further prompt work indicated here.
  **Update after Phase 4:** 2 of 7 Phase 4 re-renders (rows 3, 7 — colour clauses
  untouched by `8.3.0`) show severe colour misses (a requested dark maroon
  rendered pale blue; a requested peacock blue-green with silver embroidery
  rendered cream with gold). Both are named/hex colours on rows that also had
  the worst non-colour failures in Phase 1 (total garment-identity collapse,
  wrong drape). Read as FLUX variance surfacing on a larger sample rather than
  an `8.3.0` regression (no colour-clause code changed), but this verdict should
  no longer be treated as fully closed — see `prompt-fidelity-phase4-results.json`.
- **Garment construction (one-piece vs two-piece) — improved but not fixed.**
  `8.0.0`'s D3 construction clause (CLAUDE.md §12's gharara/sharara distinction)
  prevents *some* failures (sharara, shalwar kameez, lehenga all held their
  construction) but not all — gharara collapsed to a one-piece gown twice in
  three tries, anarkali collapsed to a two-piece lehenga once in three. Worth one
  targeted fix (strengthen the D3 clause specifically for gharara and anarkali)
  before writing this off as unwinnable — the failure rate (3/6 combined across
  both garments) is high but not the same "every time, same direction" signature
  as the density/coverage findings.
- **Neckline and drape-style fidelity — improved from `7.0.0`'s total miss but
  still weak (35–39% failure).** No single dominant cause — obscured by hijab,
  wrong shape rendered, generic drape substituted for the specific named style.
  Candidate for one targeted fix (name the drape/neckline requirement closer to
  the composition directive, following D4's "early placement" logic that already
  worked for coverage), then re-verify on a sample rather than all 20 again.
- **Shot orientation — a rubric and possibly a prompt gap, not yet diagnosed.**
  One design out of 20 rendered back-facing. Too small a sample to say whether
  this is prompt-level (the composition directive doesn't pin orientation
  strongly enough) or provider stochasticity. Recommend adding an explicit
  rubric dimension for this before the next review, and watching for a repeat
  during Phase 4/5 re-renders.
- **Composition basics (framing, single subject, backdrop, subject focus) —
  fully fixed.** 0% failure across all 20, no exceptions. The `8.0.0`
  composition-forward restructure fully achieved its original goal (the
  cropped-editorial-shot problem this whole branch started from).

## Phase 4/5 before-and-after

**Phase 3** shipped three targeted fixes as `8.3.0` (see
`docs/phases/phase-prompt-builder-v8.md` and ADR 0010): gharara/anarkali
construction + a new per-silhouette clause map, a more specific square-neck
clause, and new drape-style clauses (including Bengali atpoure and
double-dupatta). Embellishment/midriff restraint were deliberately left
untouched per the verdict above.

**Phase 4** live re-rendered 7 of the 20 Phase 1 rows under `8.3.0` — not "every
row that failed Phase 1," but rows whose *documented failure mechanism*
directly matches what `8.3.0` actually changed. Full detail, including the
exact persisted prompt text and per-row evidence, is in
`docs/phases/prompt-fidelity-phase4-results.json`. Summary:

| row | garment/ceremony | targeted fix | verdict |
|---|---|---|---|
| 3 | gharara / mehndi | construction + silhouette | **not fixed** — still one continuous garment |
| 5 | anarkali / reception | *(none — see below)* | **no clean signal**, excluded from scoring |
| 7 | saree / pheras | Bengali atpoure drape | **not fixed** — still generic single-shoulder nivi |
| 8 | lehenga / anand karaj | square neck | **fixed** — clean square edge, right-angle corners |
| 16 | gharara / reception | construction + silhouette, square neck | **fixed** — both dimensions clean in the same image |
| 17 | anarkali / pheras | construction + silhouette | **fixed** — resolves the worst Phase 1 collapse |
| 19 | shalwar kameez / baraat | double-dupatta drape | **not fixed** — still reads as one dupatta |

Row 5 was re-checked against the actual Phase 1 fail table and the actual sent
prompt during Phase 4 review: its documented fails (neckline, drape) use
values (`boat_neck`, `one_shoulder`) that `8.3.0` did not change, so no
`8.3.0` fix was actually under test for this row. It is excluded from the
scored result below rather than forced into a verdict — a scoping miss in the
original row selection, not a data point.

By fix, across the rows that actually carry it:

- **Square-neck wording — 2/2, reliable.** Rows 8 and 16 both render a clean,
  unambiguous square neckline. Ready to consider closed without further
  targeted re-testing.
- **Gharara/anarkali construction + silhouette — split by garment; see Phase 5
  below for why this is no longer a clean "2/3 improved" reading.** Anarkali:
  2/2 across both a previously-failing (17) and a previously-passing (10)
  sample — looks stable. Gharara: 1 previously-failing case fixed (16), but
  the *only* previously-passing gharara case (9) produced three different
  outcomes across its full 3-render allowance (pass, clean fail, ambiguous) —
  not a clean regression, but not stable either. Net effect on gharara
  specifically cannot be called an improvement, and the matrix has no fourth
  gharara row to sample further. Row 3 also carries the worst colour miss in
  the batch (see below), so its failure may be partly entangled with a
  broader misread rather than a clean isolated construction miss —
  unconfirmed, a caveat only on row 3.
- **Bengali atpoure drape — 0/1, did not work.** Confirmed via direct database
  read that row 7's prompt correctly contains the new clause; FLUX still
  rendered a generic nivi drape. One sample only, but a clean negative result
  worth recording rather than re-attempting a fourth time per the plan's
  stopping rule.
- **Double-dupatta drape — 0/3, did not work.** Rows 3, 8 and 19 all carry the
  confirmed-correct "two dupattas, one at the head, one trailing" clause; all
  three render what reads as a single dupatta or veil, not two visually
  distinct pieces. Consistent across three independent rows and three
  different garments — this reads as a FLUX compositional/counting limitation
  ("render two of this draped item, in two different positions") rather than
  a prompt-wording problem, similar in kind to the embellishment-restraint and
  midriff-restraint findings above: a model prior the prompt-builder layer is
  unlikely to overcome by rewording further.

**Unplanned finding, not scored above:** rows 3 and 7 both show severe fabric-
colour misses unrelated to anything `8.3.0` changed (see the "Update after
Phase 4" note on colour fidelity earlier in this document). Verified genuine
(not a review mix-up) by re-fetching the stored image bytes directly from the
`design_images` storage backend and hashing them against the reviewed file.
Flagged as a candidate for a future targeted look, not investigated further
here.

**Phase 5** (regression-check a sample of Phase 1 *passes*, to confirm `8.3.0`
did not silently break something that previously worked) ran 4 of a planned 6
rows before exhausting today's `LIVE_GENERATION_DAILY_COUNT_LIMIT` (a hard
gate, confirmed at exactly 50/50 by a direct read of the Redis count ledger —
independent of the dollar budget, which was also at its own floor throughout).
Rows were priority-ordered so the highest-value regression checks — previously
*passing* cases of the exact code `8.3.0` rewrote — ran first. Full detail in
`docs/phases/prompt-fidelity-phase5-results.json`. Summary:

| row | garment/ceremony | Phase 1 verdict | Phase 5 (8.3.0) verdict |
|---|---|---|---|
| 9 | gharara / walima | clean — genuine knee-flare construction | **inconclusive** — see tiebreaker below |
| 10 | anarkali / mehndi | 1 fail (sleeves only) | **stable** — construction/silhouette still clean |
| 2 | lehenga / walima | clean, zero fails | **isolated failure** (`structured_submission_ambiguous`), not retried |
| 13 | lehenga / baraat | 2 fails (colour, fabric character) | **stable** — same two pre-existing fails, nothing new |
| 20 | saree / walima | 2 fails (density, midriff) | not attempted (count limit exhausted) |
| 12 | sharara / nikah | 2 fails (fabric character, density) | not attempted (count limit exhausted) |

**This is the headline result of the whole study, and it resolved into
something messier than a clean pass or fail.** Row 9 was the *only*
Phase-1-passing gharara construction sample in the 20-design matrix. Its
first Phase 5 render regressed cleanly (one continuous garment, no waist
definition at all — indistinguishable from row 3's failure). Because n=1 was
too thin to call a regression on its own, a tiebreaker third render was
authorized (within the plan's 3-renders-per-design cap). Two attempts at that
tiebreaker failed outright with `structured_submission_ambiguous` — root-caused
to Docker Desktop's engine going unreachable on the local machine (confirmed
independently via `docker compose ps` failing identically), not an Anthropic
or prompt-content issue; both are non-rendering transport failures and don't
count against the render cap. The third attempt, after Docker was restarted,
succeeded — and produced a **third, different** outcome: a visible waist band
now separates bodice from flare (unlike the clean-fail render), but the flare
itself still reads as one unified skirt with no hint of separate trouser
legs, so it's genuinely ambiguous rather than a clear pass.

Three renders of byte-identical 8.3.0 prompt text (confirmed via direct
database reads each time) produced three visually different constructions:
pass, clean fail, ambiguous. The honest conclusion is not "regressed" or
"fixed" but that **this specific design has high rendering variance under
8.3.0's gharara wording** — which is arguably a more useful (and more
concerning) finding than a clean binary would have been, since it means the
wording alone doesn't reliably determine the outcome. Counting every gharara
construction sample across the whole study (row 3: failed in Phase 1, still
fails; row 9: pass/fail/ambiguous across three renders; row 16: failed in
Phase 1, now fixed), the gharara-specific half of the construction/silhouette
fix cannot be called a clean win, and row 9 has now used its full 3-render
allowance — there is no further gharara data available anywhere in the
matrix. The anarkali half of the same fix shows no equivalent instability:
row 10 (previously passing) stayed clean alongside row 17 (previously the
worst failure in the study, now fixed), consistently.

Practically, this means the 8.3.0 gharara construction/silhouette wording
should not be treated as a settled win, but it also shouldn't be reverted on
the strength of a single bad render — the evidence now points at output
variance rather than a clean wording defect. A follow-up phase's most
defensible options: revert just the gharara half of the clause (keep the
anarkali rewrite, which has consistent positive evidence on both a fail and a
pass sample) on the grounds that unreliable output is itself a reason not to
ship the wording change, or leave it as documented, known-unreliable behaviour
and revisit if a future evaluation phase adds more gharara samples to the
matrix. Both are legitimate; this document doesn't pick one.

Row 2's isolated failure and the still-unattempted rows 20/12 are not evidence
either way — they are simply gaps the count-limit exhaustion left in the
regression sample, to be picked up if a further check is ever authorized.

The colour-fidelity concern also strengthened during Phase 5: row 10 is a
*third* independent severe colour miss (bright marigold orange requested,
pale sage rendered), alongside Phase 4's rows 3 and 7 — see the "Update after
Phase 4" note above. Three misses out of nine rows re-rendered since Phase 2
declared this dimension "fixed, validated at scale" on 12 samples is enough
that the verdict should be treated as reopened, not just caveated.
