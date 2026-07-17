"""Activation-lifecycle tests for activate_questionnaire_version."""

import pytest
from django.contrib.auth import get_user_model

from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.schema_validation import QuestionnaireSchemaError
from sitara.questionnaire.services import (
    QuestionnaireActivationError,
    activate_questionnaire_version,
)

from .utils import make_version, valid_schema

pytestmark = pytest.mark.django_db

User = get_user_model()


class TestActivation:
    def test_draft_becomes_the_active_version(self):
        staff = User.objects.create_user(
            email="staff-activate@example.com", password="Correct-Horse-Battery-2026!"
        )
        draft = make_version(version=1)
        schema_before = draft.schema

        activated = activate_questionnaire_version(draft, activated_by=staff)

        assert activated.status == QuestionnaireVersion.Status.ACTIVE
        assert activated.activated_at is not None
        assert activated.activated_by == staff
        # The schema is preserved byte-for-byte by activation.
        assert activated.schema == schema_before
        activated.refresh_from_db()
        assert activated.status == QuestionnaireVersion.Status.ACTIVE

    def test_activated_by_may_be_omitted(self):
        activated = activate_questionnaire_version(make_version(version=1))
        assert activated.status == QuestionnaireVersion.Status.ACTIVE
        assert activated.activated_by is None

    def test_previous_active_version_is_retired_in_the_same_call(self):
        first = make_version(version=1)
        activate_questionnaire_version(first)
        second = make_version(version=2)

        activate_questionnaire_version(second)

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.status == QuestionnaireVersion.Status.RETIRED
        assert second.status == QuestionnaireVersion.Status.ACTIVE
        assert (
            QuestionnaireVersion.objects.filter(status=QuestionnaireVersion.Status.ACTIVE).count()
            == 1
        )

    @pytest.mark.parametrize("status", ["active", "retired"])
    def test_only_a_draft_can_be_activated(self, status):
        row = make_version(version=1, status=status)
        with pytest.raises(QuestionnaireActivationError):
            activate_questionnaire_version(row)

    def test_malformed_schema_is_never_activated(self):
        draft = make_version(version=1, schema={"schema_version": 1})
        with pytest.raises(QuestionnaireSchemaError):
            activate_questionnaire_version(draft)
        draft.refresh_from_db()
        assert draft.status == QuestionnaireVersion.Status.DRAFT
        assert draft.activated_at is None

    def test_failed_activation_leaves_the_prior_active_version_unchanged(self):
        current = make_version(version=1)
        activate_questionnaire_version(current)
        current.refresh_from_db()
        activated_at_before = current.activated_at

        bad_draft = make_version(version=2, schema={"schema_version": 1, "rules": "nope"})
        with pytest.raises(QuestionnaireSchemaError):
            activate_questionnaire_version(bad_draft)

        # The whole transaction rolled back: v1 still active, untouched.
        current.refresh_from_db()
        bad_draft.refresh_from_db()
        assert current.status == QuestionnaireVersion.Status.ACTIVE
        assert current.activated_at == activated_at_before
        assert bad_draft.status == QuestionnaireVersion.Status.DRAFT

    def test_poisoned_rule_values_never_replace_the_active_version(self):
        # A draft whose rule values would previously have raised TypeError
        # must fail with QuestionnaireSchemaError and roll back completely.
        current = make_version(version=1)
        activate_questionnaire_version(current)
        current.refresh_from_db()
        activated_at_before = current.activated_at

        poisoned = valid_schema()
        poisoned["rules"][0]["when"]["values"] = [{"poison": "value"}]
        bad_draft = make_version(version=2, schema=poisoned)
        with pytest.raises(QuestionnaireSchemaError):
            activate_questionnaire_version(bad_draft)

        current.refresh_from_db()
        bad_draft.refresh_from_db()
        assert current.status == QuestionnaireVersion.Status.ACTIVE
        assert current.activated_at == activated_at_before
        assert bad_draft.status == QuestionnaireVersion.Status.DRAFT
        assert bad_draft.activated_at is None

    def test_activation_replaces_rather_than_mutates(self):
        # The replacement flow end to end: v1 active, v2 drafted with a
        # different schema, v2 activated. v1's schema never changed.
        first = make_version(version=1)
        activate_questionnaire_version(first)
        first_schema = first.schema

        changed = valid_schema()
        changed["title"] = "Second edition"
        second = make_version(version=2, schema=changed)
        activate_questionnaire_version(second)

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.schema == first_schema
        assert first.status == QuestionnaireVersion.Status.RETIRED
        assert second.schema["title"] == "Second edition"
        assert second.status == QuestionnaireVersion.Status.ACTIVE
