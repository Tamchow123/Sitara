"""Admin workflow for the rights-controlled catalogue (Phase 5B).

Staff-only, service-backed: rights verification, image ingestion, asset
approval and retirement all run through the locking services — the forms
cannot set any lifecycle field directly. Approved and retired assets are
read-only; approved assets cannot be deleted; rights records linked to an
approved asset freeze entirely. Error messages shown to staff are the
services' safe messages or a generic sentence — never a traceback, never
raw upload data; unexpected failures log only the row UUID and the
exception type.
"""

import logging

from django.contrib import admin, messages

from .forms import InspirationAssetAdminForm
from .image_processing import InspirationImageError
from .models import InspirationAsset, UsageRights
from .services import (
    AssetApprovalError,
    AssetIngestError,
    RightsVerificationError,
    approve_inspiration_asset,
    ingest_inspiration_image,
    retire_inspiration_asset,
    verify_usage_rights,
)

logger = logging.getLogger(__name__)


def _exactly_one(modeladmin, request, queryset, noun: str):
    if queryset.count() != 1:
        modeladmin.message_user(request, f"Select exactly one {noun}.", messages.ERROR)
        return None
    return queryset.first()


@admin.register(UsageRights)
class UsageRightsAdmin(admin.ModelAdmin):
    list_display = (
        "rights_holder",
        "rights_basis",
        "verification_status",
        "expires_at",
        "created_at",
    )
    list_filter = ("verification_status", "rights_basis")
    readonly_fields = (
        "id",
        "verification_status",
        "verified_by",
        "verified_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)
    actions = ("verify_selected",)

    def _linked_to_approved_asset(self, obj) -> bool:
        if obj is None or obj.pk is None:
            return False
        return InspirationAsset.objects.filter(
            usage_rights=obj, status=InspirationAsset.Status.APPROVED
        ).exists()

    def get_readonly_fields(self, request, obj=None):
        # Rights backing an approved asset are frozen evidence: correction
        # means retiring the asset and creating new records.
        if self._linked_to_approved_asset(obj):
            return [field.name for field in self.model._meta.fields]
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        # PROTECT already blocks deleting rights linked to ANY asset; this
        # removes the confusing admin affordance for the approved case.
        if self._linked_to_approved_asset(obj):
            return False
        return super().has_delete_permission(request, obj)

    @admin.action(description="Verify selected rights record")
    def verify_selected(self, request, queryset):
        target = _exactly_one(self, request, queryset, "rights record to verify")
        if target is None:
            return
        try:
            verify_usage_rights(target, verified_by=request.user)
        except RightsVerificationError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return
        except Exception as exc:
            logger.error(
                "usage rights verification failed unexpectedly usage_rights_id=%s "
                "exception_type=%s",
                target.pk,
                type(exc).__name__,
            )
            self.message_user(
                request,
                "Verification failed unexpectedly. The rights record is unchanged.",
                messages.ERROR,
            )
            return
        self.message_user(request, "The rights record is now verified.", messages.SUCCESS)


@admin.register(InspirationAsset)
class InspirationAssetAdmin(admin.ModelAdmin):
    form = InspirationAssetAdminForm
    list_display = ("title", "status", "garment_type", "usage_rights", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "id",
        "status",
        "image_storage_key",
        "thumbnail_storage_key",
        "image_width",
        "image_height",
        "image_size_bytes",
        "image_sha256",
        "uploaded_by",
        "approved_by",
        "approved_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)
    actions = ("approve_selected", "retire_selected")

    def get_readonly_fields(self, request, obj=None):
        # Approved and retired assets are read-only history; the model
        # save() enforces the same rule as the backstop below the admin.
        if obj is not None and obj.status != InspirationAsset.Status.DRAFT:
            return [field.name for field in self.model._meta.fields]
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        # Approved assets are live catalogue content; retired assets are
        # retained audit history. Only unfinished drafts may be deleted.
        if obj is not None and obj.status != InspirationAsset.Status.DRAFT:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)
        upload = form.cleaned_data.get("upload")
        if not upload:
            return
        try:
            ingest_inspiration_image(obj, upload, uploaded_by=request.user)
        except (InspirationImageError, AssetIngestError) as exc:
            self.message_user(
                request,
                f"The asset was saved WITHOUT an image — upload rejected: {exc}",
                messages.ERROR,
            )
        except Exception as exc:
            logger.error(
                "inspiration image ingest failed unexpectedly inspiration_asset_id=%s "
                "exception_type=%s",
                obj.pk,
                type(exc).__name__,
            )
            self.message_user(
                request,
                "The asset was saved WITHOUT an image — the upload could not " "be processed.",
                messages.ERROR,
            )

    @admin.action(description="Approve selected inspiration asset")
    def approve_selected(self, request, queryset):
        target = _exactly_one(self, request, queryset, "inspiration asset to approve")
        if target is None:
            return
        try:
            approve_inspiration_asset(target, approved_by=request.user)
        except AssetApprovalError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return
        except Exception as exc:
            logger.error(
                "inspiration asset approval failed unexpectedly inspiration_asset_id=%s "
                "exception_type=%s",
                target.pk,
                type(exc).__name__,
            )
            self.message_user(
                request,
                "Approval failed unexpectedly. The asset is unchanged.",
                messages.ERROR,
            )
            return
        self.message_user(request, "The inspiration asset is now approved.", messages.SUCCESS)

    @admin.action(description="Retire selected inspiration asset")
    def retire_selected(self, request, queryset):
        target = _exactly_one(self, request, queryset, "inspiration asset to retire")
        if target is None:
            return
        try:
            retire_inspiration_asset(target)
        except AssetApprovalError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return
        except Exception as exc:
            logger.error(
                "inspiration asset retirement failed unexpectedly inspiration_asset_id=%s "
                "exception_type=%s",
                target.pk,
                type(exc).__name__,
            )
            self.message_user(
                request,
                "Retirement failed unexpectedly. The asset is unchanged.",
                messages.ERROR,
            )
            return
        self.message_user(request, "The inspiration asset is now retired.", messages.SUCCESS)
