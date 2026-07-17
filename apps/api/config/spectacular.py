"""drf-spectacular preprocessing hooks (Phase 6).

The API's browser-facing routes are slash-OPTIONAL at runtime (``^csrf/?$``)
because the Next.js rewrite strips trailing slashes before proxying and an
``APPEND_SLASH`` redirect would loop. That optional slash is a runtime
routing concern only; the committed OpenAPI contract must show exactly one
canonical path per operation.

``normalise_trailing_slash`` collapses the optional-slash artifact to the
documented canonical spelling WITHOUT touching runtime routing (it only
rewrites the strings drf-spectacular hands to the schema generator) and
without altering path parameters such as ``{design_id}``. Health and
public-config routes are plain ``path()`` entries with no trailing slash and
are left exactly as they are.
"""

import re

# A terminal optional slash as drf-spectacular renders ``/?$`` regex routes.
_OPTIONAL_SLASH_SUFFIX = re.compile(r"/\?$")


def normalise_trailing_slash(endpoints, **kwargs):
    """Return the endpoint list with terminal optional-slash artifacts
    collapsed to a single canonical trailing slash.

    ``endpoints`` is a list of ``(path, path_regex, method, callback)``
    tuples. Only the ``path`` string is normalised; path parameters and the
    callback are preserved, and no endpoint is added or removed.
    """
    normalised = []
    for path, path_regex, method, callback in endpoints:
        canonical = _OPTIONAL_SLASH_SUFFIX.sub("/", path)
        normalised.append((canonical, path_regex, method, callback))
    return normalised
