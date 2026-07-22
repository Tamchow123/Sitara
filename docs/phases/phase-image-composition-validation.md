# Phase image-composition — operator-run live validation plan

This plan validates the **composition-first** image prompt (`PROMPT_BUILDER_VERSION`
`4.0.0`, ADR 0010) against real FLUX-1.1-pro output. The deterministic code
change is already accepted by the automated test suite and golden snapshots; this
plan covers only the **stochastic live-image observations**, which are inherently
separate from code acceptance.

> **Claude Code must not initiate paid generations.** Every step below is
> performed by a human operator who has explicitly authorised a budgeted live
> checkpoint with all documented gates satisfied. Nothing in this repository
> triggers a provider call on its own.

## 1. What we are checking

The old prompt buried the composition instruction ~600 words deep, and the first
real paid render (2026-07-22) came back as a cropped, editorial, environmentally
busy portrait. `4.0.0` moves a fixed catalogue-composition directive to the front
as the highest-priority section. This plan confirms that change actually produces
the intended full-length plain-studio catalogue framing across garment types.

## 2. Pre-run report (produce before spending anything)

Before any paid run, the operator records, in the results log (section 6):

| Item | How to determine |
|---|---|
| Proposed number of images | Recommended bounded run: **6** (one per matrix row below). |
| Estimated provider cost | Per generation Sitara reserves one Anthropic DesignSpec call **plus** one Replicate image call. Conservative ceiling per image = `REPLICATE_MAX_IMAGE_MICRO_USD` + Anthropic call max (`ANTHROPIC_MAX_INPUT_TOKENS` × `ANTHROPIC_INPUT_MICRO_USD_PER_MTOK` + `DESIGN_SPEC_MAX_OUTPUT_TOKENS` × `ANTHROPIC_OUTPUT_MICRO_USD_PER_MTOK`), all micro-USD. Multiply by the number of images. Use the operator's **own** configured values — provider prices ship unverified (default 0) and are an operator responsibility. |
| Applicable daily budget ceiling | `LIVE_GENERATION_DAILY_BUDGET_MICRO_USD` (UTC-day, atomic reserve-before-spend). The run must fit inside the remaining budget. |
| Applicable per-user / throttle ceilings | Global daily count `LIVE_GENERATION_DAILY_COUNT_LIMIT`; per-session `LIVE_GENERATION_SESSION_LIMIT` / `LIVE_GENERATION_SESSION_WINDOW_SECONDS`; per-hashed-IP `LIVE_GENERATION_IP_LIMIT` / `LIVE_GENERATION_IP_WINDOW_SECONDS`. A six-image run must stay within these. |
| Named pricing profile | `LIVE_GENERATION_PRICING_PROFILE` must be a named profile with real, dated, positive prices (`is_valid`), or admission fails closed. |
| Exact operator command / UI flow | See section 4. |
| Where results are recorded | This file's section 6 table (or a private linked artefact — see section 7). |

Do **not** proceed if the estimate exceeds the remaining daily budget or any
throttle, or if `live_cost_config_is_valid()` would return false.

## 3. Validation matrix (synthetic, non-user, non-private specs)

Use only **synthetic engineering test specifications** — never a real user's
private design. Each row is one generation. The matrix covers the required
garment spread, more than one coverage level and more than one dupatta treatment:

| # | Garment type | Ceremony (example) | Coverage level | Dupatta / drape treatment |
|---|---|---|---|---|
| 1 | Saree | Reception | Modest / full coverage | Draped pallu (saree drape) |
| 2 | Lehenga | Nikah | High neckline, full sleeves | Single dupatta over the head |
| 3 | Sharara **or** gharara | Baraat / Mehndi | Balanced coverage | Double dupatta |
| 4 | Anarkali **or** kurta-style | Walima | Moderate / open coverage | Single shoulder dupatta |
| 5 | Lehenga (second colourway) | Pheras | Sleeveless / lower coverage | No head covering |
| 6 | Gharara (or a second saree) | Mehndi | Full coverage + head covering | Contrast dupatta |

Rows 5–6 deliberately re-use garment families at a **different** coverage and
dupatta treatment so at least two coverage levels and two dupatta treatments are
each exercised. Adjust the exact ceremonies/colours as convenient; keep the
garment / coverage / dupatta spread.

## 4. Exact operator command / UI flow

1. Satisfy every safety gate for a live checkpoint (operator responsibility):
   `DEMO_MODE=false`, `ALLOW_PAID_AI_CALLS=true`, `LIVE_GENERATION_ENABLED=true`,
   a valid `LIVE_GENERATION_PRICING_PROFILE`, a positive
   `LIVE_GENERATION_DAILY_BUDGET_MICRO_USD`, a persistent `noeviction` standalone
   budget Redis (`LIVE_GENERATION_BUDGET_REDIS_URL`), and valid provider
   credentials. Rebuild the API image so `4.0.0` is actually running
   (`docker compose build api && docker compose up -d api`) — the dev image bakes
   in source, so an un-rebuilt container still serves the old builder.
2. For each matrix row, drive the **normal product flow** in the web UI: complete
   the bridalwear questionnaire with the synthetic selections, then request
   generation. This exercises the same DesignSpec → `build_image_prompt` →
   Replicate path as any live generation; no special injection path is used.
3. Capture the resulting signed image (operator download) and the persisted
   `image_prompt` / `prompt_builder_version` for that `DesignVersion` (confirm
   `4.0.0`). Do not paste private signed URLs into the PR.

Confirm before spending: exactly six generations, each within budget and
throttles, `prompt_builder_version == "4.0.0"` on the produced versions.

## 5. Scoring rubric (score every criterion per image)

| Criterion | Pass condition |
|---|---|
| Subject count | Exactly one model |
| Full-body framing | Top of head and both feet visible |
| Complete garment | Hem, lower garment, dupatta and trailing fabric remain inside the frame |
| Pose | Upright, centred and primarily front-facing |
| Background | Plain neutral studio backdrop without an environmental scene |
| Lighting | Soft and even enough to inspect garment details |
| Visual priority | Garment is more prominent than face, jewellery or setting |
| Semantic fidelity | Garment type, construction, coverage, colour and key embellishment match the spec |

Record a Pass/Fail for **each** criterion, not just an overall impression, and
note any garment-type-specific failure (e.g. a saree that reads as a stitched
gown, a gharara without the knee joint).

## 6. Results log (operator to complete)

Recommended run size: **6 outputs**. Do not spend anything without explicit
operator approval.

| # | Garment | Subject count | Full-body | Complete garment | Pose | Background | Lighting | Visual priority | Semantic fidelity | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Saree | | | | | | | | | |
| 2 | Lehenga | | | | | | | | | |
| 3 | Sharara/Gharara | | | | | | | | | |
| 4 | Anarkali/Kurta | | | | | | | | | |
| 5 | Lehenga (v2) | | | | | | | | | |
| 6 | Gharara/Saree (v2) | | | | | | | | | |

**Overall verdict:** _(operator)_ — does `4.0.0` reliably produce catalogue
framing across the matrix? List any remaining prompt-wording follow-ups.

## 7. Privacy and artefact handling

- Generated designs are private by default. **Do not** commit live generated
  images to the repository, and do not upload private user designs to a public PR.
- Use only synthetic, non-user test specifications for validation.
- Attach before/after evidence to the PR **only** when repository visibility and
  image rights permit it; otherwise link an approved private artefact or leave the
  section 6 table as placeholders for operator-supplied evidence.
- The Phase 2 images under `experiments/model-eval/outputs/` remain read-only
  visual references and must not be modified.
