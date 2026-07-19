"""The ``ingest_design_image`` operator command (Phase 11 spec §11).

Zero provider calls by construction: the command has no provider dependency,
the conftest network guard fails loudly on any socket use, and storage
resolves to the in-memory aliases.
"""

import hashlib
import io
import uuid

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.generation import errors
from sitara.generation.image_fixtures import make_synthetic_webp

from .factory import make_complete_design

pytestmark = pytest.mark.django_db

_Status = GenerationAttempt.Status


def _make_failed_staged_attempt(*, error_code=errors.IMAGE_INGEST_UNVERIFIED):
    design = make_complete_design()
    design.status = Design.Status.GENERATION_FAILED
    design.save(update_fields=["status"])
    version = DesignVersion.objects.create(
        design=design,
        version_number=1,
        design_spec={"schema_version": 1},
        design_spec_schema_version=1,
        design_spec_template_version="v1",
        design_spec_provider="fixture",
        design_spec_model="fixture-model",
        design_spec_generated_at=timezone.now(),
        image_prompt="A deterministic command-test prompt.",
        prompt_builder_version="3.0.0",
    )
    data = make_synthetic_webp()
    key = f"generation-staging/{uuid.uuid4()}/raw.webp"
    default_storage.save(key, ContentFile(data))
    attempt = GenerationAttempt.objects.create(
        design=design,
        design_version=version,
        status=_Status.FAILED,
        error_code=error_code,
        completed_at=timezone.now(),
        staged_image_storage_key=key,
        staged_image_sha256=hashlib.sha256(data).hexdigest(),
        staged_image_size_bytes=len(data),
        staged_image_width=768,
        staged_image_height=1024,
    )
    return attempt, version, data


def _call(*args) -> str:
    out = io.StringIO()
    call_command("ingest_design_image", *args, stdout=out)
    return out.getvalue()


class TestValidation:
    def test_non_uuid_attempt_is_refused(self):
        with pytest.raises(CommandError):
            _call("--attempt", "not-a-uuid")

    def test_unknown_attempt_is_refused(self):
        with pytest.raises(CommandError):
            _call("--attempt", str(uuid.uuid4()))

    def test_attempt_without_staged_data_is_refused(self, inmemory_storage):
        design = make_complete_design()
        # An ingest-stage code (so the status/code gate admits it) but with no
        # staged data at all — the staged-data check must still refuse it.
        attempt = GenerationAttempt.objects.create(
            design=design,
            status=_Status.FAILED,
            error_code=errors.IMAGE_INGEST_UNVERIFIED,
            completed_at=timezone.now(),
        )
        with pytest.raises(CommandError, match="staged"):
            _call("--attempt", str(attempt.id))

    def test_mismatched_design_version_is_refused(self, inmemory_storage):
        attempt, _version, _data = _make_failed_staged_attempt()
        other_design = make_complete_design(questionnaire=attempt.design.questionnaire_version)
        foreign_version = DesignVersion.objects.create(design=other_design, version_number=1)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(design_version=foreign_version)
        with pytest.raises(CommandError):
            _call("--attempt", str(attempt.pk))


class TestRecovery:
    def test_successful_ingest_completes_the_failed_attempt(self, inmemory_storage):
        attempt, version, _data = _make_failed_staged_attempt()
        output = _call("--attempt", str(attempt.pk))

        version.refresh_from_db()
        assert version.has_permanent_image
        attempt.refresh_from_db()
        assert attempt.status == _Status.SUCCEEDED
        assert attempt.error_code == ""
        attempt.design.refresh_from_db()
        assert attempt.design.status == Design.Status.GENERATED
        assert "zero provider calls" in output

    def test_rerun_is_idempotent(self, inmemory_storage):
        attempt, version, _data = _make_failed_staged_attempt()
        _call("--attempt", str(attempt.pk))
        version.refresh_from_db()
        first_ingested_at = version.image_ingested_at
        output = _call("--attempt", str(attempt.pk))
        version.refresh_from_db()
        assert version.image_ingested_at == first_ingested_at
        assert "succeeded" in output

    def test_non_ingest_failure_codes_are_refused(self, inmemory_storage):
        # Narrow-recovery guard: an attempt that failed for a NON-ingest reason must be
        # refused outright — silently committing permanent image data for it
        # would leave an attempt/Design state no code path can ever resolve.
        attempt, version, _data = _make_failed_staged_attempt(error_code=errors.IMAGE_POLL_TIMEOUT)
        with pytest.raises(CommandError, match="ingest"):
            _call("--attempt", str(attempt.pk))
        version.refresh_from_db()
        assert not version.has_permanent_image
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED
        assert attempt.error_code == errors.IMAGE_POLL_TIMEOUT

    def test_in_progress_attempts_are_refused(self, inmemory_storage):
        # Worker-race guard: an in-progress attempt belongs to a (possibly actively
        # retrying) worker — the command must never race it.
        attempt, version, _data = _make_failed_staged_attempt()
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=_Status.RUNNING_IMAGE, error_code="", completed_at=None
        )
        with pytest.raises(CommandError, match="not terminally failed"):
            _call("--attempt", str(attempt.pk))
        version.refresh_from_db()
        assert not version.has_permanent_image

    def test_output_reports_only_safe_fields(self, inmemory_storage):
        attempt, version, data = _make_failed_staged_attempt()
        output = _call("--attempt", str(attempt.pk))
        version.refresh_from_db()

        assert str(attempt.pk) in output
        assert str(version.pk) in output
        assert "processor_version=1.0.0" in output
        assert f"original={version.image_width}x{version.image_height}" in output
        assert f"thumbnail={version.thumbnail_width}x{version.thumbnail_height}" in output
        # Never printed: keys, hashes, prompts, answers.
        assert "design-images/" not in output
        assert "generation-staging/" not in output
        assert version.image_sha256 not in output
        assert version.thumbnail_sha256 not in output
        assert hashlib.sha256(data).hexdigest() not in output
        assert version.image_prompt not in output
