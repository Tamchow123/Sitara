"""Crash-safe canonical ingest tests (Phase 11 Part A, spec §7/§8/§13).

Zero network: staging and final storage are injected in-memory doubles, and
every recovery path is exercised without any provider fake even existing —
the ingest service has no provider dependency to call.
"""

import hashlib
import io
import uuid

import pytest
from django.conf import settings
from django.utils import timezone
from PIL import Image

from sitara.designs.models import DesignVersion, GenerationAttempt
from sitara.generation.image_fixtures import InMemoryStorage
from sitara.generation.tests.factory import make_complete_design
from sitara.media.exceptions import (
    DesignImageImmutable,
    DesignImageIngestFailed,
    DesignImageIngestRetry,
)
from sitara.media.image_processing import (
    DESIGN_IMAGE_PROCESSOR_VERSION,
    process_design_image,
)
from sitara.media.ingest import build_design_image_keys, ingest_staged_design_image

from . import images

pytestmark = pytest.mark.django_db

_Status = GenerationAttempt.Status


class CountingStorage(InMemoryStorage):
    """Records which keys were saved so reuse paths are provable."""

    def __init__(self):
        super().__init__()
        self.saved_keys = []

    def save(self, key, content):
        self.saved_keys.append(key)
        return super().save(key, content)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_version(design, **overrides) -> DesignVersion:
    values = dict(
        design=design,
        version_number=1,
        design_spec={"schema_version": 1},
        design_spec_schema_version=1,
        design_spec_template_version="v1",
        design_spec_provider="fixture",
        design_spec_model="fixture-model",
        design_spec_generated_at=timezone.now(),
        image_prompt="A deterministic ingest-test prompt.",
        prompt_builder_version="3.0.0",
    )
    values.update(overrides)
    return DesignVersion.objects.create(**values)


def _make_staged(staging, *, data=None, version=None, design=None, **attempt_overrides):
    design = design or make_complete_design()
    version = version if version is not None else _make_version(design)
    data = data if data is not None else images.webp_portrait_bytes()
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
    values = dict(
        design=design,
        design_version=version,
        status=_Status.RUNNING_IMAGE,
        staged_image_storage_key=f"generation-staging/{uuid.uuid4()}/raw.webp",
        staged_image_sha256=_sha(data),
        staged_image_size_bytes=len(data),
        staged_image_width=width,
        staged_image_height=height,
    )
    values.update(attempt_overrides)
    attempt = GenerationAttempt.objects.create(**values)
    staging._objects[attempt.staged_image_storage_key] = data
    return attempt, version


def _expected_processed(data: bytes):
    return process_design_image(
        data,
        max_edge=settings.DESIGN_IMAGE_MAX_EDGE,
        thumbnail_edge=settings.DESIGN_IMAGE_THUMBNAIL_EDGE,
        full_quality=settings.DESIGN_IMAGE_WEBP_QUALITY,
        thumbnail_quality=settings.DESIGN_IMAGE_THUMBNAIL_QUALITY,
        max_bytes=settings.GENERATION_RAW_MAX_BYTES,
        max_pixels=settings.GENERATION_RAW_MAX_PIXELS,
    )


class TestNormalIngest:
    def test_normal_ingest_writes_two_verified_webp_objects(self):
        staging, final = InMemoryStorage(), CountingStorage()
        attempt, version = _make_staged(staging)
        result = ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)

        keys = build_design_image_keys(attempt.design_id, version.pk)
        assert sorted(final.saved_keys) == sorted([keys.original, keys.thumbnail])
        for key in (keys.original, keys.thumbnail):
            stored = Image.open(io.BytesIO(final._objects[key]))
            assert stored.format == "WEBP"

        data = staging._objects[attempt.staged_image_storage_key]
        expected = _expected_processed(data)
        assert result.image_storage_key == keys.original
        assert result.image_sha256 == expected.original_sha256
        assert result.image_size_bytes == len(expected.original_bytes)
        assert (result.image_width, result.image_height) == (
            expected.original_width,
            expected.original_height,
        )
        assert result.thumbnail_storage_key == keys.thumbnail
        assert result.thumbnail_sha256 == expected.thumbnail_sha256
        assert result.thumbnail_size_bytes == len(expected.thumbnail_bytes)
        assert result.image_processor_version == DESIGN_IMAGE_PROCESSOR_VERSION
        assert result.image_ingested_at is not None
        assert result.has_permanent_image

    def test_second_ingest_is_idempotent_with_no_further_writes(self):
        staging, final = InMemoryStorage(), CountingStorage()
        attempt, version = _make_staged(staging)
        first = ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        writes_after_first = list(final.saved_keys)
        second = ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        assert final.saved_keys == writes_after_first
        assert second.image_sha256 == first.image_sha256
        assert second.image_ingested_at == first.image_ingested_at

    def test_staged_metadata_is_retained_after_ingest(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, _version = _make_staged(staging)
        ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        attempt.refresh_from_db()
        assert attempt.staged_image_storage_key
        assert staging.exists(attempt.staged_image_storage_key)


class TestObjectRecovery:
    def test_existing_matching_original_is_reused(self):
        staging, final = InMemoryStorage(), CountingStorage()
        attempt, version = _make_staged(staging)
        data = staging._objects[attempt.staged_image_storage_key]
        expected = _expected_processed(data)
        keys = build_design_image_keys(attempt.design_id, version.pk)
        final._objects[keys.original] = expected.original_bytes  # crash left it behind
        result = ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        assert final.saved_keys == [keys.thumbnail]  # only the missing one written
        assert result.has_permanent_image

    def test_existing_matching_thumbnail_is_reused(self):
        staging, final = InMemoryStorage(), CountingStorage()
        attempt, version = _make_staged(staging)
        data = staging._objects[attempt.staged_image_storage_key]
        expected = _expected_processed(data)
        keys = build_design_image_keys(attempt.design_id, version.pk)
        final._objects[keys.thumbnail] = expected.thumbnail_bytes
        result = ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        assert final.saved_keys == [keys.original]
        assert result.has_permanent_image

    def test_both_objects_written_but_metadata_lost_recovers_the_metadata(self):
        staging, final = InMemoryStorage(), CountingStorage()
        attempt, version = _make_staged(staging)
        data = staging._objects[attempt.staged_image_storage_key]
        expected = _expected_processed(data)
        keys = build_design_image_keys(attempt.design_id, version.pk)
        final._objects[keys.original] = expected.original_bytes
        final._objects[keys.thumbnail] = expected.thumbnail_bytes
        result = ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        assert final.saved_keys == []  # nothing rewritten
        assert result.has_permanent_image
        assert result.image_sha256 == expected.original_sha256

    def test_metadata_present_verifies_objects_without_reprocessing(self, monkeypatch):
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, version = _make_staged(staging)
        ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)

        def _must_not_process(*args, **kwargs):
            raise AssertionError("reprocessing must not run when metadata is complete")

        monkeypatch.setattr("sitara.media.ingest.process_design_image", _must_not_process)
        # Staging storage must not even be read on this path.
        result = ingest_staged_design_image(attempt, staging_storage=None, final_storage=final)
        assert result.has_permanent_image

    def test_fresh_attempt_without_staged_fields_finalises_on_ingested_version(self):
        # FUNC-001 regression: the metadata-already-committed fast path must
        # work purely off the DesignVersion's committed provenance — a fresh
        # attempt linked to an already-ingested version but carrying NO staged
        # fields of its own must verify and return, never terminally fail.
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, version = _make_staged(staging)
        ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        # Terminalise the first attempt so the fresh one does not violate the
        # single-in-progress-per-design constraint.
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=_Status.SUCCEEDED, completed_at=timezone.now()
        )
        fresh = GenerationAttempt.objects.create(
            design=attempt.design, design_version=version, status=_Status.QUEUED
        )
        result = ingest_staged_design_image(fresh, staging_storage=None, final_storage=final)
        assert result.has_permanent_image
        assert result.pk == version.pk

    def test_metadata_present_but_object_missing_fails(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, version = _make_staged(staging)
        ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        version.refresh_from_db()
        del final._objects[version.image_storage_key]
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)

    def test_metadata_present_but_object_divergent_fails(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, version = _make_staged(staging)
        ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        version.refresh_from_db()
        final._objects[version.image_storage_key] = b"corrupted-after-commit"
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)


class TestConflicts:
    def test_conflicting_final_object_fails_without_overwrite(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, version = _make_staged(staging)
        keys = build_design_image_keys(attempt.design_id, version.pk)
        final._objects[keys.original] = b"someone else's object"
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        assert final._objects[keys.original] == b"someone else's object"
        version.refresh_from_db()
        assert not version.has_permanent_image

    def test_key_renaming_backend_fails_and_cleans_up(self):
        staging = InMemoryStorage()

        class RenamingStorage(InMemoryStorage):
            def save(self, key, content):
                renamed = key + ".alt"
                self._objects[renamed] = content.read()
                return renamed

        final = RenamingStorage()
        attempt, version = _make_staged(staging)
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        assert final._objects == {}  # best-effort cleanup removed the rename
        version.refresh_from_db()
        assert not version.has_permanent_image

    def test_concurrent_divergent_metadata_raises_immutable(self, monkeypatch):
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, version = _make_staged(staging)

        import sitara.media.ingest as ingest_module

        real_read = ingest_module._read_verified_staged_bytes

        def read_then_race(*args, **kwargs):
            data = real_read(*args, **kwargs)
            # Simulate a concurrent ingest committing DIFFERENT provenance
            # between our unlocked read and the final locked write. (Direct
            # QuerySet.update: deliberately simulating out-of-band state.)
            DesignVersion.objects.filter(pk=version.pk).update(
                image_storage_key="design-images/other/original.webp",
                image_sha256="a" * 64,
                image_size_bytes=1,
                image_width=1,
                image_height=1,
                thumbnail_storage_key="design-images/other/thumbnail.webp",
                thumbnail_sha256="b" * 64,
                thumbnail_size_bytes=1,
                thumbnail_width=1,
                thumbnail_height=1,
                image_processor_version="0.0.1",
                image_ingested_at=timezone.now(),
            )
            return data

        monkeypatch.setattr(ingest_module, "_read_verified_staged_bytes", read_then_race)
        with pytest.raises(DesignImageImmutable):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        version.refresh_from_db()
        assert version.image_sha256 == "a" * 64  # existing provenance untouched


class TestStagedSourceValidation:
    def test_missing_staged_object_fails(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, _version = _make_staged(staging)
        del staging._objects[attempt.staged_image_storage_key]
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)

    def test_staged_hash_mismatch_fails(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, _version = _make_staged(staging)
        staging._objects[attempt.staged_image_storage_key] = images.webp_portrait_bytes(
            width=512, height=683
        )
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)

    def test_staged_dimension_mismatch_fails(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        data = images.webp_portrait_bytes()
        attempt, _version = _make_staged(staging, data=data)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            staged_image_width=999, staged_image_height=999
        )
        attempt.refresh_from_db()
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)

    def test_decompression_bomb_during_revalidation_is_classified(self, monkeypatch):
        # TEST-005: the staged-bytes revalidation opens the image with Pillow
        # directly; a decompression bomb tripping Pillow's own threshold must
        # surface as the safe, generic DesignImageIngestFailed — never an
        # unclassified PIL exception escaping the crash-safe service.
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, _version = _make_staged(staging)
        # Lower Pillow's bomb threshold below the staged 768x1024 image so
        # Image.open raises DecompressionBombError inside revalidation.
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
        with pytest.raises(DesignImageIngestFailed) as exc:
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        message = str(exc.value)
        assert message == "the staged object failed verification"
        assert "pixel" not in message.lower()  # no Pillow internals leak

    def test_attempt_without_staged_data_fails(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        design = make_complete_design()
        version = _make_version(design)
        attempt = GenerationAttempt.objects.create(
            design=design, design_version=version, status=_Status.RUNNING_IMAGE
        )
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)

    def test_attempt_version_ownership_mismatch_fails(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        design_a = make_complete_design()
        design_b = make_complete_design(questionnaire=design_a.questionnaire_version)
        foreign_version = _make_version(design_b)
        attempt, _ = _make_staged(staging, design=design_a, version=foreign_version)
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)

    def test_version_without_spec_or_prompt_fails(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        design = make_complete_design()
        bare_version = DesignVersion.objects.create(design=design, version_number=1)
        attempt, _ = _make_staged(staging, design=design, version=bare_version)
        with pytest.raises(DesignImageIngestFailed):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)


class TestTransientFailures:
    def test_transient_exists_failure_is_retryable(self):
        staging = InMemoryStorage()

        class BlippingStorage(InMemoryStorage):
            def exists(self, key):
                raise ConnectionError("storage blip")

        attempt, _version = _make_staged(staging)
        with pytest.raises(DesignImageIngestRetry):
            ingest_staged_design_image(
                attempt, staging_storage=staging, final_storage=BlippingStorage()
            )

    def test_transient_save_failure_is_retryable_then_recovers(self):
        staging = InMemoryStorage()

        class BlippingSave(InMemoryStorage):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def save(self, key, content):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("storage blip")
                return super().save(key, content)

        final = BlippingSave()
        attempt, _version = _make_staged(staging)
        with pytest.raises(DesignImageIngestRetry):
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        result = ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        assert result.has_permanent_image


class TestSafeLogs:
    """Spec §13: no key/hash/prompt leaks through LOGS (the exception half
    lives in TestSafeMessages below). Mirrors the catalogue ingest precedent."""

    def test_success_log_carries_only_uuids_and_processor_version(self, caplog):
        import logging

        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, version = _make_staged(staging)
        with caplog.at_level(logging.DEBUG, logger="sitara.media.ingest"):
            result = ingest_staged_design_image(
                attempt, staging_storage=staging, final_storage=final
            )
        assert str(attempt.pk) in caplog.text
        assert str(version.pk) in caplog.text
        assert DESIGN_IMAGE_PROCESSOR_VERSION in caplog.text
        assert attempt.staged_image_storage_key not in caplog.text
        assert attempt.staged_image_sha256 not in caplog.text
        assert result.image_storage_key not in caplog.text
        assert result.image_sha256 not in caplog.text
        assert version.image_prompt not in caplog.text

    def test_transport_error_details_never_reach_the_logs(self, caplog):
        # A real backend exception may embed endpoints/credentials/keys in its
        # message; the ingest module must classify it into a generic exception
        # and log NOTHING containing the original text.
        import logging

        marker = "endpoint http://minio:9000 credential sitara-minio secret"
        staging = InMemoryStorage()

        class LeakyStorage(InMemoryStorage):
            def exists(self, key):
                raise OSError(f"{marker} key={key}")

        attempt, _version = _make_staged(staging)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(DesignImageIngestRetry) as exc:
                ingest_staged_design_image(
                    attempt, staging_storage=staging, final_storage=LeakyStorage()
                )
        assert marker not in caplog.text
        assert marker not in str(exc.value)
        assert attempt.staged_image_storage_key not in caplog.text


@pytest.mark.django_db(transaction=True)
class TestRowLockSerialisation:
    """TEST-002: the final metadata write must serialise on the REAL
    PostgreSQL DesignVersion row lock — proven with a second connection
    genuinely holding SELECT ... FOR UPDATE, not a monkeypatched simulation."""

    def test_ingest_blocks_on_a_held_version_row_lock_then_completes(self):
        import threading
        import time

        from django.db import connection as main_connection

        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, version = _make_staged(staging)

        held = threading.Event()
        release = threading.Event()
        ingest_done = threading.Event()
        ingest_error: list = []

        def hold_lock():
            # django.db.connection is thread-local: this thread gets its own
            # real PostgreSQL connection (same pattern as the advisory-lock
            # serialisation test in generation/tests/test_pipeline.py).
            from django.db import connection as lock_connection

            try:
                with lock_connection.cursor() as cursor:
                    cursor.execute("BEGIN")
                    cursor.execute(
                        "SELECT id FROM designs_designversion WHERE id = %s FOR UPDATE",
                        [str(version.pk)],
                    )
                    held.set()
                    release.wait(timeout=20)
                    cursor.execute("COMMIT")
            finally:
                lock_connection.close()

        def run_ingest():
            from django.db import connection as thread_connection

            try:
                ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
            except Exception as exc:  # noqa: BLE001 - recorded for the assertion
                ingest_error.append(exc)
            finally:
                ingest_done.set()
                thread_connection.close()

        holder = threading.Thread(target=hold_lock)
        holder.start()
        try:
            assert held.wait(timeout=10)
            ingester = threading.Thread(target=run_ingest)
            ingester.start()
            # While the row lock is held the ingest CANNOT commit metadata:
            # give it ample time to reach the locked write, then prove no
            # provenance is visible and the ingest is still blocked.
            time.sleep(1.0)
            with main_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT image_storage_key FROM designs_designversion WHERE id = %s",
                    [str(version.pk)],
                )
                assert cursor.fetchone()[0] == ""
            assert not ingest_done.is_set()
            # Release the lock: the blocked ingest completes with one
            # consistent committed outcome.
            release.set()
            ingester.join(timeout=20)
            assert ingest_done.is_set()
            assert ingest_error == []
            version.refresh_from_db()
            assert version.has_permanent_image
        finally:
            release.set()
            holder.join(timeout=20)


class TestSafeMessages:
    def test_exceptions_never_leak_keys_hashes_or_prompts(self):
        staging, final = InMemoryStorage(), InMemoryStorage()
        attempt, version = _make_staged(staging)
        keys = build_design_image_keys(attempt.design_id, version.pk)
        final._objects[keys.original] = b"conflict"
        with pytest.raises(DesignImageIngestFailed) as conflict_exc:
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        del final._objects[keys.original]
        del staging._objects[attempt.staged_image_storage_key]
        with pytest.raises(DesignImageIngestFailed) as missing_exc:
            ingest_staged_design_image(attempt, staging_storage=staging, final_storage=final)
        for exc in (conflict_exc, missing_exc):
            message = str(exc.value)
            assert attempt.staged_image_storage_key not in message
            assert attempt.staged_image_sha256 not in message
            assert keys.original not in message
            assert "design-images/" not in message
            assert "generation-staging/" not in message
            assert version.image_prompt not in message
