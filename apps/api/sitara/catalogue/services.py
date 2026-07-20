"""Catalogue lifecycle services (Phase 5B).

These are the ONLY code paths that verify rights, attach an image to an
asset, approve an asset or retire one. The admin calls them from actions;
ordinary form saves can do none of these things. Every service is atomic,
locks the rows it changes, and raises a small safe domain exception whose
message never contains uploaded bytes, filenames, storage endpoints,
credentials or image metadata. Logs carry only the operation name, the
row UUID and an exception type.
"""

import logging
import secrets

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from sitara.content_safety import GeneratedContentRejected, scan_generated_text

from .image_processing import process_inspiration_upload
from .models import InspirationAsset, UsageRights

logger = logging.getLogger(__name__)

REQUIRED_USAGE_FLAGS = (
    "allows_public_display",
    "allows_ai_input",
    "allows_derivative_generation",
    "allows_commercial_use",
)


class RightsVerificationError(Exception):
    """The rights record cannot be verified. Messages are safe to show."""


class AssetIngestError(Exception):
    """The image could not be attached to the asset. Messages are safe."""


class AssetApprovalError(Exception):
    """The asset cannot be approved or retired. Messages are safe."""


def verify_usage_rights(rights: UsageRights, *, verified_by) -> UsageRights:
    """Atomically move one PENDING rights record to VERIFIED.

    Incomplete rights are never silently verified: evidence, a named
    holder, a future (or absent) expiry, all four usage permissions and —
    when attribution is required — attribution text are all preconditions.
    A rejected record can never become verified; corrections are new
    records.
    """
    with transaction.atomic():
        locked = UsageRights.objects.select_for_update().get(pk=rights.pk)
        if locked.verification_status == UsageRights.VerificationStatus.REJECTED:
            raise RightsVerificationError(
                "A rejected rights record cannot be verified; create a new corrected record."
            )
        if locked.verification_status != UsageRights.VerificationStatus.PENDING:
            raise RightsVerificationError("Only a pending rights record can be verified.")
        if not locked.evidence_reference.strip():
            raise RightsVerificationError("An evidence reference is required before verification.")
        if not locked.rights_holder.strip():
            raise RightsVerificationError("A rights holder is required before verification.")
        now = timezone.now()
        if locked.expires_at is not None and locked.expires_at <= now:
            raise RightsVerificationError("The rights have already expired.")
        for flag in REQUIRED_USAGE_FLAGS:
            if not getattr(locked, flag):
                raise RightsVerificationError(
                    "All four usage permissions (public display, AI input, derivative "
                    "generation, commercial use) are required for catalogue approval."
                )
        if locked.attribution_required and not locked.attribution_text.strip():
            raise RightsVerificationError(
                "Attribution text is required when attribution is required."
            )
        locked.verification_status = UsageRights.VerificationStatus.VERIFIED
        locked.verified_by = verified_by
        locked.verified_at = now
        locked.save(
            update_fields=["verification_status", "verified_by", "verified_at", "updated_at"]
        )
        logger.info("usage rights verified usage_rights_id=%s", locked.pk)
        return locked


def _delete_quietly(storage_key: str) -> None:
    try:
        default_storage.delete(storage_key)
    except Exception as exc:
        # Cleanup is best-effort; an orphaned PRIVATE object is preferable
        # to masking the original failure.
        logger.error(
            "inspiration image cleanup failed storage_delete exception_type=%s",
            type(exc).__name__,
        )


def ingest_inspiration_image(asset: InspirationAsset, uploaded_file, *, uploaded_by=None):
    """Sanitise an upload and attach both derivatives to a draft asset.

    Storage objects are written first, the database row second; a failure
    at either point removes whatever was already written, so no row ever
    points at incomplete storage and no orphaned write survives a database
    failure. The raw original upload is never stored. Keys are
    server-generated from the asset UUID and a random revision — no
    filenames, no identity data.
    """
    if asset.status != InspirationAsset.Status.DRAFT:
        raise AssetIngestError("Only a draft asset can receive an image.")
    if asset.image_storage_key:
        raise AssetIngestError(
            "This asset already has a processed image; retire it and create "
            "a new asset to replace the image."
        )

    # Raises InspirationImageError (safe message) on any rejected upload.
    processed = process_inspiration_upload(uploaded_file)

    revision = secrets.token_hex(8)
    prefix = f"catalogue/inspiration/{asset.pk}/{revision}"
    written_keys: list[str] = []
    try:
        image_key = default_storage.save(f"{prefix}/image.webp", ContentFile(processed.image_bytes))
        written_keys.append(image_key)
        thumbnail_key = default_storage.save(
            f"{prefix}/thumbnail.webp", ContentFile(processed.thumbnail_bytes)
        )
        written_keys.append(thumbnail_key)
    except Exception as exc:
        for key in written_keys:
            _delete_quietly(key)
        logger.error(
            "inspiration image ingest failed storage_write inspiration_asset_id=%s "
            "exception_type=%s",
            asset.pk,
            type(exc).__name__,
        )
        raise AssetIngestError("The image could not be stored.") from None

    try:
        with transaction.atomic():
            locked = InspirationAsset.objects.select_for_update().get(pk=asset.pk)
            if locked.status != InspirationAsset.Status.DRAFT or locked.image_storage_key:
                raise AssetIngestError("Only a draft asset without an image can be updated.")
            locked.image_storage_key = image_key
            locked.thumbnail_storage_key = thumbnail_key
            locked.image_width = processed.width
            locked.image_height = processed.height
            locked.image_size_bytes = processed.size_bytes
            locked.image_sha256 = processed.sha256
            if uploaded_by is not None:
                locked.uploaded_by = uploaded_by
            locked.save()
    except AssetIngestError:
        for key in written_keys:
            _delete_quietly(key)
        raise
    except Exception as exc:
        for key in written_keys:
            _delete_quietly(key)
        logger.error(
            "inspiration image ingest failed database_update inspiration_asset_id=%s "
            "exception_type=%s",
            asset.pk,
            type(exc).__name__,
        )
        raise AssetIngestError("The image could not be attached to the asset.") from None

    logger.info("inspiration image ingested inspiration_asset_id=%s", locked.pk)
    return locked


def approve_inspiration_asset(asset: InspirationAsset, *, approved_by) -> InspirationAsset:
    """Atomically approve one draft asset for public catalogue display.

    Locks the asset AND its rights record, then requires: a processed
    image and thumbnail, title and alt text, verified unexpired rights,
    all four usage permissions and complete attribution when required.
    """
    with transaction.atomic():
        locked = InspirationAsset.objects.select_for_update().get(pk=asset.pk)
        if locked.status != InspirationAsset.Status.DRAFT:
            raise AssetApprovalError("Only a draft asset can be approved.")
        if not locked.image_storage_key or not locked.thumbnail_storage_key:
            raise AssetApprovalError("The asset has no processed image yet.")
        if not locked.title.strip():
            raise AssetApprovalError("A title is required before approval.")
        if not locked.alt_text.strip():
            raise AssetApprovalError("Alt text is required before approval.")
        # Defence in depth (Phase 13): the same generated-content safety scan
        # applied to inspiration metadata at generation time also gates
        # approval, so an unsafe alt_text/cultural_context can never become
        # approved in the first place. Selection-time revalidation remains
        # the authoritative pre-spend gate for already-approved legacy assets.
        try:
            scan_generated_text(locked.alt_text)
            if locked.cultural_context.strip():
                scan_generated_text(locked.cultural_context)
        except GeneratedContentRejected as exc:
            raise AssetApprovalError(
                "The asset's metadata failed the content safety check."
            ) from exc
        if locked.usage_rights_id is None:
            raise AssetApprovalError("A usage rights record is required before approval.")
        rights = UsageRights.objects.select_for_update().get(pk=locked.usage_rights_id)
        if rights.verification_status != UsageRights.VerificationStatus.VERIFIED:
            raise AssetApprovalError("The usage rights record has not been verified.")
        now = timezone.now()
        if rights.expires_at is not None and rights.expires_at <= now:
            raise AssetApprovalError("The usage rights have expired.")
        for flag in REQUIRED_USAGE_FLAGS:
            if not getattr(rights, flag):
                raise AssetApprovalError("The usage rights do not carry every required permission.")
        if rights.attribution_required and not rights.attribution_text.strip():
            raise AssetApprovalError("The usage rights require attribution text.")
        locked.status = InspirationAsset.Status.APPROVED
        locked.approved_by = approved_by
        locked.approved_at = now
        locked.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        logger.info("inspiration asset approved inspiration_asset_id=%s", locked.pk)
        return locked


def retire_inspiration_asset(asset: InspirationAsset) -> InspirationAsset:
    """Atomically retire an asset: it disappears from every public endpoint
    on the next request and can never become active again. The row, its
    rights link and its audit history are all retained."""
    with transaction.atomic():
        locked = InspirationAsset.objects.select_for_update().get(pk=asset.pk)
        if locked.status == InspirationAsset.Status.RETIRED:
            raise AssetApprovalError("The asset is already retired.")
        locked.status = InspirationAsset.Status.RETIRED
        locked.save(update_fields=["status", "updated_at"])
        logger.info("inspiration asset retired inspiration_asset_id=%s", locked.pk)
        return locked
