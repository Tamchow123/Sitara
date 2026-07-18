"""Atomic image-prompt persistence: save-together, constraints, immutability."""

import threading

import pytest
from django.db import IntegrityError, transaction

from sitara.designs.models import DesignVersion
from sitara.generation.context import build_generation_context
from sitara.generation.prompt_builder import (
    PROMPT_BUILDER_VERSION,
    ImagePromptBuildError,
    build_image_prompt,
)
from sitara.generation.prompt_service import (
    ImagePromptImmutable,
    build_and_store_image_prompt,
)
from sitara.generation.services import generate_design_spec_for_design

from . import fakes
from .factory import make_complete_design

pytestmark = pytest.mark.django_db


def _version_with_spec() -> DesignVersion:
    design = make_complete_design()
    ss = build_generation_context(design).source_selections
    return generate_design_spec_for_design(
        design, provider=fakes.SequenceProvider([fakes.valid_result(ss)])
    )


class TestSaveTogether:
    def test_prompt_and_version_persist_together(self):
        version = _version_with_spec()
        assert version.image_prompt == ""
        updated = build_and_store_image_prompt(version)
        updated.refresh_from_db()
        assert updated.image_prompt
        assert updated.prompt_builder_version == PROMPT_BUILDER_VERSION
        # The stored prompt is exactly what the deterministic builder produces.
        from sitara.generation.design_spec import DesignSpec

        expected = build_image_prompt(DesignSpec.model_validate(updated.design_spec))
        assert updated.image_prompt == expected

    def test_prompt_requires_a_design_spec(self):
        design = make_complete_design()
        # A legacy-style version with no DesignSpec at all.
        version = DesignVersion.objects.create(design=design, version_number=1)
        with pytest.raises(ImagePromptBuildError):
            build_and_store_image_prompt(version)


class TestDatabaseConstraints:
    def test_prompt_without_builder_version_is_rejected(self):
        version = _version_with_spec()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                DesignVersion.objects.filter(pk=version.pk).update(
                    image_prompt="a prompt", prompt_builder_version=""
                )

    def test_prompt_without_a_spec_violates_requires_spec(self):
        design = make_complete_design()
        version = DesignVersion.objects.create(design=design, version_number=1)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                DesignVersion.objects.filter(pk=version.pk).update(
                    image_prompt="a prompt", prompt_builder_version=PROMPT_BUILDER_VERSION
                )

    def test_existing_phase8_row_without_prompt_remains_valid(self):
        # A version with a spec but no image prompt is valid (Phase 8 shape).
        version = _version_with_spec()
        version.refresh_from_db()
        assert version.design_spec is not None
        assert version.image_prompt == ""
        assert version.prompt_builder_version == ""


class TestImmutability:
    def test_same_build_is_idempotent(self):
        version = _version_with_spec()
        first = build_and_store_image_prompt(version)
        prompt = first.image_prompt
        second = build_and_store_image_prompt(first)
        second.refresh_from_db()
        assert second.image_prompt == prompt
        assert second.prompt_builder_version == PROMPT_BUILDER_VERSION

    def test_existing_different_prompt_is_not_overwritten(self):
        version = build_and_store_image_prompt(_version_with_spec())
        original = version.image_prompt
        # Simulate a historical prompt produced by a different builder version.
        DesignVersion.objects.filter(pk=version.pk).update(prompt_builder_version="0.9.0")
        stale = DesignVersion.objects.get(pk=version.pk)
        with pytest.raises(ImagePromptImmutable):
            build_and_store_image_prompt(stale)
        stale.refresh_from_db()
        assert stale.image_prompt == original
        assert stale.prompt_builder_version == "0.9.0"


class TestAdminReadOnly:
    def test_image_prompt_fields_are_read_only(self):
        from sitara.designs.admin import DesignVersionAdmin

        assert "image_prompt" in DesignVersionAdmin.readonly_fields
        assert "prompt_builder_version" in DesignVersionAdmin.readonly_fields


@pytest.mark.django_db(transaction=True)
def test_concurrent_builds_store_one_identical_prompt():
    design = make_complete_design()
    ss = build_generation_context(design).source_selections
    version = generate_design_spec_for_design(
        design, provider=fakes.SequenceProvider([fakes.valid_result(ss)])
    )

    start = threading.Event()
    errors: list = []

    def worker():
        from django.db import connection as thread_connection

        try:
            start.wait(timeout=10)
            build_and_store_image_prompt(version)
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion
            errors.append(exc)
        finally:
            thread_connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []  # both succeed: one writes, one is idempotent
    version.refresh_from_db()
    assert version.prompt_builder_version == PROMPT_BUILDER_VERSION
    assert version.image_prompt
    # Exactly one stored prompt value.
    assert DesignVersion.objects.filter(design=design).count() == 1
