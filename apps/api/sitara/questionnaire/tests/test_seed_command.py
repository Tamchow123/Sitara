"""``seed_questionnaire`` — the path CI depends on.

A CI database is built fresh every run, so the branch that matters here is the
one a developer's long-lived database never takes: create the version from a
committed fixture and activate it. These tests exercise that path directly
rather than trusting the idempotent "already active" branch, which is the only
one a local run usually reaches.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from sitara.questionnaire.models import QuestionnaireVersion


@pytest.mark.django_db
class TestSeedQuestionnaire:
    def test_creates_and_activates_on_an_empty_database(self):
        assert not QuestionnaireVersion.objects.exists()

        call_command("seed_questionnaire")

        active = QuestionnaireVersion.objects.get(status=QuestionnaireVersion.Status.ACTIVE)
        # Defaults to the NEWEST committed fixture, so a future v5 is picked up
        # without touching CI.
        newest = max(
            int(p.stem.rsplit("_v", 1)[1])
            for p in (__import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures").glob(
                "questionnaire_v*.json"
            )
        )
        assert active.version == newest
        assert active.schema

    def test_named_version_is_honoured(self):
        call_command("seed_questionnaire", "--schema-version", 1)

        active = QuestionnaireVersion.objects.get(status=QuestionnaireVersion.Status.ACTIVE)
        assert active.version == 1

    def test_running_twice_is_a_no_op(self):
        call_command("seed_questionnaire")
        before = QuestionnaireVersion.objects.get(status=QuestionnaireVersion.Status.ACTIVE)

        call_command("seed_questionnaire")

        after = QuestionnaireVersion.objects.get(status=QuestionnaireVersion.Status.ACTIVE)
        assert after.pk == before.pk
        assert after.activated_at == before.activated_at
        assert QuestionnaireVersion.objects.count() == 1

    def test_activating_a_second_version_retires_the_first(self):
        call_command("seed_questionnaire", "--schema-version", 1)
        call_command("seed_questionnaire", "--schema-version", 2)

        # The one-active invariant holds through the service, not through this
        # command — which is the point of routing activation through it.
        assert (
            QuestionnaireVersion.objects.filter(status=QuestionnaireVersion.Status.ACTIVE).count()
            == 1
        )
        assert (
            QuestionnaireVersion.objects.get(version=1).status
            == QuestionnaireVersion.Status.RETIRED
        )

    def test_unknown_version_is_refused(self):
        with pytest.raises(CommandError, match="No committed fixture"):
            call_command("seed_questionnaire", "--schema-version", 99)

        assert not QuestionnaireVersion.objects.exists()

    def test_a_retired_version_is_never_reactivated(self):
        call_command("seed_questionnaire", "--schema-version", 1)
        call_command("seed_questionnaire", "--schema-version", 2)

        # Republishing a retired definition would resurrect answers the schema
        # no longer accepts; a new version is the only way forward.
        with pytest.raises(CommandError, match="retired"):
            call_command("seed_questionnaire", "--schema-version", 1)
