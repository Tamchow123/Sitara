"""Runtime security-header assertions (Phase 16, Part D).

Exercises the actual middleware stack via the Django test client: the CSP
middleware and Django's SecurityMiddleware/XFrameOptionsMiddleware emit the
headers on a real response. Uses the liveness endpoint (no DB, no auth).
"""

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def test_api_response_carries_the_restrictive_csp():
    response = Client().get("/api/v1/health/live")
    assert response.status_code == 200
    assert (
        response.headers.get("Content-Security-Policy")
        == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )


def test_api_response_carries_frame_nosniff_and_referrer_headers():
    response = Client().get("/api/v1/health/live")
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Referrer-Policy") == "same-origin"


def test_admin_requires_authentication_when_enabled():
    # ADMIN_ENABLED defaults True in the (non-production) test environment, so
    # the route is mounted — but anonymous access is never served; enabling the
    # admin never bypasses Django's staff/superuser gate.
    response = Client().get("/admin/")
    assert response.status_code in (301, 302)
    assert "/admin/login" in response.headers.get("Location", "")
