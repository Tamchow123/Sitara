"""Request/job correlation context (Phase 16, Part E).

A bounded, request-local correlation id (and, for Celery, the attempt/job id)
carried via ``contextvars`` so structured log records and Sentry events can be
tied to one request or one generation attempt WITHOUT threading an id through
every call. The context is always cleared after each request/task (success and
exception) so it never leaks between them.

Nothing sensitive is stored here: a request id is a server-owned UUID (or a
client-supplied one only when it is a valid canonical UUID), and the attempt id
is the generation attempt UUID — never a raw session key, IP, email or token.
"""

from __future__ import annotations

import contextvars
import uuid

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "sitara_request_id", default=None
)
_attempt_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "sitara_attempt_id", default=None
)


def coerce_request_id(raw) -> str:
    """Return a canonical request id: a client-supplied ``X-Request-ID`` is
    honoured ONLY when it parses as a canonical UUID (which also bounds its
    length); anything malformed, oversized or absent is replaced with a fresh
    server-generated UUID."""
    if raw:
        try:
            return str(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            pass
    return str(uuid.uuid4())


def set_request_id(value: str | None):
    return _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_attempt_id(value: str | None):
    return _attempt_id.set(value)


def get_attempt_id() -> str | None:
    return _attempt_id.get()


def clear() -> None:
    """Reset both ids — called in a ``finally`` after every request/task so the
    context never leaks into the next one on a reused worker thread/process."""
    _request_id.set(None)
    _attempt_id.set(None)
