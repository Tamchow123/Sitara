"""DesignVersion refinement-lineage and GenerationAttempt refinement-kind
database constraints (Phase 14 Part A) — the final backstop below the
refinement services added in Parts B/C."""

import pytest
from django.contrib import admin
from django.db import IntegrityError, transaction
from django.utils import timezone

from sitara.designs.models import Design, DesignSession, DesignVersion, GenerationAttempt

pytestmark = pytest.mark.django_db


def make_design() -> Design:
    session = DesignSession.objects.create()
    return Design.objects.create(design_session=session)


def make_version(design=None, **kwargs) -> DesignVersion:
    design = design or make_design()
    fields = {"design": design, "version_number": 1}
    fields.update(kwargs)
    return DesignVersion.objects.create(**fields)


SPEC_PROVENANCE = {
    "design_spec": {"schema_version": 1},
    "design_spec_schema_version": 1,
    "design_spec_template_version": "2.0.0",
    "design_spec_provider": "fixture",
    "design_spec_model": "fixture-model",
}


def full_spec_provenance(**overrides):
    data = dict(SPEC_PROVENANCE)
    data["design_spec_generated_at"] = timezone.now()
    data.update(overrides)
    return data


_EMPTY_HASH = "e" * 64
_VALID_REQUEST = {"schema_version": 1, "change_type": "colour_story", "note": ""}


def make_refined_child(parent, **overrides) -> DesignVersion:
    fields = {
        "design": parent.design,
        "version_number": 2,
        "parent_version": parent,
        "refinement_request": _VALID_REQUEST,
        "refinement_request_schema_version": 1,
        "refinement_request_sha256": _EMPTY_HASH,
    }
    fields.update(full_spec_provenance())
    fields.update(overrides)
    return DesignVersion.objects.create(**fields)


class TestVersionOneHasNoParent:
    def test_version_one_without_parent_is_valid(self):
        version = make_version()
        assert version.parent_version_id is None
        assert version.refinement_request is None

    def test_version_one_with_parent_is_blocked(self):
        design = make_design()
        v1 = make_version(design=design)
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                design=design,
                version_number=1,
                parent_version=v1,
                refinement_request=_VALID_REQUEST,
                refinement_request_schema_version=1,
                refinement_request_sha256=_EMPTY_HASH,
            )


class TestVersionTwoRequiresParent:
    def test_version_two_with_parent_and_complete_request_is_valid(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        v2 = make_refined_child(v1)
        v2.refresh_from_db()
        assert v2.parent_version_id == v1.pk
        assert v2.refinement_request == _VALID_REQUEST
        assert v2.refinement_request_schema_version == 1
        assert v2.refinement_request_sha256 == _EMPTY_HASH

    def test_version_two_without_parent_is_blocked(self):
        design = make_design()
        make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(design=design, version_number=2, **full_spec_provenance())


class TestRefinementRequestAllOrNone:
    def test_partial_refinement_provenance_missing_schema_version_is_blocked(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_refined_child(v1, refinement_request_schema_version=None)

    def test_partial_refinement_provenance_missing_hash_is_blocked(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_refined_child(v1, refinement_request_sha256="")

    def test_partial_refinement_provenance_missing_request_is_blocked(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_refined_child(v1, refinement_request=None)

    def test_wrong_schema_version_is_blocked(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_refined_child(v1, refinement_request_schema_version=2)

    def test_malformed_hash_is_blocked(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_refined_child(v1, refinement_request_sha256="not-a-hash")

    def test_uppercase_hash_is_blocked(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_refined_child(v1, refinement_request_sha256="E" * 64)


class TestParentRequiresRequestAndViceVersa:
    def test_parent_without_refinement_request_is_blocked(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                design=design,
                version_number=2,
                parent_version=v1,
                **full_spec_provenance(),
            )

    def test_refinement_request_without_parent_is_blocked(self):
        design = make_design()
        make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_version(
                design=design,
                version_number=2,
                refinement_request=_VALID_REQUEST,
                refinement_request_schema_version=1,
                refinement_request_sha256=_EMPTY_HASH,
                **full_spec_provenance(),
            )


class TestSelfParenting:
    def test_self_parenting_is_blocked_at_database_level(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        v1.parent_version = v1
        v1.refinement_request = _VALID_REQUEST
        v1.refinement_request_schema_version = 1
        v1.refinement_request_sha256 = _EMPTY_HASH
        with pytest.raises(IntegrityError), transaction.atomic():
            v1.save()


class TestExistingVersionsRemainValid:
    def test_legacy_version_without_refinement_fields_is_unaffected(self):
        version = make_version()
        assert DesignVersion.objects.filter(pk=version.pk).exists()
        version.refresh_from_db()
        assert version.parent_version_id is None
        assert version.refinement_request is None
        assert version.refinement_request_schema_version is None
        assert version.refinement_request_sha256 == ""


class TestDesignVersionAdminReadOnly:
    def test_refinement_fields_are_read_only(self):
        model_admin = admin.site._registry[DesignVersion]
        readonly = set(model_admin.readonly_fields)
        for field in (
            "parent_version",
            "refinement_request",
            "refinement_request_schema_version",
            "refinement_request_sha256",
        ):
            assert field in readonly


# ---------------------------------------------------------------------------
# GenerationAttempt.generation_kind / source_design_version / seed_reused
# ---------------------------------------------------------------------------


def make_attempt(design=None, **kwargs) -> GenerationAttempt:
    design = design or make_design()
    fields = {"design": design}
    fields.update(kwargs)
    return GenerationAttempt.objects.create(**fields)


class TestGenerationKind:
    def test_initial_attempt_defaults_and_has_no_source_version(self):
        attempt = make_attempt()
        assert attempt.generation_kind == GenerationAttempt.GenerationKind.INITIAL
        assert attempt.source_design_version_id is None
        assert attempt.seed_reused is False

    def test_refinement_attempt_requires_source_version(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_attempt(generation_kind=GenerationAttempt.GenerationKind.REFINEMENT)

    def test_refinement_attempt_with_source_version_is_valid(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        attempt = make_attempt(
            design=design,
            generation_kind=GenerationAttempt.GenerationKind.REFINEMENT,
            source_design_version=v1,
            seed_reused=True,
        )
        attempt.refresh_from_db()
        assert attempt.source_design_version_id == v1.pk
        assert attempt.seed_reused is True

    def test_initial_attempt_with_source_version_is_blocked(self):
        design = make_design()
        v1 = make_version(design=design, **full_spec_provenance())
        with pytest.raises(IntegrityError), transaction.atomic():
            make_attempt(
                design=design,
                generation_kind=GenerationAttempt.GenerationKind.INITIAL,
                source_design_version=v1,
            )


class TestGenerationAttemptAdminReadOnly:
    def test_refinement_kind_fields_are_read_only(self):
        model_admin = admin.site._registry[GenerationAttempt]
        readonly = set(model_admin.readonly_fields)
        for field in ("generation_kind", "source_design_version", "seed_reused"):
            assert field in readonly
