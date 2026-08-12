"""Design API (Phase 7): list, create, retrieve, update, validate.

DRF views with two deliberate deviations from the project defaults, both
unchanged since Phase 4:

- ``AllowAny`` — ownership is session-based and anonymous workspaces are a
  feature, so authentication cannot be the gate. The ownership filter in
  ``ownership.accessible_designs`` is the mandatory access control.
- ``@csrf_protect`` on dispatch — DRF's SessionAuthentication only enforces
  CSRF for already-authenticated requests, but these endpoints accept
  anonymous unsafe requests too. Nothing here is csrf_exempt.

Phase 7 extends the draft with a linked questionnaire version and validated
answers. All answer validation and persistence is authoritative in
``services.update_design_draft`` (one atomic, row-locked transaction); views
stay thin. Inaccessible designs are 404, never 403. Every response carries
``Cache-Control: no-store``.

Phase 22 (ADR 0025) retired the curated inspiration catalogue, so a draft no
longer carries selectable references: the only references a design can gain
are the user's own uploads, through their own endpoint below.
"""

import logging
import uuid
from functools import wraps

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser, MultiPartParser
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
    REFINEMENT_CHANGE_TYPES,
    REFINEMENT_REQUEST_SCHEMA_VERSION,
    RefinementNoteUnsafe,
    RefinementRequestInvalid,
    normalise_refinement_request,
)
from sitara.generation.refinement_service import (
    RefinementCategoryUnavailable,
    RefinementLimitReached,
    RefinementSourceUnavailable,
)
from sitara.media.account_delivery import (
    AccountEmailDisabled,
    AccountEmailRecipientUnavailable,
    recipient_for,
    require_account_email_enabled,
    safe_stored_filename,
)
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

from .annotation_service import (
    AnnotationConflict,
    AnnotationDocumentInvalid,
    AnnotationImageNotReady,
    AnnotationVersionGone,
    annotation_payload,
    delete_annotation_document,
    read_annotation_document,
    replace_annotation_document,
)
from .grant_service import (
    GrantDesignFull,
    GrantMintingThrottled,
    GrantMintingUnavailable,
    GrantUnusable,
    create_reference_upload_grant,
    enforce_grant_upload_address_throttle,
    enforce_grant_upload_code_throttle,
    enforce_mint_throttle,
    record_grant_use,
    resolve_reference_upload_grant,
    revoke_reference_upload_grants,
)
from .jobs import _iso, public_job_payload
from .models import (
    Design,
    DesignInspirationUpload,
    DesignRenderDelivery,
    DesignVersion,
    GenerationAttempt,
)
from .openapi import (
    AnnotationConflictErrorSerializer,
    AnnotationDocumentResponseSerializer,
    AnnotationDocumentWriteSerializer,
    DesignDetailResponseSerializer,
    DesignListResponseSerializer,
    DesignReferencesResponseSerializer,
    DesignResultResponseSerializer,
    DesignValidationSuccessSerializer,
    DesignVersionImagesResponseSerializer,
    GenerationJobResponseSerializer,
    GrantUploadResponseSerializer,
    GrantUploadWriteSerializer,
    InspirationUploadResponseSerializer,
    InspirationUploadWriteSerializer,
    ReferenceUploadGrantResponseSerializer,
    ReferenceUploadGrantRevokedSerializer,
    RenderSendResponseSerializer,
    RenderSendStateResponseSerializer,
    WalkInSessionEndedSerializer,
)
from .ownership import accessible_designs, accessible_generation_attempts
from .render_delivery import (
    RenderDeliveryThrottled,
    RenderDeliveryThrottleUnavailable,
    RenderNotReady,
    SendLimitReached,
    enforce_send_throttles,
    owner_of,
    remembered_filename,
    require_render_ready,
    reserve_send,
    send_allowance,
)
from .result import (
    DesignResultNotReady,
    DesignResultUnavailable,
    design_result_payload,
    load_inspiration_acknowledgements,
    load_lineage,
    load_validated_design_spec,
)
from .serializers import (
    GALLERY_OFFSET_MAX,
    GALLERY_PAGE_SIZE_DEFAULT,
    GALLERY_PAGE_SIZE_MAX,
    DesignWriteSerializer,
    RefinementWriteSerializer,
    RenderSendSerializer,
    design_detail_payload,
    design_list_item_payload,
    inspiration_upload_payload,
    inspiration_uploads_payload,
)
from .services import (
    DraftUpdateError,
    WorkspaceCoordinationError,
    design_completion_errors,
    end_walk_in_session,
    resolve_current_design_session,
    update_design_draft,
)
from .tasks import send_design_render
from .upload_service import (
    InspirationUploadError,
    InspirationUploadThrottled,
    create_inspiration_upload,
    delete_inspiration_upload,
    enforce_upload_throttle,
    reject_oversized_body,
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


def _reference_upload_unavailable() -> Response:
    """The ONE answer a phone gets for a code it cannot use.

    Expired, revoked, spent and never-existed all land here with the same
    status, the same code and the same body. Nothing above may branch on which
    it was and nothing may log which it was: telling the four apart is exactly
    how a guessed code would become a way to discover that a design exists."""
    return _error(
        "reference_upload_unavailable",
        "This link is no longer usable. Please ask for a new code.",
        status.HTTP_404_NOT_FOUND,
    )


def _grant_upload_throttled(retry_after: int) -> Response:
    return _error(
        "reference_upload_rate_limited",
        "Too many attempts for now. Please try again shortly.",
        status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Cache-Control": "no-store", "Retry-After": str(int(retry_after))},
    )


def _grant_upload_unavailable() -> Response:
    return _error(
        "reference_upload_throttle_unavailable",
        "Adding photographs is temporarily unavailable. Please try again shortly.",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )


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


def _upload_error(exc: InspirationUploadError) -> Response:
    """One place mapping an upload service code onto an HTTP status.

    Every message is the service's own generic sentence — never a filename,
    storage key or exception text."""
    http_status = {
        "rights_not_acknowledged": status.HTTP_400_BAD_REQUEST,
        "invalid_image": status.HTTP_400_BAD_REQUEST,
        "duplicate_image": status.HTTP_409_CONFLICT,
        "inspiration_limit_reached": status.HTTP_409_CONFLICT,
        "storage_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "upload_throttle_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "upload_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    }.get(exc.code, status.HTTP_400_BAD_REQUEST)
    return _error(exc.code, exc.message, http_status)


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
    """The decoded JSON body, or a controlled 400.

    ``RecursionError`` alongside ``ParseError`` because a pathologically nested
    body defeats a byte ceiling. CPython's JSON scanner recurses once per nesting
    level and raises ``RecursionError`` — a ``RuntimeError``, so it slips past
    DRF's own ``except ValueError`` and escaped as a 500. Measured: about 40KB of
    ``[[[[…]]]]`` was enough, on every endpoint that parses a JSON body. The stack
    has fully unwound to this frame by the time it is caught here, so there is
    headroom to build the response."""
    try:
        return request.data, None
    except (ParseError, RecursionError):
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
    return kwargs


def _detail(design_id) -> dict:
    design = Design.objects.select_related("questionnaire_version").get(pk=design_id)
    return design_detail_payload(design)


def _designs_with_a_concept(owned):
    """``owned`` narrowed to designs that actually produced a concept.

    "Produced a concept" means the same thing the gallery card means by it: at
    least one version whose permanent image has landed. That deliberately excludes
    three kinds of row a person would not call a concept — a questionnaire still
    being answered, a generation still running, and one that failed — so the
    account gallery shows work, not work-in-progress.

    ``Exists`` rather than a join with ``.distinct()``: a design with an original
    and a refinement matches the join twice, and de-duplicating afterwards would
    make ``count()`` and the slice disagree about how many rows there are. A
    subquery asks the only question that matters — is there one? — and cannot
    multiply rows.

    Both keys are checked because the ingest service writes them together, so
    either one missing means there is nothing to show; this is the same condition
    ``_version_row_payload`` reports as ``has_image``, kept identical on purpose
    so a listed design always has a picture for its card."""
    return owned.filter(
        Exists(
            DesignVersion.objects.filter(design=OuterRef("pk"))
            .exclude(image_storage_key="")
            .exclude(thumbnail_storage_key="")
        )
    )


def _read_gallery_page(request) -> tuple[tuple[int, int, bool], Response | None]:
    """``(limit, offset, generated_only)`` for the design list, or a controlled 400.

    Refused rather than clamped when the value is not a non-negative integer, so a
    client with a bug is told about it instead of silently getting page one — the
    same reason every write endpoint here rejects an unknown field. An oversized
    ``limit`` IS clamped, because asking for more than the ceiling is a reasonable
    request that has a correct answer; asking with ``limit=banana`` is not. An
    ``offset`` past ``GALLERY_OFFSET_MAX`` is refused for the same reason as
    ``banana`` rather than clamped like an oversized ``limit``: no row lives there,
    so there is nothing correct to return — and an unbounded one would reach
    PostgreSQL as an out-of-range SQL literal.

    ``generated`` is a separate question from paging and is parsed the same strict
    way: exactly ``true`` or ``false``, with anything else refused rather than
    treated as false. Defaulting to false keeps this endpoint's existing contract —
    it lists designs, and a draft is a design — while letting the account gallery
    ask for the narrower thing it actually shows.

    Reads ``request.query_params``, never a body: this is a safe method."""
    raw_limit = request.query_params.get("limit")
    raw_offset = request.query_params.get("offset")
    raw_generated = request.query_params.get("generated")
    values: dict[str, int] = {}
    errors: dict[str, list[str]] = {}
    for name, raw, default in (
        ("limit", raw_limit, GALLERY_PAGE_SIZE_DEFAULT),
        ("offset", raw_offset, 0),
    ):
        if raw is None:
            values[name] = default
            continue
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            errors[name] = ["This must be a whole number."]
            continue
        if parsed < 0:
            errors[name] = ["This cannot be negative."]
            continue
        # Refused, not clamped: unlike an oversized ``limit``, an offset this far
        # out cannot name a row that exists, so there is no correct answer to
        # give it. It has to be caught here rather than left to the slice, because
        # the offset reaches PostgreSQL as a SQL literal and one past bigint raises
        # a DataError that DRF does not wrap — an HTML 500 instead of JSON.
        if name == "offset" and parsed > GALLERY_OFFSET_MAX:
            errors[name] = ["This is beyond the largest page that can be requested."]
            continue
        values[name] = parsed
    # Not `raw_generated == "true"`: that would silently read "1", "yes" and
    # "banana" all as false, so a client asking the wrong way would be told its
    # drafts are concepts rather than told it asked wrongly.
    generated_only = False
    if raw_generated is not None:
        if raw_generated not in ("true", "false"):
            errors["generated"] = ['This must be "true" or "false".']
        else:
            generated_only = raw_generated == "true"
    if errors:
        return (GALLERY_PAGE_SIZE_DEFAULT, 0, False), _validation_failed(errors)
    # A limit of 0 would mean "give me nothing", which no caller wants and which
    # makes an empty page indistinguishable from the end of the list.
    limit = min(values["limit"], GALLERY_PAGE_SIZE_MAX) or GALLERY_PAGE_SIZE_DEFAULT
    return (limit, values["offset"], generated_only), None


@method_decorator(csrf_protect, name="dispatch")
class DesignListCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="designs_list",
        tags=_DESIGN_TAGS,
        parameters=[
            OpenApiParameter(
                name="limit",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    f"How many designs to return, newest first. Defaults to "
                    f"{GALLERY_PAGE_SIZE_DEFAULT} and is capped at {GALLERY_PAGE_SIZE_MAX}."
                ),
            ),
            OpenApiParameter(
                name="offset",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    f"How many designs to skip, for paging through the gallery. "
                    f"Must be between 0 and {GALLERY_OFFSET_MAX}."
                ),
            ),
            OpenApiParameter(
                name="generated",
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    'Pass "true" to return only designs that actually produced a '
                    "concept — at least one version whose image has landed. Excludes "
                    "a questionnaire still being answered, a generation still "
                    "running, and one that failed. Defaults to false, which returns "
                    'every design the caller owns. Anything other than "true" or '
                    '"false" is refused rather than read as false.'
                ),
            ),
        ],
        responses={
            200: DesignListResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "gallery_disabled: the operator has not enabled the account "
                    "concept gallery (ADR 0027). A controlled refusal, not a 404 — "
                    "the surface exists and is switched off."
                ),
            ),
        },
        summary="List your designs",
        description=(
            "Returns the private designs owned by the current session or "
            "account as compact rows (no questionnaire schema, no inspiration "
            "records, no job snapshot), newest first, each with its versions in "
            "creation order. Carries no signed image URL — a gallery mints one "
            "per card through the ownership-checked images endpoint. Bounded page "
            "size. A list request never creates a workspace. Requires "
            "ACCOUNT_GALLERY_ENABLED. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request):
        # Reading the whole list is the gallery, and the gallery is gated
        # (ADR 0027). Refused BEFORE the page parameters are read and before
        # any ownership query runs, so a disabled deployment answers a
        # signed-in caller and a stranger identically and does no work.
        #
        # 503 with a stable code, exactly as `email_delivery_disabled` does it:
        # a 404 would say the surface was never there, and it was — an operator
        # can switch it back on. Only GET is gated; POST still creates designs,
        # because the walk-in flow starts there and gating it would end the
        # product rather than the gallery.
        if not settings.ACCOUNT_GALLERY_ENABLED:
            return _error(
                "gallery_disabled",
                "Browsing past concepts is not available at the moment.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        page, page_failure = _read_gallery_page(request)
        if page_failure is not None:
            return page_failure
        limit, offset, generated_only = page

        # Listing never creates a workspace (accessible_designs resolves
        # with create=False); an anonymous browser that has not designed
        # anything gets an empty list and no database row.
        owned = accessible_designs(request)
        # Narrowed BEFORE the count, so `total` reports the number of rows this
        # caller can actually page through. Filtering after the slice would make
        # the count describe a different set from the one returned, and the
        # gallery's "showing your N most recent of M" would be a lie.
        if generated_only:
            owned = _designs_with_a_concept(owned)
        # Counted before slicing, so a caller can tell "that is all of them" from
        # "there is another page" without asking for one.
        total = owned.count()
        # Both levels prefetched together: without the second, every version in
        # every card would ask for its own attempt, and the query count would grow
        # with the number of concepts someone owns rather than staying flat.
        designs = owned.prefetch_related("versions", "versions__generation_attempts")[
            offset : offset + limit
        ]
        return Response(
            {
                "designs": [design_list_item_payload(design) for design in designs],
                "total": total,
                "limit": limit,
                "offset": offset,
            },
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
            "version and answers; status is server-owned (draft). Answers are "
            "validated authoritatively and roll back with the insert on any "
            "failure. " + _OWNERSHIP_NOTE
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
            # update commit together, so a failed answer update never leaves
            # behind an empty workspace or a half-saved draft.
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
            "Returns the full draft: linked questionnaire (or null), answers, "
            "the design's own uploaded references and any historical curated "
            "selection (always reported unavailable since the catalogue was "
            "retired). " + _OWNERSHIP_NOTE
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
            "once) and answers (draft-validated). Only a draft — or a "
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

_ACCOUNT_REQUIRED_RESPONSE = OpenApiResponse(
    ErrorEnvelopeSerializer,
    description=(
        "authentication_required — producing a concept needs an account (ADR "
        "0023). Answering the questionnaire, saving a draft and uploading "
        "references do NOT; only this last step does. Never a redirect: route to "
        "your own sign-in screen on seeing this code, then repeat the request. "
        "The draft and its answers are untouched and still there afterwards."
    ),
)


def _require_account(request) -> Response | None:
    """Refuse an anonymous caller, or None to continue (Phase 21, ADR 0023).

    Producing a concept is the one action in this API that requires an account.
    Everything before it does not: a visitor answers the whole questionnaire,
    picks colours, uploads references and saves a draft without signing in, and
    ADR 0004's anonymous ownership continues to govern all of that. Only the last
    step — the one that costs a provider call and produces something worth keeping
    — needs somewhere durable to keep it.

    Checked BEFORE ownership deliberately, which is the opposite of the usual
    order in this module. Everywhere else the ownership filter runs first so that
    a foreign UUID is indistinguishable from a nonexistent one; here the refusal
    is about the CALLER's capability and says nothing whatever about the
    resource, so answering it first leaks strictly less than a 404 would — and
    spares an anonymous visitor a "not found" for a design they are looking at.

    A 401 with a stable code, never a redirect: an API that redirects a JSON
    request to a login page produces an HTML body a client cannot read. The
    frontend routes to /login itself on seeing this code."""
    if request.user.is_authenticated:
        return None
    return _error(
        "authentication_required",
        "Sign in or create an account to generate your concept. Your answers are saved.",
        status.HTTP_401_UNAUTHORIZED,
    )


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
            401: _ACCOUNT_REQUIRED_RESPONSE,
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
        # An account first (ADR 0023) — before ownership, and see _require_account
        # for why that order is the safer one here rather than the usual reverse.
        account_failure = _require_account(request)
        if account_failure is not None:
            return account_failure

        # Ownership filter FIRST, UUID lookup second — indistinguishable 404.
        # This is also where a just-signed-in visitor's anonymous workspace is
        # claimed, so someone who answered everything anonymously and then signed
        # in finds their own design here. If the claim could not happen — their
        # browser session pointed at a workspace another user already owns, which
        # ADR 0004 deliberately never transfers — this is a plain not_found. The
        # honest answer is that we cannot see that design, NEVER that generation
        # failed.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return _not_found()

        key, key_failure = _read_idempotency_key(request)
        if key_failure is not None:
            return key_failure

        # The content type is checked here rather than left entirely to DRF's
        # parser negotiation, because a multipart body never reaches DRF intact:
        # Django's CSRF check reads request.POST looking for csrfmiddlewaretoken,
        # and the upload parser consumes the stream WITHOUT caching it. DRF then
        # sees an empty request — its Request._parse catches the resulting
        # RawPostDataException and, since this view lists no form parser, hands
        # back empty data rather than raising — so a form submission would be
        # read as the empty body this endpoint accepts, and would enqueue paid
        # work. That is the reason for the check. (This view ALSO used to read
        # request.body directly, which is not protected by DRF's rescue and
        # escaped as an unhandled 500; that read is gone.)
        #
        # Exact match on the normalised main type, not a prefix: media types are
        # case-insensitive per RFC 7231, and a startswith test would also admit
        # application/json-patch+json, leaving the unrelated exact-{} body check
        # as the only thing refusing it. The parameters are split off here rather
        # than trusted to Django — `request.content_type` keeps them, so
        # "application/json; charset=utf-8" arrives with the charset attached
        # (measured; an earlier version of this comment claimed otherwise and the
        # test below is what corrected it).
        media_type = (request.content_type or "").split(";")[0].strip().lower()
        if media_type not in {"", "application/json"}:
            return _error(
                "unsupported_media_type",
                "This endpoint accepts application/json only.",
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        # Accept EITHER a genuinely empty request body OR exactly the JSON
        # object {}. Anything else — including JSON null (which parses to
        # None), arrays and scalars — is rejected, so no out-of-contract shape
        # can enqueue paid work. DRF represents an empty body as an empty
        # QueryDict, which is dict-like and compares equal to {}, so both
        # accepted shapes fall out of the one check below.
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
class DesignVersionAnnotationsView(APIView):
    """The owner's private editorial overlay for one owned DesignVersion (Phase 19).

    Ownership filtering runs BEFORE the design UUID lookup, and the version must
    belong to that owned design — an inaccessible or nonexistent design OR
    version is one indistinguishable 404, so a caller knowing only a
    DesignVersion UUID gains nothing.

    ``csrf_protect`` on dispatch, not DRF's SessionAuthentication alone: these
    endpoints accept anonymous unsafe requests (an anonymous browser workspace is
    a feature), and SessionAuthentication only enforces CSRF for already
    authenticated callers.

    Nothing here touches the generated image. DELETE clears the overlay only, and
    no response exposes a storage key, hash, user id, DesignSession id or signed
    URL."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    def _get_owned_version(self, request, design_id: str, version_id: str):
        # Ownership filter FIRST, UUID lookup second — never the reverse.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return None
        return DesignVersion.objects.filter(design=design, pk=version_id).first()

    @staticmethod
    def _not_ready() -> Response:
        return _error(
            "design_image_not_ready",
            "This design version has no viewable image yet.",
            status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _conflict(current_revision: int) -> Response:
        # Only the code, a safe message and the server's current revision. The
        # stored document is never echoed back — a conflict must not become a
        # channel for reading private note text.
        return Response(
            {
                "error": {
                    "code": "annotation_conflict",
                    "message": (
                        "These annotations changed since your last save. "
                        "Reload to see the latest marks."
                    ),
                },
                "revision": current_revision,
            },
            status=status.HTTP_409_CONFLICT,
            headers=NO_STORE,
        )

    @extend_schema(
        operation_id="designs_version_annotations_retrieve",
        tags=_DESIGN_TAGS,
        responses={
            200: AnnotationDocumentResponseSerializer,
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            409: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="design_image_not_ready: no permanent image has been ingested yet.",
            ),
        },
        summary="Get your private annotations for a design version",
        description=(
            "Returns the owner's annotation overlay. Before anything has been "
            "saved this is a 200 with an empty item list and revision 0 — not a "
            "404 — so revision 0 is the unambiguous never-saved signal and the "
            "client needs no separate branch. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request, design_id: str, version_id: str):
        version = self._get_owned_version(request, design_id, version_id)
        if version is None:
            return _not_found()
        try:
            document, revision, updated_at = read_annotation_document(version)
        except AnnotationImageNotReady:
            return self._not_ready()
        return Response(annotation_payload(document, revision, updated_at), headers=NO_STORE)

    @extend_schema(
        operation_id="designs_version_annotations_replace",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request={"application/json": AnnotationDocumentWriteSerializer},
        responses={
            200: AnnotationDocumentResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            409: OpenApiResponse(
                AnnotationConflictErrorSerializer,
                description=(
                    "annotation_conflict: expected_revision is stale, and the stored "
                    "document is unchanged. The body carries the server's current "
                    "revision and never the document itself. Or design_image_not_ready."
                ),
            ),
        },
        summary="Replace your private annotations for a design version",
        description=(
            "Replaces the COMPLETE overlay atomically — there is no partial "
            "update, so a removed mark is unambiguous. Send the revision you hold "
            "as expected_revision (0 to create); a stale value is refused and "
            "nothing is overwritten. A successful write returns 200 with the "
            "stored document and its incremented revision. The revision counts "
            "writes, not content changes, so replaying identical content with the "
            "current revision still increments. " + _OWNERSHIP_NOTE
        ),
    )
    def put(self, request, design_id: str, version_id: str):
        version = self._get_owned_version(request, design_id, version_id)
        if version is None:
            return _not_found()
        body, parse_failure = _parse_body(request)
        if parse_failure is not None:
            return parse_failure
        if not isinstance(body, dict):
            return _error(
                "annotation_invalid",
                "The annotation document is not valid.",
                status.HTTP_400_BAD_REQUEST,
            )
        # expected_revision is a transport concern, not part of the stored
        # document, so it is split off before the document is validated.
        payload = {key: value for key, value in body.items() if key != "expected_revision"}
        expected = body.get("expected_revision")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            return _error(
                "annotation_invalid",
                "A valid expected_revision is required.",
                status.HTTP_400_BAD_REQUEST,
            )
        try:
            document, revision, updated_at = replace_annotation_document(
                version, payload, expected_revision=expected
            )
        except AnnotationImageNotReady:
            return self._not_ready()
        except AnnotationVersionGone:
            # Purged between the ownership lookup and the lock. The same 404 a
            # fresh request would get, so the answer stays consistent.
            return _not_found()
        except AnnotationConflict as exc:
            return self._conflict(exc.current_revision)
        except AnnotationDocumentInvalid as exc:
            # The service's message is already generic; it never quotes the
            # rejected note back.
            return _error(exc.code, exc.message, status.HTTP_400_BAD_REQUEST)
        return Response(annotation_payload(document, revision, updated_at), headers=NO_STORE)

    @extend_schema(
        operation_id="designs_version_annotations_delete",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request=None,
        responses={
            204: OpenApiResponse(description="Cleared."),
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            409: OpenApiResponse(ErrorEnvelopeSerializer, description="design_image_not_ready."),
        },
        summary="Clear your private annotations for a design version",
        description=(
            "Removes the overlay document. The generated image is never touched. "
            "Idempotent: clearing when nothing is stored still succeeds. " + _OWNERSHIP_NOTE
        ),
    )
    def delete(self, request, design_id: str, version_id: str):
        version = self._get_owned_version(request, design_id, version_id)
        if version is None:
            return _not_found()
        try:
            delete_annotation_document(version)
        except AnnotationImageNotReady:
            return self._not_ready()
        except AnnotationVersionGone:
            return _not_found()
        return Response(status=status.HTTP_204_NO_CONTENT, headers=NO_STORE)


@method_decorator(csrf_protect, name="dispatch")
class _DesignVersionSendView(APIView):
    """Queue an email of one owned version's render to the owner (Phase 19, §8).

    Two concrete endpoints subclass this — the concept screen sends the plain
    canonical render, the annotation workspace sends the annotated composite —
    differing only in ``kind``. Email replaces download entirely; removing the
    download button is a product decision, **not** a privacy control, since the
    image renders in the browser and can always be saved.

    **The recipient is never accepted from the caller, in any field, ever.** It
    is ``request.user.email``, read server-side, and the task re-derives it
    independently from the design's own session. An endpoint that mails an
    attachment to a caller-chosen address is an open relay.

    Until Phase 21 that guarantee was structural — the view had no request body
    at all, so there was nothing an address could arrive in. It now takes exactly
    one optional field, ``filename``, so the guarantee is an explicit and tested
    one instead: ``RenderSendSerializer`` rejects every other key, and a request
    naming ``email``/``to``/``cc``/``bcc`` fails whole rather than succeeding
    partially with the forbidden field quietly dropped. Silently ignoring such a
    field would teach a client it worked.

    Order matters and is fixed: CSRF, then ownership, then the body, then the
    gate, then readiness, then the allowance, then the throttles. The body is
    read early because a malformed request should be answered as one rather than
    being masked by a capability refusal — it reserves nothing and sends nothing.
    Throttling last means a cross-origin page cannot burn a victim's quota, and a
    throttled caller still cannot distinguish an owned design from one that never
    existed.

    ``GET`` reports the same view's send state (allowance used and the name to
    pre-fill) so the client can show what is left before the last send is spent.

    The response carries no address. The client already knows the account's own
    address from ``/auth/me`` and uses that for its confirmation copy — echoing
    it here would put it in a response body for no benefit."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    # JSON only. The documented contract is a JSON object, and leaving the form
    # parsers enabled would give the one caller-influenced value in the delivery
    # path a second, undocumented way in.
    parser_classes = [JSONParser]

    #: Set by each concrete subclass.
    kind: str = ""

    def _get_owned_version(self, request, design_id: str, version_id: str):
        # Ownership filter FIRST, UUID lookup second — never the reverse.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return None
        return DesignVersion.objects.filter(design=design, pk=version_id).first()

    @staticmethod
    def _limit_reached(used: int, limit: int) -> Response:
        """The lifetime allowance for this render is spent.

        409, not 429. A rate limit says "not now" and carries a ``Retry-After``;
        this says "not again", and a recovery window would be a lie. The message
        names the numbers rather than being generic, because a ceiling the user
        cannot see coming is one they experience as a bug."""
        return _error(
            "send_limit_reached",
            f"You have already emailed this concept {used} times, "
            f"which is the maximum of {limit}.",
            status.HTTP_409_CONFLICT,
        )

    def get(self, request, design_id: str, version_id: str):
        """What the owner needs before they press Send: how many of this render's
        sends are left, and what to pre-fill the name field with.

        The only endpoint that returns the remembered filename, and only to the
        owner of that design — ownership filtering runs before the lookup, so a
        foreign or nonexistent design is the same 404 as everywhere else.
        ``no-store`` because the payload carries the owner's own free text.

        Identity-free it is not, so it does not opt out of authentication; but it
        is a safe method and creates nothing."""
        version = self._get_owned_version(request, design_id, version_id)
        if version is None:
            return _not_found()

        used, limit = send_allowance(version, self.kind)
        return Response(
            {
                "send": {
                    "used": used,
                    "limit": limit,
                    "suggested_filename": self._suggested_filename(version),
                }
            },
            headers=NO_STORE,
        )

    def _suggested_filename(self, version) -> str:
        """The name to pre-fill, in order of preference.

        The owner's own last choice for this exact render; failing that, the
        design's title, sanitised and truncated by the same rules the attachment
        will use. **Never a note.** A note is the most personal free text in the
        product — it says what someone dislikes about a garment they intend to
        wear — and turning it into a default filename would put it in a message
        header (CLAUDE.md §7)."""
        remembered = remembered_filename(version, self.kind)
        if remembered:
            return remembered
        return safe_stored_filename(version.design.title)

    def _read_requested_name(self, request) -> tuple[str, Response | None]:
        """The caller's chosen name, or the 400 that refuses it.

        Accepts a genuinely empty body as well as ``{}``, because the client sent
        no body at all before this field existed and a stored client should not
        break on an upgrade. DRF gives an empty body an empty ``QueryDict``, which
        is dict-like and carries no fields, so no special case is needed — and
        deliberately none is written: probing ``request.body`` first would raise
        ``RawPostDataException`` on any request whose stream the CSRF middleware
        has already read.

        Note WHY ``request.data`` is safe here where ``request.body`` was not,
        because it is not obvious and has been misread: DRF's ``Request._parse``
        catches ``RawPostDataException`` itself, and for a view that lists no form
        parser it returns empty data instead of raising. So a multipart body
        reaches this method as an empty mapping — measured, not assumed — and the
        chosen name is simply absent rather than crashing the request. A
        ``request.body`` read has no such rescue, which is why the generate
        endpoint's own probe of it produced an unhandled 500 until Phase 21."""
        body, parse_failure = _parse_body(request)
        if parse_failure is not None:
            return "", parse_failure
        serializer = RenderSendSerializer(data=body)
        if not serializer.is_valid():
            if set(serializer.errors) == {"filename"}:
                # The name itself was refused, which is the stylist's to correct.
                # Distinct from a client sending a field it has no business
                # sending, which is a defect in the client.
                return "", _error(
                    "filename_invalid",
                    " ".join(str(message) for message in serializer.errors["filename"]),
                    status.HTTP_400_BAD_REQUEST,
                )
            return "", _validation_failed(serializer.errors)
        return serializer.validated_data.get("filename", ""), None

    def post(self, request, design_id: str, version_id: str):
        version = self._get_owned_version(request, design_id, version_id)
        if version is None:
            return _not_found()

        # Read BEFORE the gate and the throttles, so a malformed body is answered
        # as a malformed body rather than costing quota or being masked by a
        # capability refusal. It reserves nothing and sends nothing.
        requested_name, name_failure = self._read_requested_name(request)
        if name_failure is not None:
            return name_failure

        try:
            require_account_email_enabled()
        except AccountEmailDisabled:
            return _error(
                "email_delivery_disabled",
                "Emailing your concept is not available at the moment.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            require_render_ready(version)
        except RenderNotReady:
            return _error(
                "design_image_not_ready",
                "This design version has no viewable image yet.",
                status.HTTP_409_CONFLICT,
            )

        try:
            # The owning account's address, from the row. An anonymous workspace
            # owns designs but has no account, and there is deliberately no
            # fallback: no prompt for an address, no silent success.
            recipient = recipient_for(owner_of(version))
        except AccountEmailRecipientUnavailable:
            return _error(
                "email_recipient_unavailable",
                "Sign in to have your concept emailed to your account address.",
                status.HTTP_409_CONFLICT,
            )

        used, limit = send_allowance(version, self.kind)
        if used >= limit:
            # The lifetime allowance for this exact render is spent. Refused
            # BEFORE the throttles so pressing Send on a finished render cannot
            # exhaust the quota a genuinely new one needs — the same reasoning
            # the old already-sent short-circuit carried. An unlocked read is
            # enough here because reserve_send re-checks under the row lock; this
            # only decides whether to charge quota for work that will not happen.
            return self._limit_reached(used, limit)

        try:
            enforce_send_throttles(request, recipient)
        except RenderDeliveryThrottled as exc:
            return _error(
                "email_send_limit_reached",
                "You have sent this many concepts for now. Please try again later.",
                status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Cache-Control": "no-store", "Retry-After": str(exc.retry_after)},
            )
        except RenderDeliveryThrottleUnavailable:
            # An infrastructure fault is not the caller's abuse, so this is a
            # 503 rather than the 429 — and carries no Retry-After, because no
            # recovery window is known.
            return _error(
                "email_send_unavailable",
                "Emailing your concept is temporarily unavailable. Please try again later.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            # Authoritative, under the row lock: two concurrent last-allowance
            # requests cannot both pass here. Committed before the enqueue below,
            # so the task can never run ahead of its own reservation.
            epoch = reserve_send(version, self.kind, requested_name=requested_name)
        except SendLimitReached as exc:
            return self._limit_reached(exc.used, exc.limit)

        if epoch is None:
            # A send for this render is already in flight, or the version was
            # purged under us. Nothing new to queue. Answered exactly as a fresh
            # reservation is, because the previous behaviour was to enqueue a
            # second task the claim then refused — indistinguishable from here,
            # and queueing work known to no-op is strictly worse.
            logger.info(
                "render_send.already_in_flight",
                extra={"design_version_id": str(version.pk), "kind": self.kind},
            )
            return Response(
                {"send": {"status": "queued"}},
                status=status.HTTP_202_ACCEPTED,
                headers=NO_STORE,
            )

        try:
            # Row UUIDs and an epoch only. No address, no bytes, no URL and no
            # filename crosses the queue.
            send_design_render.delay(str(version.pk), self.kind, epoch)
        except Exception as exc:
            # The broker is a SECOND Redis, configured independently of the
            # throttle cache (CELERY_BROKER_URL vs REDIS_CACHE_URL, different
            # databases by default), so it can be down while the cache that just
            # answered is healthy. Without this the caller would get an
            # unhandled 500 instead of the documented envelope, having already
            # spent throttle quota on a send that never queued. Same broad catch
            # at the same boundary as generation/pipeline.py's QueueUnavailable.
            logger.warning(
                "render_send.enqueue_failed",
                extra={
                    "design_version_id": str(version.pk),
                    "kind": self.kind,
                    "exception_type": type(exc).__name__,
                },
            )
            return _error(
                "email_send_unavailable",
                "Emailing your concept is temporarily unavailable. Please try again later.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {"send": {"status": "queued"}},
            status=status.HTTP_202_ACCEPTED,
            headers=NO_STORE,
        )


_SEND_RESPONSES = {
    202: OpenApiResponse(RenderSendResponseSerializer, description="Queued for delivery."),
    403: OpenApiResponse(ErrorEnvelopeSerializer, description="CSRF token missing/invalid."),
    404: OpenApiResponse(
        ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
    ),
    409: OpenApiResponse(
        ErrorEnvelopeSerializer,
        description=(
            "design_image_not_ready; email_recipient_unavailable when the "
            "workspace is anonymous and so has no account address; or "
            "send_limit_reached when this render's lifetime allowance of sends "
            "is spent. The last carries no Retry-After — no waiting returns an "
            "allowance that is spent for good."
        ),
    ),
    429: OpenApiResponse(
        ErrorEnvelopeSerializer,
        description="email_send_limit_reached. Carries Retry-After.",
    ),
    503: OpenApiResponse(
        ErrorEnvelopeSerializer,
        description=(
            "email_delivery_disabled when the operator has not enabled account "
            "email, or email_send_unavailable when the throttle store cannot be "
            "reached. Neither carries Retry-After."
        ),
    ),
}

_SEND_STATE_RESPONSES = {
    200: OpenApiResponse(
        RenderSendStateResponseSerializer,
        description="How many sends this render has used, and what to pre-fill the name with.",
    ),
    404: OpenApiResponse(
        ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
    ),
}

_SEND_NOTE = (
    "The recipient is always your own account address, read server-side. The "
    "ONLY accepted body field is an optional 'filename' — no address may be "
    "supplied in any field, and a request carrying one fails whole rather than "
    "partially succeeding. Whatever you type as the file name travels in the "
    "message headers and is retained by the mail relay and the receiving host. "
    "The response never contains an address. Delivery is asynchronous: a 202 "
    "means queued, not sent."
)

_SEND_STATE_NOTE = (
    "Read this before offering a send: it reports how many of this render's "
    "lifetime allowance of sends are used, and the name to pre-fill — the name "
    "you last chose for this render, or your design's title. Never a note."
)


class DesignVersionSendView(_DesignVersionSendView):
    kind = DesignRenderDelivery.PLAIN

    @extend_schema(
        operation_id="designs_versions_send_state",
        tags=_DESIGN_TAGS,
        responses=_SEND_STATE_RESPONSES,
        summary="How many concept-image sends are left, and the name to pre-fill",
        description=_SEND_STATE_NOTE + " " + _OWNERSHIP_NOTE,
    )
    def get(self, request, design_id: str, version_id: str):
        return super().get(request, design_id, version_id)

    @extend_schema(
        operation_id="designs_versions_send_create",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request={"application/json": RenderSendSerializer},
        responses=_SEND_RESPONSES,
        summary="Email yourself this design version's concept image",
        description=(
            "Queues the plain canonical render as a PNG attachment. "
            + _SEND_NOTE
            + " "
            + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request, design_id: str, version_id: str):
        return super().post(request, design_id, version_id)


class DesignVersionAnnotationsSendView(_DesignVersionSendView):
    kind = DesignRenderDelivery.ANNOTATED

    @extend_schema(
        operation_id="designs_versions_annotations_send_state",
        tags=_DESIGN_TAGS,
        responses=_SEND_STATE_RESPONSES,
        summary="How many annotated-concept sends are left, and the name to pre-fill",
        description=_SEND_STATE_NOTE + " " + _OWNERSHIP_NOTE,
    )
    def get(self, request, design_id: str, version_id: str):
        return super().get(request, design_id, version_id)

    @extend_schema(
        operation_id="designs_versions_annotations_send_create",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request={"application/json": RenderSendSerializer},
        responses=_SEND_RESPONSES,
        summary="Email yourself this design version's annotated concept",
        description=(
            "Queues the annotated composite — the image with your marks drawn on "
            "it and a numbered note legend beneath — as a PNG attachment. Your "
            "note text is in the attachment only; the message body never carries "
            "it. " + _SEND_NOTE + " " + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request, design_id: str, version_id: str):
        return super().post(request, design_id, version_id)


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
            401: _ACCOUNT_REQUIRED_RESPONSE,
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
                    "refinement_source_unavailable / refinement_category_unavailable / "
                    "design_not_refinable."
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
            "call, and never echoed back. The requestable change_type values "
            "are " + ", ".join(sorted(REFINEMENT_CHANGE_TYPES)) + "; each "
            "changes the one canonical selection it is named after, plus the "
            "descriptive text around it. A category the design's own "
            "questionnaire version cannot express is refused with "
            "refinement_category_unavailable. " + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request, design_id: str):
        # An account first (ADR 0023). In practice a refiner already has one —
        # they cannot have reached a version-1 concept without it — so this is
        # defence in depth against a session that expired between generating and
        # refining, which would otherwise spend a provider call for a caller with
        # nowhere to keep the result.
        account_failure = _require_account(request)
        if account_failure is not None:
            return account_failure

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
        except RefinementCategoryUnavailable:
            # ADR 0028. The category exists but owns nothing this design's
            # questionnaire version can express, so it could only ever return an
            # unchanged concept. 409 rather than 400: the request is well formed,
            # it is this design that cannot accept it.
            return _error(
                "refinement_category_unavailable",
                "This change cannot be applied to this design.",
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


def _reject_oversized_upload(view_func):
    """Wire-level body-size gate, deliberately applied OUTSIDE ``csrf_protect``.

    The ordering is load-bearing, not cosmetic. Django's CSRF check reads
    ``request.POST`` first and only falls back to the ``X-CSRFToken`` header,
    and touching ``POST`` fully receives and parses the multipart body (spooling
    anything over ``FILE_UPLOAD_MAX_MEMORY_SIZE`` to disk). Any check inside the
    view therefore runs *after* the body has already been taken, so this has to
    be the outermost dispatch wrapper.

    It returns the response itself rather than raising: an exception here is
    outside DRF's dispatch and would render a Django HTML error page. The check
    reads only ``Content-Length`` — no session, no database — so its answer is
    identical for an owned, a foreign and a nonexistent design and cannot be
    used to probe for one."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            reject_oversized_body(request)
        except InspirationUploadError as exc:
            return JsonResponse(
                {"error": {"code": exc.code, "message": exc.message}},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                headers=NO_STORE,
            )
        return view_func(request, *args, **kwargs)

    return wrapper


@method_decorator(_reject_oversized_upload, name="dispatch")
@method_decorator(csrf_protect, name="dispatch")
class DesignInspirationUploadView(APIView):
    """Upload one of the user's OWN inspiration images to their design.

    Multipart, anonymous-session-owned and CSRF-protected like every other
    unsafe design endpoint. The client's filename and declared content type are
    never read: ``designs.upload_processing`` trusts only the decoded image, and
    the storage key is server-generated. The upload shares the design's
    ``MAX_INSPIRATION_IMAGES`` reference budget, which since ADR 0025 nothing
    else draws on."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    # Multipart only — a user upload is a file, and keeping the parser list
    # narrow keeps the documented contract honest.
    parser_classes = [MultiPartParser]

    @extend_schema(
        operation_id="designs_inspiration_upload_create",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request={"multipart/form-data": InspirationUploadWriteSerializer},
        responses={
            201: InspirationUploadResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            409: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="The design's inspiration limit is already reached.",
            ),
            413: OpenApiResponse(
                ErrorEnvelopeSerializer, description="The request body is too large."
            ),
            429: OpenApiResponse(ErrorEnvelopeSerializer, description="Too many uploads for now."),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="The image could not be stored, or uploads are briefly unavailable.",
            ),
        },
        summary="Upload an inspiration image",
        description=(
            "Sanitises one uploaded image (JPEG, PNG or single-frame WebP) into "
            "a clean WebP: EXIF orientation applied, then all EXIF/GPS/XMP/ICC "
            "metadata stripped. The original bytes are never stored. The user "
            "must affirm they hold the rights to the image. Uploads and curated "
            "selections share one limit. " + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request, design_id: str):
        # The body-size gate already ran outside csrf_protect (see
        # _reject_oversized_upload). The throttle runs here instead, AFTER CSRF
        # and ownership, so a cross-origin page cannot burn a victim's quota and
        # a rate-limited caller still cannot tell an owned design from one that
        # does not exist. Both precede request.data, so nothing is decoded or
        # stored for a refused request.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return _not_found()
        try:
            enforce_upload_throttle(request)
        except InspirationUploadThrottled as exc:
            return _error(
                "upload_rate_limited",
                "Too many uploads for now. Please try again shortly.",
                status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Cache-Control": "no-store", "Retry-After": str(int(exc.retry_after))},
            )
        except InspirationUploadError as exc:
            # The throttle cache itself is unreachable: refused, but as an
            # infrastructure fault (503), never as the caller's own abuse.
            return _upload_error(exc)
        serializer = InspirationUploadWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_failed(serializer.errors)
        try:
            upload = create_inspiration_upload(
                design,
                serializer.validated_data["image"],
                rights_acknowledged=serializer.validated_data["rights_acknowledged"],
            )
        except InspirationUploadError as exc:
            return _upload_error(exc)
        return Response(
            {"upload": inspiration_upload_payload(upload)},
            status=status.HTTP_201_CREATED,
            headers=NO_STORE,
        )


class _OwnedUploadMixin:
    """Shared ownership resolution for the two per-upload endpoints.

    A mixin rather than a base view, deliberately: inheriting one endpoint from
    the other would also inherit its HTTP methods, so the image URL would
    silently accept DELETE."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    def _get_owned(self, request, design_id: str, upload_id: str):
        # Ownership filter FIRST, UUID lookup second — never the reverse, and
        # an upload belonging to somebody else's design is the same 404 as one
        # that does not exist.
        return DesignInspirationUpload.objects.filter(
            pk=upload_id, design__in=accessible_designs(request), design_id=design_id
        ).first()


@method_decorator(csrf_protect, name="dispatch")
class DesignInspirationUploadDetailView(_OwnedUploadMixin, APIView):
    """Remove one of the design's own uploaded inspirations."""

    @extend_schema(
        operation_id="designs_inspiration_upload_delete",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        responses={
            204: OpenApiResponse(description="Removed."),
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer, description="The image could not be removed."
            ),
        },
        summary="Remove an uploaded inspiration image",
        description=("Deletes the private object and then the row. " + _OWNERSHIP_NOTE),
    )
    def delete(self, request, design_id: str, upload_id: str):
        upload = self._get_owned(request, design_id, upload_id)
        if upload is None:
            return _not_found()
        try:
            delete_inspiration_upload(upload.design, upload)
        except InspirationUploadError as exc:
            return _upload_error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT, headers=NO_STORE)


class DesignInspirationUploadImageView(_OwnedUploadMixin, APIView):
    """Stream one uploaded inspiration's sanitised bytes to its owner.

    A streaming, ownership-checked endpoint rather than a signed URL: these are
    private user images with no rights record, so no bearer URL to them should
    exist at all. No storage key or storage URL is ever exposed."""

    @extend_schema(
        operation_id="designs_inspiration_upload_image",
        tags=_DESIGN_TAGS,
        responses={
            200: OpenApiResponse(description="The sanitised WebP image bytes."),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer, description="The image is temporarily unreadable."
            ),
        },
        summary="An uploaded inspiration image",
        description=(
            "Streams the sanitised WebP for one of the design's own uploads. "
            "No storage keys or storage URLs are exposed. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request, design_id: str, upload_id: str):
        upload = self._get_owned(request, design_id, upload_id)
        if upload is None:
            return _not_found()
        try:
            with default_storage.open(upload.storage_key, "rb") as handle:
                data = handle.read()
        except Exception as exc:
            # An owned upload whose private object is unexpectedly unreadable:
            # a safe 503 logging only the row UUIDs and the exception type —
            # never the key, the storage endpoint or the exception text.
            logger.error(
                "design inspiration upload unreadable design_id=%s upload_id=%s "
                "exception_type=%s",
                design_id,
                upload.pk,
                type(exc).__name__,
            )
            return _error(
                "image_unavailable",
                "The image is temporarily unavailable.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        response = HttpResponse(data, content_type="image/webp")
        response["Content-Disposition"] = "inline"
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "no-store"
        return response


class DesignReferencesView(APIView):
    """This design's own uploaded references, and nothing else (Phase 22).

    A narrow read for one question the phone-handoff panel asks repeatedly:
    "has a photograph arrived yet?" It polls every couple of seconds for as long
    as a code is live, and answering that with ``GET /designs/<id>/`` meant
    re-sending the whole versioned questionnaire schema, the customer's saved
    answers and the latest job snapshot every time — none of it part of the
    question, none of it changed since the last poll, and all of it competing
    for the same shop wifi the customer's phone is using to push the photograph.

    Owner-only through the ordinary ownership filter, so a design that is not
    the caller's is the same indistinguishable 404 as everywhere else. Read-only
    and identity-bearing rather than identity-free: it names a private
    resource, so it authenticates the session like every other design read.

    It is emphatically NOT reachable behind a handoff grant — a grant is
    upload-only, permanently (ADR 0026 non-goal). This endpoint answers the
    IPAD's own session, which already owns the design."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="designs_references_retrieve",
        tags=_DESIGN_TAGS,
        responses={
            200: DesignReferencesResponseSerializer,
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
        },
        summary="List a design's own uploaded references",
        description=(
            "The design's own uploaded reference images, ordered by position — "
            "the same objects the design detail carries, without the "
            "questionnaire, the answers or the job snapshot. Intended for the "
            "phone-handoff panel's arrival poll, which needs this and nothing "
            "else. No image bytes and no signed URL: those come only from the "
            "ownership-checked image endpoint. " + _OWNERSHIP_NOTE
        ),
    )
    def get(self, request, design_id: str):
        # Ownership filter FIRST, UUID lookup second — never the reverse.
        design = accessible_designs(request).filter(pk=design_id).first()
        if design is None:
            return _not_found()
        # Through the SHARED builder, not a second comprehension: which uploads
        # belong in this list is one decision, and the design detail already
        # makes it. A copy here would agree today and drift the moment either
        # side gained a prefetch or an exclusion.
        return Response(
            {"inspiration_uploads": inspiration_uploads_payload(design)},
            headers=NO_STORE,
        )


@method_decorator(csrf_protect, name="dispatch")
class DesignReferenceGrantView(APIView):
    """Mint or revoke this design's phone-handoff grant (Phase 22, ADR 0026).

    Owner-only, through the ordinary ownership filter, so a design that is not
    the caller's is the same indistinguishable 404 as everywhere else. POST
    returns the plaintext secret — the ONE response in this API that does — and
    DELETE revokes every live grant on the design.

    There is no GET. A grant's secret cannot be read back after it is minted
    (the row holds only a digest), and listing grants would say something about
    a design without adding anything the owner cannot already see on the screen
    that minted one."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    def _get_owned(self, request, design_id: str) -> Design | None:
        # Ownership filter FIRST, UUID lookup second — never the reverse.
        return accessible_designs(request).filter(pk=design_id).first()

    @extend_schema(
        operation_id="designs_reference_grant_create",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request=None,
        responses={
            201: ReferenceUploadGrantResponseSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
            409: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "inspiration_limit_reached: the design's reference slots are "
                    "already full, so a code would be spent the moment it was shown."
                ),
            ),
            429: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Too many codes asked for just now."
            ),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="Handoff codes are briefly unavailable (throttle cache outage).",
            ),
        },
        summary="Start a phone handoff",
        description=(
            "Mints a short-lived, revocable grant letting the customer's own "
            "phone add reference photographs to THIS design, and returns its "
            "plaintext secret exactly once so the caller can render a QR code. "
            "The secret is stored only as a digest and can never be read back. "
            "It is a bearer credential: whoever sees the code can use it, and "
            "that exposure is accepted and bounded, not removed. It grants "
            "upload only — there is no read path behind it. Minting revokes any "
            "earlier live grant on the same design. " + _OWNERSHIP_NOTE
        ),
    )
    def post(self, request, design_id: str):
        design = self._get_owned(request, design_id)
        if design is None:
            return _not_found()
        # AFTER ownership, exactly as the upload endpoint orders it: a
        # cross-origin page must not be able to burn a victim's quota, and a
        # throttled caller still cannot tell an owned design from one that does
        # not exist.
        try:
            enforce_mint_throttle(request)
        except GrantMintingThrottled as exc:
            return _error(
                "grant_rate_limited",
                "Too many codes for now. Please try again shortly.",
                status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Cache-Control": "no-store", "Retry-After": str(int(exc.retry_after))},
            )
        except GrantMintingUnavailable:
            return _error(
                "grant_throttle_unavailable",
                "Phone handoff is temporarily unavailable. Please try again shortly.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        def _limit_reached():
            # ONE construction, deliberately, for both refusals below. Routing
            # the locked refusal through GrantDesignFull is only worth anything
            # if it answers identically to the cheap pre-check — that is what
            # gives the screen one case to handle instead of two. Two
            # hand-written copies would let a reworded message drift them apart
            # with every test still green.
            return _error(
                "inspiration_limit_reached",
                f"You can use at most {settings.MAX_INSPIRATION_IMAGES} inspiration images.",
                status.HTTP_409_CONFLICT,
            )

        remaining = max(settings.MAX_INSPIRATION_IMAGES - design.inspiration_slots_used(), 0)
        if remaining <= 0:
            # Refused rather than minted-and-immediately-spent: a code that
            # cannot work is worse than no code, because the customer scans it
            # and blames their phone.
            #
            # A CHEAP pre-check only. It reads unlocked, so the authoritative
            # one is inside the mint's own row lock; this exists to avoid
            # generating a secret we are about to throw away.
            return _limit_reached()
        try:
            grant, plaintext = create_reference_upload_grant(design)
        except GrantDesignFull:
            # The design filled up between the pre-check and the lock — the
            # stylist's own third photo landing while they tapped "Show the
            # code". Same answer as the pre-check, decided somewhere it could
            # not be overtaken.
            return _limit_reached()
        return Response(
            {
                "grant": {
                    "id": str(grant.id),
                    # The only time this value is ever in a response body.
                    "token": plaintext,
                    "expires_at": _iso(grant.expires_at),
                    "slots_remaining": remaining,
                }
            },
            status=status.HTTP_201_CREATED,
            headers=NO_STORE,
        )

    @extend_schema(
        operation_id="designs_reference_grant_revoke",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        responses={
            200: ReferenceUploadGrantRevokedSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer, description="Not found or not owned (indistinguishable)."
            ),
        },
        summary="Stop accepting photographs from a phone",
        description=(
            "Revokes every live grant on this design. Idempotent: revoking when "
            "there is nothing to revoke succeeds and reports zero. Unlike a "
            "signed storage URL this really does stop working, because it "
            "resolves through Sitara rather than through the object store. " + _OWNERSHIP_NOTE
        ),
    )
    def delete(self, request, design_id: str):
        design = self._get_owned(request, design_id)
        if design is None:
            return _not_found()
        revoked = revoke_reference_upload_grants(design)
        return Response({"revoked": revoked}, headers=NO_STORE)


@method_decorator(_reject_oversized_upload, name="dispatch")
@method_decorator(csrf_protect, name="dispatch")
class ReferenceUploadGrantUploadView(APIView):
    """Add a reference photograph to a design from the customer's own phone.

    The one thing a handoff grant opens, and the only thing (Phase 22, ADR
    0026). It takes no design id in its path or its body: the code names the
    design, so a grant for design A can never reach design B — not because a
    check refuses it, but because there is nowhere to express it.

    Anonymous and CSRF-protected like every other unsafe design endpoint. It
    consults no session ownership at all, which is the point: the phone has no
    account, no workspace and no relationship to the shop's iPad beyond the code
    it scanned. The grant is the whole authorisation, and it is a bearer
    credential — whoever holds the code can upload. That exposure is accepted
    and bounded (short TTL, one live code per design, revocable, upload-only,
    rate-limited), not removed.

    **Every unusable code gets one answer.** Expired, revoked, never-existed and
    spent (the design's reference slots are full) are one 404 with one body.
    That is §15's private-resource enumeration rule applied to a new identifier.
    A rejection about the caller's OWN file — too large, not an image, already
    added — is answered honestly instead, because it reveals nothing about the
    design and the person holding the phone can act on it."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser]

    @extend_schema(
        operation_id="reference_uploads_create",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request={"multipart/form-data": GrantUploadWriteSerializer},
        responses={
            201: GrantUploadResponseSerializer,
            400: ValidationErrorEnvelopeSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            404: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "reference_upload_unavailable: the code is expired, revoked, "
                    "spent, or was never real. One indistinguishable answer for "
                    "all four — nothing here reveals whether a design exists."
                ),
            ),
            409: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="This exact image has already been added to the design.",
            ),
            413: OpenApiResponse(
                ErrorEnvelopeSerializer, description="The request body is too large."
            ),
            429: OpenApiResponse(ErrorEnvelopeSerializer, description="Too many attempts."),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description="The image could not be stored, or uploads are briefly unavailable.",
            ),
        },
        summary="Add a reference photograph using a handoff code",
        description=(
            "Sanitises one uploaded image (JPEG, PNG or single-frame WebP) into "
            "a clean WebP — EXIF orientation applied, then all EXIF/GPS/XMP/ICC "
            "metadata stripped — and attaches it to the design the code names. "
            "The original bytes are never stored, and no filename or declared "
            "content type is read. The rights affirmation must be given HERE, by "
            "the person choosing the image: a tick on the shop's screen does not "
            "carry across the handoff. Upload only — this code grants no way to "
            "read the design, its answers, its versions or any generated image."
        ),
    )
    def post(self, request):
        # Both windows run BEFORE the code is resolved; each says why in its own
        # docstring, and the ORDER between them is what the two calls here fix.
        try:
            enforce_grant_upload_address_throttle(request)
        except GrantMintingThrottled as exc:
            return _grant_upload_throttled(exc.retry_after)
        except GrantMintingUnavailable:
            return _grant_upload_unavailable()

        serializer = GrantUploadWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_failed(serializer.errors)
        validated = serializer.validated_data

        try:
            enforce_grant_upload_code_throttle(validated["grant_token"])
        except GrantMintingThrottled as exc:
            return _grant_upload_throttled(exc.retry_after)
        except GrantMintingUnavailable:
            return _grant_upload_unavailable()

        try:
            grant = resolve_reference_upload_grant(validated["grant_token"])
        except GrantUnusable:
            return _reference_upload_unavailable()

        try:
            create_inspiration_upload(
                grant.design,
                validated["image"],
                rights_acknowledged=validated["rights_acknowledged"],
                # Re-checked under the design row lock, immediately before the
                # row is written. Resolution above happened before the decode,
                # sanitise and storage write, which is long enough for a
                # hand-back or the idle timeout to revoke this code mid-upload.
                require_live_grant=grant,
            )
        except InspirationUploadError as exc:
            if exc.code in ("inspiration_limit_reached", "grant_unusable"):
                # Two ways for the CODE rather than the file to be at fault:
                # the design filled up, so this code is spent; or the code was
                # revoked while this upload was being processed. Both answer as
                # the same 404 as an unknown code — "spent" or "revoked" would
                # otherwise confirm that the design behind a guessed code is
                # real. Both checks ran under the Design row lock inside the
                # upload service, so they hold under concurrent uploads through
                # one code and against a concurrent hand-back.
                return _reference_upload_unavailable()
            # Everything else is about the caller's own file, so it is answered
            # honestly: the person holding the phone can act on "too large" and
            # can act on nothing at all if told only "unavailable".
            return _upload_error(exc)

        record_grant_use(grant)
        # A constant, not a remaining-slots count. The cap is on the DESIGN and
        # MAX_INSPIRATION_IMAGES is public, so a count would let the phone
        # subtract its own uploads and recover how many references the design
        # already had — a read capability reached by arithmetic, and the shop
        # flow (stylist adds one on the iPad, then mints a code) triggers it on
        # the very first upload. See GrantUploadAcceptedSerializer.
        return Response(
            {"upload": {"accepted": True}},
            status=status.HTTP_201_CREATED,
            headers=NO_STORE,
        )


@method_decorator(csrf_protect, name="dispatch")
class WalkInSessionEndView(APIView):
    """Hand the shop's screen back (Phase 22, ADR 0027).

    "Finish and hand back". Ends the walk-in session in front of the iPad:
    forgets the workspace pointer and revokes any live handoff code, so the
    next person to sit down starts clean.

    Three things it deliberately does NOT do.

    It does not sign the shop out. The account is the boutique's and the next
    customer using it is the intended state — putting a login screen between
    every customer would buy no privacy and cost the stylist a password every
    time.

    It does not delete anything. Those concepts are the shop's work product; it
    decides whether to pass them to the customer. Ending a session ends this
    browser's claim on a workspace, not the workspace.

    It reports nothing about what it ended. The next person may already be
    looking at the screen."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="designs_end_walk_in_session",
        tags=_DESIGN_TAGS,
        parameters=[CSRF_HEADER_PARAMETER],
        request=None,
        responses={
            200: WalkInSessionEndedSerializer,
            403: OpenApiResponse(
                ErrorEnvelopeSerializer, description="CSRF token missing/invalid."
            ),
            503: OpenApiResponse(
                ErrorEnvelopeSerializer,
                description=(
                    "The browser session row could not be locked, so the "
                    "hand-back did not happen. Fails closed and says so rather "
                    "than reporting a hand-back it did not perform."
                ),
            ),
        },
        summary="Finish and hand back",
        description=(
            "Ends the walk-in session on a shared shop device: drops the "
            "workspace pointer and revokes any live phone-handoff code. "
            "Idempotent — ending when there is nothing to end succeeds and "
            "reports false. Does not sign out, and deletes nothing. A "
            "server-enforced idle timeout does the same thing unprompted, "
            "because customers walk away without anyone tapping this."
        ),
    )
    def post(self, request):
        try:
            ended = end_walk_in_session(request)
        except WorkspaceCoordinationError as exc:
            # The stylist is about to turn this screen towards someone else on
            # the strength of the answer, so a hand-back that could not be
            # coordinated must fail visibly rather than report success.
            return _workspace_unavailable(exc)
        return Response({"ended": ended}, headers=NO_STORE)
