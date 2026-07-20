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
        # Phase 14: version_number==2 now always requires refinement
        # provenance (parent + request), so this exercises that alongside
        # the pre-Phase-8 spec-less state.
        design = Design.objects.create(design_session=DesignSession.objects.create())
        v1 = make_version(design=design, version_number=1)
        version = make_version(
            design=design,
            version_number=2,
            image_storage_key="",
            parent_version=v1,
            refinement_request={"schema_version": 1, "change_type": "colour_story", "note": ""},
            refinement_request_schema_version=1,
            refinement_request_sha256="e" * 64,
        )
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


_EMPTY_HASH = "e" * 64
_VALID_SNAPSHOT = {"schema_version": 1, "items": []}


class TestInspirationContextProvenance:
    """DesignVersion.inspiration_context/_schema_version/_sha256 database
    constraints (Phase 13) — the final backstop below the generation
    service's snapshot persistence."""

    def test_legacy_version_without_inspiration_context_is_valid(self):
        version = make_version()
        assert version.inspiration_context is None
        assert version.inspiration_context_schema_version is None
        assert version.inspiration_context_sha256 == ""

    def test_null_provenance_alongside_a_spec_is_valid(self):
        version = make_version(**full_provenance())
        version.refresh_from_db()
        assert version.inspiration_context is None

    def test_complete_empty_snapshot_provenance_is_valid(self):
        version = make_version(
            **full_provenance(
                inspiration_context=_VALID_SNAPSHOT,
                inspiration_context_schema_version=1,
                inspiration_context_sha256=_EMPTY_HASH,
            )
        )
        version.refresh_from_db()
        assert version.inspiration_context == _VALID_SNAPSHOT

    def test_complete_selected_snapshot_provenance_is_valid(self):
        snapshot = {
            "schema_version": 1,
            "items": [
                {
                    "asset_id": "11111111-1111-1111-1111-111111111111",
                    "position": 1,
                    "provider_cues": {
                        "garment_type": "lehenga",
                        "visual_description": "A description.",
                        "cultural_context": None,
                    },
                    "acknowledgement": {"title": "A look", "attribution": ""},
                }
            ],
        }
        version = make_version(
            **full_provenance(
                inspiration_context=snapshot,
                inspiration_context_schema_version=1,
                inspiration_context_sha256=_EMPTY_HASH,
            )
        )
        version.refresh_from_db()
        assert version.inspiration_context == snapshot

    def test_context_without_schema_version_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                **full_provenance(
                    inspiration_context=_VALID_SNAPSHOT,
                    inspiration_context_sha256=_EMPTY_HASH,
                )
            )

    def test_context_without_hash_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                **full_provenance(
                    inspiration_context=_VALID_SNAPSHOT,
                    inspiration_context_schema_version=1,
                )
            )

    def test_schema_version_without_context_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                **full_provenance(
                    inspiration_context_schema_version=1,
                    inspiration_context_sha256=_EMPTY_HASH,
                )
            )

    def test_hash_without_context_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(**full_provenance(inspiration_context_sha256=_EMPTY_HASH))

    def test_invalid_schema_version_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                **full_provenance(
                    inspiration_context=_VALID_SNAPSHOT,
                    inspiration_context_schema_version=2,
                    inspiration_context_sha256=_EMPTY_HASH,
                )
            )

    def test_malformed_hash_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                **full_provenance(
                    inspiration_context=_VALID_SNAPSHOT,
                    inspiration_context_schema_version=1,
                    inspiration_context_sha256="not-a-valid-hash",
                )
            )

    def test_uppercase_hash_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                **full_provenance(
                    inspiration_context=_VALID_SNAPSHOT,
                    inspiration_context_schema_version=1,
                    inspiration_context_sha256="E" * 64,
                )
            )

    def test_context_without_design_spec_is_blocked(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                design_spec=None,
                inspiration_context=_VALID_SNAPSHOT,
                inspiration_context_schema_version=1,
                inspiration_context_sha256=_EMPTY_HASH,
            )

    def test_inspiration_context_admin_fields_are_read_only(self):
        model_admin = admin.site._registry[DesignVersion]
        readonly = set(model_admin.readonly_fields)
        for field in (
            "inspiration_context",
            "inspiration_context_schema_version",
            "inspiration_context_sha256",
        ):
            assert field in readonly
