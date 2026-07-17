"""Shared OpenAPI schema serializers for the stable API error envelope.

Every Sitara API error is the same JSON shape — ``{"error": {"code",
"message"}}`` — with an optional per-field ``fields`` mapping on validation
failures. These serializers exist ONLY to document that contract for
drf-spectacular; they are never used to render runtime responses (the views
build the envelope directly). Keeping them here, rather than duplicated per
app, is deliberate: the envelope is genuinely cross-cutting, while each
app's own request/response shapes live in that app's ``openapi.py``.
"""

from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers

# Shared documentation for the CSRF header carried on unsafe browser
# requests. The token is bootstrapped from GET /api/v1/auth/csrf/ and echoed
# back here; see the top-level API description for the full session/CSRF flow.
CSRF_HEADER_PARAMETER = OpenApiParameter(
    name="X-CSRFToken",
    type=str,
    location=OpenApiParameter.HEADER,
    required=True,
    description=(
        "CSRF token obtained from GET /api/v1/auth/csrf/. Required on every "
        "unsafe (POST/PATCH) browser request; a missing or stale token yields "
        "403 csrf_failed."
    ),
)


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="Stable machine-readable error code.")
    message = serializers.CharField(help_text="Safe, user-facing message; never echoes secrets.")


class ErrorEnvelopeSerializer(serializers.Serializer):
    """The standard error body for 401/403/404/429/503 responses."""

    error = ErrorDetailSerializer()


class FieldValidationErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="Stable machine-readable error code.")
    message = serializers.CharField(help_text="Safe, user-facing message.")
    fields = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        help_text="Per-field validation messages keyed by field name.",
    )


class ValidationErrorEnvelopeSerializer(serializers.Serializer):
    """The error body for 400 validation failures (adds ``fields``)."""

    error = FieldValidationErrorDetailSerializer()
