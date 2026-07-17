"""Shared helpers for catalogue tests.

All test images are generated in memory with Pillow — no third-party or
real photographic image is ever committed to the repository.
"""

from io import BytesIO

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from sitara.catalogue.models import InspirationAsset, UsageRights
from sitara.catalogue.services import (
    approve_inspiration_asset,
    ingest_inspiration_image,
)

CATALOGUE_LIST_URL = "/api/v1/inspiration-assets/"


def image_url(asset) -> str:
    return f"/api/v1/inspiration-assets/{asset.pk}/image/"


def thumbnail_url(asset) -> str:
    return f"/api/v1/inspiration-assets/{asset.pk}/thumbnail/"


def make_image_bytes(
    fmt: str = "JPEG",
    size: tuple[int, int] = (320, 200),
    color: tuple = (180, 40, 90),
    mode: str = "RGB",
    **save_kwargs,
) -> bytes:
    if mode == "P":
        image = Image.new("RGB", size, color).convert("P")
    else:
        image = Image.new(mode, size, color)
    buffer = BytesIO()
    image.save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def make_upload(
    data: bytes, name: str = "upload.jpg", content_type: str = "image/jpeg"
) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


def make_rights(*, verified: bool = False, verified_by=None, **overrides) -> UsageRights:
    fields = {
        "rights_basis": UsageRights.RightsBasis.PERMISSION_GRANTED,
        "rights_holder": "Test Rights Holder",
        "evidence_reference": "evidence/test-permission-record",
        "allows_public_display": True,
        "allows_ai_input": True,
        "allows_derivative_generation": True,
        "allows_commercial_use": True,
    }
    fields.update(overrides)
    if verified:
        fields.setdefault("verification_status", UsageRights.VerificationStatus.VERIFIED)
        fields.setdefault("verified_at", timezone.now())
        fields.setdefault("verified_by", verified_by)
    return UsageRights.objects.create(**fields)


def make_asset(**overrides) -> InspirationAsset:
    fields = {
        "title": "Emerald velvet bridal look",
        "alt_text": "Front view of an emerald bridal outfit with gold embroidery.",
        "garment_type": "lehenga",
        "cultural_context": "Broad Pakistani bridal styling reference.",
    }
    fields.update(overrides)
    return InspirationAsset.objects.create(**fields)


def make_asset_with_image(**overrides) -> InspirationAsset:
    asset = make_asset(**overrides)
    upload = make_upload(make_image_bytes())
    return ingest_inspiration_image(asset, upload)


def make_eligible_asset(*, approved_by=None, rights=None, **overrides) -> InspirationAsset:
    """A fully approved, publicly eligible asset with real stored bytes."""
    if rights is None:
        rights = make_rights(verified=True)
    asset = make_asset_with_image(usage_rights=rights, **overrides)
    return approve_inspiration_asset(asset, approved_by=approved_by)


def list_all_storage_keys(prefix: str = "") -> list[str]:
    """Every key currently in the (in-memory) test storage."""
    directories, files = default_storage.listdir(prefix)
    keys = [f"{prefix}/{name}" if prefix else name for name in files]
    for directory in directories:
        sub = f"{prefix}/{directory}" if prefix else directory
        keys.extend(list_all_storage_keys(sub))
    return keys
