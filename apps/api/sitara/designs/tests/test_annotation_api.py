"""Private annotation endpoints: ownership, CSRF, concurrency (Phase 19 Part B).

Every flow runs with REAL CSRF enforcement. The suite's job is to prove three
things the endpoints exist to guarantee: an annotation is reachable only by the
design's owner and every other case is the SAME 404; a stale write never
overwrites a newer one; and no response leaks anything private.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.utils import timezone

from sitara.designs.annotation_schema import ANNOTATION_SCHEMA_VERSION
from sitara.designs.models import DesignAnnotationDocument, DesignSession, DesignVersion

from .utils import (
    bootstrap_csrf,
    create_owned_design_id,
    create_pending_design_version,
    create_ready_design_version,
    csrf_client,
    login,
    logout,
    register,
    send_json,
    unique_email,
)

pytestmark = pytest.mark.django_db

WIDTH = 1536
HEIGHT = 2048


def annotations_url(design_id, version_id) -> str:
    return f"/api/v1/designs/{design_id}/versions/{version_id}/annotations/"


def item(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "type": "pin",
        "geometry": {"point": {"x": 0.5, "y": 0.25}},
        "note": "Raise the neckline about 2 cm.",
        "palette": "terracotta",
        "created_order": 1,
    }
    base.update(overrides)
    return base


def write_body(items=None, *, expected_revision=0, **overrides) -> dict:
    body = {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "image_width": WIDTH,
        "image_height": HEIGHT,
        "items": [item()] if items is None else items,
        "expected_revision": expected_revision,
    }
    body.update(overrides)
    return body


def owned_version(client=None, *, with_storage_objects: bool = False):
    """A design owned by ``client`` (a fresh anonymous session by default) with
    one fully ingested version, which is the precondition for annotating."""
    client = client or csrf_client()
    design_id = create_owned_design_id(client)
    version = create_ready_design_version(design_id, with_storage_objects=with_storage_objects)
    return client, design_id, version


def stored_state(version):
    """The document row as the database holds it, or None.

    Rejection tests assert on this, not only on the status code: a 4xx with the
    right code is not evidence that nothing was written. Returns the tuple that
    must be identical before and after a refused write."""
    document = DesignAnnotationDocument.objects.filter(design_version=version).first()
    if document is None:
        return None
    return (
        document.revision,
        document.schema_version,
        json.dumps(document.document, sort_keys=True),
    )


# --- reading ----------------------------------------------------------------


def test_get_before_any_save_returns_an_empty_document_at_revision_zero():
    """Not a 404. Revision 0 is the unambiguous never-saved signal, so the
    client renders immediately with no separate branch — and the server's own
    canonical dimensions come back so the overlay can be sized correctly."""
    client, design_id, version = owned_version()
    response = client.get(annotations_url(design_id, version.id))
    assert response.status_code == 200, response.content
    body = response.json()["annotations"]
    assert body["items"] == []
    assert body["revision"] == 0
    assert body["updated_at"] is None
    assert body["image_width"] == version.image_width
    assert body["image_height"] == version.image_height
    assert body["schema_version"] == ANNOTATION_SCHEMA_VERSION


def test_every_response_is_no_store():
    """A private overlay must never be retained by a cache."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    get_response = client.get(annotations_url(design_id, version.id))
    put_response = send_json(
        client, "put", annotations_url(design_id, version.id), write_body(), token=token
    )
    delete_response = client.delete(annotations_url(design_id, version.id), HTTP_X_CSRFTOKEN=token)
    for response in (get_response, put_response, delete_response):
        assert response.headers["Cache-Control"] == "no-store"


def test_a_version_with_no_permanent_image_is_a_controlled_conflict():
    """Annotations bind to the canonical image's dimensions, which do not exist
    until ingest completes — so there is nothing to annotate yet."""
    client = csrf_client()
    design_id = create_owned_design_id(client)
    version = create_pending_design_version(design_id)
    token = bootstrap_csrf(client)

    get_response = client.get(annotations_url(design_id, version.id))
    put_response = send_json(
        client, "put", annotations_url(design_id, version.id), write_body(), token=token
    )
    delete_response = client.delete(annotations_url(design_id, version.id), HTTP_X_CSRFTOKEN=token)

    for response in (get_response, put_response, delete_response):
        assert response.status_code == 409, response.content
        assert response.json()["error"]["code"] == "design_image_not_ready"


# --- writing ----------------------------------------------------------------


def test_anonymous_owner_can_write_and_read_back_their_own_annotations():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    marks = [
        item(created_order=1),
        item(
            id=str(uuid.uuid4()),
            type="rectangle",
            geometry={"x": 0.2, "y": 0.6, "width": 0.5, "height": 0.2},
            note="Widen the champagne hem border.",
            palette="sage",
            created_order=2,
        ),
    ]
    response = send_json(
        client,
        "put",
        annotations_url(design_id, version.id),
        write_body(marks, expected_revision=0),
        token=token,
    )
    assert response.status_code == 200, response.content
    body = response.json()["annotations"]
    assert body["revision"] == 1
    assert len(body["items"]) == 2

    read_back = client.get(annotations_url(design_id, version.id)).json()["annotations"]
    assert read_back["revision"] == 1
    assert [i["created_order"] for i in read_back["items"]] == [1, 2]
    assert read_back["items"][1]["palette"] == "sage"


def test_authenticated_owner_can_write_and_read_back_their_own_annotations():
    client = csrf_client()
    register(client, unique_email())
    client, design_id, version = owned_version(client)
    token = bootstrap_csrf(client)
    response = send_json(
        client, "put", annotations_url(design_id, version.id), write_body(), token=token
    )
    assert response.status_code == 200, response.content
    assert client.get(annotations_url(design_id, version.id)).json()["annotations"]["revision"] == 1


def test_promotion_from_anonymous_to_authenticated_preserves_annotation_access():
    """Login keeps the anonymous workspace pointer and the next design request
    claims it, so annotations written before signing in stay reachable after."""
    client = csrf_client()
    client, design_id, version = owned_version(client)
    token = bootstrap_csrf(client)
    assert (
        send_json(
            client, "put", annotations_url(design_id, version.id), write_body(), token=token
        ).status_code
        == 200
    )

    email = unique_email()
    register(client, email)

    response = client.get(annotations_url(design_id, version.id))
    assert response.status_code == 200, response.content
    assert response.json()["annotations"]["revision"] == 1


def test_put_replaces_the_whole_document_rather_than_merging():
    """There is no partial update: the client owns the overlay and sends all of
    it, so a removed mark is unambiguous rather than an omitted field."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    send_json(
        client,
        "put",
        annotations_url(design_id, version.id),
        write_body([item(created_order=1), item(id=str(uuid.uuid4()), created_order=2)]),
        token=token,
    )
    send_json(
        client,
        "put",
        annotations_url(design_id, version.id),
        write_body([item(created_order=1)], expected_revision=1),
        token=token,
    )
    body = client.get(annotations_url(design_id, version.id)).json()["annotations"]
    assert len(body["items"]) == 1
    assert body["revision"] == 2


def test_an_invalid_document_is_a_controlled_400():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    response = send_json(
        client,
        "put",
        annotations_url(design_id, version.id),
        write_body([item(palette="#ff0000")]),
        token=token,
    )
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == "annotation_invalid"
    assert stored_state(version) is None


def test_a_dimension_mismatch_is_rejected_not_corrected():
    """A client that disagrees about the image it drew over is a client whose
    coordinates cannot be trusted."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    response = send_json(
        client,
        "put",
        annotations_url(design_id, version.id),
        write_body(image_width=WIDTH + 10),
        token=token,
    )
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == "annotation_dimension_mismatch"
    assert stored_state(version) is None


@pytest.mark.parametrize("expected", [None, "1", -1, 1.5, True, [], {}])
def test_a_malformed_expected_revision_is_rejected(expected):
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    body = write_body()
    body["expected_revision"] = expected
    response = send_json(client, "put", annotations_url(design_id, version.id), body, token=token)
    assert response.status_code == 400, response.content
    assert stored_state(version) is None


def test_malformed_json_is_a_controlled_400():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    response = client.put(
        annotations_url(design_id, version.id),
        data="{not json",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] in {"invalid_json", "annotation_invalid"}
    assert stored_state(version) is None


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda body: body.update({"items": [item(palette="#ff0000")]}), id="bad_item"),
        pytest.param(lambda body: body.update({"image_width": WIDTH + 10}), id="dimensions"),
        pytest.param(lambda body: body.update({"expected_revision": "1"}), id="bad_revision"),
        pytest.param(lambda body: body.update({"schema_version": 99}), id="bad_schema_version"),
        pytest.param(lambda body: body.pop("items"), id="missing_items"),
    ],
)
def test_every_rejected_write_leaves_an_existing_document_byte_identical(mutate):
    """The whole safety story of this endpoint is that a refused write changes
    nothing, so it is asserted against the stored row rather than inferred from
    the status code."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    url = annotations_url(design_id, version.id)
    saved = send_json(client, "put", url, write_body([item(note="Keep me.")]), token=token)
    assert saved.status_code == 200, saved.content
    before = stored_state(version)

    body = write_body([item(note="Overwrite me.")], expected_revision=1)
    mutate(body)
    response = send_json(client, "put", url, body, token=token)

    assert 400 <= response.status_code < 500, response.content
    assert stored_state(version) == before


# --- deleting ---------------------------------------------------------------


def test_delete_clears_only_the_annotations_never_the_generated_image():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    send_json(client, "put", annotations_url(design_id, version.id), write_body(), token=token)
    before = {
        name: getattr(version, name)
        for name in (
            *DesignVersion.PERMANENT_IMAGE_CHAR_FIELDS,
            *DesignVersion.PERMANENT_IMAGE_NULLABLE_FIELDS,
        )
    }

    response = client.delete(annotations_url(design_id, version.id), HTTP_X_CSRFTOKEN=token)

    assert response.status_code == 204, response.content
    assert not DesignAnnotationDocument.objects.filter(design_version=version).exists()
    version.refresh_from_db()
    assert {name: getattr(version, name) for name in before} == before
    # And the version itself still exists — clearing marks is not deleting work.
    assert DesignVersion.objects.filter(pk=version.pk).exists()


def test_delete_is_idempotent():
    """A client retrying a clear-all must not be told it failed."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    first = client.delete(annotations_url(design_id, version.id), HTTP_X_CSRFTOKEN=token)
    second = client.delete(annotations_url(design_id, version.id), HTTP_X_CSRFTOKEN=token)
    assert first.status_code == 204
    assert second.status_code == 204


def test_writing_again_after_a_delete_starts_from_revision_zero():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    send_json(client, "put", annotations_url(design_id, version.id), write_body(), token=token)
    client.delete(annotations_url(design_id, version.id), HTTP_X_CSRFTOKEN=token)
    assert client.get(annotations_url(design_id, version.id)).json()["annotations"]["revision"] == 0
    response = send_json(
        client,
        "put",
        annotations_url(design_id, version.id),
        write_body(expected_revision=0),
        token=token,
    )
    assert response.status_code == 200, response.content
    assert response.json()["annotations"]["revision"] == 1


# --- ownership: every failure is the same 404 -------------------------------


def test_a_second_browser_receives_an_indistinguishable_404():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    send_json(client, "put", annotations_url(design_id, version.id), write_body(), token=token)

    stranger = csrf_client()
    stranger_token = bootstrap_csrf(stranger)
    url = annotations_url(design_id, version.id)

    get_response = stranger.get(url)
    put_response = send_json(stranger, "put", url, write_body(), token=stranger_token)
    delete_response = stranger.delete(url, HTTP_X_CSRFTOKEN=stranger_token)

    for response in (get_response, put_response, delete_response):
        assert response.status_code == 404, response.content
        assert response.json()["error"]["code"] == "not_found"


def test_another_account_receives_an_indistinguishable_404():
    owner = csrf_client()
    register(owner, unique_email())
    owner, design_id, version = owned_version(owner)
    token = bootstrap_csrf(owner)
    send_json(owner, "put", annotations_url(design_id, version.id), write_body(), token=token)
    logout(owner)

    other = csrf_client()
    other_email = unique_email()
    register(other, other_email)
    login(other, other_email)

    response = other.get(annotations_url(design_id, version.id))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_nonexistent_design_is_the_same_404_as_a_foreign_one():
    """Indistinguishable: knowing a UUID must never confirm it exists."""
    client, design_id, version = owned_version()
    foreign = csrf_client()
    unknown_url = annotations_url(uuid.uuid4(), uuid.uuid4())
    foreign_url = annotations_url(design_id, version.id)
    assert foreign.get(unknown_url).json() == foreign.get(foreign_url).json()


def test_a_version_belonging_to_another_design_is_a_404():
    """The version must belong to the owned design, so a caller holding a valid
    version UUID cannot pair it with a design they do own."""
    client, first_design_id, first_version = owned_version()
    second_design_id = create_owned_design_id(client)
    second_version = create_ready_design_version(second_design_id, with_storage_objects=False)

    response = client.get(annotations_url(first_design_id, second_version.id))
    assert response.status_code == 404, response.content


def test_a_failed_get_creates_no_workspace():
    """An anonymous GET that finds nothing must not leave a DesignSession row
    behind, or probing would grow the database."""
    before = DesignSession.objects.count()
    csrf_client().get(annotations_url(uuid.uuid4(), uuid.uuid4()))
    assert DesignSession.objects.count() == before


# --- CSRF -------------------------------------------------------------------


def test_unsafe_methods_require_csrf():
    """DRF's SessionAuthentication does not cover anonymous unsafe requests, so
    these endpoints carry normal Django CSRF enforcement."""
    client, design_id, version = owned_version()
    bootstrap_csrf(client)
    url = annotations_url(design_id, version.id)

    put_response = client.put(url, data=json.dumps(write_body()), content_type="application/json")
    delete_response = client.delete(url)

    assert put_response.status_code == 403
    assert delete_response.status_code == 403


def test_get_does_not_require_csrf():
    client, design_id, version = owned_version()
    assert client.get(annotations_url(design_id, version.id)).status_code == 200


# --- optimistic concurrency -------------------------------------------------


def test_a_stale_revision_conflicts_and_leaves_the_stored_document_unchanged():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    url = annotations_url(design_id, version.id)
    original = item(note="The original note.")
    send_json(client, "put", url, write_body([original]), token=token)

    stale = send_json(
        client,
        "put",
        url,
        write_body([item(note="A stale overwrite.")], expected_revision=0),
        token=token,
    )

    assert stale.status_code == 409, stale.content
    assert stale.json()["error"]["code"] == "annotation_conflict"
    assert stale.json()["revision"] == 1
    stored = client.get(url).json()["annotations"]
    assert stored["revision"] == 1
    assert stored["items"][0]["note"] == "The original note."


def test_the_conflict_body_never_echoes_the_stored_document():
    """A conflict must not become a channel for reading private note text back."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    url = annotations_url(design_id, version.id)
    secret = "The private measurement is 34 inches."
    send_json(client, "put", url, write_body([item(note=secret)]), token=token)

    conflict = send_json(client, "put", url, write_body(expected_revision=0), token=token)

    assert conflict.status_code == 409
    body = conflict.json()
    assert set(body.keys()) == {"error", "revision"}
    assert "items" not in body
    assert "schema_version" not in body
    assert secret not in conflict.content.decode()
    assert "34 inches" not in conflict.content.decode()


def test_each_successful_write_increments_the_revision_exactly_once():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    url = annotations_url(design_id, version.id)
    for expected in range(4):
        response = send_json(
            client, "put", url, write_body(expected_revision=expected), token=token
        )
        assert response.status_code == 200, response.content
        assert response.json()["annotations"]["revision"] == expected + 1


def test_replaying_identical_content_with_the_current_revision_still_increments():
    """The revision counts WRITES, not content changes. Documented behaviour:
    comparing content instead would make an idempotent retry indistinguishable
    from a real edit."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    url = annotations_url(design_id, version.id)
    marks = [item()]
    send_json(client, "put", url, write_body(marks), token=token)
    again = send_json(client, "put", url, write_body(marks, expected_revision=1), token=token)
    assert again.status_code == 200
    assert again.json()["annotations"]["revision"] == 2


def test_replaying_an_already_applied_payload_with_a_stale_revision_conflicts():
    """Not a silent success: the client resolves it by reloading."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    url = annotations_url(design_id, version.id)
    marks = [item()]
    send_json(client, "put", url, write_body(marks), token=token)
    replay = send_json(client, "put", url, write_body(marks, expected_revision=0), token=token)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "annotation_conflict"


def test_writing_with_a_saved_revision_when_nothing_is_stored_conflicts():
    """The document was cleared in another tab. A conflict rather than a silent
    create, so this tab's unsaved work is never quietly discarded."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    url = annotations_url(design_id, version.id)
    response = send_json(client, "put", url, write_body(expected_revision=3), token=token)
    assert response.status_code == 409, response.content
    assert response.json()["revision"] == 0
    # A conflict, so the write really was refused — not created and then reported.
    assert stored_state(version) is None


# --- privacy of the payload -------------------------------------------------


def test_no_response_exposes_private_fields():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    url = annotations_url(design_id, version.id)
    send_json(client, "put", url, write_body(), token=token)
    raw = client.get(url).content.decode()

    for leaked in (
        version.image_storage_key,
        version.thumbnail_storage_key,
        version.image_sha256,
        version.thumbnail_sha256,
        version.image_processor_version,
        str(version.design.design_session_id),
    ):
        assert leaked not in raw
    for key in ("storage_key", "sha256", "design_session", "user", "url", "prompt", "design_spec"):
        assert key not in raw


def test_there_is_no_lookup_by_annotation_id_alone():
    """No endpoint accepts an annotation document UUID, so its id grants
    nothing even if it leaked."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    send_json(client, "put", annotations_url(design_id, version.id), write_body(), token=token)
    document = DesignAnnotationDocument.objects.get(design_version=version)
    for candidate in (
        f"/api/v1/annotations/{document.id}/",
        f"/api/v1/designs/{design_id}/annotations/{document.id}/",
    ):
        assert client.get(candidate).status_code == 404


# --- refinement independence over HTTP --------------------------------------


def test_a_refined_version_starts_with_no_annotations_over_http():
    """Annotations belong to the exact image they were drawn over, so a
    refinement inherits none of them."""
    client, design_id, original = owned_version()
    token = bootstrap_csrf(client)
    send_json(client, "put", annotations_url(design_id, original.id), write_body(), token=token)

    refined = DesignVersion.objects.create(
        design_id=design_id,
        version_number=2,
        design_spec={"schema_version": 1},
        design_spec_schema_version=1,
        design_spec_template_version="v1",
        design_spec_provider="fixture",
        design_spec_model="fixture-model",
        design_spec_generated_at=timezone.now(),
        image_prompt="A refined prompt.",
        prompt_builder_version="3.0.0",
        parent_version=original,
        refinement_request={"schema_version": 1, "change_type": "colour_story", "note": ""},
        refinement_request_schema_version=1,
        refinement_request_sha256="d" * 64,
        image_storage_key=f"design-images/{design_id}/v2/original.webp",
        image_sha256="e" * 64,
        image_size_bytes=1000,
        image_width=WIDTH,
        image_height=HEIGHT,
        thumbnail_storage_key=f"design-images/{design_id}/v2/thumbnail.webp",
        thumbnail_sha256="f" * 64,
        thumbnail_size_bytes=100,
        thumbnail_width=384,
        thumbnail_height=512,
        image_processor_version="1.0.0",
        image_ingested_at=timezone.now(),
    )

    refined_body = client.get(annotations_url(design_id, refined.id)).json()["annotations"]
    assert refined_body["revision"] == 0
    assert refined_body["items"] == []
    # And the original is untouched.
    assert (
        client.get(annotations_url(design_id, original.id)).json()["annotations"]["revision"] == 1
    )


# --- route shape ------------------------------------------------------------


def test_the_route_is_slash_optional():
    """The Next.js rewrite strips trailing slashes, and an APPEND_SLASH redirect
    through the proxy would loop and drop the body."""
    client, design_id, version = owned_version()
    without = annotations_url(design_id, version.id).rstrip("/")
    assert client.get(without).status_code == 200


# --- hostile bodies that defeat a byte ceiling ------------------------------


@pytest.mark.parametrize("depth", [5_000, 20_000, 60_000])
def test_a_pathologically_nested_body_is_a_controlled_400(depth):
    """The guard lives in the shared ``_parse_body`` and is covered across all its
    callers in ``test_json_body_limits``; this pins the annotation endpoint's own
    answer, because a deep body can also die downstream in the document contract
    rather than in the parser."""
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    response = client.put(
        annotations_url(design_id, version.id),
        data="[" * depth + "]" * depth,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 400, response.status_code
    assert response.json()["error"]["code"] in {"invalid_json", "annotation_invalid"}


def test_a_pathologically_nested_body_leaves_nothing_stored():
    client, design_id, version = owned_version()
    token = bootstrap_csrf(client)
    client.put(
        annotations_url(design_id, version.id),
        data='{"items":' * 30_000 + "null" + "}" * 30_000,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert not DesignAnnotationDocument.objects.filter(design_version=version).exists()
    # And the endpoint still works afterwards — no poisoned connection state.
    assert client.get(annotations_url(design_id, version.id)).status_code == 200
