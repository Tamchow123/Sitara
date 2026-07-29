"""Retention purge + stuck-generation reconciliation tests (Phase 16, Part C).

Provider-free. Uses the generation package's autouse in-memory ``design_images``
storage plus the opt-in ``inmemory_storage`` fixture for the default (staging)
store, so both permanent and staging object deletions are exercised without
touching MinIO.
"""

from __future__ import annotations

import datetime
import threading
import uuid
from datetime import timedelta

import pytest
from django.conf import settings as settings_module
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

    def test_purge_deletes_the_users_own_uploaded_inspiration_objects(self, inmemory_storage):
        # REL-003: the upload rows are CASCADE'd by the Design delete, so if the
        # purge did not enumerate their keys first, the private objects would be
        # orphaned in the bucket with nothing left naming them.
        from sitara.designs.models import DesignInspirationUpload

        design, _version, _attempt, _staged = _old_generated_design()
        upload_key = f"design-uploads/{design.pk}/{uuid.uuid4().hex}/image.webp"
        default_storage.save(upload_key, ContentFile(b"sanitised-webp"))
        DesignInspirationUpload.objects.create(
            design=design,
            position=1,
            storage_key=upload_key,
            image_width=40,
            image_height=60,
            image_size_bytes=14,
            image_sha256="d" * 64,
            rights_acknowledged_at=timezone.now(),
        )

        result = maintenance.purge_expired_designs()

        assert result["purged"] == 1
        assert not DesignInspirationUpload.objects.exists()
        assert not default_storage.exists(upload_key)

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


class TestOrphanedUploadSweep:
    """The upload path writes its object BEFORE the row that names it, so a
    crash in that window leaves an object no row will ever reference. The
    retention purge enumerates keys FROM rows and is structurally blind to it;
    this sweeper is the only thing that removes one."""

    _PREFIX = "design-uploads"

    # Objects are aged by moving the sweep's clock FORWARD rather than by
    # backdating storage internals: get_modified_time is set by the backend and
    # is not writable through any public API. LATER is far past the default
    # one-hour grace window, NOW leaves a freshly written object inside it.
    LATER = timedelta(seconds=7200)

    def _object(self, *, design_id=None, body=b"sanitised-webp"):
        """Write one upload-shaped object and return its key."""
        key = f"{self._PREFIX}/{design_id or uuid.uuid4()}/{uuid.uuid4().hex}/image.webp"
        default_storage.save(key, ContentFile(body))
        return key

    def _sweep(self, *, after=None, run=0):
        """Run the sweep as if ``after`` had elapsed since the objects landed.

        ``run`` advances the clock by whole sweep intervals, which is what makes
        the rotation offset move — successive SCHEDULED runs are an interval
        apart, so a test doing three sweeps in the same second would otherwise
        replay the identical window every time."""
        elapsed = self.LATER if after is None else after
        elapsed += timedelta(seconds=run * settings_module.USER_UPLOAD_SWEEP_INTERVAL_SECONDS)
        return maintenance.sweep_orphaned_upload_objects(now=timezone.now() + elapsed)

    def _row(self, design, key, *, position=1, sha="e" * 64):
        from sitara.designs.models import DesignInspirationUpload

        return DesignInspirationUpload.objects.create(
            design=design,
            position=position,
            storage_key=key,
            image_width=40,
            image_height=60,
            image_size_bytes=14,
            image_sha256=sha,
            rights_acknowledged_at=timezone.now(),
        )

    def test_an_orphaned_object_is_deleted(self, inmemory_storage):
        key = self._object()
        result = self._sweep()
        assert result["deleted"] == 1
        assert not default_storage.exists(key)

    def test_a_referenced_object_is_never_deleted(self, inmemory_storage):
        design = _design()
        key = self._object(design_id=design.pk)
        self._row(design, key)
        result = self._sweep()
        assert result["deleted"] == 0
        assert default_storage.exists(key)

    def test_an_object_inside_the_grace_window_is_never_deleted(self, inmemory_storage):
        # A young unreferenced object may simply be a request still in flight —
        # deleting it would destroy an image the user just uploaded.
        key = self._object()
        result = self._sweep(after=timedelta(seconds=60))
        assert result["deleted"] == 0
        assert result["skipped_recent"] == 1
        assert default_storage.exists(key)

    def test_the_grace_window_is_the_configured_one(self, inmemory_storage, settings):
        settings.USER_UPLOAD_ORPHAN_GRACE_SECONDS = 30
        key = self._object()
        assert self._sweep(after=timedelta(seconds=60))["deleted"] == 1
        assert not default_storage.exists(key)

    def test_only_the_upload_prefix_is_ever_examined(self, inmemory_storage):
        # Catalogue assets, permanent design images, staging objects and the
        # demo pack all live in the same bucket. None may be reachable.
        others = [
            "catalogue/inspiration/abc/1/image.webp",
            "design-images/abc/def/original.webp",
            "generation-staging/abc/raw.webp",
            "demo-assets/pack/manifest.json",
        ]
        for key in others:
            default_storage.save(key, ContentFile(b"not-an-upload"))
        orphan = self._object()

        result = self._sweep()

        assert result["deleted"] == 1
        assert not default_storage.exists(orphan)
        for key in others:
            assert default_storage.exists(key), key
        # Not merely undeleted — never even looked at.
        assert result["examined"] == 1

    def test_the_sweep_is_bounded_and_says_so_when_truncated(self, inmemory_storage, settings):
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 2
        for _ in range(5):
            self._object()

        result = self._sweep()

        assert result["examined"] == 2
        assert result["deleted"] == 2
        # A truncated sweep must not read like a complete one.
        assert result["truncated"] is True

    def test_an_untruncated_sweep_reports_so(self, inmemory_storage, settings):
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 50
        self._object()
        result = self._sweep()
        assert result["truncated"] is False

    def test_repeated_runs_converge_and_are_idempotent(self, inmemory_storage, settings):
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 2
        for _ in range(3):
            self._object()

        # Successive SCHEDULED runs, an interval apart — which is what advances
        # the rotation. An emptied design directory still exists in the listing,
        # so without rotation runs 2+ would re-visit the same two directories
        # and the third orphan would never be reached.
        deleted = sum(self._sweep(run=index)["deleted"] for index in range(4))

        assert deleted == 3
        settled = self._sweep(run=9)
        assert settled["deleted"] == 0  # nothing left, and no error
        assert settled["examined"] == 0

    def test_a_row_committing_during_the_sweep_saves_its_object(
        self, inmemory_storage, monkeypatch
    ):
        # The late re-check exists for exactly this: the up-front referenced-key
        # set was READ before the row existed, so only the per-key re-check can
        # save the object. The preload is forced to completion before the row
        # commits — otherwise Django's laziness would evaluate it afterwards and
        # the race would never be reproduced.
        from sitara.designs.models import DesignInspirationUpload

        design = _design()
        key = self._object(design_id=design.pk)
        real_filter = DesignInspirationUpload.objects.filter
        state = {"preloaded": False}

        class _AlreadyRead:
            """The referenced set frozen at the instant BEFORE the row landed."""

            def __init__(self, rows):
                self._rows = rows

            def values_list(self, *_args, **_kwargs):
                return self._rows

        def _filter(*args, **kwargs):
            if "storage_key__in" in kwargs and not state["preloaded"]:
                state["preloaded"] = True
                # Read the set now, while no row exists...
                frozen = list(real_filter(*args, **kwargs).values_list("storage_key", flat=True))
                # ...then the row commits, after the read and before the
                # re-check — the exact window the re-check guards.
                self._row(design, key)
                return _AlreadyRead(frozen)
            return real_filter(*args, **kwargs)

        monkeypatch.setattr(DesignInspirationUpload.objects, "filter", _filter)

        result = self._sweep()

        assert state["preloaded"] is True, "the referenced-key lookup must have run"
        assert result["deleted"] == 0
        assert default_storage.exists(key)

    def test_a_delete_failure_is_counted_and_retried_next_run(self, inmemory_storage, monkeypatch):
        key = self._object()
        calls = {"n": 0}
        real_delete = default_storage.delete

        def _flaky(target):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient storage failure")
            return real_delete(target)

        monkeypatch.setattr(default_storage, "delete", _flaky)

        first = self._sweep()
        assert first["failed"] == 1
        assert first["deleted"] == 0
        assert default_storage.exists(key)  # retained for a retry

        second = self._sweep()
        assert second["deleted"] == 1
        assert not default_storage.exists(key)

    def test_a_listing_outage_is_reported_not_raised(self, inmemory_storage, monkeypatch):
        def _boom(_prefix):
            raise OSError("storage listing unavailable")

        monkeypatch.setattr(default_storage, "listdir", _boom)

        result = self._sweep()

        assert result["listing_failed"] is True
        assert result["deleted"] == 0

    def test_an_empty_bucket_is_a_clean_no_op(self, inmemory_storage):
        result = self._sweep()
        assert result == {
            "examined": 0,
            "deleted": 0,
            "failed": 0,
            "skipped_recent": 0,
            "visited_directories": 0,
            "truncated": False,
            "listing_failed": False,
        }

    def test_no_storage_key_reaches_a_log_record(self, inmemory_storage, caplog):
        import logging

        key = self._object()
        with caplog.at_level(logging.DEBUG, logger="sitara.generation.maintenance"):
            self._sweep()
        rendered = " ".join(
            record.getMessage()
            for record in caplog.records
            if record.name == "sitara.generation.maintenance"
        )
        assert rendered, "the sweep summary line is expected"
        assert key not in rendered
        assert "design-uploads" not in rendered

    def test_an_orphan_under_a_purged_designs_prefix_is_still_swept(self, inmemory_storage):
        # The object's prefix names a design that no longer exists (its rows
        # were purged). Nothing points at it, so it must still be removed.
        design = _design()
        live_key = self._object(design_id=design.pk)
        self._row(design, live_key)
        dead_key = self._object(design_id=uuid.uuid4())

        result = self._sweep()

        assert result["deleted"] == 1
        assert default_storage.exists(live_key)
        assert not default_storage.exists(dead_key)

    def test_live_uploads_never_spend_the_budget(self, inmemory_storage, settings):
        # REL-001: the defect this closes. A handful of perfectly healthy
        # uploads sorting earlier in the listing must not exhaust the run and
        # leave the orphan behind them unexamined.
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 2
        design = _design()
        for position in range(1, 4):
            live = self._object(design_id=design.pk)
            self._row(design, live, position=position, sha=str(position) * 64)
        orphan = self._object()

        result = self._sweep()

        assert result["deleted"] == 1
        assert not default_storage.exists(orphan)
        # Only the orphan cost budget; the three live objects were free.
        assert result["examined"] == 1

    def test_every_directory_is_reached_across_scheduled_runs(self, inmemory_storage, settings):
        # The starvation regression. With a budget far smaller than the number
        # of directories, a run that always restarted at the same place would
        # leave the tail permanently unswept. The rotating offset advances by a
        # whole window per scheduled run, so repeated runs cover everything.
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 3
        orphans = [self._object() for _ in range(10)]
        assert all(default_storage.exists(key) for key in orphans)

        for run in range(8):
            self._sweep(run=run)

        assert [key for key in orphans if default_storage.exists(key)] == []

    def test_one_bounded_run_does_not_reach_everything(self, inmemory_storage, settings):
        # The other half of the same property: the bound is real. If a single
        # run swept all ten, the test above would prove nothing about rotation.
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 3
        orphans = [self._object() for _ in range(10)]

        result = self._sweep()

        assert result["truncated"] is True
        assert result["deleted"] == 3
        assert len([key for key in orphans if default_storage.exists(key)]) == 7

    def test_a_key_escaping_the_prefix_is_refused(self, inmemory_storage, monkeypatch, caplog):
        # `startswith` alone accepts "design-uploads/../catalogue/x.webp", which
        # resolves OUTSIDE the prefix. Force such a key through enumeration and
        # prove the containment check refuses it — this branch must be reachable
        # and exercised, not merely present.
        import logging

        escaped = "design-uploads/../catalogue/inspiration/asset.webp"
        default_storage.save("catalogue/inspiration/asset.webp", ContentFile(b"rights-cleared"))
        monkeypatch.setattr(maintenance, "_upload_directories", lambda _storage: ["anything"])
        monkeypatch.setattr(
            maintenance, "_iter_upload_keys", lambda _storage, _prefix: iter([escaped])
        )
        long_ago = timezone.now() - timedelta(days=365)
        monkeypatch.setattr(default_storage, "get_modified_time", lambda _k: long_ago)

        with caplog.at_level(logging.ERROR, logger="sitara.generation.maintenance"):
            result = self._sweep()

        assert result["deleted"] == 0
        assert default_storage.exists("catalogue/inspiration/asset.webp")
        assert any("outside the upload prefix" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        "escape",
        [
            "design-uploads/../catalogue/x.webp",
            "design-uploads/a/../../catalogue/x.webp",
            "catalogue/x.webp",
            "design-uploadsX/a/image.webp",
            "",
        ],
    )
    def test_containment_rejects_every_escape_shape(self, escape):
        assert maintenance._is_contained_upload_key(escape) is False

    def test_containment_accepts_a_real_upload_key(self):
        assert maintenance._is_contained_upload_key("design-uploads/a/b/image.webp") is True

    def test_a_worker_interruption_during_listing_propagates(self, inmemory_storage, monkeypatch):
        # A Celery soft time limit is an operational event, not a storage
        # outage. Reporting it as listing_failed would let the task run past its
        # deadline until the hard limit kills it mid-loop.
        from celery.exceptions import SoftTimeLimitExceeded

        def _interrupted(_prefix):
            raise SoftTimeLimitExceeded()

        monkeypatch.setattr(default_storage, "listdir", _interrupted)

        with pytest.raises(SoftTimeLimitExceeded):
            self._sweep()

    def test_a_worker_interruption_during_the_age_check_propagates(
        self, inmemory_storage, monkeypatch
    ):
        from celery.exceptions import SoftTimeLimitExceeded

        self._object()

        def _interrupted(_key):
            raise SoftTimeLimitExceeded()

        monkeypatch.setattr(default_storage, "get_modified_time", _interrupted)

        with pytest.raises(SoftTimeLimitExceeded):
            self._sweep()

    def test_a_flat_backend_returning_empty_lists_is_a_clean_no_op(
        self, inmemory_storage, monkeypatch
    ):
        # Production S3 returns ([], []) for an absent prefix rather than
        # raising FileNotFoundError the way the local/in-memory backends do.
        # Both must reach the same clean no-op.
        monkeypatch.setattr(default_storage, "listdir", lambda _prefix: ([], []))
        result = self._sweep()
        assert result["listing_failed"] is False
        assert result["deleted"] == 0
        assert result["visited_directories"] == 0

    def test_a_worker_interruption_during_a_delete_propagates(self, inmemory_storage, monkeypatch):
        # The third interruption site. Counting it as a transient delete failure
        # would let the run carry on past its deadline until the hard limit
        # killed the worker mid-loop.
        from celery.exceptions import SoftTimeLimitExceeded

        self._object()

        def _interrupted(_key):
            raise SoftTimeLimitExceeded()

        monkeypatch.setattr(default_storage, "delete", _interrupted)

        with pytest.raises(SoftTimeLimitExceeded):
            self._sweep()

    def test_the_referenced_key_lookup_is_scoped_to_this_run(self, inmemory_storage, settings):
        # A full-table load would grow with the platform on every tick, however
        # little the run then does. The query must be scoped to what was scanned.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 1
        design = _design()
        live = self._object(design_id=design.pk)
        self._row(design, live)
        for _ in range(4):
            self._object()

        with CaptureQueriesContext(connection) as captured:
            self._sweep()

        lookups = [
            query["sql"]
            for query in captured.captured_queries
            if "storage_key" in query["sql"] and " IN " in query["sql"].upper()
        ]
        assert lookups, "the referenced-key lookup must be a bounded IN query"
        # An unbounded load would have no IN clause at all.
        unscoped = [
            query["sql"]
            for query in captured.captured_queries
            if "storage_key" in query["sql"] and "WHERE" not in query["sql"].upper()
        ]
        assert unscoped == [], "the referenced-key lookup must never be a full-table load"

    def test_live_uploads_do_not_block_even_when_they_fill_the_scan(
        self, inmemory_storage, settings
    ):
        # The scan cap is budget * (MAX_INSPIRATION_IMAGES + 1) precisely so a
        # directory full of live uploads cannot crowd the orphan out of the
        # scan. Fill one design to its cap, then hide an orphan behind it.
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 2
        design = _design()
        for position in range(1, settings_module.MAX_INSPIRATION_IMAGES + 1):
            live = self._object(design_id=design.pk)
            self._row(design, live, position=position, sha=str(position) * 64)
        orphan = self._object(design_id=design.pk)

        result = self._sweep()

        assert result["deleted"] == 1
        assert not default_storage.exists(orphan)

    def test_a_naive_modified_time_is_made_aware_before_comparison(
        self, inmemory_storage, monkeypatch
    ):
        # Some storage backends return naive datetimes. Comparing one against an
        # aware cutoff raises TypeError; treating it as "now" would skip every
        # orphan forever. Prove the conversion happens and the age still decides.
        import datetime

        key = self._object()
        naive_old = datetime.datetime.now() - timedelta(days=2)
        monkeypatch.setattr(default_storage, "get_modified_time", lambda _k: naive_old)

        result = self._sweep()

        assert result["deleted"] == 1
        assert not default_storage.exists(key)

    def test_a_naive_recent_modified_time_is_still_protected_by_the_grace_window(
        self, inmemory_storage, monkeypatch
    ):
        import datetime

        key = self._object()
        naive_now = datetime.datetime.now()
        monkeypatch.setattr(default_storage, "get_modified_time", lambda _k: naive_now)

        result = self._sweep(after=timedelta(seconds=60))

        assert result["deleted"] == 0
        assert result["skipped_recent"] == 1
        assert default_storage.exists(key)

    def test_one_bloated_directory_does_not_strand_its_neighbours(self, inmemory_storage, settings):
        # REL-007. Every crashed or cleaned-up upload attempt leaves a revision
        # directory behind, so ONE design can accumulate arbitrarily many
        # orphans. Without a per-directory cap that single directory fills the
        # whole run's scan and every other directory in the window goes
        # unvisited — and, because the rotation advances by a fixed stride
        # whatever a run managed, unvisited again next time round.
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 2
        bloated = uuid.uuid4()
        cap = settings_module.MAX_INSPIRATION_IMAGES + maintenance._DIRECTORY_SCAN_SLACK
        for _ in range(cap * 3):
            self._object(design_id=bloated)
        neighbour = self._object(design_id="zzzz-neighbour")

        result = self._sweep()

        # The bloated directory did not consume the whole run.
        assert result["visited_directories"] >= 2
        assert not default_storage.exists(
            neighbour
        ), "the neighbouring directory's orphan must be reached in the same run"
        # And the bloated one still made progress rather than being skipped.
        assert result["deleted"] > 1

    def test_several_bloated_directories_share_the_budget_fairly(self, inmemory_storage, settings):
        # REL-008: with more than one backlogged directory in the same window,
        # round-robin gives each an equal share rather than letting the first
        # take everything. Each drains more slowly than it would alone — that is
        # the deliberate trade-off — but none is excluded.
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 3
        cap = settings_module.MAX_INSPIRATION_IMAGES + maintenance._DIRECTORY_SCAN_SLACK
        backlogs = {
            f"bloated-{index}": [self._object(design_id=f"bloated-{index}") for _ in range(cap * 2)]
            for index in range(3)
        }

        result = self._sweep()

        assert result["examined"] == 3
        # One deletion from each, not three from the first.
        touched = [
            name
            for name, keys in backlogs.items()
            if any(not default_storage.exists(key) for key in keys)
        ]
        assert sorted(touched) == ["bloated-0", "bloated-1", "bloated-2"]

    def test_a_bloated_directory_drains_over_successive_runs(self, inmemory_storage, settings):
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 5
        bloated = uuid.uuid4()
        cap = settings_module.MAX_INSPIRATION_IMAGES + maintenance._DIRECTORY_SCAN_SLACK
        keys = [self._object(design_id=bloated) for _ in range(cap * 2)]

        for run in range(8):
            self._sweep(run=run)

        assert [key for key in keys if default_storage.exists(key)] == []

    def test_the_per_directory_cap_leaves_a_normal_directory_fully_covered(
        self, inmemory_storage, settings
    ):
        # The cap must not bite for an ordinary design: a full set of live
        # uploads plus an orphan still fits inside one visit.
        settings.USER_UPLOAD_SWEEP_BATCH_SIZE = 5
        design = _design()
        for position in range(1, settings_module.MAX_INSPIRATION_IMAGES + 1):
            live = self._object(design_id=design.pk)
            self._row(design, live, position=position, sha=str(position) * 64)
        orphan = self._object(design_id=design.pk)

        result = self._sweep()

        assert result["truncated"] is False
        assert result["deleted"] == 1
        assert not default_storage.exists(orphan)


class TestSweepRotationOffset:
    """The rotation is what makes a bounded sweep eventually reach everything.
    Tested directly, because an off-by-one here is invisible end to end until a
    specific count/window pair happens to strand a directory."""

    def _offset(self, tick, count, window):
        moment = datetime.datetime.fromtimestamp(
            tick * settings_module.USER_UPLOAD_SWEEP_INTERVAL_SECONDS,
            tz=datetime.UTC,
        )
        return maintenance._rotation_offset(moment, count, window)

    def test_an_empty_prefix_has_no_offset(self):
        assert self._offset(7, 0, 3) == 0

    def test_the_offset_advances_by_a_whole_window_per_tick(self):
        assert [self._offset(tick, 100, 10) for tick in range(4)] == [0, 10, 20, 30]

    def test_the_offset_wraps_within_the_directory_count(self):
        assert all(0 <= self._offset(tick, 7, 3) < 7 for tick in range(50))

    def test_a_window_of_zero_still_advances(self):
        # max(1, window) — a zero stride would freeze the rotation and
        # reintroduce exactly the starvation this exists to prevent.
        assert self._offset(1, 5, 0) != self._offset(2, 5, 0)

    @pytest.mark.parametrize(
        ("count", "window"),
        [
            (10, 3),  # coprime
            (12, 6),  # window divides count
            (12, 8),  # shared factor, neither divides
            (5, 7),  # window larger than count
            (1, 1),  # degenerate
            (97, 5),  # prime count
            (100, 1),  # smallest useful window
        ],
    )
    def test_every_directory_is_covered_within_the_documented_bound(self, count, window):
        # The liveness guarantee, proven across configurations rather than for
        # the one the end-to-end test happens to use. A directory the rotation
        # never lands on is an orphan that never gets swept.
        import math

        seen: set[int] = set()
        bound = math.ceil(count / max(1, window))
        for tick in range(bound):
            start = self._offset(tick, count, window)
            seen.update((start + step) % count for step in range(max(1, window)))
        assert seen == set(range(count)), (
            f"count={count} window={window} left {sorted(set(range(count)) - seen)} unvisited "
            f"after the documented {bound} runs"
        )
