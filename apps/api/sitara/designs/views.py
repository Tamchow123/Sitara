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

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ParseError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Design
from .ownership import accessible_designs
from .serializers import DesignReadSerializer, DesignWriteSerializer
from .services import resolve_current_design_session

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

    def get(self, request):
        # Listing never creates a workspace (accessible_designs resolves
        # with create=False); an anonymous browser that has not designed
        # anything gets an empty list and no database row.
        designs = accessible_designs(request)
        return Response(
            {"designs": DesignReadSerializer(designs, many=True).data},
            headers=NO_STORE,
        )

    def post(self, request):
        body, parse_failure = _parse_body(request)
        if parse_failure is not None:
            return parse_failure
        serializer = DesignWriteSerializer(data=body)
        if not serializer.is_valid():
            return _validation_failed(serializer.errors)

        design_session = resolve_current_design_session(request, create=True)
        design = Design.objects.create(
            design_session=design_session,
            title=serializer.validated_data.get("title", ""),
            # status and answers are server-owned: always draft, always {}.
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

    def get(self, request, design_id: str):
        design = self._get_owned(request, design_id)
        if design is None:
            return _not_found()
        return Response(DesignReadSerializer(design).data, headers=NO_STORE)

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
