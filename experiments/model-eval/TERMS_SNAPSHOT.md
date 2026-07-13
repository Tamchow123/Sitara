# Provider terms snapshot — Sitara model evaluation

> Facts recorded from official provider sources on the dates shown.
> This document makes **no legal conclusions**. Items listed as
> unresolved require human review. Terms and pricing are
> time-sensitive — re-verify official pages immediately before any
> live run or production decision.

## Replicate platform terms

- Summary: Replicate assigns all right, title and interest in Output to the customer; outputs are usable commercially subject to third-party (BFL) terms. BFL-routed models additionally require the Flux Model API Agreement and BFL ToS, which prohibit military/surveillance/biometric uses. BFL ToS claims no ownership of outputs but takes a perpetual, irrevocable licence to use Inputs and Outputs to provide, develop, train and improve BFL technologies.
- Commercial use: Replicate ToS: Output usable for commercial purposes such as sale or publication, subject to any Third Party Terms.
- Input retention: Replicate privacy policy states retention "for as long as necessary to provide our Services"; no explicit prediction input/output retention window is published.
- Training on customer data: Replicate creates anonymised/aggregated Resultant Data for service improvement. BFL ToS (bfl.ai) grants BFL a perpetual, irrevocable licence to use Input and Output to train and improve its technologies; whether this clause covers Replicate-routed API traffic is unresolved.
- Verified on: 2026-07-13
- Sources:
  - https://replicate.com/terms
  - https://replicate.com/privacy
  - https://bfl.ai/legal/terms-of-service
- **Unresolved (human review required):**
  - Replicate publishes no explicit retention window for prediction inputs/outputs.
  - The separate "Flux Model API Agreement" referenced by Replicate was not fetched; confirm whether BFL's train-and-improve licence covers Replicate-routed requests.

## FLUX Schnell (`black-forest-labs/flux-schnell`)

- Model licence: Apache 2.0 (per model README)
- Commercial use: README: released under apache-2.0; usable for personal, scientific and commercial purposes.
- Input retention: Not stated on the model page; see platform terms.
- Output ownership: Replicate ToS assigns Output to the customer.
- Training on submitted data: Not stated on the model page; see platform terms.
- Pricing checked on: 2026-07-13 (https://replicate.com/black-forest-labs/flux-schnell)
- Terms verified on: 2026-07-13
- Sources:
  - https://replicate.com/black-forest-labs/flux-schnell
  - https://replicate.com/pricing

## FLUX 1.1 Pro (FLUX.1-era baseline) (`black-forest-labs/flux-1.1-pro`)

- Model licence: Proprietary, API-served via BFL; requires the Black Forest Labs API agreement and BFL Terms of Service.
- Commercial use: Governed by BFL ToS + Replicate ToS (see platform terms).
- Input retention: Not stated on the model page; see platform terms.
- Output ownership: BFL ToS claims no ownership of Outputs; Replicate ToS assigns Output to the customer.
- Training on submitted data: BFL ToS grants BFL a perpetual licence to use Input/Output to train and improve its technologies (coverage of Replicate-routed traffic unresolved).
- Pricing checked on: 2026-07-13 (https://replicate.com/black-forest-labs/flux-1.1-pro)
- Terms verified on: 2026-07-13
- Sources:
  - https://replicate.com/black-forest-labs/flux-1.1-pro
  - https://bfl.ai/legal/terms-of-service
- **Unresolved (human review required):**
  - Whether BFL's train-and-improve clause applies to Replicate-routed requests.

## FLUX.2 Pro (`black-forest-labs/flux-2-pro`)

- Model licence: Proprietary, API-served; BFL/Replicate terms apply.
- Commercial use: Governed by BFL ToS + Replicate ToS (see platform terms).
- Input retention: Not stated on the model page; see platform terms.
- Output ownership: BFL ToS claims no ownership of Outputs; Replicate ToS assigns Output to the customer.
- Training on submitted data: BFL ToS train-and-improve licence (coverage of Replicate-routed traffic unresolved).
- Pricing checked on: 2026-07-13 (https://replicate.com/black-forest-labs/flux-2-pro)
- Terms verified on: 2026-07-13
- Sources:
  - https://replicate.com/black-forest-labs/flux-2-pro
  - https://docs.bfl.ai/flux_2/flux2_overview
- **Unresolved (human review required):**
  - Additive per-run + per-MP billing formula assumed; confirm with a billed test run.
  - JSON prompting documented for FLUX.2 generally; per-variant behaviour unverified.

## FLUX.2 Max (`black-forest-labs/flux-2-max`)

- Model licence: Proprietary, API-served; BFL/Replicate terms apply.
- Commercial use: Governed by BFL ToS + Replicate ToS (see platform terms).
- Input retention: Not stated on the model page; see platform terms.
- Output ownership: BFL ToS claims no ownership of Outputs; Replicate ToS assigns Output to the customer.
- Training on submitted data: BFL ToS train-and-improve licence (coverage of Replicate-routed traffic unresolved).
- Pricing checked on: 2026-07-13 (https://replicate.com/black-forest-labs/flux-2-max)
- Terms verified on: 2026-07-13
- Sources:
  - https://replicate.com/black-forest-labs/flux-2-max
  - https://docs.bfl.ai/flux_2/flux2_overview
- **Unresolved (human review required):**
  - Additive per-run + per-MP billing formula assumed; confirm with a billed test run.
  - JSON prompting documented for FLUX.2 generally; per-variant behaviour unverified.

## FLUX.2 klein 4B (`black-forest-labs/flux-2-klein-4b`)

- Model licence: Apache 2.0 per the Replicate README ("fully open source under Apache 2.0"); BFL docs confirm klein 4B = Apache 2.0.
- Commercial use: Apache 2.0 weights; outputs usable commercially.
- Input retention: Not stated on the model page; see platform terms.
- Output ownership: Replicate ToS assigns Output to the customer.
- Training on submitted data: Not stated on the model page; see platform terms.
- Pricing checked on: 2026-07-13 (https://replicate.com/black-forest-labs/flux-2-klein-4b)
- Terms verified on: 2026-07-13
- Sources:
  - https://replicate.com/black-forest-labs/flux-2-klein-4b
  - https://docs.bfl.ai/flux_2/flux2_overview
- **Unresolved (human review required):**
  - Advertised pricing ($1 per thousand output MP) is anomalously low vs klein 9B ($0.015 per output MP) with asymmetric units — verify against a real billed prediction before trusting any calculation.
  - JSON prompting support for the klein variant specifically is unverified.
