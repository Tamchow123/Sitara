"""Versioned questionnaire taxonomy (Phase 5A).

One ``QuestionnaireVersion`` row holds one complete, immutable-once-published
questionnaire definition as JSON (``schema``). The backend definition is
authoritative: server-side answer validation (a later phase) and the
frontend's derived Zod validation (Phase 7) both flow from the same
machine-readable constraints — rules are never hand-duplicated.

Lifecycle: rows are created as drafts, activated through
``services.activate_questionnaire_version`` (never by ordinary save), and
replaced by activating a newer version, which retires them. At most one row
is active — enforced by a PostgreSQL partial unique constraint, the final
backstop against competing or bypassed activation attempts.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class QuestionnaireVersion(models.Model):
    """One complete versioned questionnaire definition."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.PositiveIntegerField(unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    schema = models.JSONField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        # Staff turnover must never delete or orphan a questionnaire.
        on_delete=models.SET_NULL,
        related_name="questionnaire_versions_created",
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="questionnaire_versions_activated",
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.CheckConstraint(
                condition=Q(version__gt=0),
                name="questionnaire_version_positive",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["draft", "active", "retired"]),
                name="questionnaire_status_valid",
            ),
            # At most ONE active questionnaire, enforced by the database —
            # the final backstop against competing or bypassed activations.
            models.UniqueConstraint(
                fields=["status"],
                condition=Q(status="active"),
                name="questionnaire_single_active",
            ),
        ]

    def __str__(self) -> str:
        return f"Questionnaire v{self.version} ({self.status})"

    def save(self, *args, **kwargs):
        # Published definitions are immutable: persisted answers (a later
        # phase) will reference them by version, so the version number and
        # schema of an active or retired row can never change through
        # normal model or admin operations. Status transitions (activation
        # retiring the previous active row) remain possible.
        if not self._state.adding:
            stored = (
                QuestionnaireVersion.objects.filter(pk=self.pk)
                .values("status", "version", "schema")
                .first()
            )
            if stored and stored["status"] in (self.Status.ACTIVE, self.Status.RETIRED):
                if self.version != stored["version"] or self.schema != stored["schema"]:
                    raise ValidationError(
                        "The version number and schema of an active or retired "
                        "questionnaire version are immutable; create and activate "
                        "a new version instead."
                    )
        super().save(*args, **kwargs)
