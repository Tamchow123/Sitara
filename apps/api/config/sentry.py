"""Privacy-safe Sentry integration (Phase 16, Part E).

Disabled entirely when no DSN is configured (the default), so tests and CI make
NO Sentry network call. When enabled it is configured to capture no PII: request
bodies are never sent, cookies and authorisation/CSRF headers are stripped,
query strings (which may carry signed-URL parameters) are removed, user identity
is dropped, and tracing is off by default. Correlation ids are attached as tags.

The exception payload is held to the same rule as the JsonFormatter (see
``config/logging.py``): only the exception TYPE survives — the exception message
and any captured stack-frame local variables are stripped, because either may
embed API keys/tokens, provider request bodies, storage keys, or raw refinement
notes. Local-variable capture is additionally disabled at the SDK level, and the
default ``LoggingIntegration`` is neutralised so log records never become Sentry
events/breadcrumbs (the JsonFormatter is the only logging channel). Sentry is
therefore limited to genuinely unhandled exceptions, reported as type + stack
location only.

``sentry_sdk`` is imported lazily inside ``init_sentry`` so importing this module
never requires the package when Sentry is disabled.
"""

from __future__ import annotations

from .correlation import get_attempt_id, get_request_id

# Headers scrubbed from every event (case-insensitive).
_SENSITIVE_HEADERS = {"cookie", "authorization", "x-csrftoken", "x-csrf-token", "x-request-id"}


def _strip_frame_locals(stacktrace) -> None:
    """Drop captured per-frame local variables from a stacktrace. Locals can hold
    secrets (tokens, provider bodies, storage keys, raw notes); we never ship
    them, even if a future SDK/integration re-enables local capture."""
    if not isinstance(stacktrace, dict):
        return
    frames = stacktrace.get("frames")
    if isinstance(frames, list):
        for frame in frames:
            if isinstance(frame, dict):
                frame.pop("vars", None)


def _scrub_exception_values(values) -> None:
    """Reduce every captured exception to its TYPE only — the message and any
    frame locals (both able to embed private data) are removed, mirroring the
    JsonFormatter's exception-type-only guarantee."""
    if not isinstance(values, list):
        return
    for value in values:
        if isinstance(value, dict):
            # Keep the type; discard the message text, which may embed input.
            value["value"] = ""
            _strip_frame_locals(value.get("stacktrace"))


def scrub_event(event, _hint=None):
    """``before_send`` hook (also directly unit-tested). Removes request bodies,
    cookies, sensitive headers, query strings and user identity; reduces any
    exception payload to its type (no message, no frame locals); and attaches the
    current correlation ids as tags. Pure dict manipulation — no ``sentry_sdk``
    import needed."""
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        request.pop("query_string", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in list(headers):
                if name.lower() in _SENSITIVE_HEADERS:
                    headers.pop(name, None)
        url = request.get("url")
        if isinstance(url, str) and "?" in url:
            # Drop a query string that could contain a signed-URL parameter.
            request["url"] = url.split("?", 1)[0]
    # Never attach user identity (email/username/ip).
    event.pop("user", None)

    # Exception message + stack-frame locals may carry secrets/input: strip both,
    # leaving only the exception type and stack location.
    exception = event.get("exception")
    if isinstance(exception, dict):
        _scrub_exception_values(exception.get("values"))
    # ``threads`` can also carry stacktraces with locals (e.g. crashed workers).
    threads = event.get("threads")
    if isinstance(threads, dict):
        for thread in threads.get("values", []) or []:
            if isinstance(thread, dict):
                _strip_frame_locals(thread.get("stacktrace"))

    tags = event.setdefault("tags", {})
    request_id = get_request_id()
    if request_id:
        tags["request_id"] = request_id
    attempt_id = get_attempt_id()
    if attempt_id:
        tags["attempt_id"] = attempt_id
    return event


def init_sentry(*, dsn: str, environment: str, release: str) -> bool:
    """Initialise Sentry ONLY when a DSN is supplied. Returns whether it was
    initialised (False = disabled, no network client constructed)."""
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            # Neutralise the default LoggingIntegration: log records must never
            # become Sentry breadcrumbs (level=None) or events (event_level=None).
            # The JsonFormatter is the only logging channel and is already limited
            # to safe fields + the exception type, so routing logs to Sentry would
            # reopen the traceback/secret leak this slice closes.
            LoggingIntegration(level=None, event_level=None),
        ],
        send_default_pii=False,
        # Never capture stack-frame local variables (they can hold secrets/input);
        # scrub_event strips them defensively too.
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        environment=environment,
        release=release or None,
        before_send=scrub_event,
    )
    return True
