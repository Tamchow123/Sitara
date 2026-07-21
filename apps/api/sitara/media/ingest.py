"""Crash-safe canonical design-image ingest (Phase 11 Part A).

Takes one GenerationAttempt whose raw provider output Phase 10 already staged
and verified, processes the canonical original + thumbnail WebP derivatives,
writes them to deterministic permanent keys on the ``design_images`` storage
alias, and persists the complete permanent-image provenance atomically on the
locked DesignVersion.

Object storage and PostgreSQL cannot commit atomically, so correctness comes
from determinism + verification, never from overwriting:

- deterministic keys (server UUIDs only) and deterministic processing mean a
  crashed ingest re-runs into exactly the same objects;
- an existing matching object is reused; an existing DIFFERENT object fails
  safely (never overwritten, never suffix-renamed around);
- objects written but metadata lost -> the rerun verifies and recovers the
  metadata; metadata present -> the rerun verifies the objects still match;
- recovery NEVER regenerates or calls a provider.

No database transaction or row lock is held while reading staging storage,
processing image bytes, writing final storage or verifying final objects —
only the short final metadata write locks the GenerationAttempt and the
DesignVersion (in that order everywhere, so lock ordering can never deadlock).

Exceptions are the generic, safe types in :mod:`sitara.media.exceptions`;
logs carry only operation names, row UUIDs and exception types.
"""

import hashlib
import io
import logging
import uuid
from dataclasses import dataclass

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, storages
from django.db import transaction
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from sitara.designs.models import DesignVersion, GenerationAttempt

from .exceptions import (
    DesignImageImmutable,
    DesignImageIngestFailed,
    DesignImageIngestRetry,
    DesignImageProcessingError,
)
from .image_processing import (
    DESIGN_IMAGE_PROCESSOR_VERSION,
    ProcessedDesignImage,
    process_design_image,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesignImageKeys:
    """The two deterministic permanent object keys for one DesignVersion."""

    original: str
    thumbnail: str


def build_design_image_keys(design_id, design_version_id) -> DesignImageKeys:
    """The single permanent key layout — pure and exhaustively tested.

    Only server-owned UUIDs appear in the path (no user identity, title,
    answer, prompt fragment, prediction id or client-controlled filename);
    coercing through :class:`uuid.UUID` guarantees canonical lowercase
    hyphenated form, so no traversal or unexpected character can ever reach a
    storage key."""
    design_uuid = uuid.UUID(str(design_id))
    version_uuid = uuid.UUID(str(design_version_id))
    prefix = f"design-images/{design_uuid}/{version_uuid}"
    return DesignImageKeys(
        original=f"{prefix}/original.webp",
        thumbnail=f"{prefix}/thumbnail.webp",
    )


def design_image_storage():
    """The permanent design-image storage, resolved at CALL time via the
    ``design_images`` alias so tests and environment overrides always apply."""
    return storages["design_images"]


# ---------------------------------------------------------------------------
# Storage helpers — shared transient-vs-confirmed discipline. A transport
# failure whose outcome is unknown raises DesignImageIngestRetry (safe to
# retry; no provider spend at risk); a confirmed conflict/corruption raises
# DesignImageIngestFailed. SoftTimeLimitExceeded always propagates so a worker
# interruption stays retryable at the task layer.
# ---------------------------------------------------------------------------


def _object_exists(store, key: str) -> bool:
    try:
        return bool(store.exists(key))
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - backend-specific transport errors
        raise DesignImageIngestRetry("storage availability could not be confirmed") from exc


def _read_object(store, key: str, max_bytes: int) -> bytes:
    try:
        with store.open(key, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - backend-specific transport errors
        raise DesignImageIngestRetry("storage read could not be completed") from exc
    if not data or len(data) > max_bytes:
        raise DesignImageIngestFailed("a stored object failed verification")
    return bytes(data)


def _verify_stored_object(store, key: str, expected_sha256: str, expected_size: int) -> None:
    """Confirm the object at ``key`` exists and matches the expected bytes."""
    if not _object_exists(store, key):
        raise DesignImageIngestFailed("a permanent object is missing")
    data = _read_object(store, key, expected_size)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise DesignImageIngestFailed("a stored object failed verification")


def _ensure_final_object(store, key: str, data: bytes, sha256: str) -> None:
    """Write one permanent object idempotently, never overwriting.

    - matching object already present -> reuse;
    - different object at our deterministic key -> confirmed conflict, fail;
    - backend renames the requested key (no-overwrite rename semantics) ->
      delete the unexpected object best-effort and fail;
    - transport failure with unknown outcome -> retryable."""
    if _object_exists(store, key):
        existing = _read_object(store, key, len(data))
        if hashlib.sha256(existing).hexdigest() == sha256:
            return
        raise DesignImageIngestFailed("a conflicting permanent object already exists")
    try:
        saved_key = store.save(key, ContentFile(data))
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - whether the object persisted is unknown
        raise DesignImageIngestRetry("storage write could not be confirmed") from exc
    if saved_key != key:
        # The backend's AUTHORITATIVE answer was a rename around an existing
        # object — a confirmed conflict, never accepted. Cleanup best-effort.
        try:
            store.delete(saved_key)
        except SoftTimeLimitExceeded:
            raise
        except Exception:  # noqa: BLE001 - best-effort cleanup only
            pass
        raise DesignImageIngestFailed("storage did not persist the requested key")
    # Read back and prove the stored bytes are exactly what was written.
    _verify_stored_object(store, key, sha256, len(data))


# ---------------------------------------------------------------------------
# Precondition and staged-source validation
# ---------------------------------------------------------------------------


def _require_version(attempt: GenerationAttempt) -> DesignVersion:
    """Preconditions that hold for EVERY ingest call, including the
    metadata-already-committed fast path: a linked version owned by the same
    Design, carrying a valid DesignSpec and immutable image prompt."""
    if attempt.design_id is None:
        raise DesignImageIngestFailed("the attempt has no design")
    if attempt.design_version_id is None:
        raise DesignImageIngestFailed("the attempt has no linked design version")
    version = DesignVersion.objects.filter(pk=attempt.design_version_id).first()
    if version is None or version.design_id != attempt.design_id:
        raise DesignImageIngestFailed("the attempt and design version do not match")
    if (
        version.design_spec is None
        or not version.image_prompt
        or not version.prompt_builder_version
    ):
        raise DesignImageIngestFailed("the design version is missing generation provenance")
    return version


def _require_staged_fields(attempt: GenerationAttempt) -> None:
    """Preconditions for the processing path ONLY: all five Phase 10 staged
    fields must be present. Deliberately NOT required for the fast path — a
    version whose permanent provenance is already committed and verified must
    finalise regardless of the calling attempt's own staging state (spec §8:
    'if DB metadata exists, verify both objects still exist and match')."""
    if not attempt.staged_image_storage_key or not attempt.staged_image_sha256:
        raise DesignImageIngestFailed("the attempt has no staged image")
    if (
        attempt.staged_image_size_bytes is None
        or attempt.staged_image_width is None
        or attempt.staged_image_height is None
    ):
        raise DesignImageIngestFailed("the attempt has no staged image")


def _read_verified_staged_bytes(staging_store, attempt: GenerationAttempt) -> bytes:
    """Bounded read of the staged object, revalidating format, dimensions and
    hash against the attempt's recorded staged metadata."""
    key = attempt.staged_image_storage_key
    if not _object_exists(staging_store, key):
        raise DesignImageIngestFailed("the staged object is missing")
    data = _read_object(staging_store, key, settings.GENERATION_RAW_MAX_BYTES)
    if hashlib.sha256(data).hexdigest() != attempt.staged_image_sha256:
        raise DesignImageIngestFailed("the staged object failed verification")
    if len(data) != attempt.staged_image_size_bytes:
        raise DesignImageIngestFailed("the staged object failed verification")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
    except Image.DecompressionBombError as exc:
        # Same classification discipline as image_sanitize.open_and_validate:
        # confirmed-bad content, never an unclassified internal error.
        raise DesignImageIngestFailed("the staged object failed verification") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise DesignImageIngestFailed("the staged object failed verification") from exc
    if image_format not in {"PNG", "JPEG", "WEBP"}:
        raise DesignImageIngestFailed("the staged object failed verification")
    if (width, height) != (attempt.staged_image_width, attempt.staged_image_height):
        raise DesignImageIngestFailed("the staged object failed verification")
    return data


def _process(data: bytes) -> ProcessedDesignImage:
    try:
        return process_design_image(
            data,
            max_edge=settings.DESIGN_IMAGE_MAX_EDGE,
            thumbnail_edge=settings.DESIGN_IMAGE_THUMBNAIL_EDGE,
            full_quality=settings.DESIGN_IMAGE_WEBP_QUALITY,
            thumbnail_quality=settings.DESIGN_IMAGE_THUMBNAIL_QUALITY,
            max_bytes=settings.GENERATION_RAW_MAX_BYTES,
            max_pixels=settings.GENERATION_RAW_MAX_PIXELS,
        )
    except DesignImageProcessingError as exc:
        # Staged bytes Phase 10 verified should always process; a rejection
        # here is confirmed-bad permanent content, never retryable.
        raise DesignImageIngestFailed("the staged image could not be processed") from exc


def _provenance_matches(
    version: DesignVersion, keys: DesignImageKeys, processed: ProcessedDesignImage
) -> bool:
    return (
        version.image_storage_key == keys.original
        and version.image_sha256 == processed.original_sha256
        and version.image_size_bytes == len(processed.original_bytes)
        and version.image_width == processed.original_width
        and version.image_height == processed.original_height
        and version.thumbnail_storage_key == keys.thumbnail
        and version.thumbnail_sha256 == processed.thumbnail_sha256
        and version.thumbnail_size_bytes == len(processed.thumbnail_bytes)
        and version.thumbnail_width == processed.thumbnail_width
        and version.thumbnail_height == processed.thumbnail_height
        and version.image_processor_version == DESIGN_IMAGE_PROCESSOR_VERSION
    )


def _verify_persisted_provenance(store, version: DesignVersion) -> None:
    """DB metadata already exists — verify both private objects still exist
    and match before treating ingest as complete (idempotent rerun)."""
    _verify_stored_object(
        store, version.image_storage_key, version.image_sha256, version.image_size_bytes
    )
    _verify_stored_object(
        store,
        version.thumbnail_storage_key,
        version.thumbnail_sha256,
        version.thumbnail_size_bytes,
    )


# ---------------------------------------------------------------------------
# The ingest service
# ---------------------------------------------------------------------------


def ingest_staged_design_image(
    attempt,
    *,
    staging_storage=None,
    final_storage=None,
) -> DesignVersion:
    """Ingest one attempt's staged image into permanent private storage.

    Returns the refreshed DesignVersion carrying complete permanent-image
    provenance. Idempotent: a rerun over completed work verifies and returns;
    conflicting existing content raises (never overwrites). Raises
    DesignImageIngestRetry / DesignImageIngestFailed / DesignImageImmutable.
    Makes ZERO provider calls under every path, including recovery."""
    staging_store = staging_storage if staging_storage is not None else default_storage
    final_store = final_storage if final_storage is not None else design_image_storage()

    attempt = GenerationAttempt.objects.get(pk=attempt.pk)
    version = _require_version(attempt)

    # Fast path — metadata already committed: verify the objects still exist
    # and match, then finalise. No staging read, no reprocessing, and no
    # requirement on the CALLING attempt's own staged fields (the committed
    # provenance is what matters).
    if version.has_permanent_image:
        _verify_persisted_provenance(final_store, version)
        return version

    _require_staged_fields(attempt)

    # No transaction/lock is held through any of this I/O.
    staged_bytes = _read_verified_staged_bytes(staging_store, attempt)
    processed = _process(staged_bytes)
    keys = build_design_image_keys(attempt.design_id, version.pk)

    _ensure_final_object(
        final_store, keys.original, processed.original_bytes, processed.original_sha256
    )
    _ensure_final_object(
        final_store, keys.thumbnail, processed.thumbnail_bytes, processed.thumbnail_sha256
    )

    # Short final transaction: lock attempt then version (fixed order), and
    # re-check everything that could have changed while I/O ran unlocked.
    with transaction.atomic():
        locked_attempt = GenerationAttempt.objects.select_for_update().get(pk=attempt.pk)
        locked_version = DesignVersion.objects.select_for_update().get(pk=version.pk)
        if (
            locked_attempt.design_version_id != locked_version.pk
            or locked_version.design_id != locked_attempt.design_id
        ):
            raise DesignImageIngestFailed("the attempt and design version do not match")
        if (
            locked_attempt.staged_image_storage_key != attempt.staged_image_storage_key
            or locked_attempt.staged_image_sha256 != attempt.staged_image_sha256
        ):
            raise DesignImageIngestFailed("the staged metadata changed during ingest")
        if locked_version.has_permanent_image:
            # A concurrent ingest finished first: identical output is the
            # deterministic case and simply idempotent; anything else must
            # never be overwritten.
            if _provenance_matches(locked_version, keys, processed):
                return locked_version
            raise DesignImageImmutable("permanent image provenance already exists")
        locked_version.image_storage_key = keys.original
        locked_version.image_sha256 = processed.original_sha256
        locked_version.image_size_bytes = len(processed.original_bytes)
        locked_version.image_width = processed.original_width
        locked_version.image_height = processed.original_height
        locked_version.thumbnail_storage_key = keys.thumbnail
        locked_version.thumbnail_sha256 = processed.thumbnail_sha256
        locked_version.thumbnail_size_bytes = len(processed.thumbnail_bytes)
        locked_version.thumbnail_width = processed.thumbnail_width
        locked_version.thumbnail_height = processed.thumbnail_height
        locked_version.image_processor_version = DESIGN_IMAGE_PROCESSOR_VERSION
        locked_version.image_ingested_at = timezone.now()
        locked_version.save(
            update_fields=[
                "image_storage_key",
                "image_sha256",
                "image_size_bytes",
                "image_width",
                "image_height",
                "thumbnail_storage_key",
                "thumbnail_sha256",
                "thumbnail_size_bytes",
                "thumbnail_width",
                "thumbnail_height",
                "image_processor_version",
                "image_ingested_at",
                "updated_at",
            ]
        )
    logger.info(
        "design image ingested attempt=%s design_version=%s processor_version=%s",
        attempt.pk,
        version.pk,
        DESIGN_IMAGE_PROCESSOR_VERSION,
    )
    return DesignVersion.objects.get(pk=version.pk)
