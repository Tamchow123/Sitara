"""DesignVersion DesignSpec-provenance database constraints (Phase 8).

Written through the ORM directly to prove the DATABASE enforces the
all-or-none provenance and positive-token invariants — the final backstop
below the generation service."""

import pytest
from django.contrib import admin
from django.db import IntegrityError, transaction
from django.utils import timezone

from sitara.designs.models import Design, DesignSession, DesignVersion

pytestmark = pytest.mark.django_db


def make_version(**kwargs) -> DesignVersion:
    session = DesignSession.objects.create()
    design = Design.objects.create(design_session=session)
    fields = {"design": design, "version_number": 1}
    fields.update(kwargs)
    return DesignVersion.objects.create(**fields)


PROVENANCE = {
    "design_spec": {"schema_version": 1},
    "design_spec_schema_version": 1,
    "design_spec_template_version": "1.0.0",
    "design_spec_provider": "fixture",
    "design_spec_model": "fixture-model",
    "design_spec_generated_at": None,  # filled per test
}


def full_provenance(**overrides):
    data = dict(PROVENANCE)
    data["design_spec_generated_at"] = timezone.now()
    data.update(overrides)
    return data


class TestAllOrNoneProvenance:
    def test_no_spec_and_no_provenance_is_valid(self):
        version = make_version()
        assert version.design_spec is None
        assert version.design_spec_provider == ""
        assert version.design_spec_schema_version is None

    def test_full_spec_with_full_provenance_is_valid(self):
        version = make_version(**full_provenance())
        version.refresh_from_db()
        assert version.design_spec == {"schema_version": 1}
        assert version.design_spec_provider == "fixture"

    def test_spec_without_provenance_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(design_spec={"schema_version": 1})

    def test_provenance_without_spec_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                design_spec=None,
                design_spec_schema_version=1,
                design_spec_template_version="1.0.0",
                design_spec_provider="fixture",
                design_spec_model="fixture-model",
                design_spec_generated_at=timezone.now(),
            )

    def test_partial_provenance_missing_model_is_blocked(self):
        data = full_provenance()
        data["design_spec_model"] = ""
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(**data)

    def test_partial_provenance_missing_timestamp_is_blocked(self):
        data = full_provenance()
        data["design_spec_generated_at"] = None
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(**data)


class TestTokenCounts:
    def test_positive_token_counts_are_valid(self):
        version = make_version(
            **full_provenance(design_spec_input_tokens=10, design_spec_output_tokens=20)
        )
        version.refresh_from_db()
        assert version.design_spec_input_tokens == 10

    def test_tokens_may_be_null_when_spec_present(self):
        version = make_version(**full_provenance())
        assert version.design_spec_input_tokens is None

    def test_zero_input_tokens_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(**full_provenance(design_spec_input_tokens=0))

    def test_zero_output_tokens_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(**full_provenance(design_spec_output_tokens=0))


class TestExistingRowsRemainValid:
    def test_legacy_version_without_spec_is_unaffected(self):
        # A version created before Phase 8 (no spec) still saves cleanly.
        version = make_version(version_number=2, image_storage_key="")
        assert DesignVersion.objects.filter(pk=version.pk).exists()


class TestAdminReadOnly:
    def test_provenance_admin_fields_are_read_only(self):
        model_admin = admin.site._registry[DesignVersion]
        readonly = set(model_admin.readonly_fields)
        for field in (
            "design_spec",
            "design_spec_schema_version",
            "design_spec_template_version",
            "design_spec_provider",
            "design_spec_model",
            "design_spec_input_tokens",
            "design_spec_output_tokens",
            "design_spec_generated_at",
        ):
            assert field in readonly
        assert model_admin.has_add_permission(request=None) is False
