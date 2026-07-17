"""Session-authentication endpoints (Phase 3B; Phase 6 schema exposure).

These are DRF ``APIView`` classes so drf-spectacular can document them, but
they deliberately keep Django's normal CSRF machinery: anonymous login and
registration must pass through ``@csrf_protect``, which DRF's
``SessionAuthentication`` only applies to already-authenticated requests.

``@csrf_protect`` is applied with ``method_decorator(..., name="dispatch")``
— wrapping ``dispatch``, NOT the view returned by ``as_view()`` (which DRF
marks ``csrf_exempt``, so a decorator on the outer view would be silently
skipped and CSRF would not be enforced). This mirrors the design endpoints.
Nothing here is ``csrf_exempt``.

The response bodies and helpers are unchanged from the original function
views: every response carries ``Cache-Control: no-store``; no password,
hash, session key, cookie or rate-limit identifier is ever returned or
logged; login failures are one generic answer for unknown email, wrong
password and inactive accounts alike.
"""

import logging

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from sitara.schema import (
    CSRF_HEADER_PARAMETER,
    ErrorEnvelopeSerializer,
    ValidationErrorEnvelopeSerializer,
)

from . import rate_limits
from .models import canonicalize_email
from .openapi import (
    AuthSuccessResponseSerializer,
    CsrfResponseSerializer,
    LoginSerializer,
    LogoutResponseSerializer,
    MeResponseSerializer,
    RegisterSerializer,
)
from .validation import (
    RequestValidationError,
    extract_email,
    extract_password,
    parse_json_body,
)

logger = logging.getLogger(__name__)

User = get_user_model()

_AUTH_TAGS = ["Authentication"]


def _json(payload: dict, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _error(code: str, message: str, status: int, fields: dict | None = None) -> JsonResponse:
    body: dict = {"error": {"code": code, "message": message}}
    if fields:
        body["error"]["fields"] = fields
    return _json(body, status=status)


def _rate_limited(retry_after: int) -> JsonResponse:
    # Which limiter triggered is deliberately not revealed.
    response = _error("auth_rate_limited", "Too many attempts. Try again later.", 429)
    response["Retry-After"] = str(retry_after)
    return response


def _auth_unavailable() -> JsonResponse:
    # Rate-limit cache down: fail CLOSED rather than authenticate without
    # protection.
    return _error("auth_unavailable", "Sign-in is temporarily unavailable. Try again shortly.", 503)


def _user_payload(user) -> dict:
    # UUID and canonical email only — never staff flags, permissions,
    # password state, last login or session details.
    return {"id": str(user.pk), "email": user.email}


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_csrf",
        tags=_AUTH_TAGS,
        request=None,
        responses={200: CsrfResponseSerializer},
        summary="Bootstrap the CSRF token and browser session",
        description=(
            "Sets the sitara_csrftoken cookie and returns the matching token, "
            "and MATERIALISES the Django database session (setting "
            "sitara_sessionid) so later anonymous design operations can "
            "coordinate. Call this before any unsafe request. The token is "
            "never logged; the session key never leaves the backend."
        ),
    )
    def get(self, request):
        """Anonymous CSRF bootstrap: sets the sitara_csrftoken cookie and
        returns the matching token. The token is never logged.

        Bootstrap also MATERIALISES the Django database session when the
        browser does not have a live one yet, so a successful call sets both
        sitara_csrftoken and sitara_sessionid. Design-workspace creation
        serialises concurrent requests by locking the browser's django_session
        row, which therefore must exist before the first unsafe request.
        Repeated bootstraps reuse the existing session; the session key is
        never returned or logged."""
        if request.session.session_key is None or not request.session.exists(
            request.session.session_key
        ):
            request.session.create()
        return _json({"csrf_token": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class RegisterView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_register",
        tags=_AUTH_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        # JSON only — the runtime rejects any other content type. Restricting
        # the media type here keeps the contract honest without a behaviour
        # change (the view reads request.body directly, not DRF parsers).
        request={"application/json": RegisterSerializer},
        responses={
            201: AuthSuccessResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            429: OpenApiResponse(ErrorEnvelopeSerializer, description="Registration rate limited."),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Authentication cache unavailable."
            ),
        },
        summary="Register and sign in",
        description="Creates an account and logs the browser in, rotating the session and token.",
    )
    def post(self, request):
        try:
            body = parse_json_body(request)
            raw_email = extract_email(body)
            password = extract_password(body, "password")
            password_confirm = extract_password(body, "password_confirm")
        except RequestValidationError as exc:
            return _error(exc.code, exc.message, 400, exc.fields or None)

        if password != password_confirm:
            return _error(
                "validation_failed",
                "Please correct the highlighted fields.",
                400,
                {"password_confirm": ["Passwords do not match."]},
            )

        email = canonicalize_email(raw_email)

        try:
            retry_after = rate_limits.check_and_count(
                "register-ip",
                rate_limits.client_ip(request),
                settings.AUTH_REGISTER_IP_LIMIT,
                settings.AUTH_REGISTER_IP_WINDOW_SECONDS,
            )
        except rate_limits.RateLimitUnavailable:
            return _auth_unavailable()
        if retry_after is not None:
            return _rate_limited(retry_after)

        pending_user = User(email=email)
        try:
            validate_password(password, user=pending_user)
        except ValidationError as exc:
            return _error(
                "validation_failed",
                "Please correct the highlighted fields.",
                400,
                {"password": list(exc.messages)},
            )

        try:
            with transaction.atomic():
                user = User.objects.create_user(email=email, password=password)
        except IntegrityError:
            # Duplicate/unavailable email: one generic answer that reveals
            # nothing about any existing account's state.
            return _error(
                "registration_failed", "Unable to create an account with those details.", 400
            )

        login(request, user)  # rotates the session key and the CSRF token
        logger.info("auth register succeeded user_id=%s", user.pk)
        return _json(
            {
                "authenticated": True,
                "user": _user_payload(user),
                "csrf_token": get_token(request),
            },
            status=201,
        )


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_login",
        tags=_AUTH_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        # JSON only — see auth_register.
        request={"application/json": LoginSerializer},
        responses={
            200: AuthSuccessResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            401: OpenApiResponse(ErrorEnvelopeSerializer, description="Invalid credentials."),
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            429: OpenApiResponse(ErrorEnvelopeSerializer, description="Login rate limited."),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Authentication cache unavailable."
            ),
        },
        summary="Sign in",
        description=(
            "Authenticates against a Django session. Failures for unknown "
            "email, wrong password and inactive accounts are one generic 401."
        ),
    )
    def post(self, request):
        try:
            body = parse_json_body(request)
            raw_email = extract_email(body)
            password = extract_password(body, "password")
        except RequestValidationError as exc:
            return _error(exc.code, exc.message, 400, exc.fields or None)

        email = canonicalize_email(raw_email)
        ip = rate_limits.client_ip(request)

        try:
            retry_after = rate_limits.check_and_count(
                "login-ip", ip, settings.AUTH_LOGIN_IP_LIMIT, settings.AUTH_LOGIN_IP_WINDOW_SECONDS
            ) or rate_limits.check_and_count(
                "login-email",
                f"{ip}:{email}",
                settings.AUTH_LOGIN_EMAIL_LIMIT,
                settings.AUTH_LOGIN_EMAIL_WINDOW_SECONDS,
            )
        except rate_limits.RateLimitUnavailable:
            return _auth_unavailable()
        if retry_after is not None:
            return _rate_limited(retry_after)

        user = authenticate(request=request, username=email, password=password)
        if user is None:
            # One generic answer for unknown email, wrong password AND inactive
            # accounts (ModelBackend already rejects inactive users).
            return _error("invalid_credentials", "Unable to sign in with those credentials.", 401)

        login(request, user)  # rotates the session key and the CSRF token
        rate_limits.clear("login-email", f"{ip}:{email}")
        logger.info("auth login succeeded user_id=%s", user.pk)
        return _json(
            {
                "authenticated": True,
                "user": _user_payload(user),
                "csrf_token": get_token(request),
            }
        )


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_logout",
        tags=_AUTH_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        # No request body: logout ignores any payload (only the CSRF header
        # and session cookie matter).
        request=None,
        responses={
            200: LogoutResponseSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
        },
        summary="Sign out",
        description=(
            "Flushes the session and returns a fresh anonymous token. "
            "Idempotent: an anonymous browser gets the same shape of answer."
        ),
    )
    def post(self, request):
        """CSRF-protected and idempotent: an anonymous browser gets the same
        shape of answer with a fresh anonymous token."""
        logout(request)  # flushes the session; safe for anonymous callers too
        return _json({"authenticated": False, "user": None, "csrf_token": get_token(request)})


class MeView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_me",
        tags=_AUTH_TAGS,
        request=None,
        responses={200: MeResponseSerializer},
        summary="Current session state",
        description=(
            "Browser session-state bootstrap. Returns the authenticated "
            "{id, email} user or an anonymous body — both are HTTP 200. "
            "Relies on the HttpOnly session cookie; anonymous access is "
            "intentional (no bearer token or API key is used)."
        ),
    )
    def get(self, request):
        """Browser session-state bootstrap; anonymous access is intentional."""
        if request.user.is_authenticated:
            return _json({"authenticated": True, "user": _user_payload(request.user)})
        return _json({"authenticated": False, "user": None})
