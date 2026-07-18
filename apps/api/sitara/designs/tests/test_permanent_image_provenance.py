"""DesignVersion permanent-image provenance constraints (Phase 11 spec §5).

Constraint tests run against real PostgreSQL — every partial combination,
shape violation and cross-field rule is exercised at INSERT time.
"""

import pytest
from django.db import DataError, IntegrityError, transaction
from django.utils import timezone

from sitara.designs.admin import DesignVersionAdmin
from sitara.designs.models import Design, DesignSession, DesignVersion

pytestmark = pytest.mark.django_db


def _design() -> Design:
    return Design.objects.create(design_session=DesignSession.objects.create())


_SPEC_PROVENANCE = dict(
    design_spec={"schema_version": 1},
    design_spec_schema_version=1,
    design_spec_template_version="v1",
    design_spec_provider="fixture",
    design_spec_model="fixture-model",
)

_PERMANENT_IMAGE = dict(
    image_storage_key="design-images/d/v/original.webp",
    image_sha256="a" * 64,
    image_size_bytes=1000,
    image_width=1536,
    image_height=2048,
    thumbnail_storage_key="design-images/d/v/thumbnail.webp",
    thumbnail_sha256="b" * 64,
    thumbnail_size_bytes=100,
    thumbnail_width=384,
    thumbnail_height=512,
    image_processor_version="1.0.0",
)


def _complete_kwargs(**overrides) -> dict:
    values = dict(
        version_number=1,
        **_SPEC_PROVENANCE,
        design_spec_generated_at=timezone.now(),
        image_prompt="A prompt.",
        prompt_builder_version="3.0.0",
        **_PERMANENT_IMAGE,
        image_ingested_at=timezone.now(),
    )
    values.update(overrides)
    return values


def _create(**overrides) -> DesignVersion:
    return DesignVersion.objects.create(design=_design(), **_complete_kwargs(**overrides))


def _assert_rejected(**overrides) -> None:
    # DataError covers over-length values the column itself refuses (e.g. a
    # 65-character hash); IntegrityError covers every CHECK constraint.
    with pytest.raises((IntegrityError, DataError)), transaction.atomic():
        _create(**overrides)


class TestAllOrNone:
    def test_legacy_row_with_all_permanent_fields_absent_is_valid(self):
        version = DesignVersion.objects.create(design=_design(), version_number=1)
        assert not version.has_permanent_image

    def test_phase10_row_with_spec_and_prompt_but_no_image_is_valid(self):
        version = _create(
            **{name: "" for name in DesignVersion.PERMANENT_IMAGE_CHAR_FIELDS},
            **{name: None for name in DesignVersion.PERMANENT_IMAGE_NULLABLE_FIELDS},
        )
        assert not version.has_permanent_image

    def test_complete_permanent_metadata_is_valid(self):
        version = _create()
        assert version.has_permanent_image

    @pytest.mark.parametrize("missing", DesignVersion.PERMANENT_IMAGE_CHAR_FIELDS)
    def test_each_missing_char_field_is_rejected(self, missing):
        _assert_rejected(**{missing: ""})

    @pytest.mark.parametrize("missing", DesignVersion.PERMANENT_IMAGE_NULLABLE_FIELDS)
    def test_each_missing_nullable_field_is_rejected(self, missing):
        _assert_rejected(**{missing: None})


class TestShapes:
    @pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "z" * 64, "a" * 65])
    def test_invalid_original_hash_shapes_are_rejected(self, bad):
        _assert_rejected(image_sha256=bad)

    @pytest.mark.parametrize("bad", ["B" * 64, "b" * 63])
    def test_invalid_thumbnail_hash_shapes_are_rejected(self, bad):
        _assert_rejected(thumbnail_sha256=bad)

    @pytest.mark.parametrize(
        "field",
        [
            "image_size_bytes",
            "image_width",
            "image_height",
            "thumbnail_size_bytes",
            "thumbnail_width",
            "thumbnail_height",
        ],
    )
    def test_zero_sizes_and_dimensions_are_rejected(self, field):
        _assert_rejected(**{field: 0})

    @pytest.mark.parametrize(
        "field",
        [
            "image_size_bytes",
            "image_width",
            "image_height",
            "thumbnail_size_bytes",
            "thumbnail_width",
            "thumbnail_height",
        ],
    )
    def test_negative_sizes_and_dimensions_are_rejected(self, field):
        _assert_rejected(**{field: -1})


class TestCrossFieldRules:
    def test_identical_original_and_thumbnail_keys_are_rejected(self):
        _assert_rejected(thumbnail_storage_key=_PERMANENT_IMAGE["image_storage_key"])

    def test_permanent_image_without_design_spec_is_rejected(self):
        _assert_rejected(
            design_spec=None,
            design_spec_schema_version=None,
            design_spec_template_version="",
            design_spec_provider="",
            design_spec_model="",
            design_spec_generated_at=None,
            image_prompt="",
            prompt_builder_version="",
        )

    def test_permanent_image_without_prompt_is_rejected(self):
        _assert_rejected(image_prompt="", prompt_builder_version="")


class TestAdminReadOnly:
    def test_every_permanent_image_field_is_read_only_in_admin(self):
        readonly = set(DesignVersionAdmin.readonly_fields)
        expected = set(DesignVersion.PERMANENT_IMAGE_CHAR_FIELDS) | set(
            DesignVersion.PERMANENT_IMAGE_NULLABLE_FIELDS
        )
        assert expected <= readonly


class TestUnknownWriteProtection:
    def test_permanent_fields_are_only_populated_together_in_practice(self):
        # Sanity: the helper used across these tests matches the model's own
        # field lists, so a future field addition cannot silently escape the
        # constraint tests.
        char_fields = set(DesignVersion.PERMANENT_IMAGE_CHAR_FIELDS)
        nullable_fields = set(DesignVersion.PERMANENT_IMAGE_NULLABLE_FIELDS)
        covered = {name for name in _complete_kwargs() if name in char_fields | nullable_fields}
        assert covered == char_fields | nullable_fields
