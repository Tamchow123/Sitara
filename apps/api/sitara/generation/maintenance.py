"""Retention purge, stuck-generation reconciliation and upload-orphan sweeping.

Three bounded, idempotent maintenance operations run periodically by Celery
Beat (the first two from Phase 16 Part C, the third added in Phase 16B):

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
  window, as does a user's own uploaded inspiration image (``design-uploads/``,
  Phase 16B) — enumerated explicitly, because the Design cascade would otherwise
  drop the only rows naming those objects. Catalogue assets, rights records, the
  shared demo source pack and every unrelated object prefix are never touched —
  only per-design ``design-images/``, ``generation-staging/`` and
  ``design-uploads/`` keys are deleted.

* ``reconcile_stuck_generations`` — mark attempts idle in a non-terminal state
  past ``GENERATION_STUCK_AFTER_SECONDS`` as failed, skipping any a live worker
  still holds the attempt advisory lock on, preserving all spend evidence and
  never enqueuing replacement paid work (see ``pipeline.reconcile_if_stuck``).

* ``sweep_orphaned_upload_objects`` — delete user-upload objects that NO row
  names. The purge above enumerates keys from rows, so it can never see an
  object orphaned by a crash between the storage write and the row commit; this
  sweeper is the only thing that removes one. It only ever looks under
  ``design-uploads/``, only at objects older than
  ``USER_UPLOAD_ORPHAN_GRACE_SECONDS``, and bounds each run by
  ``USER_UPLOAD_SWEEP_BATCH_SIZE``. Because it is the only path, it must also
  eventually REACH every object: keys that rows already name are skipped for
  free, each directory contributes a bounded slice scanned round-robin so no one
  design can spend the whole run, and the per-design directories are visited from
  a clock-derived rotating offset — so a bounded run can never leave the same
  tail permanently unvisited.

Logs carry only safe design/attempt UUIDs, counts and exception types — never an
object key, storage URL or exception body.
"""

from __future__ import annotations

import logging
import posixpath
from datetime import timedelta

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import DatabaseError, transaction
from django.utils import timezone

from sitara.designs.models import (
    Design,
    DesignInspirationUpload,
    DesignVersion,
    GenerationAttempt,
)
from sitara.designs.upload_service import UPLOAD_KEY_PREFIX
from sitara.media.ingest import design_image_storage

from . import pipeline

logger = logging.getLogger(__name__)


# How many keys beyond a design's live-upload cap one sweep run takes from a
# single directory. Small on purpose: it only has to be enough that a normal
# directory is fully covered in one visit, while a directory that has piled up
# crash-window orphans drains over several runs instead of monopolising one.
_DIRECTORY_SCAN_SLACK = 8


class _ObjectDeletionFailed(Exception):
    """A storage object could not be deleted (transient); the row is retained."""


def _delete_object(storage, key: str) -> None:
    """Delete one object, tolerating an already-missing one (idempotent under
    retry). A genuine transport failure raises so the caller retains the row."""
    if not key:
        return
    try:
        storage.delete(key)  # S3/MinIO delete is idempotent; missing is a no-op
    except SoftTimeLimitExceeded:
        # A worker interruption is not a storage failure. Classifying it as one
        # would let the caller count it and carry on past its deadline until the
        # hard limit killed the worker mid-loop.
        raise
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
        # The user's OWN uploaded inspiration images (Phase 16B). Their rows are
        # CASCADE'd by locked.delete() below, so without enumerating them here
        # their private objects would be orphaned in the bucket the first time a
        # design with uploads aged out — with no row left pointing at them. They
        # live in the default (staging) storage, like every other user-supplied
        # object; only the permanent generated images use the design_images
        # alias.
        upload_keys = [
            key for key in locked.inspiration_uploads.values_list("storage_key", flat=True) if key
        ]

        # Objects FIRST (a later retry tolerates already-missing objects).
        for key in permanent_keys:
            _delete_object(permanent, key)
        for key in staging_keys:
            _delete_object(staging, key)
        for key in upload_keys:
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


def sweep_orphaned_upload_objects(now=None) -> dict:
    """Delete user-upload objects that no row will ever name (Phase 16B).

    ``create_inspiration_upload`` writes the sanitised object BEFORE it can
    create the row, because the key is part of the row. That order is correct —
    a row pointing at a missing object would be worse — but it leaves one
    window: if the process dies between the write and the row commit, or the
    rollback path's own delete fails, the object survives with nothing naming
    it. The retention purge (``purge_expired_designs``) cannot help, because it
    enumerates keys FROM rows; an object no row names is invisible to it. This
    sweeper is the only thing that ever removes such an object — so it must
    actually REACH every object eventually, not merely be safe.

    Reaching everything, with bounded work per run:

    * The set of keys rows DO name is loaded once, up front, and a key in it is
      skipped for free. Live uploads therefore never consume the run's budget —
      without this, a few hundred perfectly healthy uploads sorting earlier in
      the listing would exhaust every run and an orphan behind them would never
      be examined at all.
    * The per-design directories are visited starting from a ROTATING offset
      derived from ``now``, wrapping around. Successive runs therefore start at
      different points, so no directory can sit permanently beyond the reach of
      a truncated run. The rotation is stateless: nothing to persist, corrupt or
      reset.
    * A run visits at most ``USER_UPLOAD_SWEEP_BATCH_SIZE`` design directories
      and examines at most that many candidate (unreferenced) objects.

    Deliberately conservative about what it deletes:

    * Only keys under ``design-uploads/``. Before each delete the key is
      normalised and re-checked for containment — a plain ``startswith`` would
      accept ``design-uploads/../catalogue/x.webp``, which resolves outside.
    * An object is a candidate only once it is older than
      ``USER_UPLOAD_ORPHAN_GRACE_SECONDS`` — comfortably longer than any upload
      request — so an object whose row is still mid-commit is never touched.
    * Row existence is re-checked immediately before each delete, so a row that
      committed during this run saves its own object.

    Cost note, stated honestly: one ``listdir`` of ``design-uploads/`` per run is
    unavoidable through the Storage API and is NOT bounded by the batch size —
    on S3 that call pages through every top-level design prefix before
    returning. What the batch size bounds is the per-directory listings and the
    candidate work that follow, which is where cost multiplies.

    Logs and the returned counters carry counts only — never a storage key.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.USER_UPLOAD_ORPHAN_GRACE_SECONDS)
    budget = settings.USER_UPLOAD_SWEEP_BATCH_SIZE
    storage = default_storage

    result = {
        "examined": 0,
        "deleted": 0,
        "failed": 0,
        "skipped_recent": 0,
        "visited_directories": 0,
        "truncated": False,
        "listing_failed": False,
    }

    try:
        directories = _upload_directories(storage)
    except SoftTimeLimitExceeded:
        raise  # a worker interruption is never a storage outage
    except FileNotFoundError:
        # No upload prefix exists yet (a fresh bucket, or everything already
        # swept). That is "nothing to do", not an outage. The S3 backend returns
        # empty lists instead of raising, which reaches the same result below.
        return result
    except Exception as exc:  # noqa: BLE001 - storage backends raise varied errors
        logger.warning("upload orphan sweep listing failed exception_type=%s", type(exc).__name__)
        result["listing_failed"] = True
        return result

    if not directories:
        return result

    # Stateless rotation: a different starting point each run, wrapping around,
    # so a truncated run never leaves the same tail permanently unvisited.
    start = _rotation_offset(now, len(directories), budget)
    ordered = directories[start:] + directories[:start]

    # Collect this run's keys FIRST, so the referenced-key lookup below can be
    # scoped to them. A design holds at most MAX_INSPIRATION_IMAGES referenced
    # uploads, so taking that many per directory PLUS a little slack guarantees
    # the scan still surfaces unreferenced keys even when a directory is full of
    # live ones — which is what stops healthy uploads blocking a run.
    #
    # One design can accumulate arbitrarily many orphaned revision directories —
    # every crashed or cleaned-up upload attempt leaves one. Two things keep such
    # a directory from monopolising a run and starving its neighbours (which the
    # rotation alone would NOT fix, since it advances by a fixed stride whatever
    # a run actually managed, so the same neighbours would be skipped on every
    # pass through that window):
    #
    #   * a PER-DIRECTORY scan cap, so the bloated one contributes a bounded
    #     slice of the scan rather than all of it, and
    #   * round-robin ORDER, so the examine budget below is spread across the
    #     directories visited instead of being spent front-to-back on whichever
    #     happened to be scanned first.
    #
    # The bloated directory still drains steadily; it just takes its turn.
    per_directory_cap = settings.MAX_INSPIRATION_IMAGES + _DIRECTORY_SCAN_SLACK
    scan_cap = budget * per_directory_cap
    per_directory: list[list[str]] = []
    scanned_total = 0
    try:
        for directory in ordered:
            if result["visited_directories"] >= budget or scanned_total >= scan_cap:
                result["truncated"] = True
                break
            result["visited_directories"] += 1
            keys: list[str] = []
            for key in _iter_upload_keys(storage, f"{UPLOAD_KEY_PREFIX}/{directory}"):
                if len(keys) >= per_directory_cap or scanned_total >= scan_cap:
                    # More here than one run should take. Truncate THIS
                    # directory and move on rather than spend the run on it.
                    result["truncated"] = True
                    break
                keys.append(key)
                scanned_total += 1
            if keys:
                per_directory.append(keys)
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - storage backends raise varied errors
        # A listing outage is transient. Report it and let the next run retry;
        # anything already collected is deliberately abandoned rather than acted
        # on from a partial listing.
        logger.warning("upload orphan sweep listing failed exception_type=%s", type(exc).__name__)
        result["listing_failed"] = True
        result["truncated"] = False
        return result

    scanned = _round_robin(per_directory)

    if not scanned:
        _log_sweep(result)
        return result

    # The keys rows name, SCOPED to what this run actually scanned. A full-table
    # load would grow without bound as the platform grows, on every tick, no
    # matter how little the run went on to do.
    referenced = set(
        DesignInspirationUpload.objects.filter(storage_key__in=scanned).values_list(
            "storage_key", flat=True
        )
    )

    candidates: list[str] = []
    for key in scanned:
        if key in referenced:
            continue  # a live upload — free, never spends the budget
        if result["examined"] >= budget:
            result["truncated"] = True
            break
        result["examined"] += 1
        try:
            modified = storage.get_modified_time(key)
        except SoftTimeLimitExceeded:
            raise
        except Exception:  # noqa: BLE001 - a vanished or unreadable object
            # Already gone, or unreadable: either way this run must not delete
            # it on an unknown age.
            continue
        if timezone.is_naive(modified):
            modified = timezone.make_aware(modified, timezone.get_default_timezone())
        if modified >= cutoff:
            # Inside the grace window — its row may still be committing.
            result["skipped_recent"] += 1
            continue
        candidates.append(key)

    for key in candidates:
        if not _is_contained_upload_key(key):
            # Enumeration should make this impossible; refuse rather than trust
            # it. Nothing outside the upload prefix is ever deleted.
            logger.error("upload orphan sweep refused a key outside the upload prefix")
            continue
        # As late as possible: a row that committed while this run was working
        # saves its own object.
        if DesignInspirationUpload.objects.filter(storage_key=key).exists():
            continue
        try:
            _delete_object(storage, key)
        except _ObjectDeletionFailed:
            # Transient; the next run re-finds it (still old, still unreferenced).
            result["failed"] += 1
            continue
        result["deleted"] += 1

    _log_sweep(result)
    return result


def _log_sweep(result: dict) -> None:
    """Counts only — a storage key must never reach a log line."""
    logger.info(
        "upload orphan sweep examined=%s deleted=%s failed=%s skipped_recent=%s "
        "visited_directories=%s truncated=%s",
        result["examined"],
        result["deleted"],
        result["failed"],
        result["skipped_recent"],
        result["visited_directories"],
        result["truncated"],
    )


def _round_robin(groups: list[list[str]]) -> list[str]:
    """Interleave per-directory key lists, one key from each in turn.

    The examine budget below is spent front-to-back over the result, so this is
    what stops a single bloated directory consuming the whole budget while a
    neighbour's lone orphan — scanned, but never examined — survives every run."""
    interleaved: list[str] = []
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index < len(group):
                interleaved.append(group[index])
    return interleaved


def _upload_directories(storage) -> list[str]:
    """The per-design directories under the upload prefix, in listing order."""
    directories, _files = storage.listdir(UPLOAD_KEY_PREFIX)
    return list(directories)


def _rotation_offset(now, count: int, window: int) -> int:
    """A starting index that advances by a whole window per scheduled run.

    Derived from ``now`` rather than persisted state: there is nothing to store,
    lose or corrupt, and an operator changing the schedule cannot strand the
    rotation. It ticks once per ``USER_UPLOAD_SWEEP_INTERVAL_SECONDS`` and
    advances by ``window`` directories per tick, so consecutive runs sweep
    ADJACENT windows. Advancing by one per tick would technically rotate, but
    would take a run per directory to come round — too slow to call reachable.

    The bound, stated precisely: while ``count`` is stable the step equals the
    window width, so successive windows abut with no gap and the whole prefix is
    covered in exactly ``ceil(count / window)`` runs — for every combination of
    count and window, not only where they divide evenly. ``count`` is NOT stable
    in production, since designs are created and purged between runs, so under
    churn that is a close approximation rather than a proof: a directory's index
    can shift beneath the rotation. What churn cannot do is systematically
    exclude a directory, which is the property that matters — the starvation
    this replaced came from a FIXED start, not a drifting one."""
    if count <= 0:
        return 0
    tick = max(1, settings.USER_UPLOAD_SWEEP_INTERVAL_SECONDS)
    stride = max(1, window)
    return (int(now.timestamp() // tick) * stride) % count


def _is_contained_upload_key(key: str) -> bool:
    """True only if ``key`` genuinely resolves inside the upload prefix.

    ``startswith`` alone is not enough: ``design-uploads/../catalogue/x.webp``
    passes it and resolves to a catalogue object. Normalising first is what
    makes this a real containment check rather than a string-shape check."""
    if not key or "\\" in key:
        return False
    normalised = posixpath.normpath(key)
    return normalised.startswith(f"{UPLOAD_KEY_PREFIX}/") and ".." not in normalised.split("/")


def _iter_upload_keys(storage, prefix: str):
    """Yield every object key under ``prefix``, depth-first and lazily.

    Lazy within one design directory: the caller stops once its candidate budget
    is spent, without draining the remaining revisions. The top-level listing is
    NOT lazy — see the cost note on ``sweep_orphaned_upload_objects``."""
    directories, files = storage.listdir(prefix)
    for name in files:
        yield f"{prefix}/{name}"
    for directory in directories:
        yield from _iter_upload_keys(storage, f"{prefix}/{directory}")
