"""Public questionnaire API (Phase 5A): serve the single active version.

Read-only and identity-free: no authentication classes, so a GET can never
create a Django session (and therefore never a DesignSession). The stored
schema is re-validated before serving — a corrupted active schema yields
the same safe 503 as a missing one, with only the version id and exception
type logged, never the malformed content or validation detail.
"""

import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from sitara.schema import ErrorEnvelopeSerializer

from .models import QuestionnaireVersion
from .openapi import ActiveQuestionnaireResponseSerializer
from .schema_validation import validate_questionnaire_schema
from .serializers import ActiveQuestionnaireSerializer

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}


def _unavailable() -> Response:
    return Response(
        {
            "error": {
                "code": "questionnaire_unavailable",
                "message": "The questionnaire is temporarily unavailable.",
            }
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers=NO_STORE,
    )


class ActiveQuestionnaireView(APIView):
    # Identity-free by design: an empty authentication list means no
    # session is ever read into being for this public read.
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="questionnaire_active",
        tags=["Questionnaire"],
        responses={
            200: ActiveQuestionnaireResponseSerializer,
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="No valid active questionnaire version is available.",
            ),
        },
        summary="Active questionnaire",
        description=(
            "Serves the single active questionnaire version as {id, version, "
            "schema}. Public and identity-free (creates no session or "
            "workspace); no authentication required. The schema is the "
            "authoritative source of question types, constraints and "
            "compatibility rules."
        ),
    )
    def get(self, request):
        active = QuestionnaireVersion.objects.filter(
            status=QuestionnaireVersion.Status.ACTIVE
        ).first()
        if active is None:
            return _unavailable()
        try:
            validate_questionnaire_schema(active.schema)
        except Exception as exc:
            # QuestionnaireSchemaError is the anticipated failure; anything
            # else means corrupted storage slipped past the validator's
            # type handling. Both get the identical safe 503, and the log
            # carries only the version id and exception type — never the
            # exception text, a traceback or any schema content.
            logger.error(
                "active questionnaire schema invalid questionnaire_version_id=%s "
                "exception_type=%s",
                active.pk,
                type(exc).__name__,
            )
            return _unavailable()
        return Response(ActiveQuestionnaireSerializer(active).data, headers=NO_STORE)
