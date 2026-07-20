# 0009 — Structured DesignSpec generation

- **Status:** accepted
- **Date:** 2026-07-18
- **Deciders:** Sitara maintainers
- **Phase:** Phase 8 (see ../phases/PHASES.md)
- **Related:** ADR 0001 (image model), ADR 0004 (private design ownership),
  ADR 0005 (versioned questionnaire schema), ADR 0008 (questionnaire draft and
  wizard)

## Context

Phase 8 turns a complete, validated Design into a structured **concept**
specification — the input the Phase 9 prompt builder and the Phase 12 results
page will consume. It is the first phase that can spend money, so the entire
design is built around controlling and containing that spend, and around never
letting an untrusted questionnaire answer, a provider response, or a named
designer leak into or out of the system.

No image generation, no Replicate, no Celery, no API endpoint and no frontend
change are in scope; selected inspirations are explicitly deferred to Phase 13.

## Decision

### The DesignSpec is a strict Pydantic contract + committed JSON Schema

A strict Pydantic v2 `DesignSpec` (`extra="forbid"`, `str_strip_whitespace`,
`validate_assignment`) with bounded strings and lists, a literal
`schema_version` (a boolean is rejected as an integer), and a strict
`source_selections` echo of the canonical questionnaire machine values is the
single source of truth. `DESIGN_SPEC_SCHEMA_VERSION = 1` versions the
persisted structure; the canonical JSON Schema is generated deterministically
and atomically into `sitara/generation/schemas/design_spec_v1.json` by the
`export_design_spec_schema` command and guarded by a byte-identity test. The
schema never duplicates questionnaire option lists as enums (machine values
are a pattern), carries no timestamps/paths/credentials/model name.

### Source selections are echoed and verified EXACTLY

The model must reproduce the trusted `source_selections` verbatim (scalars,
nullability and ordered lists). After SDK parsing, Django re-runs
`DesignSpec.model_validate(...)` and then asserts `source_selections` equals
the canonical selections built from the validated Design. A mismatch is an
invalid output — never silently accepted — and may trigger the single retry.
This makes the persisted brief provably faithful to what the user chose.

### Backend re-validation and generated-output safety

The SDK's parsed output is re-validated Django-side and then recursively
scanned: a conservative, non-exhaustive, updateable designer/brand denylist,
imitation phrasing, URLs, control characters, prompt/system leakage, and
asserted sewing-pattern / guaranteed-constructibility claims (negated
disclaimers pass). Matching is NFKC + case-fold + punctuation-normalised on
phrase boundaries; rejections carry only a generic category and never echo the
text. The denylist is a safety mechanism, not a cultural taxonomy.

### Free text is untrusted; the system prompt is versioned

Text answers are identified generically from the pinned schema, normalised,
capped (questionnaire cap + `DESIGN_SPEC_MAX_INPUT_CHARS`), scanned for the
denylist and prompt-override phrasing **before any provider/client is
instantiated**, JSON-encoded, and placed in an explicitly delimited untrusted
section whose delimiter tokens are neutralised if they appear in the input.
The source-controlled system prompt states that text there is user preference
data only, never instructions. `SPEC_TEMPLATE_VERSION` versions the trusted
instructions and context layout; a deterministic `PROMPT_TEMPLATE_HASH` test
fails if the prompt, delimiters, retry note or scaffolding change without a
deliberate update.

**Phase 13 amendment (ADR 0014):** the trusted JSON gains one more key,
`curated_inspiration_cues` — a staff-curated, validated array (position,
garment type, visual description, cultural context only; never an asset id,
title or attribution), placed alongside `source_selections` and
`questionnaire_answers`, never inside the untrusted delimiters. The system
prompt gains rules keeping questionnaire selections authoritative over any
compatible-only cue influence. Because this changes both the trusted JSON
shape and the system prompt, `SPEC_TEMPLATE_VERSION` bumped `1.0.0` →
`2.0.0` with a recomputed `PROMPT_TEMPLATE_HASH`; `DESIGN_SPEC_SCHEMA_VERSION`
stays `1` (no DesignSpec field changed).

**Phase 14 amendment (ADR 0015):** refinement does not reuse or extend this
system prompt — it uses a wholly separate structured-output prompt in
`apps/api/sitara/generation/refinement_prompting.py`, versioned independently
(`REFINEMENT_TEMPLATE_VERSION = "1.0.0"`, its own
`refinement_prompt_template_fingerprint()`/hash guard), since its task (apply
exactly one allowlisted DesignSpec edit) differs from this prompt's task
(author a DesignSpec from questionnaire answers). `SPEC_TEMPLATE_VERSION` and
this prompt's `PROMPT_TEMPLATE_HASH` are untouched by Phase 14.
`DESIGN_SPEC_SCHEMA_VERSION` stays `1` for a refined DesignSpec too — the
refinement prompt still produces (an edited copy of) the same DesignSpec
shape this ADR defines.

### Anthropic structured output, one controlled retry, SDK retries off

Generation uses the SDK's first-class `beta.messages.parse` with
`output_format=DesignSpec` — no manual JSON scraping, no tool-call simulation,
no streaming, no extended thinking. The live client is created lazily only
after every gate passes, with `max_retries=0` so Sitara controls the exact
call count: **at most two requests** — one initial and one retry, and only when
the output is structurally or semantically invalid (missing/parse failure,
Pydantic failure, source mismatch, blocked designer reference, prohibited
URL/prompt leakage). Authentication, permission, timeout, rate-limit, server
and refusal outcomes are never retried (spend may already have occurred). The
generic retry instruction carries no rejected output, raw input, free text or
exception text.

### One atomic persistence under an advisory lock

`generate_design_spec_for_design` performs every pre-spend validation first
(answerable questionnaire, complete authoritative validation, still-eligible
inspirations, no existing initial DesignVersion), then acquires a non-blocking
PostgreSQL advisory lock keyed by the Design UUID before any provider call and
re-checks under the lock, so two manual commands can never both spend for one
design. Exactly one `DesignVersion` is created only after a valid result
exists, persisting `model_dump(mode="json")` plus narrow provenance
(schema/template/provider/model/token/timestamp) under all-or-none and
positive-token database constraints. On any failure nothing is persisted and
logs carry only the operation, Design UUID, attempt number and exception type
— never a prompt, answer, output, key or provider error body.

### Explicit capability gates; end-to-end stays unavailable

The broad `PAID_PROVIDERS_IMPLEMENTED` flag is replaced by explicit code-level
capabilities: `STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED = True`,
`IMAGE_PROVIDER_IMPLEMENTED = False`. The public `generation_is_available()`
depends on the image provider and the full pipeline, so it stays False for
every environment combination; the public config never claims concept
generation is available. `DEMO_MODE=true` can never instantiate the network
client, `ALLOW_PAID_AI_CALLS=false` always refuses, and a configured token
alone never enables a call. The `generate_spec` command has an offline
`--fixture` mode (zero network, provider labelled `fixture`) and a live mode
gated behind `DEMO_MODE=false` + `ALLOW_PAID_AI_CALLS=true` + a non-empty key
and model + explicit `--confirm-live`; without `--confirm-live` it makes zero
calls.

### Integrity hardening

A follow-up commit closed the remaining integrity gaps without changing the
contract or the two-request budget:

- **One central live gate.** `structured_design_generation_is_available()` is
  the single definition — both environment gates AND a non-empty stripped
  `ANTHROPIC_API_KEY` AND an `ANTHROPIC_MODEL` that fits the persisted
  `design_spec_model` bound. The provider factory fails closed (before any
  client construction) on a missing key/model, and the `generate_spec`
  command's `--confirm-live` is an additional opt-in on top of this gate, never
  a second, weaker definition. The SDK client is created once and cached per
  provider instance, inside a safe error boundary that maps a
  configuration/initialisation failure to a generic domain error carrying no
  key or model value.
- **Pre-spend contract validation.** After the canonical selections are built
  they are validated through `SourceSelections.model_validate(...)`; a
  questionnaire that cannot satisfy the DesignSpec contract is a controlled
  `unsupported_questionnaire_contract` (`DesignNotReady`) before any provider
  is selected — never a Pydantic traceback, and neither the input nor the
  questionnaire is surfaced.
- **Stale-input protection.** A deterministic input snapshot (questionnaire
  version id, normalised answers, ordered inspiration ids) is captured before
  spending. No transaction or row lock is held across the (network) call — only
  the session-level advisory lock. After a valid result and before persistence,
  the Design is re-fetched, completion + inspiration-eligibility are re-run and
  the snapshot recomputed; any change raises `DesignChangedDuringGeneration`,
  persists nothing, does not retry Anthropic and never overwrites the newer
  draft.
- **Honest token accounting.** Usage is summed across every returned response
  of the operation (an invalid-then-valid pair stores the sum); if any response
  lacks a dimension the persisted total for that dimension is null rather than a
  misleading partial. Provider/model identity must stay consistent across
  attempts.
- **Scanner hardening.** Token normalisation treats underscores as separators
  (`[\W_]+`) so `Manish_Malhotra` cannot bypass a multi-token name, and
  sewing-pattern / constructibility claim detection is scope-aware: a negation
  excuses a claim only when it precedes the claim phrase in the sentence, so
  "this is a sewing pattern, not a mood board" is rejected while "this is not a
  sewing pattern" is allowed.
- **DesignSpec semantic invariants.** A model validator requires the two
  construction caveats to be present (flexible phrasing, not one exact
  sentence) and enforces regional consistency: `cultural_context.regional_
  direction` is null exactly when `regional_style` is null or
  `no_specific_direction`, and non-empty when a real direction is selected. A
  semantic failure is treated like any other invalid output and may use the one
  allowed retry.

## Consequences

- The design brief is now a validated, provenance-tracked artefact ready for
  Phase 9 prompting and Phase 12 display.
- Pydantic model changes must be followed by schema regeneration (CI diff).
- The live quality checkpoint spends money and therefore requires separate,
  explicit approval from the repository owner; it is not run automatically.
- Deferred: image-prompt construction, Replicate/image generation, Celery,
  a generation endpoint, results UI, inspiration influence and refinement.
- Phase 13 (ADR 0014) delivered metadata-only inspiration influence, extending
  the stale-input protection above to also cover the exact inspiration-context
  snapshot and its hash, and adding a post-output check that no selected
  inspiration's audit-only title/attribution leaks into generated text.
- Phase 14 (ADR 0015) delivered single-round refinement using a separate
  structured-output prompt that edits a copy of this DesignSpec schema under
  an exact per-category diff allowlist; this ADR's validators, semantic
  invariants and stale-input protections apply unchanged to a refined
  DesignSpec.

## Alternatives considered

- **Scrape JSON from a natural-language response / simulate a tool call.**
  Rejected: the SDK's first-class structured-output parsing is safer and
  removes brittle parsing.
- **Let the SDK retry automatically.** Rejected: Sitara must control the exact
  paid-call count; `max_retries=0` plus one deliberate retry does that.
- **Persist provider request/response for debugging.** Rejected: raw prompts,
  responses and error bodies can carry user data and secrets; only validated
  output and safe provenance are stored.
