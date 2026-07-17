"""Public inspiration catalogue API (Phase 5B).

Three identity-free GET endpoints: the catalogue list and the two image
variants. No authentication classes, so a GET can never create a Django
session (and therefore never a DesignSession). All three share ONE
eligibility definition — ``InspirationAsset.objects.publicly_eligible()``
— so a draft, retired, rights-expired, unverified or nonexistent asset is
indistinguishably 404 everywhere, and rights revocation takes effect on
the next request (every response is Cache-Control: no-store).

Image bytes stream through Django from private storage: no raw S3 URLs,
no redirects to MinIO, no signed URLs (deferred). A storage failure for
an ELIGIBLE asset is a safe 503 logging only the asset UUID, the variant
and the exception type.
"""

import logging

from django.core.files.storage import default_storage
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import InspirationAsset
from .serializers import public_asset_payload

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}


def _not_found() -> Response:
    # One body for draft, retired, expired, unverified and nonexistent:
    # a public caller can never distinguish "hidden" from "absent".
    return Response(
        {"error": {"code": "not_found", "message": "Not found."}},
        status=status.HTTP_404_NOT_FOUND,
        headers=NO_STORE,
    )


def _unavailable() -> Response:
    return Response(
        {
            "error": {
                "code": "catalogue_unavailable",
                "message": "The catalogue is temporarily unavailable.",
            }
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers=NO_STORE,
    )


class _PublicCatalogueView(APIView):
    # Identity-free by design: an empty authentication list means no
    # session is ever read into being for these public reads.
    authentication_classes: list = []
    permission_classes = [AllowAny]


class InspirationAssetListView(_PublicCatalogueView):
    def get(self, request):
        assets = InspirationAsset.objects.publicly_eligible().select_related("usage_rights")
        return Response(
            {"assets": [public_asset_payload(asset) for asset in assets]},
            headers=NO_STORE,
        )


class _InspirationImageBaseView(_PublicCatalogueView):
    variant = "image"

    def get(self, request, asset_id):
        asset = InspirationAsset.objects.publicly_eligible().filter(pk=asset_id).first()
        if asset is None:
            return _not_found()
        storage_key = (
            asset.image_storage_key if self.variant == "image" else asset.thumbnail_storage_key
        )
        try:
            with default_storage.open(storage_key, "rb") as handle:
                data = handle.read()
        except Exception as exc:
            # An eligible asset whose sanitised object is unexpectedly
            # unreadable: safe 503, logging only the asset UUID, the
            # requested variant and the exception type — never the key,
            # the storage endpoint or the exception text.
            logger.error(
                "inspiration image unavailable inspiration_asset_id=%s variant=%s "
                "exception_type=%s",
                asset.pk,
                self.variant,
                type(exc).__name__,
            )
            return _unavailable()
        response = HttpResponse(data, content_type="image/webp")
        response["Content-Disposition"] = "inline"
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "no-store"
        return response


class InspirationAssetImageView(_InspirationImageBaseView):
    variant = "image"


class InspirationAssetThumbnailView(_InspirationImageBaseView):
    variant = "thumbnail"
