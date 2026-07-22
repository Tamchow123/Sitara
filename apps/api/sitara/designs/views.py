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
import uuid

from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from sitara.generation.admission import (
    AdmissionControlUnavailable,
    GenerationLimitReached,
    LiveGenerationBudgetExhausted,
    LiveGenerationDisabled,
    daily_count_retry_after,
    enforce_live_admission,
)
from sitara.generation.cost_control import BudgetLedgerUnavailable, CountLimitReached
from sitara.generation.pipeline import (
    DesignAlreadyGenerated,
    DesignIncomplete,
    DesignNotGeneratable,
    DesignNotRefinable,
    GenerationInProgress,
    GenerationUnavailable,
    QueueUnavailable,
    enqueue_design_generation,
    enqueue_design_refinement,
)
from sitara.generation.refinement import (
    REFINEMENT_REQUEST_SCHEMA_VERSION,
    RefinementNoteUnsafe,
    RefinementRequestInvalid,
    normalise_refinement_request,
)
from sitara.generation.refinement_service import RefinementLimitReached, RefinementSourceUnavailable
from sitara.media.delivery import issue_design_image_urls
from sitara.media.exceptions import (
    DesignImageDeliveryUnavailable,
    DesignImageNotReady,
)
from sitara.questionnaire.answer_validation import QuestionnaireAnswerError
from sitara.schema import (
    CSRF_HEADER_PARAMETER,
    ErrorEnvelopeSerializer,
    ValidationErrorEnvelopeSerializer,
)

from .jobs import _iso, public_job_payload
from .models import Design, DesignVersion, GenerationAttempt
from .openapi import (
    DesignDetailResponseSerializer,
    DesignListResponseSerializer,
    DesignResultResponseSerializer,
    DesignValidationSuccessSerializer,
    DesignVersionImagesResponseSerializer,
    GenerationJobResponseSerializer,
)
from .ownership import accessible_designs, accessible_generation_attempts
from .result import (
    DesignResultNotReady,
    DesignResultUnavailable,
    design_result_payload,
    load_inspiration_acknowledgements,
    load_lineage,
    load_validated_design_spec,
)
from .serializers import (
    DesignWriteSerializer,
    RefinementWriteSerializer,
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


def _error(
    code: str,
    message: str,
    http_status: int,
    fields: dict | None = None,
    headers: dict | None = None,
) -> Response:
    body: dict = {"error": {"code": code, "message": message}}
    if fields:
        body["error"]["fields"] = fields
    return Response(body, status=http_status, headers=headers if headers is not None else NO_STORE)


def _not_found() -> Response:
    # One indistinguishable answer for nonexistent, other-session and
    # other-user designs.
    return _error("not_found", "Not found.", status.HTTP_404_NOT_FOUND)


def _generation_limit_response(retry_after: int) -> Response:
    return _error(
        "generation_limit_reached",
        "You have reached the generation limit for now. Please try again later.",
        status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Cache-Control": "no-store", "Retry-After": str(int(retry_after))},
    )


def _budget_exhausted_response() -> Response:
    return _error(
        "live_generation_budget_exhausted",
        "The daily limit for generating new concepts has been reached. "
        "Your design is saved — please try again later.",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _is_idempotent_replay(design, idempotency_key) -> bool:
    """A prior attempt already exists for this (design, key): the request is an
    idempotent replay that will produce no new attempt, spend or count slot, so
    it must not consume a session/IP throttle slot either — a legitimate client
    retry (which reuses the same key) should never throttle the honest user out."""
    return GenerationAttempt.objects.filter(design=design, idempotency_key=idempotency_key).exists()


def _enforce_admission(request, design, source_version_id=None) -> Response | None:
    """Run live admission AFTER ownership. Returns an error Response to send
    immediately, or None to proceed. Demo generation is admitted transparently.
    ``source_version_id`` is passed for a refinement so the mode gate resolves
    from the named source version, matching the refinement enqueue."""
    try:
        enforce_live_admission(request, design, source_version_id)
        return None
    except LiveGenerationDisabled:
        return _error(
            "live_generation_disabled",
            "Live concept generation is currently turned off.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except GenerationLimitReached as exc:
        return _generation_limit_response(exc.retry_after)
    except LiveGenerationBudgetExhausted:
        return _budget_exhausted_response()
    except AdmissionControlUnavailable:
        return _error(
            "generation_unavailable",
            "Generation is not currently available. Please try again shortly.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )


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
    # A design that is no longer draft-editable (generating, generated, or a
    # failed run that already linked a DesignVersion) is a state conflict, not
    # a validation error.
    if exc.code == "design_not_editable":
        return _error(exc.code, exc.message, status.HTTP_409_CONFLICT)
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
            409: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "design_not_editable: the design is no longer a draft "
                    "(generating, generated, or a failed run with a version)."
                ),
            ),
        },
        summary="Update a design",
        description=(
            "Partial draft update: title, questionnaire version (assignable "
            "once), answers (draft-validated) and inspiration selections "
            "(replaced as one ordered set). Only a draft — or a "
            "generation_failed design with no version, which returns to draft "
            "— may be edited. " + _OWNERSHIP_NOTE
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


_IDEMPOTENCY_KEY_PARAMETER = OpenApiParameter(
    name="Idempotency-Key",
    type=str,
    location=OpenApiParameter.HEADER,
    required=True,
    description=(
        "A client-generated UUID that makes the request idempotent PER DESIGN: "
        "repeating it returns the same job and queues no additional work."
    ),
)

_GENERATION_TAGS = ["Generation"]


def _read_idempotency_key(request) -> tuple[uuid.UUID | None, Response | None]:
    raw = request.headers.get("Idempotency-Key")
    if not raw:
        return None, _error(
            "invalid_idempotency_key",
            "A valid Idempotency-Key header (UUID) is required.",
            status.HTTP_400_BAD_REQUEST,
        )
    try:
        return uuid.UUID(str(raw)), None
    except (ValueError, AttributeError, TypeError):
        return None, _error(
            "invalid_idempotency_key",
            "A valid Idempotency-Key header (UUID) is required.",
            status.HTTP_400_BAD_REQUEST,
        )


@method_decorator(csrf_protect, name="dispatch")
class DesignGenerateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    # JSON only — a form/multipart submission is a 415, never parsed into an
    # empty body that would enqueue paid work outside the documented contract.
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="designs_generate",
        tags=_GENERATION_TAGS,
        parameters=[CSRF_HEADER_PARAMETER, _IDEMPOTENCY_KEY_PARAMETER],
        request=None,
        responses={
            202: GenerationJobResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            409: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "generation_in_progress / design_already_generated / design_not_generatable."
                ),
            ),
            429: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="generation_limit_reached (per-session/IP or global daily count).",
            ),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "generation_unavailable / queue_unavailable / live_generation_disabled / "
                    "live_generation_budget_exhausted."
                ),
            ),
        },
        summary="Start a design generation job",
        description=(
            "Enqueues one asynchronous generation job for a complete design and "
            "returns 202 with the public job payload and a same-origin Location "
            "header. Requires an Idempotency-Key UUID header; a repeated key "
            "returns the same job and queues no extra work. Accepts no body or "
            "exactly {}. " + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request, design_id: str):
        # Ownership filter FIRST, UUID lookup second — indistinguishable 404.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return _not_found()

        key, key_failure = _read_idempotency_key(request)
        if key_failure is not None:
            return key_failure

        # Accept EITHER a genuinely empty request body OR exactly the JSON
        # object {}. Anything else — including JSON null (which parses to
        # None), arrays and scalars — is rejected, so no out-of-contract shape
        # can enqueue paid work. The raw-body check runs BEFORE parsing so an
        # empty body never reaches the JSON parser.
        if request.body:
            body, parse_failure = _parse_body(request)
            if parse_failure is not None:
                return parse_failure
            if not isinstance(body, dict) or body != {}:
                return _error(
                    "validation_failed",
                    "This endpoint accepts no body or exactly {}.",
                    status.HTTP_400_BAD_REQUEST,
                )

        # Live admission AFTER ownership (an inaccessible design already 404'd
        # above): session/IP throttles, mode errors, budget preflight. Demo
        # bypasses all of it, and a known idempotent replay skips it entirely so
        # a legitimate retry never consumes a throttle slot.
        if not _is_idempotent_replay(design, key):
            admission_error = _enforce_admission(request, design)
            if admission_error is not None:
                return admission_error

        try:
            attempt, _created = enqueue_design_generation(design, idempotency_key=key)
        except CountLimitReached:
            return _generation_limit_response(daily_count_retry_after())
        except BudgetLedgerUnavailable:
            return _error(
                "generation_unavailable",
                "Generation is not currently available. Please try again shortly.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except DesignIncomplete as exc:
            return _validation_failed(exc.field_errors)
        except GenerationInProgress:
            return _error(
                "generation_in_progress",
                "A generation job is already in progress for this design.",
                status.HTTP_409_CONFLICT,
            )
        except DesignAlreadyGenerated:
            return _error(
                "design_already_generated",
                "This design has already been generated.",
                status.HTTP_409_CONFLICT,
            )
        except DesignNotGeneratable:
            return _error(
                "design_not_generatable",
                "This design cannot be generated.",
                status.HTTP_409_CONFLICT,
            )
        except GenerationUnavailable:
            return _error(
                "generation_unavailable",
                "Generation is not currently available.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except QueueUnavailable:
            return _error(
                "queue_unavailable",
                "The generation queue is temporarily unavailable. Try again shortly.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response = Response(
            public_job_payload(attempt), status=status.HTTP_202_ACCEPTED, headers=NO_STORE
        )
        # Same-origin relative Location — never the internal Django host.
        response["Location"] = f"/api/v1/jobs/{attempt.id}/"
        return response


# Signed image URLs are sensitive and NOT revocable before expiry — only the
# TTL ends them. Never let a cache retain them, and never leak them to a
# third party via the Referer header (see sitara.media.delivery for the full
# bearer-URL privacy model).
_IMAGE_HEADERS = {**NO_STORE, "Referrer-Policy": "no-referrer"}


class DesignVersionImagesView(APIView):
    """Short-lived signed image URLs for one owned DesignVersion (Phase 11).

    Ownership filtering runs BEFORE the design UUID lookup, and the version
    must belong to that owned design — an inaccessible or nonexistent design
    OR version is one indistinguishable 404, so a caller knowing only a
    DesignVersion UUID gains nothing. A failed GET never creates a workspace
    (accessible_designs resolves with create=False). The response exposes no
    prompt, DesignSpec, storage key, hash, provider/model/prediction id,
    seed, staging metadata or user/session identifier."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @staticmethod
    def _image_error(code: str, message: str, http_status: int) -> Response:
        # The shared _error helper with this endpoint's extended header set.
        return _error(code, message, http_status, headers=_IMAGE_HEADERS)

    @extend_schema(
        operation_id="designs_version_images_retrieve",
        tags=_DESIGN_TAGS,
        responses={
            200: DesignVersionImagesResponseSerializer,
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            409: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="design_image_not_ready: no permanent image has been ingested yet.",
            ),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="design_image_delivery_unavailable: not possible right now.",
            ),
        },
        summary="Get short-lived signed image URLs for a design version",
        description=(
            "Returns presigned GET URLs for the version's private original and "
            "thumbnail WebP images, plus their dimensions and one shared "
            "expiry. The URLs are temporary bearer URLs: anyone possessing one "
            "may use it until it expires, and logout or session rotation does "
            "not revoke it — they are short-lived and must never be stored. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request, design_id: str, version_id: str):
        # Ownership filter FIRST, UUID lookup second — indistinguishable 404,
        # and no workspace/session is ever created for a failed GET.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return self._image_error("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
        version = DesignVersion.objects.filter(design=design, pk=version_id).first()
        if version is None:
            return self._image_error("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
        try:
            issued = issue_design_image_urls(version)
        except DesignImageNotReady:
            return self._image_error(
                "design_image_not_ready",
                "This design version has no viewable image yet.",
                status.HTTP_409_CONFLICT,
            )
        except DesignImageDeliveryUnavailable as exc:
            # A storage/signing failure on the sole image-delivery path is an
            # operational incident: log the safe boundary signal (operation
            # name, row UUID, exception TYPE only — never a key, URL or raw
            # message), matching _workspace_unavailable's convention.
            cause = type(exc.__cause__).__name__ if exc.__cause__ else "unknown"
            logger.warning(
                "design image delivery unavailable design_version=%s exception_type=%s",
                version.pk,
                cause,
            )
            return self._image_error(
                "design_image_delivery_unavailable",
                "Design images are temporarily unavailable. Try again shortly.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                "images": {
                    "original": {
                        "url": issued.original_url,
                        "download_url": issued.original_download_url,
                        "width": version.image_width,
                        "height": version.image_height,
                    },
                    "thumbnail": {
                        "url": issued.thumbnail_url,
                        "width": version.thumbnail_width,
                        "height": version.thumbnail_height,
                    },
                    "expires_at": _iso(issued.expires_at),
                }
            },
            headers=_IMAGE_HEADERS,
        )


class DesignVersionResultView(APIView):
    """The private, curated concept result for one owned DesignVersion (Phase 12).

    Ownership filtering runs BEFORE the design UUID lookup, and the version
    must belong to that owned design — an inaccessible or nonexistent design
    OR version is one indistinguishable 404. A failed GET never creates a
    workspace. Before delivery the persisted DesignSpec is revalidated,
    safety-scanned and its schema version confirmed supported; corrupt,
    unsupported or unsafe content is a controlled 503, never a raw
    exception. This endpoint never issues an image URL — Phase 11's image
    endpoint remains the only signed-image URL issuer."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="designs_version_result_retrieve",
        tags=_DESIGN_TAGS,
        responses={
            200: DesignResultResponseSerializer,
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            409: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "design_result_not_ready: this design version has no complete result yet."
                ),
            ),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "design_result_unavailable: the stored content is corrupt, "
                    "unsupported or unsafe."
                ),
            ),
        },
        summary="Get the private concept result for a design version",
        description=(
            "Returns a purpose-built, curated result — title, concept summary "
            "and every DesignSpec section — revalidated and safety-scanned "
            "before delivery. Never exposes source_selections, questionnaire "
            "answers, the image prompt, provider/model/token provenance, "
            "storage keys, hashes or any signed URL. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request, design_id: str, version_id: str):
        # Ownership filter FIRST, UUID lookup second — indistinguishable 404,
        # and no workspace/session is ever created for a failed GET.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return _not_found()
        version = DesignVersion.objects.filter(design=design, pk=version_id).first()
        if version is None:
            return _not_found()
        try:
            spec = load_validated_design_spec(version)
            acknowledgements = load_inspiration_acknowledgements(version)
            lineage = load_lineage(version)
        except DesignResultNotReady:
            return _error(
                "design_result_not_ready",
                "This design version has no complete result yet.",
                status.HTTP_409_CONFLICT,
            )
        except DesignResultUnavailable as exc:
            # Safe boundary log: operation name, row UUID, exception TYPE
            # only — never the DesignSpec, title, narrative, prompt, answers,
            # storage keys, hashes or URLs.
            cause = type(exc.__cause__).__name__ if exc.__cause__ else "unknown"
            logger.warning(
                "design result unavailable design_version=%s exception_type=%s",
                version.pk,
                cause,
            )
            return _error(
                "design_result_unavailable",
                "This design's result is temporarily unavailable. Try again shortly.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            design_result_payload(version, spec, acknowledgements, lineage), headers=NO_STORE
        )


@method_decorator(csrf_protect, name="dispatch")
class DesignRefineView(APIView):
    """Start a single constrained refinement job for an owned Design
    (Phase 14). Mirrors ``DesignGenerateView``'s shape (ownership-first
    404, required Idempotency-Key header, 202 + Location) but requires a
    validated JSON body naming the source version and the one allowlisted
    change category."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="designs_refine",
        tags=_GENERATION_TAGS,
        parameters=[CSRF_HEADER_PARAMETER, _IDEMPOTENCY_KEY_PARAMETER],
        request={"application/json": RefinementWriteSerializer},
        responses={
            202: GenerationJobResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            409: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "refinement_limit_reached / refinement_in_progress / "
                    "refinement_source_unavailable / design_not_refinable."
                ),
            ),
            429: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="generation_limit_reached (per-session/IP or global daily count).",
            ),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "generation_unavailable / queue_unavailable / live_generation_disabled / "
                    "live_generation_budget_exhausted."
                ),
            ),
        },
        summary="Start a single constrained refinement job",
        description=(
            "Enqueues one asynchronous refinement job editing the design's "
            "existing version-1 concept and returns 202 with the public job "
            "payload and a same-origin Location header. Requires an "
            "Idempotency-Key UUID header; a repeated key returns the same job "
            "and queues no extra work. The body names the source version, one "
            "allowlisted change_type and an optional bounded note — the note "
            "is untrusted preference data, safety-scanned before any provider "
            "call, and never echoed back. " + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request, design_id: str):
        # Ownership filter FIRST, UUID lookup second — indistinguishable 404.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return _not_found()

        key, key_failure = _read_idempotency_key(request)
        if key_failure is not None:
            return key_failure

        body, parse_failure = _parse_body(request)
        if parse_failure is not None:
            return parse_failure
        serializer = RefinementWriteSerializer(data=body)
        if not serializer.is_valid():
            return _validation_failed(serializer.errors)
        validated = serializer.validated_data

        try:
            refinement_request = normalise_refinement_request(
                {
                    "schema_version": REFINEMENT_REQUEST_SCHEMA_VERSION,
                    "change_type": validated["change_type"],
                    "note": validated.get("note", ""),
                }
            )
        except (RefinementRequestInvalid, RefinementNoteUnsafe):
            return _error(
                "refinement_invalid",
                "Please correct the highlighted fields.",
                status.HTTP_400_BAD_REQUEST,
            )

        # Live admission AFTER ownership (and after cheap request validation, so
        # a malformed refinement is a 400, not a consumed throttle slot). Demo
        # refinement bypasses all live quotas; a known idempotent replay skips it
        # entirely. The mode gate resolves from the named source version, matching
        # the refinement enqueue.
        if not _is_idempotent_replay(design, key):
            admission_error = _enforce_admission(
                request, design, source_version_id=str(validated["source_version_id"])
            )
            if admission_error is not None:
                return admission_error

        try:
            attempt, _created = enqueue_design_refinement(
                design,
                source_version_id=str(validated["source_version_id"]),
                refinement_request=refinement_request,
                idempotency_key=key,
            )
        except CountLimitReached:
            return _generation_limit_response(daily_count_retry_after())
        except BudgetLedgerUnavailable:
            return _error(
                "generation_unavailable",
                "Generation is not currently available. Please try again shortly.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except DesignNotRefinable:
            return _error(
                "design_not_refinable",
                "This design cannot be refined.",
                status.HTTP_409_CONFLICT,
            )
        except RefinementSourceUnavailable:
            return _error(
                "refinement_source_unavailable",
                "The source version is not available for refinement.",
                status.HTTP_409_CONFLICT,
            )
        except GenerationInProgress:
            return _error(
                "refinement_in_progress",
                "A refinement job is already in progress for this design.",
                status.HTTP_409_CONFLICT,
            )
        except RefinementLimitReached:
            return _error(
                "refinement_limit_reached",
                "This design has already been refined.",
                status.HTTP_409_CONFLICT,
            )
        except GenerationUnavailable:
            return _error(
                "generation_unavailable",
                "Generation is not currently available.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except QueueUnavailable:
            return _error(
                "queue_unavailable",
                "The generation queue is temporarily unavailable. Try again shortly.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response = Response(
            public_job_payload(attempt), status=status.HTTP_202_ACCEPTED, headers=NO_STORE
        )
        response["Location"] = f"/api/v1/jobs/{attempt.id}/"
        return response


class GenerationJobView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="jobs_retrieve",
        tags=_GENERATION_TAGS,
        responses={
            200: GenerationJobResponseSerializer,
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
        },
        summary="Retrieve a generation job",
        description=(
            "Returns the public job payload (status, ids, timestamps, stable "
            "error code) for a job the caller owns. No prompt, DesignSpec, image "
            "URL or provider/storage provenance is ever exposed. Available even "
            "when live generation is currently disabled. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request, job_id: str):
        # Ownership filter FIRST (no workspace is created for an unknown
        # anonymous caller), UUID lookup second — indistinguishable 404.
        attempt = accessible_generation_attempts(request).filter(pk=job_id).first()
        if attempt is None:
            return _not_found()
        return Response(public_job_payload(attempt), headers=NO_STORE)
