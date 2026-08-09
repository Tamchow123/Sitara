"""Every JSON-accepting design endpoint survives a pathologically nested body.

A byte ceiling cannot stop this attack, which is the whole point: 40KB of
``[[[[…]]]]`` is only 40KB, but CPython's JSON scanner recurses once per nesting
level and raises ``RecursionError``. That is a ``RuntimeError``, so it slips past
DRF's ``except ValueError`` in ``JSONParser.parse`` and used to escape as an
uncontrolled 500 — reachable by anyone who can reach these endpoints at all.

The guard lives in the shared ``_parse_body`` helper, so it is tested across the
endpoints that share it rather than through one of them. Depths sit either side of
the interpreter's threshold (~5 000 levels on CPython 3.12) because that number
moves between versions and the answer must be a controlled 400 on both sides.
"""

from __future__ import annotations

import pytest

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_owned_design_id,
    csrf_client,
    design_url,
)

pytestmark = pytest.mark.django_db

# Below, around, and well past CPython 3.12's effective JSON nesting limit.
DEPTHS = [5_000, 20_000, 60_000]


def nested_body(depth: int) -> str:
    return "[" * depth + "]" * depth


def endpoints(client, design_id):
    """Every design route that decodes a JSON body through ``_parse_body``."""
    return [
        ("post", DESIGNS_URL),
        ("patch", design_url(design_id)),
        ("post", f"{DESIGNS_URL}{design_id}/generate/"),
        ("post", f"{DESIGNS_URL}{design_id}/refine/"),
    ]


@pytest.mark.parametrize("depth", DEPTHS)
def test_no_json_endpoint_returns_a_500_for_a_deeply_nested_body(depth):
    client = csrf_client()
    token = bootstrap_csrf(client)
    design_id = create_owned_design_id(client)

    for method, url in endpoints(client, design_id):
        response = getattr(client, method)(
            url,
            data=nested_body(depth),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY="11111111-1111-4111-8111-111111111111",
        )
        assert response.status_code < 500, (method, url, depth, response.status_code)


@pytest.mark.parametrize("depth", DEPTHS)
def test_the_body_is_rejected_as_invalid_json_not_silently_accepted(depth):
    """A controlled 400 with a stable code — never a 500, and never a success
    that quietly treated the unparseable body as empty.

    Which code depends on where the body dies: below the scanner's limit it parses
    into a list and the serializer rejects the shape (``validation_failed``);
    above it, decoding never finishes (``invalid_json``). Both are controlled, and
    the test accepts either because the threshold is an interpreter detail."""
    client = csrf_client()
    token = bootstrap_csrf(client)
    design_id = create_owned_design_id(client)

    response = client.patch(
        design_url(design_id),
        data=nested_body(depth),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )

    assert response.status_code == 400, response.status_code
    assert response.json()["error"]["code"] in {"invalid_json", "validation_failed"}


def test_an_ordinary_malformed_body_is_still_a_controlled_400():
    """The added guard must not have displaced the plain ParseError path."""
    client = csrf_client()
    token = bootstrap_csrf(client)
    design_id = create_owned_design_id(client)

    response = client.patch(
        design_url(design_id),
        data="{not json",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"


def test_a_deeply_nested_body_changes_no_state():
    client = csrf_client()
    token = bootstrap_csrf(client)
    design_id = create_owned_design_id(client)
    before = client.get(design_url(design_id)).json()

    client.patch(
        design_url(design_id),
        data=nested_body(20_000),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )

    assert client.get(design_url(design_id)).json() == before
