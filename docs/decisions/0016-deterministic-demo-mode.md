# 0016 — Deterministic zero-cost demo mode

- **Status:** accepted
- **Date:** 2026-07-21
- **Deciders:** Sitara maintainers
- **Phase:** Phase 15 (see ../phases/PHASES.md)
- **Related:** ADR 0001 (image model), ADR 0002 (application foundation), ADR
  0009 (structured DesignSpec generation), ADR 0010 (deterministic
  image-prompt builder), ADR 0011 (asynchronous generation pipeline), ADR
  0012 (private design-image storage), ADR 0013 (generation progress and
  results), ADR 0014 (inspiration metadata influence), ADR 0015 (single-round
  constrained refinement)

## Context

Phase 3A introduced a stub demo path (`sitara.ai_gateway.providers` and two
legacy `policy.py` getters) that was never wired into the real asynchronous
pipeline and never produced an honest end-to-end journey — it existed only so
early tests had something zero-cost to call. `LIVE_GENERATION_ENABLED`
defaults false and paid generation requires deliberate operator
configuration, so before this phase there was no way to demonstrate the
complete Sitara journey — questionnaire through result and refinement —
without either paid provider credentials or a mocked frontend. This phase
replaces the stub with a demo path that is structurally zero-cost, produces
a real `DesignSpec` and a real, permanently stored image through the exact
same durable pipeline live generation uses, and is honestly labelled
throughout so a demo result is never presented as a fresh AI-provider
render.

## Decision

### Same public APIs, same pipeline, only two stages substituted

Demo generation reuses the existing `enqueue_design_generation`/
`enqueue_design_refinement` transaction shape, the existing
`GenerationAttempt` Celery state machine (`queued` → `running_text` →
`running_image` → `succeeded`/`failed`), the existing advisory lock,
in-flight submission markers, staging key layout, canonical WebP ingest,
signed-image delivery, result API, and refinement lineage constraints — all
unmodified. Only the structured-design and image-generation provider stages
are substituted with local, deterministic adapters
(`sitara.generation.demo.provider.DemoStructuredDesignProvider`/
`DemoRefinementStructuredDesignProvider`,
`sitara.generation.demo.image_provider.DemoImageProvider`). No
`/demo/...` endpoint exists; the frontend uses the same routes, the same
generated OpenAPI types, and the same TanStack Query lifecycle as live
generation.

### The deterministic image-prompt builder is reused unchanged

`build_image_prompt` (`PROMPT_BUILDER_VERSION 3.0.0`, ADR 0010) is called
with a real, schema-validated DesignSpec exactly as live generation calls
it — never a demo-specific variant, never a parsed/reconstructed prompt.
This is what makes the demo asset selector's later matching meaningful: it
scores against the same deterministic prompt text and controlled
`DesignSpec` fields a live generation would have produced for the same
questionnaire input.

### Local deterministic structured-design and image adapters

`sitara.generation.demo.design_spec_engine.build_demo_design_spec` builds a
complete, schema-valid `DesignSpec` from the canonical questionnaire
selections, the linked questionnaire version, a controlled phrase
vocabulary (`phrases.py`), and the Phase 13 inspiration-context snapshot
where applicable — deterministic (same input → the same output, no LLM, no
raw questionnaire free text interpolated into generated narrative) and
independent of which demo asset is later available.
`sitara.generation.demo.image_provider.DemoImageProvider` implements the
exact same asynchronous `ImageProvider` protocol
(`create_prediction`/`get_prediction`/`cancel_prediction`) the live
Replicate provider implements, resolving to `succeeded` on the first poll
with a private, opaque `demo-asset://<pack-id>/<manifest-hash>/<asset-id>`
reference — never an ordinary HTTP/HTTPS URL, never a real storage key.
Only `demo_image_downloader` in the same module may resolve that scheme; the
live downloader rejects it outright.

### A versioned manifest, private demo-source storage, and a deterministic selector

`sitara.generation.demo.manifest.DemoManifest` is a strict Pydantic v2
contract (`extra="forbid"`) for the reviewed asset pack, carrying its own
`DEMO_MANIFEST_SCHEMA_VERSION`, validated for cultural/coverage guarantees
(`validate_manifest_coverage`) before it can ever become active. Demo source
images live in the same private object storage as raw generation staging,
under a deterministic, content-addressed key
(`demo-assets/<pack-id>/<manifest-hash>/<asset-id>.webp`) — never exposed
through any API, log line, or exception.
`sitara.generation.demo.selector.select_demo_asset`
(`DEMO_SELECTOR_VERSION`) applies exact garment-type hard filtering (never
approximate), then a documented weighted score across the DesignSpec's
remaining controlled fields, with a stable SHA-256 tie-break independent of
manifest ordering — the same DesignSpec and manifest always select the same
asset, in any process, on any run.

### Persisted demo identity and minimal selection provenance

`GenerationAttempt.is_demo`/`demo_selection` and `DesignVersion.is_demo`
(migration `0012_generation_attempt_designversion_is_demo`) freeze which
mode produced a version at creation time. `demo_selection` stores only the
selected `asset_id`, `manifest_hash`, `manifest_schema_version`, and
`selector_version` — never a filename, provider detail, or seed — reused
verbatim on redelivery rather than recomputed. A resume or a refinement
always inherits its source version's frozen mode; it is never re-derived
from current settings, so a later settings change can neither make an
in-flight demo attempt spend money nor silently turn a live version into a
demo one or vice versa.

### Demo precedence over every paid flag, `LIVE_GENERATION_ENABLED` applies only to live mode

`sitara.ai_gateway.policy.resolve_generation_mode()` is the single source of
truth for the public three-mode outcome (`"demo"`/`"live"`/`"unavailable"`):
when `DEMO_MODE=true`, only demo readiness is evaluated — live readiness is
**never** evaluated as a fallback from failed demo readiness, so a fully
configured paid provider can never make demo mode spend money and an unready
demo pack never silently falls back to a paid provider. `enqueue_design_generation`/
`enqueue_design_refinement` independently resolve and check the identical
precedence rule at enqueue time (a regression test,
`TestResolveGenerationModeAgreesWithEnqueue`, asserts the two stay
behaviourally consistent across the full settings matrix), resolved and
checked **before** the Design row lock is acquired so the enqueue
transaction stays a short, database-only operation. `LIVE_GENERATION_ENABLED`
continues to gate only the public live-generation API — it has no effect on
demo readiness or demo behaviour.

### No live fallback, ever

`demo_assets_unavailable` is the controlled error code for every demo
failure mode (missing manifest, invalid manifest, missing coverage, missing
asset, hash mismatch, private demo storage unavailable) — never
distinguishing which internal object or path failed, and never falling back
to a paid provider. `load_active_demo_manifest` is a deliberate fail-closed
boundary (matching the `sitara.health.checks` precedent): any storage
exception, not just an expected one, is treated as "not ready."

### Honest frontend labelling

A persistent, accessible, non-dismissible-in-a-hiding-way banner
(`role="status" aria-live="polite"`) appears site-wide whenever
`generation_mode=="demo"`. Progress copy, the result view, and the
refinement panel each carry demo-specific, honest wording — never
"contacting Claude"/"generating with FLUX"/"Replicate is rendering", never a
fake percentage, never a claim of image editing or seed continuity for a
demo refinement. Version comparison labels each version from its own
persisted `DesignResult.is_demo` — never inferred from the current public
configuration, so a demo version stays labelled demo even if the
environment later runs in live mode.

### Production demo pack as a separate human content prerequisite; development synthetic pack is not production content

The reviewed, rights-cleared production asset pack (~30-50 curated,
culturally reviewed, logo/watermark/designer-imitation-free images) is a
separate, manually budgeted, human-reviewed checkpoint that remains
**pending** — this phase delivers the pipeline that consumes such a pack,
not the pack itself. `sitara.generation.demo.synthetic_pack` is a
development-only, zero-network, programmatically generated six-asset pack
(`SyntheticPackNotAllowed` raised when `APP_ENV=production`) that exists
solely so local development and the automated test suite can exercise the
real pipeline without the reviewed pack; every synthetic asset's
`provenance_status` is `synthetic_development_placeholder` and its alt text
says so explicitly. Demo mode must never claim the production-quality pack
exists until its real files and verified rights provenance have actually
been supplied and checked.

### No public showcase gallery

Demo mode is limited to the private questionnaire → generation → result →
refinement journey for a design the current session/user owns — no public
gallery endpoint, no landing-page carousel, no unauthenticated demo-asset
listing of any kind.

### Obsolete Phase 3A synchronous demo path removed

The unused Phase 3A synchronous scaffolding
(`sitara.ai_gateway.providers.DemoStructuredDesignProvider`/
`DemoImageGenerationProvider`, `policy.get_structured_design_provider`/
`get_image_generation_provider`, and their dedicated tests) is removed —
it was never reachable from the real pipeline and predates this phase's
actual async local adapters. The live fail-closed paid-provider gateway
(`get_structured_design_generation_provider`,
`get_image_generation_provider_async`, `resolve_generation_mode`) and every
test-only fixture provider, offline fixture command, and injected-provider
test support are unaffected.

### Test fixtures remain separate from public demo functionality

`sitara.generation.fixture_provider.FixtureStructuredDesignProvider` (used
only by the automated test suite and the offline fixture management
command) and `sitara.generation.demo.design_spec_engine`/`provider` (the
real demo engine) are distinct modules with distinct provider names and
template versions (`"fixture"` vs `"demo"`/`demo-spec-1.0.0`) — the test
fixture path is never presented to a user and never reachable through the
public API; the demo engine is never used as a test double.

## Consequences

- The manual production-pack checkpoint (three-plus genuinely reviewed,
  rights-cleared images) remains pending; demo mode in any environment
  without an installed, valid manifest fails closed as `unavailable` rather
  than silently degrading.
- Phase 16 (rate limits, cost ceilings, security hardening) applies only to
  the live path; demo mode has no cost ceiling to enforce because it can
  never spend money by construction.
- A future additional demo-consuming surface (e.g. a public showcase) would
  need its own separately approved phase — this ADR's private-journey-only
  boundary is a deliberate scope limit, not a technical ceiling.

## Alternatives considered

- **Mocking the frontend directly (a fake result shortcut)** — rejected: it
  would not exercise the real pipeline, storage, or API contract, so a demo
  bug could hide a live-path bug and vice versa; CLAUDE.md's own
  non-negotiables require demo mode to be a distinct branch in the
  generation layer, never a mock hidden behind the paid-provider wrapper.
- **A single shared demo/live provider resolution function used at both the
  public `/config` endpoint and inside the enqueue transaction** — rejected
  for this phase as higher-risk than necessary: the two call sites already
  independently implement the identical precedence rule, and a cross-module
  consistency regression test closes the practical drift risk without
  touching two already-reviewed, heavily-tested enqueue functions this late
  in the phase. A future refactor may still consolidate them.
- **True image-to-image editing for demo refinement** — rejected, matching
  ADR 0015's reasoning for the live path: no evaluation has run, and demo
  refinement's job is to demonstrate the same constrained-DesignSpec-edit
  workflow live refinement uses, not to introduce a different mechanism.
- **Skipping the production-pack manual checkpoint and shipping only the
  synthetic pack everywhere** — rejected: the synthetic pack is explicitly
  abstract/placeholder imagery, unsuitable for a real public demo; shipping
  it as if it were reviewed content would violate the honest-labelling
  requirement this phase otherwise enforces everywhere else.
