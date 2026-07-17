"""Questionnaire lifecycle service.

``activate_questionnaire_version`` is the ONLY code path that makes a
questionnaire version active. Ordinary saves never activate anything, the
admin form cannot set status directly, and the database's partial unique
constraint (one active row) remains the final backstop against competing or
bypassed activation attempts.
"""

import logging

from django.db import transaction
from django.utils import timezone

from .models import QuestionnaireVersion
from .schema_validation import validate_questionnaire_schema

logger = logging.getLogger(__name__)


class QuestionnaireActivationError(Exception):
    """The version cannot be activated. Messages are safe to show."""


def activate_questionnaire_version(
    questionnaire: QuestionnaireVersion, *, activated_by=None
) -> QuestionnaireVersion:
    """Atomically make ``questionnaire`` the single active version.

    Locks the target row, validates the complete schema (malformed data is
    never silently activated), retires the current active version in the
    same transaction, and stamps ``activated_at``/``activated_by``. Only a
    draft can be activated — replacement of a retired or active version
    happens by creating and activating a NEW version, never by mutating an
    old one. On any failure the transaction rolls back and the previously
    active version stays active and unchanged.
    """
    with transaction.atomic():
        locked = QuestionnaireVersion.objects.select_for_update().get(pk=questionnaire.pk)
        if locked.status != QuestionnaireVersion.Status.DRAFT:
            raise QuestionnaireActivationError(
                "Only a draft questionnaire version can be activated."
            )
        # Raises QuestionnaireSchemaError (rolling everything back) rather
        # than ever activating a malformed definition.
        validate_questionnaire_schema(locked.schema)

        previous = (
            QuestionnaireVersion.objects.select_for_update()
            .filter(status=QuestionnaireVersion.Status.ACTIVE)
            .exclude(pk=locked.pk)
            .first()
        )
        # Retire BEFORE activating: the one-active partial unique constraint
        # is checked per statement, so the reverse order would refuse.
        if previous is not None:
            previous.status = QuestionnaireVersion.Status.RETIRED
            previous.save(update_fields=["status", "updated_at"])

        locked.status = QuestionnaireVersion.Status.ACTIVE
        locked.activated_at = timezone.now()
        locked.activated_by = activated_by
        locked.save(update_fields=["status", "activated_at", "activated_by", "updated_at"])
        logger.info(
            "questionnaire version activated questionnaire_version_id=%s version=%s",
            locked.pk,
            locked.version,
        )
        return locked
