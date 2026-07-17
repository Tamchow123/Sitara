"""Phase 3B session-authentication tests.

All flows run with REAL CSRF enforcement (Client(enforce_csrf_checks=True)).
Helpers assign a unique REMOTE_ADDR and email per test so the Redis-backed
rate limiters cannot bleed between tests.

This module doubles as a test-only ROOT_URLCONF exposing one protected DRF
probe endpoint, used solely to prove that session login satisfies the
authenticated-by-default API boundary (see docs: middleware is UX; Django
permissions are the security boundary)."""

import json
import uuid

import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import include, path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from sitara.accounts import rate_limits
from sitara.accounts.models import User

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "Correct-Horse-Battery-2026!"


@api_view(["GET"])
def protected_probe(request):
    """Test-only endpoint relying on the DRF defaults (IsAuthenticated)."""
    return Response({"probe": "ok"})


urlpatterns = [
    path("api/v1/test/protected/", protected_probe),
    path("", include("config.urls")),
]


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.test"


def unique_ip() -> str:
    raw = uuid.uuid4().bytes
    return f"10.{raw[0]}.{raw[1]}.{raw[2]}"


def csrf_client() -> Client:
    return Client(enforce_csrf_checks=True)


def bootstrap_csrf(client: Client) -> str:
    response = client.get("/api/v1/auth/csrf/")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def post_json(
    client: Client, url: str, data: dict, token: str | None = None, ip: str | None = None
):
    extra = {"REMOTE_ADDR": ip or unique_ip()}
    if token is not None:
        extra["HTTP_X_CSRFTOKEN"] = token
    return client.post(url, data=json.dumps(data), content_type="application/json", **extra)


def register(client: Client, email: str, password: str = STRONG_PASSWORD, ip: str | None = None):
    token = bootstrap_csrf(client)
    return post_json(
        client,
        "/api/v1/auth/register/",
        {"email": email, "password": password, "password_confirm": password},
        token=token,
        ip=ip,
    )


def login(client: Client, email: str, password: str = STRONG_PASSWORD, ip: str | None = None):
    token = bootstrap_csrf(client)
    return post_json(
        client, "/api/v1/auth/login/", {"email": email, "password": password}, token=token, ip=ip
    )


class TestCsrfBootstrap:
    def test_bootstrap_sets_sitara_csrf_cookie_and_returns_token(self):
        client = csrf_client()
        response = client.get("/api/v1/auth/csrf/")
        assert response.status_code == 200
        assert "sitara_csrftoken" in response.cookies
        assert response.json()["csrf_token"]
        assert response["Cache-Control"] == "no-store"

    @pytest.mark.parametrize(
        "url", ["/api/v1/auth/register/", "/api/v1/auth/login/", "/api/v1/auth/logout/"]
    )
    def test_unsafe_endpoints_without_csrf_return_json_403(self, url):
        client = csrf_client()
        response = client.post(url, data="{}", content_type="application/json")
        assert response.status_code == 403
        assert response["Content-Type"].startswith("application/json")
        body = response.json()
        assert body["error"]["code"] == "csrf_failed"
        # Django's internal failure reason is not exposed.
        assert "Referer" not in body["error"]["message"]
        assert "Origin" not in body["error"]["message"]


class TestRegistration:
    def test_valid_registration_creates_canonical_logged_in_user(self):
        client = csrf_client()
        email = unique_email()
        pre_login_token = bootstrap_csrf(client)
        response = post_json(
            client,
            "/api/v1/auth/register/",
            {
                "email": f"  {email.upper()}  ",
                "password": STRONG_PASSWORD,
                "password_confirm": STRONG_PASSWORD,
            },
            token=pre_login_token,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["authenticated"] is True
        assert body["user"]["email"] == email
        assert set(body["user"].keys()) == {"id", "email"}
        # CSRF token rotated on login.
        assert body["csrf_token"] and body["csrf_token"] != pre_login_token

        user = User.objects.get(email=email)
        assert str(user.pk) == body["user"]["id"]
        # Hashed, never plaintext.
        assert user.password != STRONG_PASSWORD
        assert user.password.startswith("pbkdf2_") or "$" in user.password
        assert user.check_password(STRONG_PASSWORD)

        # The registration response logged the browser in.
        me = client.get("/api/v1/auth/me/")
        assert me.json() == {"authenticated": True, "user": {"id": str(user.pk), "email": email}}

    @pytest.mark.parametrize(
        "password,expected_fragment",
        [
            ("Sh0rt-pw!", "too short"),
            ("1357924680246", "entirely numeric"),
            # 13 chars, so only CommonPasswordValidator can reject it.
            ("administrator", "too common"),
        ],
    )
    def test_weak_passwords_rejected_with_field_errors(self, password, expected_fragment):
        client = csrf_client()
        response = register(client, unique_email(), password=password)
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert any(expected_fragment in message for message in error["fields"]["password"])

    def test_password_similar_to_email_rejected(self):
        client = csrf_client()
        email = "similarity-check@example.test"
        response = register(client, email, password="similarity-check@example.test")
        assert response.status_code == 400
        assert "password" in response.json()["error"]["fields"]

    def test_password_confirmation_must_match(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        response = post_json(
            client,
            "/api/v1/auth/register/",
            {
                "email": unique_email(),
                "password": STRONG_PASSWORD,
                "password_confirm": "Other-Pass-2026!!",
            },
            token=token,
        )
        assert response.status_code == 400
        assert "password_confirm" in response.json()["error"]["fields"]

    def test_overlong_password_rejected_cheaply(self):
        client = csrf_client()
        response = register(client, unique_email(), password="x" * 129)
        assert response.status_code == 400
        assert "password" in response.json()["error"]["fields"]

    def test_duplicate_registration_is_generic_regardless_of_account_state(self):
        email = unique_email()
        register(csrf_client(), email)
        # Make the existing account "special" — the answer must not change.
        User.objects.filter(email=email).update(is_active=False)
        response = register(csrf_client(), email)
        assert response.status_code == 400
        body = response.json()["error"]
        assert body["code"] == "registration_failed"
        for revealing in ("exists", "active", "disabled", "taken", "staff"):
            assert revealing not in body["message"].lower()

    def test_malformed_json_and_wrong_content_type_are_controlled_400s(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        response = client.post(
            "/api/v1/auth/register/",
            data="{not json",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            REMOTE_ADDR=unique_ip(),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_json"
        response = client.post(
            "/api/v1/auth/register/",
            data="email=x",
            content_type="application/x-www-form-urlencoded",
            HTTP_X_CSRFTOKEN=token,
            REMOTE_ADDR=unique_ip(),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_content_type"


class TestLogin:
    def test_login_canonicalises_email(self):
        email = unique_email()
        register(csrf_client(), email)
        client = csrf_client()
        response = login(client, f"  {email.upper()}  ")
        assert response.status_code == 200
        assert response.json()["user"]["email"] == email

    def test_unknown_email_wrong_password_and_inactive_are_identical(self):
        email = unique_email()
        register(csrf_client(), email)
        inactive_email = unique_email()
        register(csrf_client(), inactive_email)
        User.objects.filter(email=inactive_email).update(is_active=False)

        responses = [
            login(csrf_client(), unique_email()),  # unknown email
            login(csrf_client(), email, password="Wrong-Pass-2026!!"),  # wrong password
            login(csrf_client(), inactive_email),  # inactive account
        ]
        bodies = [r.json() for r in responses]
        assert all(r.status_code == 401 for r in responses)
        assert bodies[0] == bodies[1] == bodies[2]
        assert bodies[0]["error"]["code"] == "invalid_credentials"

    def test_successful_login_rotates_session_key_and_csrf_token(self):
        email = unique_email()
        register(csrf_client(), email)

        client = csrf_client()
        pre_token = bootstrap_csrf(client)
        # Materialise an anonymous session so rotation is observable.
        session = client.session
        session["probe"] = "anonymous"
        session.save()
        anonymous_key = session.session_key

        response = post_json(
            client,
            "/api/v1/auth/login/",
            {"email": email, "password": STRONG_PASSWORD},
            token=pre_token,
        )
        assert response.status_code == 200
        assert client.session.session_key != anonymous_key
        assert response.json()["csrf_token"] != pre_token

    def test_success_clears_the_email_failure_counter(self, settings):
        settings.AUTH_LOGIN_EMAIL_LIMIT = 5
        email = unique_email()
        register(csrf_client(), email)
        ip = unique_ip()
        for _ in range(4):
            assert (
                login(csrf_client(), email, password="Wrong-Pass-2026!!", ip=ip).status_code == 401
            )
        assert login(csrf_client(), email, ip=ip).status_code == 200
        # Counter cleared: four more failures allowed again before the limit.
        for _ in range(4):
            assert (
                login(csrf_client(), email, password="Wrong-Pass-2026!!", ip=ip).status_code == 401
            )


class TestMe:
    def test_anonymous_me(self):
        response = Client().get("/api/v1/auth/me/")
        assert response.status_code == 200
        assert response.json() == {"authenticated": False, "user": None}
        assert response["Cache-Control"] == "no-store"

    def test_authenticated_me_exposes_only_id_and_email(self):
        client = csrf_client()
        email = unique_email()
        register(client, email)
        body = client.get("/api/v1/auth/me/").json()
        assert body["authenticated"] is True
        assert set(body["user"].keys()) == {"id", "email"}
        uuid.UUID(body["user"]["id"])  # UUID, parseable
        text = json.dumps(body)
        for forbidden in ("password", "staff", "superuser", "permission", "last_login", "session"):
            assert forbidden not in text


class TestLogout:
    def test_logout_flushes_session_and_old_cookie_is_dead(self):
        client = csrf_client()
        email = unique_email()
        registered = register(client, email).json()
        old_session_value = client.cookies["sitara_sessionid"].value
        assert Session.objects.filter(session_key=old_session_value).exists()

        response = post_json(client, "/api/v1/auth/logout/", {}, token=registered["csrf_token"])
        assert response.status_code == 200
        body = response.json()
        assert body["authenticated"] is False and body["user"] is None
        assert body["csrf_token"]
        # The server-side session is gone...
        assert not Session.objects.filter(session_key=old_session_value).exists()
        # ...and replaying the OLD cookie gets an anonymous answer.
        replay = Client()
        replay.cookies["sitara_sessionid"] = old_session_value
        assert replay.get("/api/v1/auth/me/").json()["authenticated"] is False

    def test_logout_is_idempotent_for_anonymous_browsers(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        first = post_json(client, "/api/v1/auth/logout/", {}, token=token)
        assert first.status_code == 200
        second = post_json(client, "/api/v1/auth/logout/", {}, token=first.json()["csrf_token"])
        assert second.status_code == 200
        assert second.json()["authenticated"] is False


@pytest.mark.urls("sitara.accounts.test_auth")
class TestSessionAuthorisationBoundary:
    def test_protected_endpoint_requires_session(self):
        assert Client().get("/api/v1/test/protected/").status_code == 403

    def test_session_login_grants_access_and_logout_revokes_it(self):
        client = csrf_client()
        email = unique_email()
        registered = register(client, email).json()
        assert client.get("/api/v1/test/protected/").status_code == 200
        old_session_value = client.cookies["sitara_sessionid"].value
        post_json(client, "/api/v1/auth/logout/", {}, token=registered["csrf_token"])
        replay = Client()
        replay.cookies["sitara_sessionid"] = old_session_value
        assert replay.get("/api/v1/test/protected/").status_code == 403


class TestRateLimits:
    def test_login_email_limit_returns_429_with_retry_after(self, settings):
        settings.AUTH_LOGIN_EMAIL_LIMIT = 3
        email = unique_email()
        ip = unique_ip()
        for _ in range(3):
            assert (
                login(csrf_client(), email, password="Wrong-Pass-2026!!", ip=ip).status_code == 401
            )
        limited = login(csrf_client(), email, password="Wrong-Pass-2026!!", ip=ip)
        assert limited.status_code == 429
        assert limited["Retry-After"].isdigit()
        assert limited.json()["error"]["code"] == "auth_rate_limited"

    def test_login_ip_limit_across_emails(self, settings):
        settings.AUTH_LOGIN_IP_LIMIT = 3
        settings.AUTH_LOGIN_EMAIL_LIMIT = 100
        ip = unique_ip()
        for _ in range(3):
            assert login(csrf_client(), unique_email(), ip=ip).status_code == 401
        assert login(csrf_client(), unique_email(), ip=ip).status_code == 429

    def test_register_ip_limit(self, settings):
        settings.AUTH_REGISTER_IP_LIMIT = 2
        ip = unique_ip()
        assert register(csrf_client(), unique_email(), ip=ip).status_code == 201
        assert register(csrf_client(), unique_email(), ip=ip).status_code == 201
        limited = register(csrf_client(), unique_email(), ip=ip)
        assert limited.status_code == 429
        assert limited["Retry-After"].isdigit()

    def test_cache_keys_contain_no_raw_identifiers(self):
        ip, email = "203.0.113.77", "leaky-address@example.test"
        key = rate_limits.build_key("login-email", f"{ip}:{email}")
        assert ip not in key
        assert "leaky-address" not in key
        assert "@" not in key
        assert key == rate_limits.build_key("login-email", f"{ip}:{email}")  # deterministic

    def test_cache_outage_fails_closed_with_503(self, monkeypatch):
        class ExplodingCache:
            def add(self, *args, **kwargs):
                raise ConnectionError("redis://:supersecret@cache.internal down")

            def incr(self, *args, **kwargs):
                raise ConnectionError("down")

        monkeypatch.setattr(rate_limits, "cache", ExplodingCache())
        response = login(csrf_client(), unique_email())
        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "auth_unavailable"
        assert "supersecret" not in json.dumps(body)


class TestResponseAndLogSafety:
    def test_auth_responses_are_never_cached(self):
        client = csrf_client()
        email = unique_email()
        registered = register(client, email)
        checks = [
            client.get("/api/v1/auth/csrf/"),
            client.get("/api/v1/auth/me/"),
            registered,
            post_json(client, "/api/v1/auth/logout/", {}, token=registered.json()["csrf_token"]),
        ]
        for response in checks:
            assert response["Cache-Control"] == "no-store"

    def test_no_credentials_or_tokens_enter_logs(self, caplog):
        client = csrf_client()
        email = unique_email()
        with caplog.at_level("DEBUG"):
            response = register(client, email)
            login(csrf_client(), email, password="Wrong-Pass-2026!!")
            login(csrf_client(), email)
        body = response.json()
        session_value = client.cookies["sitara_sessionid"].value
        for secret in (STRONG_PASSWORD, "Wrong-Pass-2026!!", body["csrf_token"], session_value):
            assert secret not in caplog.text

    def test_login_sets_only_sitara_cookies(self):
        client = csrf_client()
        email = unique_email()
        register(csrf_client(), email)
        response = login(client, email)
        cookie_names = set(response.cookies.keys())
        assert cookie_names <= {"sitara_sessionid", "sitara_csrftoken"}
        session_cookie = response.cookies["sitara_sessionid"]
        assert session_cookie["httponly"]
        assert session_cookie["samesite"] == "Lax"
        assert session_cookie["path"] == "/"
        assert not session_cookie["domain"]  # host-only cookie, no broad domain


class TestCsrfSessionMaterialisation:
    """The CSRF bootstrap must leave the browser with a LIVE database
    session: design-workspace creation coordinates concurrent requests by
    locking the django_session row, so that row must exist before the
    first unsafe request."""

    def test_bootstrap_creates_a_database_session_for_a_fresh_browser(self):
        client = csrf_client()
        assert Session.objects.count() == 0
        response = client.get("/api/v1/auth/csrf/")
        assert response.status_code == 200
        assert Session.objects.count() == 1
        # A successful bootstrap sets BOTH Sitara cookies.
        assert "sitara_sessionid" in response.cookies
        assert "sitara_csrftoken" in response.cookies
        assert response["Cache-Control"] == "no-store"

    def test_repeated_bootstrap_reuses_the_existing_browser_session(self):
        client = csrf_client()
        client.get("/api/v1/auth/csrf/")
        first_key = client.session.session_key
        assert first_key
        response = client.get("/api/v1/auth/csrf/")
        assert response.status_code == 200
        assert client.session.session_key == first_key
        assert Session.objects.count() == 1

    def test_bootstrap_replaces_a_stale_session_cookie_with_a_live_session(self):
        client = csrf_client()
        client.get("/api/v1/auth/csrf/")
        stale_key = client.session.session_key
        Session.objects.all().delete()  # simulate expiry/cleanup
        response = client.get("/api/v1/auth/csrf/")
        assert response.status_code == 200
        assert Session.objects.count() == 1
        assert client.session.session_key != stale_key

    def test_no_session_key_in_response_body_or_logs(self, caplog):
        client = csrf_client()
        with caplog.at_level("DEBUG"):
            response = client.get("/api/v1/auth/csrf/")
        session_key = client.session.session_key
        assert session_key
        assert session_key not in response.content.decode()
        assert session_key not in caplog.text
