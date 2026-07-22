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
