"""UsageRights model constraints and the verification service."""

import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from sitara.catalogue.models import UsageRights
from sitara.catalogue.services import RightsVerificationError, verify_usage_rights

from .utils import make_rights

pytestmark = pytest.mark.django_db

User = get_user_model()


def _staff():
    return User.objects.create_user(
        email="rights-staff@example.com", password="Correct-Horse-Battery-2026!"
    )


class TestUsageRightsModel:
    def test_uuid_primary_key(self):
        rights = make_rights()
        assert isinstance(rights.pk, uuid.UUID)

    def test_invalid_rights_basis_is_rejected_by_the_database(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_rights(rights_basis="scraped")

    def test_invalid_verification_status_is_rejected_by_the_database(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_rights(verification_status="approved")

    def test_verified_status_requires_verified_at(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_rights(verification_status="verified", verified_at=None)

    def test_attribution_required_needs_attribution_text(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_rights(attribution_required=True, attribution_text="")

    def test_expiry_must_be_after_verification_time(self):
        now = timezone.now()
        with pytest.raises(IntegrityError), transaction.atomic():
            make_rights(
                verification_status="verified",
                verified_at=now,
                expires_at=now - timedelta(days=1),
            )

    def test_timestamps_are_timezone_aware(self):
        rights = make_rights()
        assert rights.created_at.tzinfo is not None
        assert rights.updated_at.tzinfo is not None


class TestVerifyUsageRights:
    def test_valid_pending_record_becomes_verified(self):
        staff = _staff()
        rights = make_rights()

        verified = verify_usage_rights(rights, verified_by=staff)

        assert verified.verification_status == UsageRights.VerificationStatus.VERIFIED
        assert verified.verified_by == staff
        assert verified.verified_at is not None
        verified.refresh_from_db()
        assert verified.verification_status == UsageRights.VerificationStatus.VERIFIED

    def test_missing_evidence_is_rejected(self):
        rights = make_rights(evidence_reference="")
        with pytest.raises(RightsVerificationError, match="evidence"):
            verify_usage_rights(rights, verified_by=_staff())

    def test_blank_rights_holder_is_rejected(self):
        rights = make_rights(rights_holder="   ")
        with pytest.raises(RightsVerificationError, match="holder"):
            verify_usage_rights(rights, verified_by=_staff())

    def test_expired_rights_are_rejected(self):
        rights = make_rights(expires_at=timezone.now() - timedelta(minutes=1))
        with pytest.raises(RightsVerificationError, match="expired"):
            verify_usage_rights(rights, verified_by=_staff())

    def test_future_expiry_is_accepted(self):
        rights = make_rights(expires_at=timezone.now() + timedelta(days=365))
        verified = verify_usage_rights(rights, verified_by=_staff())
        assert verified.verification_status == UsageRights.VerificationStatus.VERIFIED

    @pytest.mark.parametrize(
        "flag",
        [
            "allows_public_display",
            "allows_ai_input",
            "allows_derivative_generation",
            "allows_commercial_use",
        ],
    )
    def test_every_usage_permission_is_required(self, flag):
        rights = make_rights(**{flag: False})
        with pytest.raises(RightsVerificationError, match="permissions"):
            verify_usage_rights(rights, verified_by=_staff())

    def test_attribution_text_mandatory_when_attribution_required(self):
        rights = make_rights(attribution_required=True, attribution_text="pending")
        # Simulate the text being blanked after creation: whitespace only.
        UsageRights.objects.filter(pk=rights.pk).update(attribution_text="   ")
        rights.refresh_from_db()
        with pytest.raises(RightsVerificationError, match="[Aa]ttribution"):
            verify_usage_rights(rights, verified_by=_staff())

    def test_rejected_record_can_never_become_verified(self):
        rights = make_rights(verification_status=UsageRights.VerificationStatus.REJECTED)
        with pytest.raises(RightsVerificationError, match="rejected"):
            verify_usage_rights(rights, verified_by=_staff())
        rights.refresh_from_db()
        assert rights.verification_status == UsageRights.VerificationStatus.REJECTED

    def test_already_verified_record_is_rejected(self):
        rights = make_rights(verified=True)
        with pytest.raises(RightsVerificationError, match="pending"):
            verify_usage_rights(rights, verified_by=_staff())

    def test_failed_verification_changes_nothing(self):
        rights = make_rights(evidence_reference="")
        with pytest.raises(RightsVerificationError):
            verify_usage_rights(rights, verified_by=_staff())
        rights.refresh_from_db()
        assert rights.verification_status == UsageRights.VerificationStatus.PENDING
        assert rights.verified_by is None
        assert rights.verified_at is None
