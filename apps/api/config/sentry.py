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


#: Everything from the first of these onwards is cut off a URL. Mirrored by
#: ``scrubUrl`` in ``apps/web/src/lib/sentry-scrub.ts`` and held there by
#: ``sentry-scrub-parity.test.ts`` — one rule, two runtimes, and the guarantee
#: is only ever as good as the weaker of them.
URL_CUT_MARKERS = "?#"

#: Breadcrumb ``data`` keys that hold a URL. Mirrored and parity-checked the
#: same way. A breadcrumb's own ``message`` is cut too — a navigation crumb's
#: message is very often just the URL.
BREADCRUMB_URL_FIELDS = ("url", "to", "from")


def _cut_url(url):
    """Keep only a URL's path — everything from the first ``?`` OR ``#`` goes.

    The query half has always been dropped: it can carry a signed-URL parameter.
    The FRAGMENT half is Phase 22's (ADR 0026). A reference upload grant reaches
    the customer's phone as ``/r#<secret>``, deliberately in the fragment because
    browsers do not send fragments to servers — but "the server never receives
    it" is not the same as "nothing on the server could ever hold it", and a
    scrubber that cut only at ``?`` would sail straight past a URL with no query
    string at all. Cutting at whichever marker comes first costs nothing and
    makes the guarantee unconditional.

    Non-strings pass through untouched, so a malformed event cannot raise inside
    ``before_send`` and lose the whole report."""
    if not isinstance(url, str):
        return url
    for index, character in enumerate(url):
        if character in URL_CUT_MARKERS:
            return url[:index]
    return url


def _scrub_breadcrumb_urls(breadcrumbs) -> None:
    """Apply the same cut to every breadcrumb URL, in both shapes the SDK uses.

    Breadcrumbs are a separate channel from ``request``, and one carrying a
    navigation URL would otherwise be the one place a fragment could survive.
    Field for field the same rule as the frontend's ``scrubBreadcrumb`` —
    ``data.url``/``to``/``from`` plus the crumb's own ``message`` — because a
    guarantee stated once about "the Sentry scrubber" (CLAUDE.md §13) is worth
    only as much as its weaker implementation. The two sensitive-HEADER sets
    deliberately differ: only the backend sees ``x-request-id``.

    An unrecognised shape is left alone rather than raising, consistent with
    every other guard in this module. That is a fail-open, so a ``sentry-sdk``
    version bump must re-check the shape a breadcrumb actually has at
    ``before_send`` time — a changed envelope would silently stop the scrubbing
    with every test here still green, because these tests supply their own."""
    if isinstance(breadcrumbs, dict):
        breadcrumbs = breadcrumbs.get("values")
    if not isinstance(breadcrumbs, list):
        return
    for crumb in breadcrumbs:
        if not isinstance(crumb, dict):
            continue
        data = crumb.get("data")
        if isinstance(data, dict):
            for field in BREADCRUMB_URL_FIELDS:
                if field in data:
                    data[field] = _cut_url(data[field])
        if "url" in crumb:
            crumb["url"] = _cut_url(crumb["url"])
        if "message" in crumb:
            crumb["message"] = _cut_url(crumb["message"])


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
    cookies, sensitive headers, user identity, and everything in a URL from the
    first ``?`` or ``#`` — in the request AND in every breadcrumb; reduces any
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
        if "url" in request:
            request["url"] = _cut_url(request["url"])
    # Never attach user identity (email/username/ip).
    event.pop("user", None)
    _scrub_breadcrumb_urls(event.get("breadcrumbs"))

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
