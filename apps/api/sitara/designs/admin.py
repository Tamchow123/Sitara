"""Admin for the design domain.

Staff see ownership through the user's email (or "anonymous"); no Django
session key or browser cookie material exists on these models to expose.
UUIDs and timestamps are read-only everywhere; DesignVersion and
GenerationAttempt are read-only-heavy (their lifecycle belongs to later
generation phases, not to hand editing). No image preview or media URL is
rendered — storage keys stay opaque strings.
"""

from django.contrib import admin

from .models import Design, DesignSession, DesignVersion, GenerationAttempt


@admin.register(DesignSession)
class DesignSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "owner_email", "created_at", "last_seen_at")
    list_filter = ("created_at", "last_seen_at")
    search_fields = ("id", "user__email")
    readonly_fields = ("id", "created_at", "updated_at", "last_seen_at")
    ordering = ("-created_at",)

    @admin.display(description="owner", ordering="user__email")
    def owner_email(self, obj: DesignSession) -> str:
        return obj.user.email if obj.user_id else "anonymous"


@admin.register(Design)
class DesignAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "status", "owner_email", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "title", "design_session__user__email")
    readonly_fields = ("id", "design_session", "status", "answers", "created_at", "updated_at")
    ordering = ("-created_at",)

    @admin.display(description="owner", ordering="design_session__user__email")
    def owner_email(self, obj: Design) -> str:
        user = obj.design_session.user
        return user.email if user else "anonymous"


@admin.register(DesignVersion)
class DesignVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "design", "version_number", "created_at")
    list_filter = ("created_at",)
    search_fields = ("id", "design__id", "design__title")
    readonly_fields = (
        "id",
        "design",
        "version_number",
        "design_spec",
        "image_storage_key",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False


@admin.register(GenerationAttempt)
class GenerationAttemptAdmin(admin.ModelAdmin):
    list_display = ("id", "design_version", "status", "error_code", "created_at", "completed_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "idempotency_key", "design_version__design__id")
    readonly_fields = (
        "id",
        "design_version",
        "idempotency_key",
        "status",
        "error_code",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False
