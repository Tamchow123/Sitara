"""Shared helpers for the design test suites.

All API flows run with REAL CSRF enforcement (enforce_csrf_checks=True).
Auth calls get a unique REMOTE_ADDR and email per test so the Redis-backed
authentication rate limiters never bleed between tests."""

import json
import uuid

from django.test import Client

STRONG_PASSWORD = "Correct-Horse-Battery-2026!"

DESIGNS_URL = "/api/v1/designs/"


def design_url(design_id) -> str:
    return f"{DESIGNS_URL}{design_id}/"


def unique_email() -> str:
    return f"designer-{uuid.uuid4().hex[:12]}@example.test"


def unique_ip() -> str:
    raw = uuid.uuid4().bytes
    return f"10.{raw[0]}.{raw[1]}.{raw[2]}"


def csrf_client() -> Client:
    return Client(enforce_csrf_checks=True)


def bootstrap_csrf(client: Client) -> str:
    response = client.get("/api/v1/auth/csrf/")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def send_json(client: Client, method: str, url: str, data=None, token=None, ip=None):
    extra = {"REMOTE_ADDR": ip or unique_ip()}
    if token is not None:
        extra["HTTP_X_CSRFTOKEN"] = token
    return getattr(client, method)(
        url,
        data=json.dumps(data if data is not None else {}),
        content_type="application/json",
        **extra,
    )


def create_design(client: Client, title=None, token: str | None = None):
    token = token or bootstrap_csrf(client)
    payload = {} if title is None else {"title": title}
    return send_json(client, "post", DESIGNS_URL, payload, token=token)


def register(client: Client, email: str, password: str = STRONG_PASSWORD):
    token = bootstrap_csrf(client)
    response = send_json(
        client,
        "post",
        "/api/v1/auth/register/",
        {"email": email, "password": password, "password_confirm": password},
        token=token,
    )
    assert response.status_code == 201, response.content
    return response


def login(client: Client, email: str, password: str = STRONG_PASSWORD):
    token = bootstrap_csrf(client)
    response = send_json(
        client,
        "post",
        "/api/v1/auth/login/",
        {"email": email, "password": password},
        token=token,
    )
    assert response.status_code == 200, response.content
    return response


def logout(client: Client):
    token = bootstrap_csrf(client)
    response = send_json(client, "post", "/api/v1/auth/logout/", {}, token=token)
    assert response.status_code == 200, response.content
    return response
