"""Request parsing and field validation for the auth endpoints.

All failures resolve to the stable error envelope::

    {"error": {"code": ..., "message": ..., "fields": {...}}}

Nothing here logs or echoes passwords."""

import json
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

EMAIL_MAX_LENGTH = 254


class RequestValidationError(Exception):
    def __init__(self, code: str, message: str, fields: dict[str, list[str]] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields or {}


def parse_json_body(request) -> dict[str, Any]:
    content_type = (request.content_type or "").lower()
    if content_type != "application/json":
        raise RequestValidationError(
            "invalid_content_type", "Requests must use the application/json content type."
        )
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise RequestValidationError(
            "invalid_json", "The request body is not valid JSON."
        ) from None
    if not isinstance(body, dict):
        raise RequestValidationError("invalid_json", "The request body must be a JSON object.")
    return body


def _field_string(body: dict[str, Any], name: str, max_length: int) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise RequestValidationError(
            "validation_failed",
            "Please correct the highlighted fields.",
            fields={name: ["This field is required."]},
        )
    if len(value) > max_length:
        raise RequestValidationError(
            "validation_failed",
            "Please correct the highlighted fields.",
            fields={name: [f"Must be at most {max_length} characters."]},
        )
    return value


def extract_email(body: dict[str, Any]) -> str:
    """Raw (pre-canonicalisation) email with format validation."""
    email = _field_string(body, "email", EMAIL_MAX_LENGTH).strip()
    try:
        validate_email(email)
    except ValidationError:
        raise RequestValidationError(
            "validation_failed",
            "Please correct the highlighted fields.",
            fields={"email": ["Enter a valid email address."]},
        ) from None
    return email


def extract_password(body: dict[str, Any], field: str = "password") -> str:
    return _field_string(body, field, settings.AUTH_PASSWORD_MAX_LENGTH)
