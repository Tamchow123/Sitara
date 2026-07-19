"""Design API contract tests: CSRF, validation, privacy and response shape.

Everything runs with enforce_csrf_checks=True — the same enforcement a real
browser faces."""

import uuid

import pytest

from sitara.designs.models import Design, DesignSession
from sitara.designs.services import DESIGN_SESSION_KEY

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_design,
    csrf_client,
    design_url,
    send_json,
)

pytestmark = pytest.mark.django_db

PUBLIC_DESIGN_KEYS = {
    "id",
    "title",
    "status",
    "questionnaire",
    "answers",
    "selected_inspirations",
    "latest_job",
    "created_at",
    "updated_at",
}


class TestCsrfEnforcement:
    def test_create_without_csrf_token_returns_json_403(self):
        client = csrf_client()
        response = client.post(DESIGNS_URL, data="{}", content_type="application/json")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_failed"
        assert Design.objects.count() == 0

    def test_patch_without_csrf_token_returns_json_403(self):
        client = csrf_client()
        response = client.patch(
            design_url(uuid.uuid4()), data="{}", content_type="application/json"
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_failed"

    def test_get_endpoints_do_not_require_csrf(self):
        client = csrf_client()
        assert client.get(DESIGNS_URL).status_code == 200


class TestList:
    def test_anonymous_browser_without_workspace_gets_empty_list(self):
        client = csrf_client()
        response = client.get(DESIGNS_URL)
        assert response.status_code == 200
        assert response.json() == {"designs": []}
        assert response["Cache-Control"] == "no-store"

    def test_listing_never_creates_a_design_session(self):
        client = csrf_client()
        client.get(DESIGNS_URL)
        client.get(DESIGNS_URL)
        assert DesignSession.objects.count() == 0
        assert DESIGN_SESSION_KEY not in client.session

    def test_designs_are_returned_newest_first(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        create_design(client, title="first", token=token)
        create_design(client, title="second", token=token)
        titles = [d["title"] for d in client.get(DESIGNS_URL).json()["designs"]]
        assert titles == ["second", "first"]


class TestCreate:
    def test_create_returns_201_with_exact_public_shape(self):
        client = csrf_client()
        response = create_design(client, title="My walima concept")
        assert response.status_code == 201
        assert response["Cache-Control"] == "no-store"
        body = response.json()
        assert set(body) == PUBLIC_DESIGN_KEYS
        assert body["title"] == "My walima concept"
        assert body["status"] == "draft"
        assert body["answers"] == {}
        assert body["latest_job"] is None
        assert uuid.UUID(body["id"])
        assert body["created_at"] and body["updated_at"]

    def test_title_may_be_omitted_or_blank(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        assert create_design(client, token=token).status_code == 201
        response = create_design(client, title="", token=token)
        assert response.status_code == 201
        assert response.json()["title"] == ""

    def test_title_is_trimmed(self):
        client = csrf_client()
        response = create_design(client, title="   Nikah look   ")
        assert response.json()["title"] == "Nikah look"

    def test_title_longer_than_120_characters_is_rejected(self):
        client = csrf_client()
        response = create_design(client, title="x" * 121)
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "validation_failed"
        assert "title" in body["error"]["fields"]
        assert Design.objects.count() == 0

    @pytest.mark.parametrize(
        "payload,offending",
        [
            ({"id": str(uuid.uuid4())}, "id"),
            ({"design_session": str(uuid.uuid4())}, "design_session"),
            ({"status": "draft"}, "status"),
            ({"answers": {"garment": "lehenga"}}, "answers"),
            ({"versions": []}, "versions"),
            ({"generation_attempts": []}, "generation_attempts"),
            ({"created_at": "2026-01-01T00:00:00Z"}, "created_at"),
            ({"updated_at": "2026-01-01T00:00:00Z"}, "updated_at"),
            ({"title": "fine", "nonsense": True}, "nonsense"),
        ],
    )
    def test_unknown_and_immutable_fields_are_rejected_not_ignored(self, payload, offending):
        client = csrf_client()
        token = bootstrap_csrf(client)
        response = send_json(client, "post", DESIGNS_URL, payload, token=token)
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "validation_failed"
        assert offending in body["error"]["fields"]
        assert Design.objects.count() == 0

    def test_non_object_json_body_is_rejected(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        response = send_json(client, "post", DESIGNS_URL, ["not", "an", "object"], token=token)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_failed"

    def test_malformed_json_is_rejected_with_a_controlled_400(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        response = client.post(
            DESIGNS_URL,
            data="{not json",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_json"

    def test_create_stores_the_workspace_pointer_not_the_django_session_key(self):
        client = csrf_client()
        create_design(client, title="private")
        design_session = DesignSession.objects.get()
        # The Django session data points at the DesignSession UUID...
        assert client.session[DESIGN_SESSION_KEY] == str(design_session.id)
        # ...and no domain column contains the raw Django session key.
        raw_session_key = client.session.session_key
        assert raw_session_key
        stored_values = [
            str(getattr(design_session, field.attname))
            for field in design_session._meta.concrete_fields
        ]
        assert raw_session_key not in stored_values


class TestRetrieve:
    def test_owner_can_retrieve_their_design(self):
        client = csrf_client()
        design_id = create_design(client, title="mine").json()["id"]
        response = client.get(design_url(design_id))
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert set(response.json()) == PUBLIC_DESIGN_KEYS

    def test_nonexistent_design_is_404(self):
        client = csrf_client()
        create_design(client)
        response = client.get(design_url(uuid.uuid4()))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_another_anonymous_browser_gets_404_not_403(self):
        owner = csrf_client()
        design_id = create_design(owner, title="private").json()["id"]
        stranger = csrf_client()
        response = stranger.get(design_url(design_id))
        # 404, never 403 — a 403 would confirm the UUID exists.
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestUpdate:
    def test_patch_updates_only_the_title(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = create_design(client, title="before", token=token).json()["id"]
        response = send_json(
            client, "patch", design_url(design_id), {"title": "after"}, token=token
        )
        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "after"
        assert body["status"] == "draft"
        assert body["answers"] == {}

    def test_patch_rejects_answers_and_other_fields(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = create_design(client, token=token).json()["id"]
        for payload, offending in (
            ({"answers": {"x": 1}}, "answers"),
            ({"status": "draft"}, "status"),
        ):
            response = send_json(client, "patch", design_url(design_id), payload, token=token)
            assert response.status_code == 400
            assert offending in response.json()["error"]["fields"]
        design = Design.objects.get(pk=design_id)
        assert design.answers == {}

    def test_patch_on_another_browsers_design_is_404(self):
        owner = csrf_client()
        design_id = create_design(owner, title="original").json()["id"]
        stranger = csrf_client()
        token = bootstrap_csrf(stranger)
        response = send_json(
            stranger, "patch", design_url(design_id), {"title": "stolen"}, token=token
        )
        assert response.status_code == 404
        assert Design.objects.get(pk=design_id).title == "original"

    def test_empty_patch_changes_nothing(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = create_design(client, title="keep", token=token).json()["id"]
        response = send_json(client, "patch", design_url(design_id), {}, token=token)
        assert response.status_code == 200
        assert response.json()["title"] == "keep"


class TestResponsePrivacy:
    def test_no_design_session_identifier_appears_in_any_response(self):
        client = csrf_client()
        create_response = create_design(client, title="secret workspace")
        design_session_id = str(DesignSession.objects.get().id)
        design_id = create_response.json()["id"]
        for response in (
            create_response,
            client.get(DESIGNS_URL),
            client.get(design_url(design_id)),
        ):
            assert design_session_id not in response.content.decode()


class TestWorkspaceCoordinationFailure:
    """A session-store failure during workspace resolution must fail
    CLOSED: controlled 503, full rollback, no unlocked fallback, and no
    database/session detail in the response or the logs."""

    POISON = "session store exploded: secret=hunter2 sessionid=abc123xyz"

    def _assert_failed_closed(self, response, caplog):
        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "design_workspace_unavailable"
        assert response["Cache-Control"] == "no-store"
        text = response.content.decode()
        for marker in ("hunter2", "abc123xyz", "RuntimeError", "Traceback", "session"):
            assert marker not in text
        # No design was created under an uncertain owner — everything
        # rolled back, including the workspace row.
        assert Design.objects.count() == 0
        assert DesignSession.objects.count() == 0
        # Logs carry only the safe breadcrumb, never the store detail.
        assert "design workspace coordination failed" in caplog.text
        assert "exception_type=RuntimeError" in caplog.text
        assert "hunter2" not in caplog.text
        assert "abc123xyz" not in caplog.text

    def test_session_row_save_failure_fails_closed(self, monkeypatch, caplog):
        from django.contrib.sessions.models import Session

        client = csrf_client()
        token = bootstrap_csrf(client)

        def exploding_save(self, *args, **kwargs):
            raise RuntimeError(TestWorkspaceCoordinationFailure.POISON)

        monkeypatch.setattr(Session, "save", exploding_save)
        with caplog.at_level("WARNING"):
            response = send_json(client, "post", DESIGNS_URL, {"title": "doomed"}, token=token)
        self._assert_failed_closed(response, caplog)

    def test_session_encode_failure_fails_closed(self, monkeypatch, caplog):
        from django.contrib.sessions.backends.base import SessionBase

        client = csrf_client()
        token = bootstrap_csrf(client)

        def exploding_encode(self, session_dict):
            raise RuntimeError(TestWorkspaceCoordinationFailure.POISON)

        monkeypatch.setattr(SessionBase, "encode", exploding_encode)
        with caplog.at_level("WARNING"):
            response = send_json(client, "post", DESIGNS_URL, {}, token=token)
        self._assert_failed_closed(response, caplog)


class TestResponseCaching:
    def test_all_design_responses_carry_no_store(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        created = create_design(client, title="t", token=token)
        design_id = created.json()["id"]
        responses = [
            created,
            client.get(DESIGNS_URL),
            client.get(design_url(design_id)),
            send_json(client, "patch", design_url(design_id), {"title": "u"}, token=token),
            client.get(design_url(uuid.uuid4())),  # even the 404s
            send_json(client, "post", DESIGNS_URL, {"bogus": 1}, token=token),  # and the 400s
        ]
        for response in responses:
            assert response["Cache-Control"] == "no-store"
