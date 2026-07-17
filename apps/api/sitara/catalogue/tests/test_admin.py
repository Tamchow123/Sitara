"""Admin workflow tests: staff-only, service-backed, no direct lifecycle
editing, safe messages, type-only logging on unexpected failure."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from sitara.catalogue.admin import InspirationAssetAdmin, UsageRightsAdmin
from sitara.catalogue.models import InspirationAsset, UsageRights

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

RIGHTS_CHANGELIST = "/admin/catalogue/usagerights/"
ASSET_CHANGELIST = "/admin/catalogue/inspirationasset/"


@pytest.fixture
def admin_client_and_user():
    user = User.objects.create_superuser(
        email="catalogue-admin@example.com", password="Correct-Horse-Battery-2026!"
    )
    client = Client()
    client.force_login(user)
    return client, user


def _run_action(client, changelist_url, action, target):
    return client.post(
        changelist_url,
        {"action": action, "_selected_action": [str(target.pk)]},
        follow=True,
    )


class TestUsageRightsAdmin:
    def test_verify_action_verifies_a_complete_pending_record(self, admin_client_and_user):
        client, user = admin_client_and_user
        rights = make_rights()
        response = _run_action(client, RIGHTS_CHANGELIST, "verify_selected", rights)
        assert response.status_code == 200
        rights.refresh_from_db()
        assert rights.verification_status == UsageRights.VerificationStatus.VERIFIED
        assert rights.verified_by == user

    def test_incomplete_record_shows_safe_message_and_stays_pending(self, admin_client_and_user):
        client, _ = admin_client_and_user
        rights = make_rights(evidence_reference="")
        response = _run_action(client, RIGHTS_CHANGELIST, "verify_selected", rights)
        text = response.content.decode()
        assert "evidence reference is required" in text
        assert "Traceback" not in text
        rights.refresh_from_db()
        assert rights.verification_status == UsageRights.VerificationStatus.PENDING

    def test_action_requires_exactly_one_selection(self, admin_client_and_user):
        client, _ = admin_client_and_user
        first, second = make_rights(), make_rights()
        response = client.post(
            RIGHTS_CHANGELIST,
            {
                "action": "verify_selected",
                "_selected_action": [str(first.pk), str(second.pk)],
            },
            follow=True,
        )
        assert "Select exactly one" in response.content.decode()
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.verification_status == UsageRights.VerificationStatus.PENDING
        assert second.verification_status == UsageRights.VerificationStatus.PENDING

    def test_unexpected_failure_is_contained_with_type_only_logging(
        self, admin_client_and_user, monkeypatch, caplog
    ):
        client, _ = admin_client_and_user
        rights = make_rights()

        def boom(rights, *, verified_by):
            raise RuntimeError("poison_marker_secret_detail")

        monkeypatch.setattr("sitara.catalogue.admin.verify_usage_rights", boom)
        with caplog.at_level("ERROR"):
            response = _run_action(client, RIGHTS_CHANGELIST, "verify_selected", rights)
        text = response.content.decode()
        assert "Verification failed unexpectedly" in text
        assert "poison_marker" not in text
        assert "Traceback" not in text
        assert f"usage_rights_id={rights.pk}" in caplog.text
        assert "exception_type=RuntimeError" in caplog.text
        assert "poison_marker" not in caplog.text

    def test_verification_status_is_never_form_editable(self):
        assert "verification_status" in UsageRightsAdmin.readonly_fields

    def test_rights_linked_to_an_approved_asset_freeze_entirely(self, admin_client_and_user):
        _, _ = admin_client_and_user
        rights = make_rights(verified=True)
        make_eligible_asset(rights=rights)
        from django.contrib.admin.sites import site

        model_admin = site._registry[UsageRights]
        readonly = model_admin.get_readonly_fields(request=None, obj=rights)
        model_field_names = {field.name for field in UsageRights._meta.fields}
        assert model_field_names <= set(readonly)


class TestInspirationAssetAdmin:
    def test_add_form_with_upload_creates_asset_and_ingests_image(self, admin_client_and_user):
        client, user = admin_client_and_user
        rights = make_rights(verified=True)
        response = client.post(
            f"{ASSET_CHANGELIST}add/",
            {
                "title": "Uploaded through admin",
                "alt_text": "A generated test image.",
                "garment_type": "lehenga",
                "cultural_context": "",
                "usage_rights": str(rights.pk),
                "upload": make_upload(make_image_bytes(), name="admin-upload.jpg"),
            },
            follow=True,
        )
        assert response.status_code == 200
        asset = InspirationAsset.objects.get(title="Uploaded through admin")
        assert asset.image_storage_key
        assert asset.thumbnail_storage_key
        assert asset.uploaded_by == user
        assert asset.status == InspirationAsset.Status.DRAFT

    def test_rejected_upload_saves_draft_without_image_and_safe_message(
        self, admin_client_and_user
    ):
        client, _ = admin_client_and_user
        response = client.post(
            f"{ASSET_CHANGELIST}add/",
            {
                "title": "Bad upload",
                "alt_text": "x",
                "garment_type": "",
                "cultural_context": "",
                "upload": make_upload(b"definitely-not-an-image", name="bad.jpg"),
            },
            follow=True,
        )
        text = response.content.decode()
        assert "WITHOUT an image" in text
        assert "Traceback" not in text
        asset = InspirationAsset.objects.get(title="Bad upload")
        assert asset.image_storage_key == ""

    def test_approve_action_approves_an_eligible_draft(self, admin_client_and_user):
        client, user = admin_client_and_user
        asset = make_asset_with_image(usage_rights=make_rights(verified=True))
        _run_action(client, ASSET_CHANGELIST, "approve_selected", asset)
        asset.refresh_from_db()
        assert asset.status == InspirationAsset.Status.APPROVED
        assert asset.approved_by == user

    def test_approve_action_refuses_incomplete_asset_safely(self, admin_client_and_user):
        client, _ = admin_client_and_user
        asset = make_asset_with_image()  # no rights record
        response = _run_action(client, ASSET_CHANGELIST, "approve_selected", asset)
        assert "rights record is required" in response.content.decode()
        asset.refresh_from_db()
        assert asset.status == InspirationAsset.Status.DRAFT

    def test_retire_action_retires_an_approved_asset(self, admin_client_and_user):
        client, _ = admin_client_and_user
        asset = make_eligible_asset()
        _run_action(client, ASSET_CHANGELIST, "retire_selected", asset)
        asset.refresh_from_db()
        assert asset.status == InspirationAsset.Status.RETIRED

    def test_status_is_never_form_editable(self, admin_client_and_user):
        client, _ = admin_client_and_user
        assert "status" in InspirationAssetAdmin.readonly_fields
        # A hostile POST carrying status/approval fields changes nothing.
        asset = make_asset(usage_rights=make_rights(verified=True))
        client.post(
            f"{ASSET_CHANGELIST}{asset.pk}/change/",
            {
                "title": asset.title,
                "alt_text": asset.alt_text,
                "garment_type": asset.garment_type,
                "cultural_context": asset.cultural_context,
                "usage_rights": str(asset.usage_rights_id),
                "status": "approved",
                "approved_at": "2026-01-01 00:00:00",
            },
            follow=True,
        )
        asset.refresh_from_db()
        assert asset.status == InspirationAsset.Status.DRAFT
        assert asset.approved_at is None

    def test_approved_assets_cannot_be_deleted(self, admin_client_and_user):
        _, user = admin_client_and_user
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        asset = make_eligible_asset()
        request = RequestFactory().get(ASSET_CHANGELIST)
        request.user = user
        model_admin = site._registry[InspirationAsset]
        assert model_admin.has_delete_permission(request, asset) is False

    def test_approved_assets_are_fully_read_only(self, admin_client_and_user):
        asset = make_eligible_asset()
        from django.contrib.admin.sites import site

        model_admin = site._registry[InspirationAsset]
        readonly = model_admin.get_readonly_fields(request=None, obj=asset)
        model_field_names = {field.name for field in InspirationAsset._meta.fields}
        assert model_field_names <= set(readonly)

    def test_model_has_no_file_field_for_the_raw_upload(self):
        from django.db.models import FileField

        assert not any(
            isinstance(field, FileField) for field in InspirationAsset._meta.get_fields()
        )
