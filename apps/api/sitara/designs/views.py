"""Design API (Phase 4): list, create, retrieve, update — private drafts only.

DRF views (so they can later join OpenAPI generation) with two deliberate
deviations from the project defaults:

- ``AllowAny`` — ownership is session-based and anonymous workspaces are a
  feature, so authentication cannot be the gate. The ownership filter in
  ``ownership.accessible_designs`` is the mandatory access control, and the
  global authenticated-by-default DRF setting stays untouched for everything
  else.
- ``@csrf_protect`` on dispatch — DRF's SessionAuthentication only enforces
  CSRF for already-authenticated requests, but these endpoints accept
  anonymous unsafe requests too. Django's decorator enforces the token on
  every POST/PATCH (safe methods are unaffected) and routes failures through
  the JSON CSRF_FAILURE_VIEW. Nothing here is csrf_exempt.

Inaccessible designs are 404, never 403: a 403 would confirm that a guessed
UUID exists. Every response carries ``Cache-Control: no-store``.
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

from sitara.schema import (
    CSRF_HEADER_PARAMETER,
    ErrorEnvelopeSerializer,
    ValidationErrorEnvelopeSerializer,
)

from .models import Design
from .openapi import DesignListResponseSerializer
from .ownership import accessible_designs
from .serializers import DesignReadSerializer, DesignWriteSerializer
from .services import WorkspaceCoordinationError, resolve_current_design_session

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


def _parse_body(request) -> tuple[dict | list | None, Response | None]:
    try:
        return request.data, None
    except ParseError:
        return None, _error(
            "invalid_json", "The request body is not valid JSON.", status.HTTP_400_BAD_REQUEST
        )


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
            "account. A list request never creates a workspace. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request):
        # Listing never creates a workspace (accessible_designs resolves
        # with create=False); an anonymous browser that has not designed
        # anything gets an empty list and no database row.
        designs = accessible_designs(request)
        return Response(
            {"designs": DesignReadSerializer(designs, many=True).data},
            headers=NO_STORE,
        )

    @extend_schema(
        operation_id="designs_create",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request=DesignWriteSerializer,
        responses={
            201: DesignReadSerializer,
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
            "Creates a private draft (title only; status and answers are "
            "server-owned). " + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request):
        body, parse_failure = _parse_body(request)
        if parse_failure is not None:
            return parse_failure
        serializer = DesignWriteSerializer(data=body)
        if not serializer.is_valid():
            return _validation_failed(serializer.errors)

        try:
            # One coherent transaction: workspace resolution (which locks
            # the browser's django_session row) and the design insert
            # commit together, so a failed insert never leaves behind an
            # empty workspace or a pointer to nothing.
            with transaction.atomic():
                design_session = resolve_current_design_session(request, create=True)
                design = Design.objects.create(
                    design_session=design_session,
                    title=serializer.validated_data.get("title", ""),
                    # status and answers are server-owned: draft and {}.
                )
        except WorkspaceCoordinationError as exc:
            # Fail closed: never fall back to UNLOCKED workspace creation,
            # and never expose database or session-store details. Log only
            # the underlying exception type.
            cause = type(exc.__cause__).__name__ if exc.__cause__ else "unknown"
            logger.warning("design workspace coordination failed exception_type=%s", cause)
            return _error(
                "design_workspace_unavailable",
                "Designs are temporarily unavailable. Try again shortly.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            DesignReadSerializer(design).data,
            status=status.HTTP_201_CREATED,
            headers=NO_STORE,
        )


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
            200: DesignReadSerializer,
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
        },
        summary="Retrieve a design",
        description=_OWNERSHIP_NOTE,
    )
    def get(self, request, design_id: str):
        design = self._get_owned(request, design_id)
        if design is None:
            return _not_found()
        return Response(DesignReadSerializer(design).data, headers=NO_STORE)

    @extend_schema(
        operation_id="designs_update",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request=DesignWriteSerializer,
        responses={
            200: DesignReadSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
        },
        summary="Update a design",
        description="Title-only update. " + _OWNERSHIP_NOTE,
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
        if "title" in serializer.validated_data:
            design.title = serializer.validated_data["title"]
            design.save(update_fields=["title", "updated_at"])
        return Response(DesignReadSerializer(design).data, headers=NO_STORE)
