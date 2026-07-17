"""Admin workflow for questionnaire versions.

Staff create and edit DRAFTS only. Activation happens exclusively through
the "Activate selected questionnaire version" action (which runs the
locking, validating service) — the form cannot set status at all, version
and schema freeze once a row is active or retired, and active versions
cannot be deleted. Retired versions remain inspectable read-only history.
"""

import logging

from django.contrib import admin, messages

from .models import QuestionnaireVersion
from .schema_validation import QuestionnaireSchemaError
from .services import QuestionnaireActivationError, activate_questionnaire_version

logger = logging.getLogger(__name__)

_ALWAYS_READONLY = (
    "id",
    "status",
    "created_by",
    "activated_by",
    "activated_at",
    "created_at",
    "updated_at",
)


@admin.register(QuestionnaireVersion)
class QuestionnaireVersionAdmin(admin.ModelAdmin):
    list_display = ("version", "status", "created_at", "activated_at")
    list_filter = ("status",)
    # The search box matches an exact version number (see
    # get_search_results); anything non-numeric matches nothing rather than
    # attempting a string lookup against an integer column.
    search_fields = ("version",)
    readonly_fields = _ALWAYS_READONLY
    ordering = ("-version",)
    actions = ("activate_selected",)

    def get_search_results(self, request, queryset, search_term):
        term = search_term.strip()
        if not term:
            return queryset, False
        if term.isdigit():
            return queryset.filter(version=int(term)), False
        return queryset.none(), False

    def get_readonly_fields(self, request, obj=None):
        # Published definitions are frozen; the model save() enforces the
        # same rule as the backstop for non-admin writes.
        if obj is not None and obj.status != QuestionnaireVersion.Status.DRAFT:
            return _ALWAYS_READONLY + ("version", "schema")
        return _ALWAYS_READONLY

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status == QuestionnaireVersion.Status.ACTIVE:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Activate selected questionnaire version")
    def activate_selected(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Select exactly one draft questionnaire version to activate.",
                messages.ERROR,
            )
            return
        target = queryset.first()
        try:
            activate_questionnaire_version(target, activated_by=request.user)
        except QuestionnaireSchemaError as exc:
            self.message_user(
                request,
                "Activation refused — the schema is invalid: " + "; ".join(exc.messages),
                messages.ERROR,
            )
            return
        except QuestionnaireActivationError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return
        except Exception as exc:
            # Defence in depth: the transaction has already rolled back, so
            # the previously active version is unchanged. Staff see a
            # generic message — never a traceback or raw schema content —
            # and the log carries only the version id and exception type.
            logger.error(
                "questionnaire activation failed unexpectedly "
                "questionnaire_version_id=%s exception_type=%s",
                target.pk,
                type(exc).__name__,
            )
            self.message_user(
                request,
                "Activation failed unexpectedly. The previously active version is unchanged.",
                messages.ERROR,
            )
            return
        self.message_user(
            request,
            f"Questionnaire version {target.version} is now active.",
            messages.SUCCESS,
        )
