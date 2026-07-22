"""Retention purge and stuck-generation reconciliation (Phase 16, Part C).

Two bounded, idempotent maintenance operations run periodically by Celery Beat:

* ``purge_expired_designs`` — delete designs older than ``DESIGN_RETENTION_DAYS``
  in bounded batches: their permanent original/thumbnail objects (via the
  ``design_images`` alias) AND their raw generation-staging objects (via the
  staging storage) are removed FIRST, then the database rows via the normal
  Design cascade. Object storage and PostgreSQL cannot be one atomic
  transaction, so the order fails safe: if object deletion fails the row is
  retained for a later retry, and a retry tolerates already-missing objects.
  Genuinely in-progress designs are skipped. This is also the single cleanup
  boundary for the crash-recovery staging objects Phase 10/11 deliberately
  retained (ADR 0017): a design's staging object lives at most the retention
  window. Catalogue assets, rights records, the shared demo source pack and
  every unrelated object prefix are never touched — only per-design
  ``design-images/`` and ``generation-staging/`` keys are deleted.

* ``reconcile_stuck_generations`` — mark attempts idle in a non-terminal state
  past ``GENERATION_STUCK_AFTER_SECONDS`` as failed, skipping any a live worker
  still holds the attempt advisory lock on, preserving all spend evidence and
  never enqueuing replacement paid work (see ``pipeline.reconcile_if_stuck``).

Logs carry only safe design/attempt UUIDs, counts and exception types — never an
object key, storage URL or exception body.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import DatabaseError, transaction
from django.utils import timezone

from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.media.ingest import design_image_storage

from . import pipeline

logger = logging.getLogger(__name__)


class _ObjectDeletionFailed(Exception):
    """A storage object could not be deleted (transient); the row is retained."""


def _delete_object(storage, key: str) -> None:
    """Delete one object, tolerating an already-missing one (idempotent under
    retry). A genuine transport failure raises so the caller retains the row."""
    if not key:
        return
    try:
        storage.delete(key)  # S3/MinIO delete is idempotent; missing is a no-op
    except Exception as exc:  # noqa: BLE001 - storage backends raise varied errors
        raise _ObjectDeletionFailed from exc


def purge_expired_designs(now=None) -> dict:
    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.DESIGN_RETENTION_DAYS)
    batch_size = settings.DESIGN_PURGE_BATCH_SIZE
    permanent = design_image_storage()
    staging = default_storage

    candidate_ids = list(
        Design.objects.filter(created_at__lt=cutoff)
        .order_by("created_at")
        .values_list("id", flat=True)[:batch_size]
    )
    purged = 0
    retained = 0
    skipped_in_progress = 0
    db_delete_failed = 0
    for design_id in candidate_ids:
        try:
            outcome = _purge_one_design(design_id, permanent, staging)
        except _ObjectDeletionFailed:
            # Transient object-store failure: the atomic block rolled back, so
            # the row is retained; a later run retries and tolerates missing
            # objects (any already-deleted ones stay deleted, which is safe).
            retained += 1
            logger.warning("design purge deferred (object deletion failed) design=%s", design_id)
            continue
        except DatabaseError:
            # The DB cascade delete failed AFTER objects were removed (deadlock /
            # transient error). The row is retained (rolled back) but its objects
            # may be gone — a later run tolerates the missing objects and retries
            # the delete. Log distinctly and continue the batch rather than crash.
            db_delete_failed += 1
            logger.warning("design purge db delete failed design=%s", design_id)
            continue
        if outcome == "purged":
            purged += 1
        elif outcome == "skipped":
            skipped_in_progress += 1

    logger.info(
        "design retention purge purged=%s retained=%s skipped_in_progress=%s db_delete_failed=%s",
        purged,
        retained,
        skipped_in_progress,
        db_delete_failed,
    )
    return {
        "purged": purged,
        "retained": retained,
        "skipped_in_progress": skipped_in_progress,
        "db_delete_failed": db_delete_failed,
    }


def _purge_one_design(design_id, permanent, staging) -> str:
    """Purge ONE design under its row lock so it serialises with the enqueue
    services (which lock the same Design row): a concurrent enqueue either
    commits before us (we then see its in-progress attempt and skip) or blocks
    until we finish. Objects are deleted before the DB cascade; a transient
    object failure raises ``_ObjectDeletionFailed`` and a DB cascade failure
    raises ``DatabaseError`` — both roll the transaction back and are handled by
    the caller. Returns "purged", "skipped" (in-progress) or "gone" (already
    removed by a concurrent run)."""
    with transaction.atomic():
        locked = Design.objects.select_for_update().filter(pk=design_id).first()
        if locked is None:
            return "gone"  # a concurrent purge already removed it
        # Never purge a design with a genuinely in-progress attempt — under the
        # row lock this is authoritative against a racing enqueue. The stuck-job
        # reconciler resolves stale work before retention would remove it.
        if GenerationAttempt.objects.filter(
            design=locked, status__in=GenerationAttempt.IN_PROGRESS_STATUSES
        ).exists():
            return "skipped"

        permanent_keys: list[str] = []
        for version in DesignVersion.objects.filter(design=locked):
            if version.image_storage_key:
                permanent_keys.append(version.image_storage_key)
            if version.thumbnail_storage_key:
                permanent_keys.append(version.thumbnail_storage_key)
        staging_keys: set[str] = set()
        for attempt in GenerationAttempt.objects.filter(design=locked):
            if attempt.staged_image_storage_key:
                staging_keys.add(attempt.staged_image_storage_key)
            else:
                # Crash-window recovery: a worker may have uploaded
                # generation-staging/<attempt>/raw.<ext> before committing the
                # staged_image_storage_key column, so a blank column does NOT mean
                # no object exists. Delete every bounded deterministic candidate
                # for that attempt by its known layout (delete tolerates missing),
                # so a crash-window object is never orphaned past retention.
                for extension in pipeline._STAGED_EXTENSIONS:
                    staging_keys.add(pipeline._staged_key(attempt.id, extension))

        # Objects FIRST (a later retry tolerates already-missing objects).
        for key in permanent_keys:
            _delete_object(permanent, key)
        for key in staging_keys:
            _delete_object(staging, key)

        # Then the DB rows via the normal Design cascade (versions, attempts,
        # inspiration through-rows). Catalogue assets/rights are PROTECT'd and
        # never deleted here.
        locked.delete()
        return "purged"


def reconcile_stuck_generations(now=None) -> dict:
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.GENERATION_STUCK_AFTER_SECONDS)
    batch_size = settings.GENERATION_STUCK_BATCH_SIZE

    stale_ids = list(
        GenerationAttempt.objects.filter(
            status__in=GenerationAttempt.IN_PROGRESS_STATUSES, updated_at__lt=cutoff
        )
        .order_by("updated_at")
        .values_list("id", flat=True)[:batch_size]
    )
    reconciled = 0
    skipped = 0
    progressed = 0
    for attempt_id in stale_ids:
        outcome = pipeline.reconcile_if_stuck(attempt_id, cutoff)
        if outcome == "reconciled":
            reconciled += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            progressed += 1

    logger.info(
        "stuck-generation reconcile reconciled=%s skipped_locked=%s progressed=%s",
        reconciled,
        skipped,
        progressed,
    )
    return {"reconciled": reconciled, "skipped": skipped, "progressed": progressed}
