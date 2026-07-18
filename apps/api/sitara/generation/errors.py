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

# Catch-all for anything unclassified — an unexpected exception becomes this,
# never a raw message.
INTERNAL_GENERATION_ERROR = "internal_generation_error"

# The complete allowlist. Persisted/returned codes are validated against it.
GENERATION_ERROR_CODES = frozenset(
    {
        QUEUE_UNAVAILABLE,
        GENERATION_UNAVAILABLE,
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
        INTERNAL_GENERATION_ERROR,
    }
)


def is_valid_error_code(code: str) -> bool:
    return code in GENERATION_ERROR_CODES
