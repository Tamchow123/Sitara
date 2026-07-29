# 0019 — Reference-image conditioning and the flux-2-max switch

- **Status:** accepted
- **Date:** 2026-07-29
- **Deciders:** project owner (the rights decision is the owner's, taken with the
  provider-terms evidence in view and reaffirmed after it was put explicitly)
- **Phase:** 16B (inserted; see ../phases/PHASES.md and ../phases/phases-16b.md)
- **Related:** ADR 0001 (image model), ADR 0006 (rights-controlled inspiration
  catalogue), ADR 0010 (deterministic image-prompt builder), ADR 0014
  (rights-safe inspiration metadata influence — **overridden here**), ADR 0017
  (live-generation security and cost controls), ADR 0018 (Phase 16B)

> **Decision:** the fail-closed reference-image boundary is deliberately
> **overridden**. `ReferenceImagesNotEnabled` is lifted, and the bytes of the
> references a user actually selected — **their own uploads AND the curated
> catalogue presets** — are sent to the image provider, through short-lived
> signed URLs minted inside the generation job. `DEFAULT_IMAGE_MODEL` becomes
> `black-forest-labs/flux-2-max`, which accepts reference images. Live
> generation remains disabled by default and every Phase 16/ADR 0017 gate,
> budget ceiling and throttle still applies.

## Context

ADR 0014 (Phase 13) drew a hard line: selected inspiration image bytes, URLs and
storage keys are **never** sent to any AI provider; only a frozen, hashed
metadata snapshot (`garment_type`, `alt_text`, `cultural_context`) crosses the
boundary, and `ReferenceImagesNotEnabled` stays fail-closed "with nothing to
enable". That ADR named the reason for deferral precisely: sending image bytes
"raises rights, pricing, and provider-terms questions a scoped, separately
authorised evaluation would need to answer first".

This ADR is that authorisation. It is a decision to accept the exposure, not a
finding that the exposure went away.

Phase 16B also introduces the other half of the feature: a user can now upload up
to three of **their own** reference photographs (ADR 0018, "Authorised scope
change: user-uploaded inspiration images"). Metadata-only influence cannot use a
user's own photograph at all — there is no curated `alt_text` for it, and
inventing one would be a fabricated description of an image nobody reviewed. A
reference control that quietly ignored the photographs a bride chose would be
worse than not offering it.

## Decision

### The rights and provider-terms exposure, stated plainly

The evidence in `experiments/model-eval/TERMS_SNAPSHOT.md` was put to the project
owner explicitly, and the override was reaffirmed with it in view. It says:

- **BFL's terms take a perpetual, irrevocable licence over Inputs** (and Outputs)
  to provide, develop, **train and improve** BFL's technologies.
- **Whether that clause covers Replicate-routed traffic is unresolved.** The
  separate "Flux Model API Agreement" referenced by Replicate was not fetched.
- **Replicate publishes no explicit retention window** for prediction inputs or
  outputs — only "for as long as necessary to provide our Services".

So: once live generation is enabled and a bride's own photograph is sent as a
reference, that image may be retained for an unstated period and may fall under a
perpetual training licence. Nothing in this codebase can undo that after the
fact. This paragraph is the point of this ADR; any summary of it that omits the
exposure is wrong.

Two consequences follow, and both are binding:

1. **The user-facing upload copy must say so** before a user uploads — plainly,
   in the upload UI, not buried in a policy page.
2. **Enabling live generation is the moment the exposure becomes real.** With
   `LIVE_GENERATION_ENABLED=false` (the default) and in demo mode, no reference
   byte leaves the machine.

### What is sent, and what still is not

Sent, once live generation is enabled: the sanitised WebP derivatives of the
references the user selected for **this** design — their uploads and the curated
catalogue assets they picked — as short-lived signed GET URLs.

Still never sent, unchanged from ADR 0014:

- a storage key, a bucket name, an object-store endpoint, or any long-lived URL;
- an asset UUID, title, attribution, rights record, internal note or verifier
  identity;
- anything about an asset the user did **not** select, or one that fails
  `publicly_eligible()` at generation time.

The metadata path is unchanged and still applies: the frozen, hashed
`InspirationContextSnapshot` remains the structured-generation influence, is
still re-validated against `publicly_eligible()` and the safety scan on every
generation, and its persisted audit record remains immutable. Reference bytes are
an **addition** to that path, not a replacement for it.

### The signed URLs are minted in the job and never persisted

A reference URL is a temporary bearer credential. It is minted inside the Celery
task immediately before submission, with a TTL bounded to the generation window,
and is never persisted to a column, returned in an API response, written to a
log, or included in the prompt. `DesignVersion.image_prompt` is unchanged: the
deterministic builder still produces one positive natural-language prompt from
the DesignSpec and knows nothing about references (ADR 0010 is untouched, and
`PROMPT_BUILDER_VERSION` does not move for this).

Eligibility is re-checked at mint time, not trusted from selection: a catalogue
asset whose rights lapsed between selection and generation is dropped rather than
signed. An upload's row is the authority for its own key.

### `flux-2-max` replaces `flux-1.1-pro` as the default image model

`flux-1.1-pro` (ADR 0001) has no reference-image input, so the decision above
forces a model change. `black-forest-labs/flux-2-max` accepts `input_images`
(up to 8; Sitara sends at most `MAX_INSPIRATION_IMAGES` = 3).

The rendering profile is re-derived rather than carried over: `safety_tolerance`
moves to the 1–5 range, `prompt_upsampling` is not sent, and the aspect-ratio and
resolution vocabularies differ. **These parameter details come from the recorded
decision, not from a fresh provider-schema fetch, and no live call has been made
to verify them** — they must be re-checked against the provider's published
schema at the budgeted live checkpoint before any real spend.

This is a model change without a **new** blind evaluation, which ADR 0001's
process would otherwise require. That is deliberate and narrow: the model is
being chosen for a capability `flux-1.1-pro` does not have, not on a quality
claim.

It is worth being precise about what the existing evidence says, because "no new
evaluation" is not the same as "never evaluated". Phase 2 DID score flux-2-max,
blind, in the same screening: pooled mean **4.2833** against flux-1.1-pro's
**4.2667**, with **zero** hard-failure flags — a difference ADR 0001 itself calls
meaningless at that sample size. flux-2-max was not rejected on quality; ADR 0001
retained it as the "research / potential future premium benchmark" and chose
flux-1.1-pro on **utility**: ~5.9× faster and ~6.25× cheaper per generation. So
the frozen evidence is *consistent* with this switch and does not disfavour it —
but it is not the basis for it, and the latency and cost penalties ADR 0001
identified are real and are being accepted along with everything else here. The
Phase 2 evidence is untouched.

### The cost ceiling moves with the model

flux-2-max is materially more expensive per image. The maintainer's recorded
figures are **~$0.07** against flux-1.1-pro's **~$0.04**, roughly **1.75×** —
and those numbers are worth exactly what their source is worth. They come from
the recorded decision, **not** from `TERMS_SNAPSHOT.md`, which carries no dollar
figures at all and whose own unresolved list flags that the flux-2 family's
"additive per-run + per-MP billing formula [is] assumed; confirm with a billed
test run". Treat them as an order-of-magnitude estimate for sizing the ceiling,
never as a verified price. ADR 0017 deliberately asserts no price for this
reason; this ADR gives figures only because an operator needs something to size
against, and they must be re-derived from Replicate's live billing formula
before any real spend. Prices remain operator configuration
(`REPLICATE_MAX_IMAGE_MICRO_USD`, default 0 = unconfigured = fails closed), and a
model change **must** bump `LIVE_GENERATION_PRICING_PROFILE` so accounting can
never continue under a stale price. An operator carrying over the old profile
would under-reserve by ~43% on every image; the profile-version rule is what
prevents that, and it is not optional here.

Every other ADR 0017 control is unchanged: reserve-before-spend, the atomic daily
micro-USD ceiling, per-session and hashed-IP throttles, the global daily count
limit, and the requirement for a persistent `noeviction` standalone budget Redis.

### Demo mode is untouched

Demo generation constructs no reference URL, signs nothing, writes nothing to
storage for this path, and makes no provider call. The zero-cost guarantee is not
weakened by any part of this decision.

## Consequences

- CLAUDE.md §13's absolute prohibition on sending inspiration bytes to a provider
  is superseded for user-selected references; the amended text names this ADR and
  keeps every other prohibition (uploads into the catalogue, remote URL imports,
  scraping, automatic rights verification, public ACLs, exposing storage keys)
  intact.
- ADR 0014's "Metadata-only influence, not reference-image conditioning" section
  and its "Direct reference-image conditioning — rejected" alternative are
  superseded by this ADR; the rest of ADR 0014 (the snapshot contract, double
  re-validation, leakage checks, audit immutability, acknowledgement handling)
  stands unchanged and still governs the metadata path.
- ADR 0001's selected model is superseded for the default tier.
- The manual budgeted live checkpoint remains **pending** and is now larger: it
  must confirm the flux-2-max parameter names AND re-derive the per-image price
  from the live billing formula against official sources before spend. Neither
  the parameter set nor the price in this ADR has been verified against a
  provider response.
- CLAUDE.md §2's rights principle is qualified rather than left absolute: a
  catalogue asset still needs staff-verified, evidenced rights (ADR 0006), while
  a user's own upload rests on a bounded per-upload self-affirmation. The two
  bars are different on purpose and the weaker one must never be described as
  the stronger.
- A future decision to withdraw this override cannot retrieve anything already
  sent. If it is withdrawn, the honest step is to stop sending, not to claim the
  earlier sends were undone.

## Alternatives considered

- **Keep the metadata-only boundary and offer uploads for private reference
  only** (shown back to the user on the review screen, never sent) — rejected by
  the project owner: it makes the upload control decorative, since the generated
  concept would not reflect the photographs at all.
- **Send only the curated catalogue presets, never a user's own photograph** —
  rejected: the catalogue's rights records cover display and AI input for
  approved assets, so this would have been the narrower step, but it leaves the
  bride's own references — the ones she actually cares about — with no effect,
  which is the whole reason the feature was asked for.
- **Wait for a scoped provider-terms review to resolve the BFL/Replicate
  coverage question** — rejected by the project owner as blocking indefinitely on
  a clause only the providers can clarify; the decision was taken with the
  uncertainty recorded rather than resolved.
- **Switch to a model with clearer input-retention terms** — no candidate with
  reference-image support and materially better published terms was identified in
  the frozen Phase 2 evidence; re-opening model selection would need its own
  budgeted evaluation phase.
