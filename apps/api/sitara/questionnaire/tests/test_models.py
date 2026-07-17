"""QuestionnaireVersion model and database-constraint tests (PostgreSQL)."""

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from sitara.questionnaire.models import QuestionnaireVersion

from .utils import make_version, valid_schema

pytestmark = pytest.mark.django_db


class TestModelBasics:
    def test_uuid_primary_key(self):
        first = make_version(version=1)
        second = make_version(version=2)
        assert isinstance(first.pk, uuid.UUID)
        assert isinstance(second.pk, uuid.UUID)
        assert first.pk != second.pk

    def test_default_status_is_draft(self):
        assert make_version().status == QuestionnaireVersion.Status.DRAFT

    def test_ordering_puts_newest_version_first(self):
        make_version(version=1)
        make_version(version=3)
        make_version(version=2)
        assert list(QuestionnaireVersion.objects.values_list("version", flat=True)) == [3, 2, 1]


class TestDatabaseConstraints:
    def test_version_zero_is_rejected_by_the_database(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(version=0)

    def test_version_must_be_globally_unique(self):
        make_version(version=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(version=1)

    def test_unknown_status_is_rejected_by_the_database(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(status="published")

    def test_at_most_one_active_version(self):
        make_version(version=1, status="active")
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(version=2, status="active")

    def test_many_draft_and_retired_versions_may_coexist(self):
        make_version(version=1, status="retired")
        make_version(version=2, status="retired")
        make_version(version=3, status="draft")
        make_version(version=4, status="draft")
        assert QuestionnaireVersion.objects.count() == 4


class TestImmutability:
    @pytest.mark.parametrize("status", ["active", "retired"])
    def test_schema_is_immutable_once_published(self, status):
        row = make_version(status=status)
        row.schema = {**valid_schema(), "title": "Edited"}
        with pytest.raises(ValidationError):
            row.save()

    @pytest.mark.parametrize("status", ["active", "retired"])
    def test_version_number_is_immutable_once_published(self, status):
        row = make_version(version=1, status=status)
        row.version = 99
        with pytest.raises(ValidationError):
            row.save()

    def test_draft_schema_and_version_stay_editable(self):
        row = make_version(version=1, status="draft")
        row.version = 2
        row.schema = {**valid_schema(), "title": "Edited draft"}
        row.save()
        row.refresh_from_db()
        assert row.version == 2
        assert row.schema["title"] == "Edited draft"

    def test_status_transition_alone_is_allowed_on_published_rows(self):
        # Activation must be able to retire the previous active version.
        row = make_version(status="active")
        row.status = QuestionnaireVersion.Status.RETIRED
        row.save(update_fields=["status", "updated_at"])
        row.refresh_from_db()
        assert row.status == QuestionnaireVersion.Status.RETIRED
