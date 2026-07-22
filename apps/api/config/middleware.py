"""Security-header middleware (Phase 16, Part D).

Adds a Content-Security-Policy to every response. Django's built-in
SecurityMiddleware/XFrameOptionsMiddleware already emit HSTS, nosniff, referrer
policy, cross-origin-opener-policy and X-Frame-Options from settings; only CSP
has no built-in, so it lives here.

The JSON API loads no browser resources, so it gets the most restrictive policy
(``default-src 'none'``). The Django admin — only ever reachable when the admin
route is actually mounted (see config/urls.py) — gets a policy compatible with
its own inline styles/scripts. An existing CSP header on a response is never
overwritten.
"""

from django.conf import settings

from .correlation import coerce_request_id, set_attempt_id, set_request_id


class RequestCorrelationMiddleware:
    """Establish a request-local correlation id for the duration of the request
    (Phase 16, Part E). Accepts a client ``X-Request-ID`` only when it is a valid
    canonical UUID (otherwise a fresh one is generated), echoes the effective id
    back in the ``X-Request-ID`` response header, and ALWAYS clears the context
    afterwards — on success and on exception — so it never leaks between
    requests on a reused worker thread."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = coerce_request_id(request.headers.get("X-Request-ID"))
        request_token = set_request_id(request_id)
        attempt_token = set_attempt_id(None)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            return response
        finally:
            # Reset to the previous context values (None at request scope), so a
            # reused thread never inherits this request's ids.
            request_token.var.reset(request_token)
            attempt_token.var.reset(attempt_token)


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if "Content-Security-Policy" not in response:
            if request.path.startswith("/admin/"):
                response["Content-Security-Policy"] = settings.ADMIN_CONTENT_SECURITY_POLICY
            else:
                response["Content-Security-Policy"] = settings.API_CONTENT_SECURITY_POLICY
        return response
