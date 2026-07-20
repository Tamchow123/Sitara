"""Admin for the design domain.

Staff see ownership through the user's email (or "anonymous"); no Django
session key or browser cookie material exists on these models to expose.
UUIDs and timestamps are read-only everywhere; DesignVersion and
GenerationAttempt are read-only-heavy (their lifecycle belongs to later
generation phases, not to hand editing). No image preview or media URL is
rendered — storage keys stay opaque strings.
"""

from django.contrib import admin

from .models import (
    Design,
    DesignInspiration,
    DesignSession,
    DesignVersion,
    GenerationAttempt,
)


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
    readonly_fields = (
        "id",
        "design_session",
        "questionnaire_version",
        "status",
        "answers",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    @admin.display(description="owner", ordering="design_session__user__email")
    def owner_email(self, obj: Design) -> str:
        user = obj.design_session.user
        return user.email if user else "anonymous"


@admin.register(DesignInspiration)
class DesignInspirationAdmin(admin.ModelAdmin):
    list_display = ("id", "design", "inspiration_asset", "position", "created_at")
    list_filter = ("created_at",)
    search_fields = ("id", "design__id", "inspiration_asset__id")
    readonly_fields = ("id", "design", "inspiration_asset", "position", "created_at")
    ordering = ("design", "position")

    def has_add_permission(self, request):
        # Selections are created only through the design API service.
        return False


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
        "design_spec_schema_version",
        "design_spec_template_version",
        "design_spec_provider",
        "design_spec_model",
        "design_spec_input_tokens",
        "design_spec_output_tokens",
        "design_spec_generated_at",
        "image_prompt",
        "prompt_builder_version",
        # Inspiration-context provenance (Phase 13) is immutable audit data —
        # read-only everywhere; never rendered as a link or preview.
        "inspiration_context",
        "inspiration_context_schema_version",
        "inspiration_context_sha256",
        # Refinement lineage provenance (Phase 14) is immutable audit data —
        # read-only everywhere.
        "parent_version",
        "refinement_request",
        "refinement_request_schema_version",
        "refinement_request_sha256",
        # Permanent-image provenance (Phase 11) is immutable audit data —
        # read-only everywhere; no preview or URL is ever rendered.
        "image_storage_key",
        "image_sha256",
        "image_size_bytes",
        "image_width",
        "image_height",
        "thumbnail_storage_key",
        "thumbnail_sha256",
        "thumbnail_size_bytes",
        "thumbnail_width",
        "thumbnail_height",
        "image_processor_version",
        "image_ingested_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False


@admin.register(GenerationAttempt)
class GenerationAttemptAdmin(admin.ModelAdmin):
    list_display = ("id", "design", "design_version", "status", "error_code", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "idempotency_key", "design__id", "design_version__id")
    readonly_fields = (
        "id",
        "design",
        "design_version",
        "idempotency_key",
        "celery_task_id",
        "status",
        "error_code",
        "started_at",
        "completed_at",
        "generation_kind",
        "source_design_version",
        "seed_reused",
        "image_provider",
        "image_model",
        "image_prediction_id",
        "image_seed",
        "image_submission_in_flight",
        "image_parameters",
        "staged_image_storage_key",
        "staged_image_sha256",
        "staged_image_size_bytes",
        "staged_image_width",
        "staged_image_height",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False
