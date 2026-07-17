"""Public active-questionnaire endpoint tests.

The endpoint is read-only and identity-free: it must never create a Django
session or a DesignSession, never leak staff or lifecycle fields, and must
answer the same safe 503 whether the active version is missing or corrupt.
"""

import pytest
from django.contrib.sessions.models import Session
from django.test import Client

from sitara.designs.models import DesignSession
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.services import activate_questionnaire_version

from .utils import QUESTIONNAIRE_ACTIVE_URL, make_version

pytestmark = pytest.mark.django_db


def _activate(version: int = 1) -> QuestionnaireVersion:
    row = make_version(version=version)
    return activate_questionnaire_version(row)


class TestActiveEndpoint:
    def test_exact_response_shape(self):
        active = _activate()
        response = Client().get(QUESTIONNAIRE_ACTIVE_URL)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"id", "version", "schema"}
        assert body["id"] == str(active.id)
        assert body["version"] == 1
        assert body["schema"] == active.schema

    def test_cache_control_no_store(self):
        _activate()
        assert Client().get(QUESTIONNAIRE_ACTIVE_URL)["Cache-Control"] == "no-store"

    def test_slash_optional_routing(self):
        _activate()
        assert Client().get("/api/v1/questionnaire/active").status_code == 200

    def test_no_session_or_design_session_is_created_by_get(self):
        _activate()
        client = Client()
        response = client.get(QUESTIONNAIRE_ACTIVE_URL)
        assert response.status_code == 200
        assert "sitara_sessionid" not in response.cookies
        assert "sitara_csrftoken" not in response.cookies
        assert Session.objects.count() == 0
        assert DesignSession.objects.count() == 0

    def test_no_staff_information_leaks(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        creator = User.objects.create_user(
            email="creator-secret@example.com", password="Correct-Horse-Battery-2026!"
        )
        activator = User.objects.create_user(
            email="activator-secret@example.com", password="Correct-Horse-Battery-2026!"
        )
        draft = make_version(version=1, created_by=creator)
        activate_questionnaire_version(draft, activated_by=activator)

        response = Client().get(QUESTIONNAIRE_ACTIVE_URL)
        text = response.content.decode()
        for marker in (
            "creator-secret",
            "activator-secret",
            str(creator.pk),
            str(activator.pk),
            "created_by",
            "activated_by",
            "created_at",
            "updated_at",
            "activated_at",
        ):
            assert marker not in text


class TestUnavailable:
    def _assert_safe_503(self, response):
        assert response.status_code == 503
        body = response.json()
        assert body == {
            "error": {
                "code": "questionnaire_unavailable",
                "message": "The questionnaire is temporarily unavailable.",
            }
        }
        assert response["Cache-Control"] == "no-store"

    def test_missing_active_version_returns_safe_503(self):
        self._assert_safe_503(Client().get(QUESTIONNAIRE_ACTIVE_URL))

    def test_draft_versions_are_never_served(self):
        make_version(version=1, status="draft")
        self._assert_safe_503(Client().get(QUESTIONNAIRE_ACTIVE_URL))

    def test_retired_versions_are_never_served(self):
        make_version(version=1, status="retired")
        self._assert_safe_503(Client().get(QUESTIONNAIRE_ACTIVE_URL))

    def test_corrupt_active_schema_returns_safe_503(self, caplog):
        active = _activate()
        # update() bypasses the model's immutability guard, simulating
        # storage-level corruption of the active schema.
        QuestionnaireVersion.objects.filter(pk=active.pk).update(
            schema={"schema_version": 1, "poison_marker_field": "poison_marker_value"}
        )
        with caplog.at_level("ERROR"):
            response = Client().get(QUESTIONNAIRE_ACTIVE_URL)
        self._assert_safe_503(response)
        # The malformed content never reaches the client...
        assert "poison_marker" not in response.content.decode()
        # ...and the log carries only the version id and exception type.
        assert f"questionnaire_version_id={active.pk}" in caplog.text
        assert "exception_type=QuestionnaireSchemaError" in caplog.text
        assert "poison_marker" not in caplog.text

    def _assert_only_id_and_type_logged(self, caplog, response, pk, exception_type):
        self._assert_safe_503(response)
        assert "poison_marker" not in response.content.decode()
        assert f"questionnaire_version_id={pk}" in caplog.text
        assert f"exception_type={exception_type}" in caplog.text
        assert "poison_marker" not in caplog.text
        assert "Traceback" not in caplog.text

    def test_object_inside_when_values_returns_safe_503(self, caplog):
        # Before the validator was total over rule-value types, this shape
        # raised TypeError (unhashable dict) and the endpoint answered 500.
        active = _activate()
        corrupt = active.schema
        corrupt["rules"][0]["when"]["values"] = [{"poison_marker_key": "poison_marker_value"}]
        QuestionnaireVersion.objects.filter(pk=active.pk).update(schema=corrupt)
        with caplog.at_level("ERROR"):
            response = Client().get(QUESTIONNAIRE_ACTIVE_URL)
        self._assert_only_id_and_type_logged(
            caplog, response, active.pk, "QuestionnaireSchemaError"
        )

    def test_nested_list_inside_restrict_values_returns_safe_503(self, caplog):
        active = _activate()
        corrupt = active.schema
        corrupt["rules"][0]["then"]["values"] = [["poison_marker_nested"]]
        QuestionnaireVersion.objects.filter(pk=active.pk).update(schema=corrupt)
        with caplog.at_level("ERROR"):
            response = Client().get(QUESTIONNAIRE_ACTIVE_URL)
        self._assert_only_id_and_type_logged(
            caplog, response, active.pk, "QuestionnaireSchemaError"
        )

    def test_object_as_rule_operator_returns_safe_503(self, caplog):
        # Enum fields were the last place a malformed shape could raise
        # TypeError from a frozenset membership test before the type check.
        active = _activate()
        corrupt = active.schema
        corrupt["rules"][0]["when"]["operator"] = {"poison_marker_key": "poison_marker_value"}
        QuestionnaireVersion.objects.filter(pk=active.pk).update(schema=corrupt)
        with caplog.at_level("ERROR"):
            response = Client().get(QUESTIONNAIRE_ACTIVE_URL)
        self._assert_only_id_and_type_logged(
            caplog, response, active.pk, "QuestionnaireSchemaError"
        )

    def test_list_as_question_type_returns_safe_503(self, caplog):
        active = _activate()
        corrupt = active.schema
        corrupt["steps"][0]["questions"][0]["type"] = ["poison_marker_list"]
        QuestionnaireVersion.objects.filter(pk=active.pk).update(schema=corrupt)
        with caplog.at_level("ERROR"):
            response = Client().get(QUESTIONNAIRE_ACTIVE_URL)
        self._assert_only_id_and_type_logged(
            caplog, response, active.pk, "QuestionnaireSchemaError"
        )

    def test_unexpected_validation_exception_returns_safe_503(self, caplog, monkeypatch):
        # Defence in depth: even if a validator gap lets some structure
        # raise something other than QuestionnaireSchemaError, the public
        # response is the same safe 503 and the exception text never
        # reaches the log.
        active = _activate()

        def boom(schema):
            raise RuntimeError("poison_marker_secret_detail")

        monkeypatch.setattr("sitara.questionnaire.views.validate_questionnaire_schema", boom)
        with caplog.at_level("ERROR"):
            response = Client().get(QUESTIONNAIRE_ACTIVE_URL)
        self._assert_only_id_and_type_logged(caplog, response, active.pk, "RuntimeError")
