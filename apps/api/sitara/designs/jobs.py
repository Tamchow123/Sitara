"""The single public shape of a generation job (Phase 10).

A ``GenerationAttempt`` carries private provenance — provider, model, prediction
id, seed, server-authored parameters, staged storage key, image hash/size and
the Celery task id. NONE of that is public. The job payload exposes only the
lifecycle: ids, status, a stable error code and timestamps. Keeping the shape
in one function means no view can accidentally widen it.
"""

from rest_framework import serializers

from sitara.generation import errors as generation_errors

from .models import Design, GenerationAttempt

# One shared DRF field for ISO-8601 timestamps, matching the rest of the API.
_DATETIME = serializers.DateTimeField()


def _iso(value):
    return _DATETIME.to_representation(value) if value is not None else None


def latest_generation_attempt(design: Design) -> GenerationAttempt | None:
    """The design's most recent generation attempt, or None.

    Selected deterministically (newest ``created_at``, UUID as the
    tie-breaker) so durable navigation — resuming a generating design,
    revisiting a generated one, or returning to a failed one with a linked
    version — always lands on the same attempt. Used only to expose one
    sanitised public job snapshot on design detail; never on design-list
    responses."""
    return design.generation_attempts.order_by("-created_at", "-id").first()


def _public_error_code(code: str) -> str | None:
    """Defence in depth on the allowlist boundary: a persisted code outside
    the stable set (only possible via legacy data or manual intervention —
    migration 0005 normalises legacy rows) is reported as the generic
    internal code rather than echoed."""
    if not code:
        return None
    if not generation_errors.is_valid_error_code(code):
        return generation_errors.INTERNAL_GENERATION_ERROR
    return code


def public_job_payload(attempt: GenerationAttempt) -> dict:
    """The stable ``{"job": {...}}`` body for one accessible attempt.

    Deliberately omits every provider/storage/provenance field. ``error_code``
    is one of the stable machine codes (or null); it is never a provider
    message."""
    return {
        "job": {
            "id": str(attempt.id),
            "design_id": str(attempt.design_id),
            "design_version_id": (
                str(attempt.design_version_id) if attempt.design_version_id else None
            ),
            "status": attempt.status,
            "error_code": _public_error_code(attempt.error_code),
            "created_at": _iso(attempt.created_at),
            "updated_at": _iso(attempt.updated_at),
            "started_at": _iso(attempt.started_at),
            "completed_at": _iso(attempt.completed_at),
        }
    }
