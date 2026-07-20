"""InspirationAsset model constraints, immutability and lifecycle services."""

import re
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from sitara.catalogue.models import InspirationAsset, UsageRights
from sitara.catalogue.services import (
    AssetApprovalError,
    approve_inspiration_asset,
    retire_inspiration_asset,
)

from .utils import (
    make_asset,
    make_asset_with_image,
    make_eligible_asset,
    make_image_bytes,
    make_rights,
    make_upload,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


def _staff():
    return User.objects.create_user(
        email="asset-staff@example.com", password="Correct-Horse-Battery-2026!"
    )


class TestInspirationAssetModel:
    def test_uuid_primary_key(self):
        assert isinstance(make_asset().pk, uuid.UUID)

    def test_invalid_status_is_rejected_by_the_database(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_asset(status="published")

    def test_partial_image_metadata_is_rejected(self):
        # All-or-none: a storage key without the rest of the facts can
        # never be persisted.
        with pytest.raises(IntegrityError), transaction.atomic():
            make_asset(image_storage_key="catalogue/inspiration/x/y/image.webp")

    def test_zero_dimensions_are_rejected(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_asset(
                image_storage_key="catalogue/inspiration/x/y/image.webp",
                thumbnail_storage_key="catalogue/inspiration/x/y/thumbnail.webp",
                image_width=0,
                image_height=100,
                image_size_bytes=100,
                image_sha256="a" * 64,
            )

    def test_approved_status_requires_approved_at(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            InspirationAsset.objects.filter(pk=make_asset().pk).update(status="approved")

    def test_rights_relationship_is_protected(self):
        rights = make_rights()
        make_asset(usage_rights=rights)
        with pytest.raises(ProtectedError):
            rights.delete()

    def test_storage_keys_contain_no_filename_or_identity(self):
        staff = _staff()
        asset = make_asset()
        from sitara.catalogue.services import ingest_inspiration_image

        upload = make_upload(make_image_bytes(), name="bride-family-secret-photo.jpg")
        asset = ingest_inspiration_image(asset, upload, uploaded_by=staff)

        pattern = re.compile(
            rf"^catalogue/inspiration/{asset.pk}/[0-9a-f]{{16}}/(image|thumbnail)\.webp$"
        )
        for key in (asset.image_storage_key, asset.thumbnail_storage_key):
            assert pattern.match(key), key
            assert "bride" not in key
            assert "secret" not in key
            assert staff.email not in key
            assert str(staff.pk) not in key.replace(str(asset.pk), "")


class TestAssetImmutability:
    def test_approved_asset_content_is_frozen(self):
        asset = make_eligible_asset()
        asset.title = "Edited after approval"
        with pytest.raises(ValidationError):
            asset.save()

    def test_approved_asset_cannot_return_to_draft(self):
        asset = make_eligible_asset()
        asset.status = InspirationAsset.Status.DRAFT
        with pytest.raises(ValidationError):
            asset.save()

    def test_retired_asset_is_terminally_immutable(self):
        asset = make_eligible_asset()
        retire_inspiration_asset(asset)
        asset.refresh_from_db()
        asset.status = InspirationAsset.Status.APPROVED
        with pytest.raises(ValidationError):
            asset.save()


class TestApproveInspirationAsset:
    def test_valid_draft_is_approved(self):
        staff = _staff()
        rights = make_rights(verified=True)
        asset = make_asset_with_image(usage_rights=rights)

        approved = approve_inspiration_asset(asset, approved_by=staff)

        assert approved.status == InspirationAsset.Status.APPROVED
        assert approved.approved_by == staff
        assert approved.approved_at is not None

    def test_rights_less_asset_is_rejected(self):
        asset = make_asset_with_image()
        with pytest.raises(AssetApprovalError, match="rights record is required"):
            approve_inspiration_asset(asset, approved_by=_staff())

    def test_unverified_rights_are_rejected(self):
        asset = make_asset_with_image(usage_rights=make_rights())
        with pytest.raises(AssetApprovalError, match="not been verified"):
            approve_inspiration_asset(asset, approved_by=_staff())

    def test_expired_rights_are_rejected(self):
        rights = make_rights(
            verified=True,
            verified_at=timezone.now() - timedelta(days=30),
            expires_at=timezone.now() - timedelta(days=1),
        )
        asset = make_asset_with_image(usage_rights=rights)
        with pytest.raises(AssetApprovalError, match="expired"):
            approve_inspiration_asset(asset, approved_by=_staff())

    @pytest.mark.parametrize(
        "flag",
        [
            "allows_public_display",
            "allows_ai_input",
            "allows_derivative_generation",
            "allows_commercial_use",
        ],
    )
    def test_missing_usage_permission_is_rejected(self, flag):
        rights = make_rights(verified=True)
        asset = make_asset_with_image(usage_rights=rights)
        # Simulate revocation after verification.
        UsageRights.objects.filter(pk=rights.pk).update(**{flag: False})
        with pytest.raises(AssetApprovalError, match="permission"):
            approve_inspiration_asset(asset, approved_by=_staff())

    def test_missing_image_is_rejected(self):
        asset = make_asset(usage_rights=make_rights(verified=True))
        with pytest.raises(AssetApprovalError, match="no processed image"):
            approve_inspiration_asset(asset, approved_by=_staff())

    def test_missing_alt_text_is_rejected(self):
        asset = make_asset_with_image(alt_text="", usage_rights=make_rights(verified=True))
        with pytest.raises(AssetApprovalError, match="[Aa]lt text"):
            approve_inspiration_asset(asset, approved_by=_staff())

    def test_missing_title_is_rejected(self):
        asset = make_asset_with_image(title="", usage_rights=make_rights(verified=True))
        with pytest.raises(AssetApprovalError, match="title"):
            approve_inspiration_asset(asset, approved_by=_staff())

    def test_unsafe_alt_text_is_rejected(self):
        asset = make_asset_with_image(
            alt_text="Styled after Sabyasachi's signature look.",
            usage_rights=make_rights(verified=True),
        )
        with pytest.raises(AssetApprovalError, match="safety check"):
            approve_inspiration_asset(asset, approved_by=_staff())
        asset.refresh_from_db()
        assert asset.status == InspirationAsset.Status.DRAFT

    def test_unsafe_cultural_context_is_rejected(self):
        asset = make_asset_with_image(
            cultural_context="Visit https://example.com for more.",
            usage_rights=make_rights(verified=True),
        )
        with pytest.raises(AssetApprovalError, match="safety check"):
            approve_inspiration_asset(asset, approved_by=_staff())

    def test_safe_metadata_is_approved(self):
        asset = make_asset_with_image(
            alt_text="Front view of an emerald bridal outfit with gold embroidery.",
            cultural_context="Broad Pakistani bridal styling reference.",
            usage_rights=make_rights(verified=True),
        )
        approved = approve_inspiration_asset(asset, approved_by=_staff())
        assert approved.status == InspirationAsset.Status.APPROVED

    def test_incomplete_attribution_is_rejected(self):
        rights = make_rights(verified=True, attribution_required=True, attribution_text="x")
        asset = make_asset_with_image(usage_rights=rights)
        UsageRights.objects.filter(pk=rights.pk).update(attribution_text="   ")
        with pytest.raises(AssetApprovalError, match="attribution"):
            approve_inspiration_asset(asset, approved_by=_staff())

    def test_non_draft_cannot_be_approved(self):
        asset = make_eligible_asset()
        with pytest.raises(AssetApprovalError, match="draft"):
            approve_inspiration_asset(asset, approved_by=_staff())


class TestRetireInspirationAsset:
    def test_approved_asset_can_be_retired(self):
        asset = make_eligible_asset()
        retired = retire_inspiration_asset(asset)
        assert retired.status == InspirationAsset.Status.RETIRED

    def test_retired_asset_keeps_rights_and_audit_history(self):
        staff = _staff()
        rights = make_rights(verified=True)
        asset = make_eligible_asset(rights=rights, approved_by=staff)
        retired = retire_inspiration_asset(asset)
        retired.refresh_from_db()
        assert retired.usage_rights == rights
        assert retired.approved_by == staff
        assert retired.approved_at is not None
        assert retired.image_storage_key

    def test_retired_asset_cannot_be_approved_again(self):
        asset = make_eligible_asset()
        retire_inspiration_asset(asset)
        asset.refresh_from_db()
        with pytest.raises(AssetApprovalError):
            approve_inspiration_asset(asset, approved_by=_staff())

    def test_already_retired_is_refused(self):
        asset = make_eligible_asset()
        retire_inspiration_asset(asset)
        asset.refresh_from_db()
        with pytest.raises(AssetApprovalError, match="already retired"):
            retire_inspiration_asset(asset)
