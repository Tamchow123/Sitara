"""Rights-controlled inspiration catalogue (Phase 5B).

A small, staff-managed catalogue of rights-approved inspiration images. Two
tables: ``UsageRights`` records WHY an image may be used (basis, holder,
evidence, the four usage permissions) and carries its own verification
lifecycle; ``InspirationAsset`` is one sanitised catalogue image whose
public visibility requires BOTH its own approval AND verified, unexpired,
fully-permissive rights — re-checked on every public query through
``InspirationAsset.objects.publicly_eligible()``.

Deliberately absent: original filenames, EXIF or any upload metadata,
public S3 URLs, provider data, prompts, user uploads. Storage keys are
server-generated (asset UUID + random revision) and never leave the
backend; public delivery streams through eligibility-checked views.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

RIGHTS_HOLDER_MAX_LENGTH = 200
ASSET_TITLE_MAX_LENGTH = 150
ASSET_ALT_TEXT_MAX_LENGTH = 300
ASSET_CULTURAL_CONTEXT_MAX_LENGTH = 500
GARMENT_TYPE_MAX_LENGTH = 64
STORAGE_KEY_MAX_LENGTH = 255
INTERNAL_NOTES_MAX_LENGTH = 2_000
ATTRIBUTION_TEXT_MAX_LENGTH = 500

_MACHINE_ID_VALIDATOR = RegexValidator(
    regex=r"^[a-z][a-z0-9_]{1,63}$",
    message="Must be a lower-case machine identifier (letters, digits, underscores).",
)

_SHA256_VALIDATOR = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="Must be exactly 64 lower-case hexadecimal characters.",
)


class UsageRights(models.Model):
    """The documented permission to use one inspiration image.

    Verification is a one-way, service-only transition: pending → verified
    (through ``services.verify_usage_rights``) or pending → rejected. A
    rejected record can never become verified — corrections happen by
    creating a new record, preserving the audit trail."""

    class RightsBasis(models.TextChoices):
        OWNED = "owned", "Owned"
        COMMISSIONED = "commissioned", "Commissioned"
        LICENSED = "licensed", "Licensed"
        PUBLIC_DOMAIN = "public_domain", "Public domain"
        PERMISSION_GRANTED = "permission_granted", "Permission granted"

    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rights_basis = models.CharField(max_length=32, choices=RightsBasis.choices)
    rights_holder = models.CharField(max_length=RIGHTS_HOLDER_MAX_LENGTH)
    source_url = models.URLField(max_length=500, blank=True)
    # Where the signed licence / permission email / ownership proof lives
    # (an internal document reference — never fetched, never a credential).
    evidence_reference = models.CharField(max_length=500, blank=True)
    licence_name = models.CharField(max_length=200, blank=True)
    licence_url = models.URLField(max_length=500, blank=True)
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        # Staff turnover must never delete or orphan a rights record.
        on_delete=models.SET_NULL,
        related_name="usage_rights_verified",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    allows_public_display = models.BooleanField(default=False)
    allows_ai_input = models.BooleanField(default=False)
    allows_derivative_generation = models.BooleanField(default=False)
    allows_commercial_use = models.BooleanField(default=False)
    attribution_required = models.BooleanField(default=False)
    attribution_text = models.CharField(max_length=ATTRIBUTION_TEXT_MAX_LENGTH, blank=True)
    internal_notes = models.TextField(max_length=INTERNAL_NOTES_MAX_LENGTH, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "usage rights record"
        verbose_name_plural = "usage rights records"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    rights_basis__in=[
                        "owned",
                        "commissioned",
                        "licensed",
                        "public_domain",
                        "permission_granted",
                    ]
                ),
                name="catalogue_rights_basis_valid",
            ),
            models.CheckConstraint(
                condition=Q(verification_status__in=["pending", "verified", "rejected"]),
                name="catalogue_rights_verification_status_valid",
            ),
            models.CheckConstraint(
                condition=~Q(verification_status="verified") | Q(verified_at__isnull=False),
                name="catalogue_rights_verified_requires_timestamp",
            ),
            models.CheckConstraint(
                condition=Q(attribution_required=False) | ~Q(attribution_text=""),
                name="catalogue_rights_attribution_text_required",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__isnull=True)
                | Q(verified_at__isnull=True)
                | Q(expires_at__gt=F("verified_at")),
                name="catalogue_rights_expiry_after_verification",
            ),
        ]

    def __str__(self) -> str:
        return f"UsageRights {self.id} ({self.rights_basis}, {self.verification_status})"


class InspirationAssetQuerySet(models.QuerySet):
    def publicly_eligible(self):
        """The ONE definition of public visibility, shared by the catalogue
        list and both image endpoints: an approved asset whose rights are
        verified, unexpired and carry every required permission. Rights
        revocation therefore takes effect on the next request."""
        return self.filter(
            status=InspirationAsset.Status.APPROVED,
            usage_rights__verification_status=UsageRights.VerificationStatus.VERIFIED,
            usage_rights__allows_public_display=True,
            usage_rights__allows_ai_input=True,
            usage_rights__allows_derivative_generation=True,
            usage_rights__allows_commercial_use=True,
        ).filter(
            Q(usage_rights__expires_at__isnull=True)
            | Q(usage_rights__expires_at__gt=timezone.now())
        )


class InspirationAsset(models.Model):
    """One sanitised, rights-linked catalogue image.

    Image bytes live in private object storage under server-generated keys;
    this row stores only the keys and sanitised-output facts (dimensions,
    byte size, SHA-256 of the sanitised main image). Approval and
    retirement are service-only transitions; ``save`` freezes approved
    content and makes retirement terminal as the backstop below the admin
    and services."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=ASSET_TITLE_MAX_LENGTH, blank=True)
    alt_text = models.CharField(max_length=ASSET_ALT_TEXT_MAX_LENGTH, blank=True)
    garment_type = models.CharField(
        max_length=GARMENT_TYPE_MAX_LENGTH, blank=True, validators=[_MACHINE_ID_VALIDATOR]
    )
    cultural_context = models.CharField(max_length=ASSET_CULTURAL_CONTEXT_MAX_LENGTH, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    usage_rights = models.OneToOneField(
        UsageRights,
        null=True,
        blank=True,
        # Rights records are the audit trail: they can never be deleted out
        # from under an asset.
        on_delete=models.PROTECT,
        related_name="asset",
    )
    # Object-storage keys only — never URLs. Server-generated:
    # catalogue/inspiration/<asset-uuid>/<random>/{image,thumbnail}.webp
    image_storage_key = models.CharField(max_length=STORAGE_KEY_MAX_LENGTH, blank=True)
    thumbnail_storage_key = models.CharField(max_length=STORAGE_KEY_MAX_LENGTH, blank=True)
    image_width = models.PositiveIntegerField(null=True, blank=True)
    image_height = models.PositiveIntegerField(null=True, blank=True)
    image_size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    # SHA-256 of the SANITISED main WebP (the original upload is discarded).
    image_sha256 = models.CharField(max_length=64, blank=True, validators=[_SHA256_VALIDATOR])
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspiration_assets_uploaded",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspiration_assets_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = InspirationAssetQuerySet.as_manager()

    # Everything that must never change once an asset is approved. Status
    # itself is handled separately (approved → retired stays possible).
    _FROZEN_ONCE_APPROVED = (
        "title",
        "alt_text",
        "garment_type",
        "cultural_context",
        "usage_rights_id",
        "image_storage_key",
        "thumbnail_storage_key",
        "image_width",
        "image_height",
        "image_size_bytes",
        "image_sha256",
        "uploaded_by_id",
        "approved_by_id",
        "approved_at",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["draft", "approved", "retired"]),
                name="catalogue_asset_status_valid",
            ),
            # Image metadata is all-or-none: a row either has no processed
            # image at all or every fact about it. No half-ingested rows.
            models.CheckConstraint(
                condition=(
                    Q(image_storage_key="")
                    & Q(thumbnail_storage_key="")
                    & Q(image_width__isnull=True)
                    & Q(image_height__isnull=True)
                    & Q(image_size_bytes__isnull=True)
                    & Q(image_sha256="")
                )
                | (
                    ~Q(image_storage_key="")
                    & ~Q(thumbnail_storage_key="")
                    & Q(image_width__isnull=False)
                    & Q(image_height__isnull=False)
                    & Q(image_size_bytes__isnull=False)
                    & ~Q(image_sha256="")
                ),
                name="catalogue_asset_image_metadata_all_or_none",
            ),
            models.CheckConstraint(
                condition=~Q(status="approved") | Q(approved_at__isnull=False),
                name="catalogue_asset_approved_requires_timestamp",
            ),
            models.CheckConstraint(
                condition=(Q(image_width__isnull=True) | Q(image_width__gt=0))
                & (Q(image_height__isnull=True) | Q(image_height__gt=0))
                & (Q(image_size_bytes__isnull=True) | Q(image_size_bytes__gt=0)),
                name="catalogue_asset_image_facts_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.title or f"Untitled inspiration asset {self.id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            stored = InspirationAsset.objects.filter(pk=self.pk).first()
            if stored is not None:
                if stored.status == self.Status.RETIRED:
                    raise ValidationError(
                        "A retired inspiration asset is immutable; create a new asset instead."
                    )
                if stored.status == self.Status.APPROVED:
                    if self.status == self.Status.DRAFT:
                        raise ValidationError(
                            "An approved inspiration asset cannot return to draft; "
                            "retire it and create a new asset instead."
                        )
                    for field in self._FROZEN_ONCE_APPROVED:
                        if getattr(self, field) != getattr(stored, field):
                            raise ValidationError(
                                "An approved inspiration asset is immutable apart from "
                                "retirement; retire it and create a new asset instead."
                            )
        super().save(*args, **kwargs)
