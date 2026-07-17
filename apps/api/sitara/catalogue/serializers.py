"""Public catalogue serialization (Phase 5B).

Exactly the public surface and nothing else. Deliberately absent: the
rights record UUID, evidence references, verifier identity, uploaded_by /
approved_by, storage keys, SHA-256, dimensions, byte size and any licence
detail not already part of the approved public attribution text. Image
URLs are relative Django endpoints — never storage URLs.
"""

from .models import InspirationAsset


def public_asset_payload(asset: InspirationAsset) -> dict:
    """The public catalogue entry for one eligible asset."""
    return {
        "id": str(asset.id),
        "title": asset.title,
        "alt_text": asset.alt_text,
        "garment_type": asset.garment_type,
        "cultural_context": asset.cultural_context,
        # Attribution text is authored for public display at verification
        # time; it is the ONLY rights field that ever leaves the backend.
        "attribution": asset.usage_rights.attribution_text if asset.usage_rights else "",
        "image_url": f"/api/v1/inspiration-assets/{asset.id}/image/",
        "thumbnail_url": f"/api/v1/inspiration-assets/{asset.id}/thumbnail/",
    }
