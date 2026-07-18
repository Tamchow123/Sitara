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
STRUCTURED_PROVIDER_REFUSED = "structured_provider_refused"

# Prompt stage.
PROMPT_BUILD_FAILED = "prompt_build_failed"

# Image (Replicate) stage.
IMAGE_PROVIDER_UNAVAILABLE = "image_provider_unavailable"
IMAGE_SUBMISSION_AMBIGUOUS = "image_submission_ambiguous"
IMAGE_PREDICTION_FAILED = "image_prediction_failed"
IMAGE_PREDICTION_CANCELED = "image_prediction_canceled"
IMAGE_PREDICTION_ABORTED = "image_prediction_aborted"
IMAGE_POLL_TIMEOUT = "image_poll_timeout"
IMAGE_DOWNLOAD_FAILED = "image_download_failed"
IMAGE_OUTPUT_INVALID = "image_output_invalid"
IMAGE_STAGING_FAILED = "image_staging_failed"

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
        INTERNAL_GENERATION_ERROR,
    }
)


def is_valid_error_code(code: str) -> bool:
    return code in GENERATION_ERROR_CODES
