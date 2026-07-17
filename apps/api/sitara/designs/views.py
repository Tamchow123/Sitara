"""Design API (Phase 7): list, create, retrieve, update, validate.

DRF views with two deliberate deviations from the project defaults, both
unchanged since Phase 4:

- ``AllowAny`` — ownership is session-based and anonymous workspaces are a
  feature, so authentication cannot be the gate. The ownership filter in
  ``ownership.accessible_designs`` is the mandatory access control.
- ``@csrf_protect`` on dispatch — DRF's SessionAuthentication only enforces
  CSRF for already-authenticated requests, but these endpoints accept
  anonymous unsafe requests too. Nothing here is csrf_exempt.

Phase 7 extends the draft with a linked questionnaire version, validated
answers and ordered inspiration selections. All answer/selection validation
and persistence is authoritative in ``services.update_design_draft`` (one
atomic, row-locked transaction); views stay thin. Inaccessible designs are
404, never 403. Every response carries ``Cache-Control: no-store``.
"""

import logging

from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ParseError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from sitara.questionnaire.answer_validation import QuestionnaireAnswerError
from sitara.schema import (
    CSRF_HEADER_PARAMETER,
    ErrorEnvelopeSerializer,
    ValidationErrorEnvelopeSerializer,
)

from .models import Design
from .openapi import (
    DesignDetailResponseSerializer,
    DesignListResponseSerializer,
    DesignValidationSuccessSerializer,
)
from .ownership import accessible_designs
from .serializers import (
    DesignWriteSerializer,
    design_detail_payload,
    design_list_item_payload,
)
from .services import (
    DraftUpdateError,
    WorkspaceCoordinationError,
    design_completion_errors,
    resolve_current_design_session,
    update_design_draft,
)

_DESIGN_TAGS = ["Designs"]
_OWNERSHIP_NOTE = (
    "Ownership is by Django session (anonymous workspace) OR authenticated "
    "account — never by knowing a UUID. Anything inaccessible returns an "
    "indistinguishable 404."
)

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}


def _error(code: str, message: str, http_status: int, fields: dict | None = None) -> Response:
    body: dict = {"error": {"code": code, "message": message}}
    if fields:
        body["error"]["fields"] = fields
    return Response(body, status=http_status, headers=NO_STORE)


def _not_found() -> Response:
    # One indistinguishable answer for nonexistent, other-session and
    # other-user designs.
    return _error("not_found", "Not found.", status.HTTP_404_NOT_FOUND)


def _validation_failed(errors: dict) -> Response:
    fields = {name: [str(message) for message in messages] for name, messages in errors.items()}
    return _error(
        "validation_failed",
        "Please correct the highlighted fields.",
        status.HTTP_400_BAD_REQUEST,
        fields,
    )


def _draft_error(exc: DraftUpdateError) -> Response:
    if exc.field_errors:
        return _validation_failed(exc.field_errors)
    return _error(exc.code, exc.message, status.HTTP_400_BAD_REQUEST)


def _workspace_unavailable(exc: WorkspaceCoordinationError) -> Response:
    # Fail closed: never fall back to UNLOCKED workspace creation, and never
    # expose database or session-store details. Log only the exception type.
    cause = type(exc.__cause__).__name__ if exc.__cause__ else "unknown"
    logger.warning("design workspace coordination failed exception_type=%s", cause)
    return _error(
        "design_workspace_unavailable",
        "Designs are temporarily unavailable. Try again shortly.",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _parse_body(request) -> tuple[dict | list | None, Response | None]:
    try:
        return request.data, None
    except ParseError:
        return None, _error(
            "invalid_json", "The request body is not valid JSON.", status.HTTP_400_BAD_REQUEST
        )


def _draft_kwargs(validated: dict, *, include_title: bool) -> dict:
    """Only the draft fields actually present in the request become kwargs, so
    an omitted field stays UNSET (untouched) in the service."""
    kwargs: dict = {}
    if include_title and "title" in validated:
        kwargs["title"] = validated["title"]
    if "questionnaire_version_id" in validated:
        kwargs["questionnaire_version_id"] = str(validated["questionnaire_version_id"])
    if "answers" in validated:
        kwargs["answers"] = validated["answers"]
    if "inspiration_asset_ids" in validated:
        kwargs["inspiration_asset_ids"] = [str(a) for a in validated["inspiration_asset_ids"]]
    return kwargs


def _detail(design_id) -> dict:
    design = Design.objects.select_related("questionnaire_version").get(pk=design_id)
    return design_detail_payload(design)


@method_decorator(csrf_protect, name="dispatch")
class DesignListCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="designs_list",
        tags=_DESIGN_TAGS,
        responses={200: DesignListResponseSerializer},
        summary="List your designs",
        description=(
            "Returns the private designs owned by the current session or "
            "account as compact rows (no questionnaire schema, no inspiration "
            "records). A list request never creates a workspace. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request):
        # Listing never creates a workspace (accessible_designs resolves
        # with create=False); an anonymous browser that has not designed
        # anything gets an empty list and no database row.
        designs = accessible_designs(request)
        return Response(
            {"designs": [design_list_item_payload(design) for design in designs]},
            headers=NO_STORE,
        )

    @extend_schema(
        operation_id="designs_create",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        # JSON only — keep the contract honest (the view parses JSON only).
        request={"application/json": DesignWriteSerializer},
        responses={
            201: DesignDetailResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Design workspace temporarily unavailable."
            ),
        },
        summary="Create a design",
        description=(
            "Creates a private draft. Accepts optional title, questionnaire "
            "version, answers and inspiration selections; status is "
            "server-owned (draft). Answers and inspirations are validated "
            "authoritatively and roll back together on any failure. " + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request):
        body, parse_failure = _parse_body(request)
        if parse_failure is not None:
            return parse_failure
        serializer = DesignWriteSerializer(data=body)
        if not serializer.is_valid():
            return _validation_failed(serializer.errors)
        validated = serializer.validated_data

        try:
            # One coherent transaction: workspace resolution (which locks the
            # browser's django_session row), the design insert AND the draft
            # update commit together, so a failed answer/inspiration update
            # never leaves behind an empty workspace or a half-saved draft.
            with transaction.atomic():
                design_session = resolve_current_design_session(request, create=True)
                design = Design.objects.create(
                    design_session=design_session,
                    title=validated.get("title", ""),
                )
                draft_kwargs = _draft_kwargs(validated, include_title=False)
                if draft_kwargs:
                    update_design_draft(design, **draft_kwargs)
                payload = _detail(design.pk)
        except WorkspaceCoordinationError as exc:
            return _workspace_unavailable(exc)
        except QuestionnaireAnswerError as exc:
            return _validation_failed(exc.errors)
        except DraftUpdateError as exc:
            return _draft_error(exc)
        return Response(payload, status=status.HTTP_201_CREATED, headers=NO_STORE)


@method_decorator(csrf_protect, name="dispatch")
class DesignDetailView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    def _get_owned(self, request, design_id: str) -> Design | None:
        # Ownership filter FIRST, UUID lookup second — never the reverse.
        return accessible_designs(request).filter(pk=design_id).first()

    @extend_schema(
        operation_id="designs_retrieve",
        tags=_DESIGN_TAGS,
        responses={
            200: DesignDetailResponseSerializer,
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
        },
        summary="Retrieve a design",
        description=(
            "Returns the full draft: linked questionnaire (or null), answers "
            "and ordered inspiration selections with live availability. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request, design_id: str):
        design = self._get_owned(request, design_id)
        if design is None:
            return _not_found()
        return Response(design_detail_payload(design), headers=NO_STORE)

    @extend_schema(
        operation_id="designs_update",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        # JSON only — keep the contract honest (the view parses JSON only).
        request={"application/json": DesignWriteSerializer},
        responses={
            200: DesignDetailResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
        },
        summary="Update a design",
        description=(
            "Partial draft update: title, questionnaire version (assignable "
            "once), answers (draft-validated) and inspiration selections "
            "(replaced as one ordered set). " + _OWNERSHIP_NOTE
        ),
    )
    def patch(self, request, design_id: str):
        design = self._get_owned(request, design_id)
        if design is None:
            return _not_found()
        body, parse_failure = _parse_body(request)
        if parse_failure is not None:
            return parse_failure
        serializer = DesignWriteSerializer(data=body)
        if not serializer.is_valid():
            return _validation_failed(serializer.errors)
        draft_kwargs = _draft_kwargs(serializer.validated_data, include_title=True)
        if not draft_kwargs:
            # Nothing to change (empty patch): return the current state.
            return Response(design_detail_payload(design), headers=NO_STORE)
        try:
            update_design_draft(design, **draft_kwargs)
            payload = _detail(design.pk)
        except QuestionnaireAnswerError as exc:
            return _validation_failed(exc.errors)
        except DraftUpdateError as exc:
            return _draft_error(exc)
        return Response(payload, headers=NO_STORE)


@method_decorator(csrf_protect, name="dispatch")
class DesignValidateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="designs_validate",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request=None,
        responses={
            200: DesignValidationSuccessSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
        },
        summary="Validate a design draft",
        description=(
            "Performs NO generation. Re-checks the persisted draft with "
            "complete validation (every visible required question answered, "
            "minimum counts/lengths) and re-checks that every selected "
            'inspiration is still eligible. Returns {"valid": true} or a '
            "controlled 400 with question/selection errors. " + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request, design_id: str):
        # Ownership filter FIRST, UUID lookup second. No request body.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return _not_found()
        # One shared definition of completeness (see services) so the endpoint
        # and the generation pre-spend check can never drift.
        errors = design_completion_errors(design)
        if errors:
            return _validation_failed(errors)
        return Response({"valid": True}, headers=NO_STORE)
