"""Retention purge + stuck-generation reconciliation tests (Phase 16, Part C).

Provider-free. Uses the generation package's autouse in-memory ``design_images``
storage plus the opt-in ``inmemory_storage`` fixture for the default (staging)
store, so both permanent and staging object deletions are exercised without
touching MinIO.
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import DatabaseError, transaction
from django.utils import timezone

from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.designs.tests.utils import create_ready_design_version
from sitara.generation import cost_control, errors, maintenance
from sitara.generation.pipeline import _attempt_lock_keys

from .factory import make_active_v1, make_complete_design

pytestmark = pytest.mark.django_db

_Status = GenerationAttempt.Status


def _design():
    """A complete design reusing the single active v1 questionnaire (version is
    globally unique — creating a second collides across designs in one test)."""
    from sitara.questionnaire.models import QuestionnaireVersion

    questionnaire = (
        QuestionnaireVersion.objects.filter(version=1, status="active").first() or make_active_v1()
    )
    return make_complete_design(questionnaire=questionnaire)


def _age_design(design, *, days):
    Design.objects.filter(pk=design.pk).update(created_at=timezone.now() - timedelta(days=days))


def _staged_attempt(design, version, *, status=_Status.SUCCEEDED, **extra):
    staged_key = f"generation-staging/{uuid.uuid4()}/raw.webp"
    default_storage.save(staged_key, ContentFile(b"staged-bytes"))
    fields = dict(
        design=design,
        design_version=version,
        status=status,
        staged_image_storage_key=staged_key,
        staged_image_sha256="c" * 64,
        staged_image_size_bytes=10,
        staged_image_width=8,
        staged_image_height=8,
    )
    if status == _Status.SUCCEEDED:
        fields["completed_at"] = timezone.now()
    fields.update(extra)
    attempt = GenerationAttempt.objects.create(**fields)
    return attempt, staged_key


def _old_generated_design(*, days=40):
    design = _design()
    design.status = Design.Status.GENERATED
    design.save(update_fields=["status"])
    version = create_ready_design_version(design.id)  # writes permanent objects
    attempt, staged_key = _staged_attempt(design, version)
    _age_design(design, days=days)
    return design, version, attempt, staged_key


class TestRetentionPurge:
    def test_purge_deletes_rows_and_permanent_and_staging_objects(self, inmemory_storage):
        design, version, attempt, staged_key = _old_generated_design()
        permanent = maintenance.design_image_storage()
        assert permanent.exists(version.image_storage_key)
        assert permanent.exists(version.thumbnail_storage_key)
        assert default_storage.exists(staged_key)

        result = maintenance.purge_expired_designs()

        assert result["purged"] == 1
        assert not Design.objects.filter(pk=design.pk).exists()
        assert not DesignVersion.objects.filter(pk=version.pk).exists()
        assert not GenerationAttempt.objects.filter(pk=attempt.pk).exists()
        assert not permanent.exists(version.image_storage_key)
        assert not permanent.exists(version.thumbnail_storage_key)
        assert not default_storage.exists(staged_key)  # staging cleanup boundary

    def test_purge_deletes_crash_window_staging_object_without_a_recorded_key(
        self, inmemory_storage
    ):
        # A worker uploaded generation-staging/<attempt>/raw.webp then crashed
        # before committing staged_image_storage_key: the column is blank but the
        # object exists. Purge must still delete it (by its deterministic key) so
        # it is never orphaned past retention.
        design = _design()
        attempt = GenerationAttempt.objects.create(
            design=design,
            status=_Status.FAILED,
            error_code=errors.INTERNAL_GENERATION_ERROR,
            completed_at=timezone.now(),
        )
        orphan_key = f"generation-staging/{attempt.id}/raw.webp"
        default_storage.save(orphan_key, ContentFile(b"crash-window-bytes"))
        _age_design(design, days=40)
        assert attempt.staged_image_storage_key == ""
        assert default_storage.exists(orphan_key)

        result = maintenance.purge_expired_designs()

        assert result["purged"] == 1
        assert not Design.objects.filter(pk=design.pk).exists()
        assert not default_storage.exists(orphan_key)  # crash-window object removed

    def test_purge_tolerates_already_missing_objects(self, inmemory_storage):
        design, version, _attempt, staged_key = _old_generated_design()
        # Simulate a prior partial run: objects already gone, rows still present.
        maintenance.design_image_storage().delete(version.image_storage_key)
        maintenance.design_image_storage().delete(version.thumbnail_storage_key)
        default_storage.delete(staged_key)

        result = maintenance.purge_expired_designs()
        assert result["purged"] == 1
        assert not Design.objects.filter(pk=design.pk).exists()

    def test_storage_failure_retains_the_row_for_retry(self, inmemory_storage, monkeypatch):
        design, _version, _attempt, _key = _old_generated_design()

        def boom(_key):
            raise OSError("storage unavailable")

        monkeypatch.setattr(maintenance.design_image_storage(), "delete", boom)
        result = maintenance.purge_expired_designs()
        assert result["retained"] == 1
        assert result["purged"] == 0
        assert Design.objects.filter(pk=design.pk).exists()  # retained for retry

    def test_unrelated_objects_are_never_deleted(self, inmemory_storage):
        design, _v, _a, _k = _old_generated_design()
        # Objects that resemble catalogue / demo-source assets must survive.
        permanent = maintenance.design_image_storage()
        catalogue_key = "catalogue/inspiration/asset.webp"
        demo_key = "demo-assets/pack/hash/asset.webp"
        permanent.save(catalogue_key, ContentFile(b"catalogue"))
        default_storage.save(demo_key, ContentFile(b"demo"))

        maintenance.purge_expired_designs()
        assert permanent.exists(catalogue_key)
        assert default_storage.exists(demo_key)

    def test_batching_is_bounded(self, inmemory_storage, settings):
        settings.DESIGN_PURGE_BATCH_SIZE = 2
        for _ in range(3):
            _old_generated_design()
        result = maintenance.purge_expired_designs()
        assert result["purged"] == 2  # bounded to the batch
        assert Design.objects.count() == 1

    def test_in_progress_designs_are_skipped(self, inmemory_storage):
        design = _design()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        GenerationAttempt.objects.create(design=design, status=_Status.RUNNING_IMAGE)
        _age_design(design, days=40)
        result = maintenance.purge_expired_designs()
        assert result["skipped_in_progress"] == 1
        assert Design.objects.filter(pk=design.pk).exists()

    def test_recent_designs_are_not_purged(self, inmemory_storage):
        design, _v, _a, _k = _old_generated_design(days=1)  # within retention
        result = maintenance.purge_expired_designs()
        assert result["purged"] == 0
        assert Design.objects.filter(pk=design.pk).exists()

    def test_duplicate_delivery_is_idempotent(self, inmemory_storage):
        _old_generated_design()
        first = maintenance.purge_expired_designs()
        second = maintenance.purge_expired_designs()
        assert first["purged"] == 1
        assert second["purged"] == 0  # nothing left; a re-run is a safe no-op

    def test_db_delete_failure_is_contained_and_batch_continues(
        self, inmemory_storage, monkeypatch
    ):
        # REL-002: a DB cascade-delete failure (after objects were removed) for
        # one design must be logged/counted distinctly and NOT abort the batch.
        target, _v, _a, _k = _old_generated_design()
        _old_generated_design()  # a second, healthy candidate

        real_delete = Design.delete

        def selective_delete(self, *args, **kwargs):
            if self.pk == target.pk:
                raise DatabaseError("simulated cascade delete failure")
            return real_delete(self, *args, **kwargs)

        monkeypatch.setattr(Design, "delete", selective_delete)
        result = maintenance.purge_expired_designs()
        assert result["db_delete_failed"] == 1
        assert result["purged"] == 1  # the other candidate still processed
        assert Design.objects.filter(pk=target.pk).exists()  # retained for retry

    @pytest.mark.django_db(transaction=True)
    def test_purge_serialises_on_the_design_row_lock(self, inmemory_storage):
        # REL-001: purge takes the same Design row lock enqueue takes, so a
        # concurrent writer that creates an in-progress attempt under that lock
        # is respected — purge blocks until the writer commits, then skips.
        design, *_ = _old_generated_design()
        lock_held = threading.Event()
        release_lock = threading.Event()
        purge_done = threading.Event()
        result: dict = {}

        def holder():
            from django.db import connection as conn

            try:
                with transaction.atomic():
                    Design.objects.select_for_update().get(pk=design.pk)
                    GenerationAttempt.objects.create(design=design, status=_Status.RUNNING_TEXT)
                    lock_held.set()
                    release_lock.wait(timeout=10)
                # committing the atomic block releases the row lock
            finally:
                conn.close()

        def purger():
            from django.db import connection as conn

            try:
                lock_held.wait(timeout=10)
                result["res"] = maintenance.purge_expired_designs()  # blocks on the lock
                purge_done.set()
            finally:
                conn.close()

        th = threading.Thread(target=holder)
        tp = threading.Thread(target=purger)
        th.start()
        tp.start()
        try:
            assert lock_held.wait(timeout=10)
            # While the writer holds the row lock, purge must not have finished.
            assert not purge_done.wait(timeout=1.0)
            release_lock.set()
            assert purge_done.wait(timeout=10)
        finally:
            release_lock.set()
            th.join(timeout=10)
            tp.join(timeout=10)
        # Purge saw the in-progress attempt committed under the lock and skipped.
        assert result["res"]["skipped_in_progress"] == 1
        assert Design.objects.filter(pk=design.pk).exists()
        # Cleanup rows created with transaction=True (no auto-rollback here).
        Design.objects.filter(pk=design.pk).delete()


def _stuck_attempt(design, *, status=_Status.RUNNING_TEXT, minutes_idle=30, **extra):
    attempt = GenerationAttempt.objects.create(design=design, status=status, **extra)
    GenerationAttempt.objects.filter(pk=attempt.pk).update(
        updated_at=timezone.now() - timedelta(minutes=minutes_idle)
    )
    attempt.refresh_from_db()
    return attempt


class TestStuckReconciler:
    def test_stale_unlocked_attempt_becomes_failed(self):
        design = _design()
        attempt = _stuck_attempt(design)
        result = maintenance.reconcile_stuck_generations()
        assert result["reconciled"] == 1
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED
        assert attempt.error_code == errors.GENERATION_STUCK
        assert attempt.completed_at is not None
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATION_FAILED

    def test_recent_attempt_is_not_reconciled(self):
        design = _design()
        _stuck_attempt(design, minutes_idle=1)  # within the stuck threshold
        result = maintenance.reconcile_stuck_generations()
        assert result["reconciled"] == 0

    def test_attempt_with_held_lock_is_skipped(self):
        design = _design()
        attempt = _stuck_attempt(design)
        key_high, key_low = _attempt_lock_keys(attempt.id)
        holding = threading.Event()
        release = threading.Event()

        def holder():
            from django.db import connection as thread_connection

            try:
                with thread_connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(%s, %s)", [key_high, key_low])
                    holding.set()
                    release.wait(timeout=10)
                    cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [key_high, key_low])
            finally:
                thread_connection.close()

        thread = threading.Thread(target=holder)
        thread.start()
        try:
            assert holding.wait(timeout=10)
            result = maintenance.reconcile_stuck_generations()
            assert result["skipped"] == 1
            attempt.refresh_from_db()
            assert attempt.status == _Status.RUNNING_TEXT  # untouched
        finally:
            release.set()
            thread.join(timeout=10)

    def test_unresolved_markers_and_cost_reservation_are_preserved(self):
        design = _design()
        attempt = _stuck_attempt(
            design,
            status=_Status.RUNNING_IMAGE,
            image_submission_in_flight=True,
            image_prediction_id="pred-123",
        )
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            cost_reserved_micro_usd=5000, cost_estimated_micro_usd=5000
        )
        # A count slot was reserved for this attempt (it entered processing).
        cost_control.reserve_count(
            cost_control.count_reservation_id(design.id, attempt.idempotency_key)
        )
        before = cost_control.get_ledger().count_for_today()

        maintenance.reconcile_stuck_generations()
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED
        assert attempt.image_submission_in_flight is True  # marker preserved
        assert attempt.image_prediction_id == "pred-123"  # prediction preserved
        assert attempt.cost_reserved_micro_usd == 5000  # reservation retained
        # The count is NOT released — this attempt may have entered provider work.
        assert cost_control.get_ledger().count_for_today() == before

    def test_provably_pre_spend_attempt_releases_its_count(self):
        design = _design()
        # queued, no markers, no prediction, no staged output -> provably no call.
        attempt = _stuck_attempt(design, status=_Status.QUEUED)
        cost_control.reserve_count(
            cost_control.count_reservation_id(design.id, attempt.idempotency_key)
        )
        assert cost_control.get_ledger().count_for_today() == 1

        maintenance.reconcile_stuck_generations()
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED
        # The daily count slot is safely returned (no provider work could occur).
        assert cost_control.get_ledger().count_for_today() == 0

    def test_demo_attempt_count_is_never_touched(self):
        design = _design()
        attempt = _stuck_attempt(design, status=_Status.QUEUED, is_demo=True)
        maintenance.reconcile_stuck_generations()
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED
        # Demo attempts never had a live count/cost reservation to release.
        assert cost_control.get_ledger().count_for_today() == 0

    def test_duplicate_delivery_is_idempotent(self):
        design = _design()
        _stuck_attempt(design)
        first = maintenance.reconcile_stuck_generations()
        second = maintenance.reconcile_stuck_generations()
        assert first["reconciled"] == 1
        assert second["reconciled"] == 0  # already terminal
