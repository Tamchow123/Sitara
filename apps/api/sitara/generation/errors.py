"""Stable, source-controlled generation error codes (Phase 10).

The ONLY values that may ever be persisted onto ``GenerationAttempt.error_code``
or returned to a caller. They are deliberately coarse machine codes — never a
provider error body, exception message, prompt, answer, output URL or storage
key. An unexpected exception is always mapped to ``internal_generation_error``
so no raw text can leak through the job API or the logs.

Keeping the allowlist in one module means a persisted code can be asserted
against it in tests and a reviewer can see the complete surface at a glance.
"""

# Enqueue / availability boundary.
QUEUE_UNAVAILABLE = "queue_unavailable"
GENERATION_UNAVAILABLE = "generation_unavailable"
# Demo mode is active but its asset pack is not ready: missing/invalid
# manifest, required garment coverage absent, a selected asset missing or
# hash-mismatched, or private demo storage unavailable. Never exposes which
# internal object or path failed; never falls back to a paid provider.
DEMO_ASSETS_UNAVAILABLE = "demo_assets_unavailable"

# Domain-readiness failures discovered during the task.
DESIGN_INCOMPLETE = "design_incomplete"
DESIGN_CHANGED = "design_changed"

# Structured (Anthropic) stage.
STRUCTURED_GENERATION_FAILED = "structured_generation_failed"
# The provider MAY have accepted (and billed) a text request that was never
# confirmed either way (crash or ambiguous transport failure between
# submission and version linkage). Spend is unresolved, so — like every code
# outside ``pipeline._SPEND_RESOLVED_CODES`` — the enqueue guard blocks
# regeneration whenever the failed attempt carries submission evidence.
STRUCTURED_SUBMISSION_AMBIGUOUS = "structured_submission_ambiguous"
STRUCTURED_PROVIDER_REFUSED = "structured_provider_refused"

# Prompt stage.
PROMPT_BUILD_FAILED = "prompt_build_failed"

# Image (Replicate) stage.
IMAGE_PROVIDER_UNAVAILABLE = "image_provider_unavailable"
# The provider MAY have accepted (and billed) a create call that was never
# confirmed either way. Spend is unresolved, so — like every code outside
# ``pipeline._SPEND_RESOLVED_CODES`` — the enqueue guard blocks regeneration
# whenever the failed attempt carries submission evidence.
IMAGE_SUBMISSION_AMBIGUOUS = "image_submission_ambiguous"
IMAGE_PREDICTION_FAILED = "image_prediction_failed"
IMAGE_PREDICTION_CANCELED = "image_prediction_canceled"
IMAGE_PREDICTION_ABORTED = "image_prediction_aborted"
IMAGE_POLL_TIMEOUT = "image_poll_timeout"
IMAGE_DOWNLOAD_FAILED = "image_download_failed"
IMAGE_OUTPUT_INVALID = "image_output_invalid"
IMAGE_STAGING_FAILED = "image_staging_failed"
# Staged-output state could NOT be confirmed: transient storage failures
# outlasted the bounded retry budget. Distinct from ``image_staging_failed``
# (content CONFIRMED bad) because already-paid output may still be intact —
# the enqueue guard keeps blocking regeneration for this code whenever
# provider spend may have occurred (staged metadata, an accepted prediction
# id, or a still-set in-flight submission marker on the failed attempt; see
# ``pipeline._SPEND_RESOLVED_CODES`` for the confirmed-outcome allowlist).
IMAGE_STAGING_UNVERIFIED = "image_staging_unverified"

# Permanent ingest (Phase 11 stage E). Both codes occur AFTER paid output was
# already staged, so NEITHER may ever cause automatic image resubmission or
# admit another paid prediction (neither is in ``_SPEND_RESOLVED_CODES``, and
# a failed ingest attempt still carries its staged metadata, which blocks the
# enqueue guard). Recovery reruns ONLY storage verification/ingest — a bounded
# task retry for the unverified code, or the ``ingest_design_image`` operator
# command.
# Transient or ambiguous permanent-storage availability; content state unknown.
IMAGE_INGEST_UNVERIFIED = "image_ingest_unverified"
# Confirmed corrupt, conflicting or invalid permanent content.
IMAGE_INGEST_FAILED = "image_ingest_failed"

# Refinement (Phase 14) text stage. Distinguished from the initial-generation
# structured-stage codes so a client/frontend can render honest refinement
# wording without inspecting generation_kind.
# The client-submitted refinement request itself was invalid or its note
# failed the pre-provider safety scan — a controlled 400 at enqueue time; this
# code is never persisted onto a GenerationAttempt (the request is rejected
# before any attempt is created), but is listed here so it can share the same
# stable-code contract as every other refinement/API error surface.
REFINEMENT_INVALID = "refinement_invalid"
# Every allowed attempt produced output identical to the source DesignSpec.
REFINEMENT_NO_CHANGE = "refinement_no_change"
# A technical structured-generation failure other than "no change" (invalid
# output after retries, a disallowed field change, an unsafe output).
REFINEMENT_GENERATION_FAILED = "refinement_generation_failed"
# This design has already been refined once, or MAX_DESIGN_VERSIONS is
# already reached.
REFINEMENT_LIMIT_REACHED = "refinement_limit_reached"
# The source version is missing, not version 1, or its persisted provenance
# (spec/prompt/image/inspiration-context) is incomplete or corrupt.
REFINEMENT_SOURCE_UNAVAILABLE = "refinement_source_unavailable"

# The ingest-stage terminal codes the operator recovery path may act on —
# defined ONCE so the ``ingest_design_image`` command's admission gate and
# ``pipeline.finalise_ingest_recovery``'s completion guard can never drift.
INGEST_STAGE_ERROR_CODES = frozenset({IMAGE_INGEST_FAILED, IMAGE_INGEST_UNVERIFIED})

# Live-generation cost control (Phase 16). Raised when the atomic pre-spend
# reservation immediately before a billable provider call would exceed the hard
# daily budget ceiling. The reservation fails BEFORE any submission marker is
# set, so the attempt carries no submission evidence and is freely retryable
# (it is deliberately NOT in ``_SPEND_RESOLVED_CODES`` and provably never billed).
# Also returned as a synchronous 503 by the cheap enqueue-time budget preflight
# (Phase 16 Part B) — the same stable code across both transport positions.
LIVE_GENERATION_BUDGET_EXHAUSTED = "live_generation_budget_exhausted"

# Catch-all for anything unclassified — an unexpected exception becomes this,
# never a raw message.
INTERNAL_GENERATION_ERROR = "internal_generation_error"

# The complete allowlist. Persisted/returned codes are validated against it.
GENERATION_ERROR_CODES = frozenset(
    {
        QUEUE_UNAVAILABLE,
        GENERATION_UNAVAILABLE,
        DEMO_ASSETS_UNAVAILABLE,
        DESIGN_INCOMPLETE,
        DESIGN_CHANGED,
        STRUCTURED_GENERATION_FAILED,
        STRUCTURED_SUBMISSION_AMBIGUOUS,
        STRUCTURED_PROVIDER_REFUSED,
        PROMPT_BUILD_FAILED,
        IMAGE_PROVIDER_UNAVAILABLE,
        IMAGE_SUBMISSION_AMBIGUOUS,
        IMAGE_PREDICTION_FAILED,
        IMAGE_PREDICTION_CANCELED,
        IMAGE_PREDICTION_ABORTED,
        IMAGE_POLL_TIMEOUT,
        IMAGE_DOWNLOAD_FAILED,
        IMAGE_OUTPUT_INVALID,
        IMAGE_STAGING_FAILED,
        IMAGE_STAGING_UNVERIFIED,
        IMAGE_INGEST_UNVERIFIED,
        IMAGE_INGEST_FAILED,
        REFINEMENT_INVALID,
        REFINEMENT_NO_CHANGE,
        REFINEMENT_GENERATION_FAILED,
        REFINEMENT_LIMIT_REACHED,
        REFINEMENT_SOURCE_UNAVAILABLE,
        LIVE_GENERATION_BUDGET_EXHAUSTED,
        INTERNAL_GENERATION_ERROR,
    }
)


def is_valid_error_code(code: str) -> bool:
    return code in GENERATION_ERROR_CODES
