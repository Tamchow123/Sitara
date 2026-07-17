"""Public catalogue API tests.

All three endpoints are read-only and identity-free: they must never
create a Django session or a DesignSession, must expose only approved
assets with verified, unexpired, fully-permissive rights, and must answer
an indistinguishable 404 for anything else.
"""

from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from django.contrib.sessions.models import Session
from django.core.files.storage import default_storage
from django.test import Client
from django.utils import timezone
from PIL import Image

from sitara.catalogue.models import UsageRights
from sitara.designs.models import DesignSession

from .utils import (
    CATALOGUE_LIST_URL,
    image_url,
    make_asset,
    make_eligible_asset,
    make_rights,
    thumbnail_url,
)

pytestmark = pytest.mark.django_db


def _revoke(asset, **rights_updates):
    UsageRights.objects.filter(pk=asset.usage_rights_id).update(**rights_updates)


class TestCatalogueList:
    def test_exact_public_response_shape(self):
        asset = make_eligible_asset(
            rights=make_rights(
                verified=True,
                attribution_required=True,
                attribution_text="Used with permission from the rights holder.",
            )
        )
        response = Client().get(CATALOGUE_LIST_URL)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"assets"}
        assert body["assets"] == [
            {
                "id": str(asset.id),
                "title": "Emerald velvet bridal look",
                "alt_text": "Front view of an emerald bridal outfit with gold embroidery.",
                "garment_type": "lehenga",
                "cultural_context": "Broad Pakistani bridal styling reference.",
                "attribution": "Used with permission from the rights holder.",
                "image_url": f"/api/v1/inspiration-assets/{asset.id}/image/",
                "thumbnail_url": f"/api/v1/inspiration-assets/{asset.id}/thumbnail/",
            }
        ]

    def test_cache_control_no_store(self):
        assert Client().get(CATALOGUE_LIST_URL)["Cache-Control"] == "no-store"

    def test_slash_optional_routing(self):
        assert Client().get("/api/v1/inspiration-assets").status_code == 200

    def test_draft_assets_are_excluded(self):
        make_asset(usage_rights=make_rights(verified=True))
        assert Client().get(CATALOGUE_LIST_URL).json() == {"assets": []}

    def test_retired_assets_are_excluded(self):
        from sitara.catalogue.services import retire_inspiration_asset

        asset = make_eligible_asset()
        retire_inspiration_asset(asset)
        assert Client().get(CATALOGUE_LIST_URL).json() == {"assets": []}

    def test_expired_rights_exclude_the_asset(self):
        asset = make_eligible_asset(
            rights=make_rights(
                verified=True,
                verified_at=timezone.now() - timedelta(days=30),
                expires_at=timezone.now() + timedelta(minutes=1),
            )
        )
        # Move the expiry into the past (update() keeps constraints happy:
        # it is still after verified_at).
        _revoke(asset, expires_at=timezone.now() - timedelta(seconds=1))
        assert Client().get(CATALOGUE_LIST_URL).json() == {"assets": []}

    def test_unverified_rights_exclude_the_asset(self):
        asset = make_eligible_asset()
        _revoke(asset, verification_status=UsageRights.VerificationStatus.PENDING)
        assert Client().get(CATALOGUE_LIST_URL).json() == {"assets": []}

    @pytest.mark.parametrize(
        "flag",
        [
            "allows_public_display",
            "allows_ai_input",
            "allows_derivative_generation",
            "allows_commercial_use",
        ],
    )
    def test_missing_permission_excludes_the_asset(self, flag):
        asset = make_eligible_asset()
        _revoke(asset, **{flag: False})
        assert Client().get(CATALOGUE_LIST_URL).json() == {"assets": []}

    def test_no_internal_fields_leak(self):
        rights = make_rights(
            verified=True,
            evidence_reference="evidence-secret-reference",
            internal_notes="internal-notes-secret",
            licence_name="licence-name-internal",
            source_url="https://source-url-internal.example.com/page",
        )
        asset = make_eligible_asset(rights=rights)
        text = Client().get(CATALOGUE_LIST_URL).content.decode()
        for marker in (
            str(rights.pk),
            "evidence-secret-reference",
            "internal-notes-secret",
            "licence-name-internal",
            "source-url-internal",
            asset.image_storage_key,
            asset.image_sha256,
            "image_storage_key",
            "sha256",
            "image_width",
            "image_size_bytes",
            "uploaded_by",
            "approved_by",
            "verified_by",
            "verification_status",
        ):
            assert marker not in text

    def test_no_session_or_design_session_is_created(self):
        make_eligible_asset()
        client = Client()
        response = client.get(CATALOGUE_LIST_URL)
        assert response.status_code == 200
        assert "sitara_sessionid" not in response.cookies
        assert "sitara_csrftoken" not in response.cookies
        assert Session.objects.count() == 0
        assert DesignSession.objects.count() == 0


class TestImageDelivery:
    def _assert_webp_response(self, response):
        assert response.status_code == 200
        assert response["Content-Type"] == "image/webp"
        assert response["Content-Disposition"] == "inline"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["Cache-Control"] == "no-store"
        image = Image.open(BytesIO(response.content))
        assert image.format == "WEBP"

    def test_main_image_streams_valid_webp(self):
        asset = make_eligible_asset()
        self._assert_webp_response(Client().get(image_url(asset)))

    def test_thumbnail_streams_valid_webp(self):
        asset = make_eligible_asset()
        self._assert_webp_response(Client().get(thumbnail_url(asset)))

    def test_ineligible_assets_are_indistinguishable_404s(self):
        from sitara.catalogue.services import retire_inspiration_asset

        draft = make_asset(usage_rights=make_rights(verified=True))
        retired = make_eligible_asset()
        retire_inspiration_asset(retired)
        revoked = make_eligible_asset()
        _revoke(revoked, verification_status=UsageRights.VerificationStatus.PENDING)
        nonexistent_url = image_url(draft).replace(
            str(draft.pk), "00000000-0000-0000-0000-000000000000"
        )

        responses = [
            Client().get(image_url(draft)),
            Client().get(image_url(retired)),
            Client().get(image_url(revoked)),
            Client().get(nonexistent_url),
        ]
        for response in responses:
            assert response.status_code == 404
        bodies = {response.content for response in responses}
        assert len(bodies) == 1

    def test_storage_outage_returns_safe_503(self, caplog):
        asset = make_eligible_asset()
        default_storage.delete(asset.image_storage_key)
        with caplog.at_level("ERROR"):
            response = Client().get(image_url(asset))
        assert response.status_code == 503
        assert response.json() == {
            "error": {
                "code": "catalogue_unavailable",
                "message": "The catalogue is temporarily unavailable.",
            }
        }
        assert response["Cache-Control"] == "no-store"
        assert f"inspiration_asset_id={asset.pk}" in caplog.text
        assert "variant=image" in caplog.text
        assert "exception_type=" in caplog.text
        # Never the storage key, path or endpoint.
        assert "catalogue/inspiration" not in caplog.text

    def test_no_session_is_created_by_image_requests(self):
        asset = make_eligible_asset()
        client = Client()
        client.get(image_url(asset))
        client.get(thumbnail_url(asset))
        assert Session.objects.count() == 0
        assert DesignSession.objects.count() == 0


class TestNoProviderCalls:
    def test_catalogue_code_references_no_ai_provider(self):
        """The catalogue is pure storage-and-rights plumbing: nothing in
        the package touches the AI gateway or any provider SDK."""
        package_dir = Path(__file__).resolve().parent.parent
        for source_file in package_dir.glob("*.py"):
            source = source_file.read_text(encoding="utf-8").lower()
            for marker in ("replicate", "anthropic", "ai_gateway"):
                assert marker not in source, f"{source_file.name} references {marker}"
