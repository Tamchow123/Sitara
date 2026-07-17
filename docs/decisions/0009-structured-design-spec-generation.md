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

## Consequences

- The design brief is now a validated, provenance-tracked artefact ready for
  Phase 9 prompting and Phase 12 display.
- Pydantic model changes must be followed by schema regeneration (CI diff).
- The live quality checkpoint spends money and therefore requires separate,
  explicit approval from the repository owner; it is not run automatically.
- Deferred: image-prompt construction, Replicate/image generation, Celery,
  a generation endpoint, results UI, inspiration influence and refinement.

## Alternatives considered

- **Scrape JSON from a natural-language response / simulate a tool call.**
  Rejected: the SDK's first-class structured-output parsing is safer and
  removes brittle parsing.
- **Let the SDK retry automatically.** Rejected: Sitara must control the exact
  paid-call count; `max_retries=0` plus one deliberate retry does that.
- **Persist provider request/response for debugging.** Rejected: raw prompts,
  responses and error bodies can carry user data and secrets; only validated
  output and safe provenance are stored.
