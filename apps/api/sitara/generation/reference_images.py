"""Short-lived signed reference URLs for the image provider (Phase 16B, ADR 0019).

This module is the ONLY place that turns a design's chosen inspirations into
something a provider can fetch. It exists because ADR 0019 deliberately
overrides ADR 0014's rule that inspiration bytes never reach a provider — read
that ADR before changing anything here; its centre is the exposure being
accepted (a perpetual, irrevocable BFL licence over Inputs, unresolved coverage
of Replicate-routed traffic, no published retention window), not the feature.

What crosses the boundary is deliberately narrow:

- ONLY the references the user selected for THIS design — their own uploads and
  the curated catalogue assets they picked. Never a whole bucket, never another
  design's, never an unselected asset.
- ONLY as a presigned GET URL with a short TTL, minted here and handed straight
  to the provider. The URL is never persisted, returned by an API, cached or
  logged; nor is the storage key it was built from.
- Catalogue eligibility is re-checked HERE, at mint time, against
  ``publicly_eligible()`` — never trusted from the moment of selection. An asset
  whose rights lapsed, expired or were revoked in between is silently dropped
  rather than signed. An upload's own row is the authority for its own key,
  since a user's own photograph has no catalogue rights record (ADR 0018).

Demo generation never calls this. The caller gates on the attempt's frozen
``is_demo`` flag, so a demo run constructs no URL, signs nothing and reaches no
provider — the zero-cost guarantee is untouched.

Two operational consequences an operator must know before enabling live
generation (both fail CLOSED rather than degrading quietly):

- The signing origin must be **https and reachable from the provider**. A URL
  is signed against ``S3_SIGNED_URL_ENDPOINT_URL``, which is the *browser*-facing
  origin; a local ``http://localhost:9000`` MinIO satisfies neither condition, so
  :class:`~sitara.ai_gateway.image_generation.ReferenceImagesRejected` stops the
  request at the boundary instead of paying for a generation the provider cannot
  complete.
- The filesystem design-image backend cannot sign anything, so a design that
  HAS references fails here rather than sending a URL that resolves nowhere.
  A design with no references is unaffected and generates normally.
"""

import logging

from django.conf import settings

from sitara.ai_gateway.image_generation import MAX_REFERENCE_IMAGES
from sitara.catalogue.models import InspirationAsset
from sitara.media.delivery import DesignImageDeliveryUnavailable, S3DesignImageSigner

logger = logging.getLogger(__name__)

# The provider's own ceiling on reference images, imported from the boundary
# that ENFORCES it rather than restated here — two copies of one provider fact
# drift, and drift here either wastes a signing round-trip on a request the
# boundary will reject, or silently sends fewer references than the user chose.
# Sitara's own cap is MAX_INSPIRATION_IMAGES (3), well under this; the
# truncation below is a backstop against a future cap increase, not the
# working limit.
PROVIDER_MAX_REFERENCE_IMAGES = MAX_REFERENCE_IMAGES


def reference_image_urls(design, *, signer=None, ttl_seconds=None) -> tuple[str, ...]:
    """Presigned GET URLs for ``design``'s selected references, in a stable
    order: the user's own uploads first, then the curated catalogue assets.

    Returns an empty tuple when there is nothing to send — which is the normal
    case, since inspiration is optional. Never raises for an ineligible or
    missing asset: that reference is dropped and generation continues without
    it, because failing a whole generation over one lapsed catalogue asset would
    punish the user for something only staff can fix.

    A signing failure IS surfaced (the caller treats it as a provider-stage
    error), because silently sending fewer references than the user chose would
    misrepresent what the concept was built from."""
    keys = _selected_storage_keys(design)
    if not keys:
        return ()

    if signer is None and settings.DESIGN_IMAGE_STORAGE_BACKEND != "s3":
        # Same fail-closed stance as Phase 11 browser delivery: the
        # development-only filesystem backend has no signing path, and a
        # dangling URL sent to a paid provider is a charge for nothing.
        raise DesignImageDeliveryUnavailable(
            "reference images cannot be signed for this design-image backend"
        )

    ttl = int(ttl_seconds or settings.DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS)
    active_signer = signer if signer is not None else S3DesignImageSigner()
    urls: list[str] = []
    for key in keys[:PROVIDER_MAX_REFERENCE_IMAGES]:
        # A server-owned filename: the provider never learns the storage key,
        # and no user-supplied name has ever been retained to leak here.
        urls.append(active_signer.sign_get(key, ttl_seconds=ttl, filename="reference.webp"))
    # Count only — never a key, never a URL.
    logger.info("reference images signed design_id=%s count=%s", design.pk, len(urls))
    return tuple(urls)


def _selected_storage_keys(design) -> list[str]:
    """The storage keys behind this design's selected references.

    Uploads come first because they are the user's own photographs — the
    references they care most about — and a provider that weights earlier inputs
    more heavily should see those first. Both lists are ordered by the position
    the user themselves chose."""
    keys = [
        upload.storage_key
        for upload in design.inspiration_uploads.order_by("position")
        if upload.storage_key
    ]

    selections = list(design.inspiration_selections.order_by("position"))
    if selections:
        asset_ids = [selection.inspiration_asset_id for selection in selections]
        # Re-validated at mint time, not trusted from selection: this is the
        # same single definition the catalogue list and image endpoints use.
        eligible = {
            asset.pk: asset
            for asset in InspirationAsset.objects.publicly_eligible().filter(pk__in=asset_ids)
        }
        for selection in selections:
            asset = eligible.get(selection.inspiration_asset_id)
            if asset is None:
                # Rights lapsed, expired or were revoked since selection. Drop
                # it; log the design only, never the asset id or its key.
                logger.info(
                    "reference image skipped (not currently eligible) design_id=%s", design.pk
                )
                continue
            if asset.image_storage_key:
                keys.append(asset.image_storage_key)
    return keys
