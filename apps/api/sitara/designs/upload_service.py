"""User inspiration uploads: lifecycle, storage coordination and the shared cap.

The view stays thin; everything that spans storage and more than one row lives
here, following the same shape as the catalogue's own ingest service:

- the sanitised object is written FIRST and the row second, and a failure at
  either point removes whatever was already written, so no row ever points at
  incomplete storage and no orphaned write survives a database failure;
- deletion removes the object BEFORE the row (the maintenance pattern used
  everywhere else in this codebase), so a storage failure leaves a row that a
  retry — or the Phase 16B sweeper — can act on, never an unreachable object;
- the storage key is server-generated from the design UUID and a random
  revision. No filename, client content type, user identity or session data
  ever reaches a key.

The cap is the one thing curated selections and uploads share: their COMBINED
count is bounded by ``settings.MAX_INSPIRATION_IMAGES``, checked here under a
row lock on the owning design so two concurrent uploads cannot both pass.
"""

import logging
import secrets

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from sitara.accounts.rate_limits import RateLimitUnavailable, check_and_count, client_ip

from .models import Design, DesignInspirationUpload
from .upload_processing import InspirationUploadRejected, process_user_inspiration_upload

logger = logging.getLogger(__name__)

# The private object-key namespace for user uploads. Kept distinct from
# ``catalogue/`` and ``design-images/`` so a key can never be mistaken for
# rights-cleared catalogue content or for a generated design image.
UPLOAD_KEY_PREFIX = "design-uploads"

# Slack over the configured image bound for multipart framing (boundaries,
# headers, the small rights-acknowledgement field). Generous on purpose: this
# gate exists to reject the absurd, not to second-guess a legitimate body.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024

# A key namespace of its own, so an upload counter can never collide with — or
# be cleared by — an authentication one.
_THROTTLE_PREFIX = "uploadrl"


class InspirationUploadThrottled(Exception):
    """Too many upload attempts from this session or address.

    ``retry_after`` is the window length in seconds — a conservative,
    non-revealing hint, never a precise countdown."""

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__("upload rate limit reached")


def reject_oversized_body(request) -> None:
    """Reject an over-large request from ``Content-Length``, BEFORE the
    multipart parser touches the body.

    Django's parser fully receives a multipart body — spooling anything over
    ``FILE_UPLOAD_MAX_MEMORY_SIZE`` to disk — before any application code sees a
    file, so the in-process byte gate in ``upload_processing`` runs far too late
    to protect the host. This is a cheap wire-level pre-check on an ANONYMOUS
    endpoint, and the caller must run it before anything touches ``request.POST``
    or ``request.data`` (the view wraps it outside ``csrf_protect`` for exactly
    that reason — Django's CSRF check reads ``POST`` first).

    A missing or unparsable ``Content-Length`` is allowed through, and that is
    safe rather than a hole worth patching: ``WSGIRequest`` wraps the input in a
    ``LimitedStream`` bounded by exactly that header, treating an absent or
    unparsable one as ZERO. So a chunked or length-less body yields no bytes to
    the parser at all, and an understated one is truncated at the figure it
    declared. Overstating it is the only way to make Django read something
    large, and that is what this check refuses. An in-process handler counting
    received bytes would therefore be unreachable code — the ceiling is already
    enforced before the parser is handed anything.

    A reverse-proxy body-size limit is still worth having: it stops an absurd
    body at the edge instead of paying for a Python request to reject it."""
    raw = request.META.get("CONTENT_LENGTH")
    try:
        declared = int(raw)
    except (TypeError, ValueError):
        return
    if declared > settings.USER_UPLOAD_MAX_BYTES + _MULTIPART_OVERHEAD_BYTES:
        raise InspirationUploadError(
            "upload_too_large", "That image is larger than the maximum allowed size."
        )


def enforce_upload_throttle(request) -> None:
    """Session- and IP-scoped fixed-window throttle for the anonymous upload
    endpoint.

    The per-design cap bounds STORED uploads, not request RATE: upload, delete,
    re-upload costs an attacker one full decode per round with nothing stopping
    the loop.

    The counter itself is the project's ONE fixed-window primitive
    (``accounts.rate_limits.check_and_count``) — the same hashed-identifier,
    fail-closed implementation the auth throttles use, given its own key
    namespace rather than a second hand-maintained copy. The session check
    short-circuits the IP check exactly as the login view's pair does, so one
    refused request costs one increment.

    A cache outage still refuses the request, but as
    ``upload_throttle_unavailable`` (503) rather than a rate-limit breach (429):
    an infrastructure fault must not be reported to the caller — or to whoever
    is watching the 429 rate — as their own abuse."""
    try:
        retry_after = check_and_count(
            "upload_session",
            request.session.session_key or client_ip(request),
            settings.USER_UPLOAD_SESSION_LIMIT,
            settings.USER_UPLOAD_SESSION_WINDOW_SECONDS,
            prefix=_THROTTLE_PREFIX,
        ) or check_and_count(
            "upload_ip",
            client_ip(request),
            settings.USER_UPLOAD_IP_LIMIT,
            settings.USER_UPLOAD_IP_WINDOW_SECONDS,
            prefix=_THROTTLE_PREFIX,
        )
    except RateLimitUnavailable:
        raise InspirationUploadError(
            "upload_throttle_unavailable",
            "Uploads are temporarily unavailable. Please try again shortly.",
        ) from None
    if retry_after is not None:
        raise InspirationUploadThrottled(retry_after)


class InspirationUploadError(Exception):
    """An upload could not be accepted or removed.

    ``code`` is a stable machine code and ``message`` is a safe, generic
    sentence — neither ever echoes uploaded bytes, a filename, a storage key or
    exception text."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _delete_quietly(storage_key: str) -> bool:
    """Best-effort object delete. Returns whether it succeeded; logs only the
    exception TYPE, never the key or the storage endpoint."""
    try:
        default_storage.delete(storage_key)
    except Exception as exc:
        logger.error(
            "design inspiration upload cleanup failed storage_delete exception_type=%s",
            type(exc).__name__,
        )
        return False
    return True


def inspiration_slots_used(design: Design) -> int:
    """How many of the design's shared inspiration slots are already taken."""
    return design.inspiration_selections.count() + design.inspiration_uploads.count()


def _lowest_free_position(design: Design) -> int:
    """The smallest unused upload position for this design.

    Positions are REUSED rather than monotonically increasing. A monotonic
    ``MAX(position) + 1`` would exceed the model's ``position <=
    MAX_INSPIRATION_POSITION`` backstop as soon as a user deleted anything other
    than their highest-positioned upload and uploaded a replacement — an
    ordinary "swap this photo" flow that would then fail permanently, because
    the same number is recomputed on every retry.

    Only ever called with the design row locked and after the cap check, so a
    free position always exists; the guard is a defensive backstop, not a
    reachable path."""
    taken = set(design.inspiration_uploads.values_list("position", flat=True))
    for position in range(1, settings.MAX_INSPIRATION_IMAGES + 1):
        if position not in taken:
            return position
    raise InspirationUploadError(
        "inspiration_limit_reached",
        f"You can use at most {settings.MAX_INSPIRATION_IMAGES} inspiration images.",
    )


def create_inspiration_upload(
    design: Design, uploaded_file, *, rights_acknowledged: bool
) -> DesignInspirationUpload:
    """Sanitise one user upload and attach it to ``design``.

    Raises :class:`InspirationUploadError` for every rejection; the caller maps
    the code onto an HTTP response. The client's filename and declared content
    type are never read — the decoded image is the only thing trusted."""
    if not rights_acknowledged:
        raise InspirationUploadError(
            "rights_not_acknowledged",
            "Confirm you have the right to use this image before uploading it.",
        )

    limit = settings.MAX_INSPIRATION_IMAGES
    # A cheap pre-check so an over-limit request never spends CPU decoding an
    # image. It is NOT the guarantee — the locked re-check below is.
    if inspiration_slots_used(design) >= limit:
        raise InspirationUploadError(
            "inspiration_limit_reached",
            f"You can use at most {limit} inspiration images.",
        )

    try:
        processed = process_user_inspiration_upload(uploaded_file)
    except InspirationUploadRejected as exc:
        raise InspirationUploadError("invalid_image", str(exc)) from None

    revision = secrets.token_hex(8)
    storage_key = None
    try:
        storage_key = default_storage.save(
            f"{UPLOAD_KEY_PREFIX}/{design.pk}/{revision}/image.webp",
            ContentFile(processed.image_bytes),
        )
    except Exception as exc:
        logger.error(
            "design inspiration upload failed storage_write design_id=%s exception_type=%s",
            design.pk,
            type(exc).__name__,
        )
        raise InspirationUploadError(
            "storage_unavailable", "The image could not be stored. Please try again."
        ) from None

    try:
        with transaction.atomic():
            # Serialise concurrent uploads for one design on the design row, so
            # the combined cap and the position sequence are both decided by one
            # writer at a time.
            locked = Design.objects.select_for_update().get(pk=design.pk)
            if inspiration_slots_used(locked) >= limit:
                raise InspirationUploadError(
                    "inspiration_limit_reached",
                    f"You can use at most {limit} inspiration images.",
                )
            if locked.inspiration_uploads.filter(image_sha256=processed.sha256).exists():
                raise InspirationUploadError(
                    "duplicate_image", "You have already uploaded this image."
                )
            upload = DesignInspirationUpload.objects.create(
                design=locked,
                position=_lowest_free_position(locked),
                storage_key=storage_key,
                image_width=processed.width,
                image_height=processed.height,
                image_size_bytes=processed.size_bytes,
                image_sha256=processed.sha256,
                rights_acknowledged_at=timezone.now(),
            )
    except InspirationUploadError:
        _delete_quietly(storage_key)
        raise
    except Exception as exc:
        _delete_quietly(storage_key)
        logger.error(
            "design inspiration upload failed row_write design_id=%s exception_type=%s",
            design.pk,
            type(exc).__name__,
        )
        raise InspirationUploadError(
            "storage_unavailable", "The image could not be stored. Please try again."
        ) from None

    logger.info("design inspiration upload stored design_id=%s upload_id=%s", design.pk, upload.pk)
    return upload


def delete_inspiration_upload(design: Design, upload: DesignInspirationUpload) -> None:
    """Remove one upload: its private object first, then its row.

    Objects before rows, deliberately: if the object delete fails the row
    survives, so the private bytes stay reachable by a retry (and by the
    lifecycle sweeper) instead of being silently orphaned in the bucket."""
    if not _delete_quietly(upload.storage_key):
        raise InspirationUploadError(
            "storage_unavailable", "The image could not be removed. Please try again."
        )
    try:
        DesignInspirationUpload.objects.filter(pk=upload.pk, design=design).delete()
    except Exception as exc:
        # The object is already gone, so a row-delete failure (deadlock,
        # transient connection loss) leaves a row naming nothing. Deleting an
        # absent object is idempotent, so a retried DELETE self-heals it; what
        # must not happen is this escaping as an unhandled HTML 500 instead of
        # the JSON envelope the caller can act on. Only the exception TYPE is
        # logged — never the storage key or the exception text.
        logger.error(
            "design inspiration upload removal failed row_delete design_id=%s "
            "upload_id=%s exception_type=%s",
            design.pk,
            upload.pk,
            type(exc).__name__,
        )
        raise InspirationUploadError(
            "storage_unavailable", "The image could not be removed. Please try again."
        ) from None
    logger.info("design inspiration upload removed design_id=%s upload_id=%s", design.pk, upload.pk)
