"""Short-lived signed design-image delivery (Phase 11 Part B).

Issues presigned GET URLs for one DesignVersion's permanent original and
thumbnail. Ownership is checked by the CALLER (the API view filters through
``accessible_designs`` before any lookup); this module's job is the storage
side: require complete permanent provenance, confirm both private objects
still exist, and sign browser-reachable GET-only URLs with the configured
short TTL.

Privacy model (documented, deliberate): after issuance a presigned URL is a
TEMPORARY BEARER URL — anyone possessing it may use it until expiry; logout,
session rotation or account switching does not revoke it. URLs are therefore
short-lived and never persisted, cached or logged. A future authenticated
backend proxy is the upgrade path when immediate revocation or stricter
delivery controls are required.

The signing client targets ``S3_SIGNED_URL_ENDPOINT_URL`` (an externally
reachable origin — browsers cannot resolve the internal Docker MinIO host);
blank means the normal regional S3 endpoint. The signing client is used ONLY
for presigning — never for object upload/read calls (those go through the
``design_images`` storage backend). For the filesystem backend there is no
browser delivery path in Phase 11 (no backend image proxy exists), so
delivery fails closed with :class:`DesignImageDeliveryUnavailable` — never a
filesystem path or permanent public URL.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

import boto3
from botocore.config import Config as BotoConfig
from django.conf import settings
from django.utils import timezone

from .exceptions import DesignImageDeliveryUnavailable, DesignImageNotReady
from .ingest import design_image_storage

# Fixed, server-owned inline filenames — never derived from user input.
_ORIGINAL_FILENAME = "design-original.webp"
_THUMBNAIL_FILENAME = "design-thumbnail.webp"
# Fixed, server-owned attachment filename for the downloadable original
# (Phase 12) — deliberately generic, never the design title or any
# user-controlled value.
_DOWNLOAD_FILENAME = "sitara-concept.webp"

# The only Content-Disposition values a caller may request — never derived
# from user input, so the signed URL can never be steered into an arbitrary
# disposition string.
_ALLOWED_DISPOSITIONS = frozenset({"inline", "attachment"})

# Hard in-process bound on the WHOLE existence-check phase (both keys,
# checked concurrently). The browser's shared transport aborts every API
# call at 5s (apps/web/src/lib/transport.ts REQUEST_TIMEOUT_MS), and this
# deadline bounds only the storage phase — the rest of the request cycle
# spends the remaining slack, so this is a tight budget, not spare headroom.
# Independent of the storage client's own botocore timeouts (which are sized
# for the slower async ingest path).
EXISTENCE_DEADLINE_SECONDS = 3.5

# ONE shared, bounded pool for every request's existence checks, so the
# total threads this path can ever hold is a fixed process-wide cap — never
# a function of request rate. The cap is a deliberately conservative
# placeholder pending real traffic data: four fully concurrent deliveries
# (two checks each) per worker process, comfortably above a small
# deployment's concurrency — revisit alongside the WSGI worker/thread
# configuration. An expired deadline ABANDONS its checks: the running ones
# occupy their slots until the storage client itself gives up (worst case
# is roughly (connect 5s + read 10s) x 2 attempts, about 30s — see the
# design_images client_config), and queued not-yet-started ones are
# cancelled outright. Under a sustained storage hang the pool therefore
# SATURATES instead of growing: new requests' submissions stay queued, their
# deadlines expire, and they return the controlled 503 immediately — a
# natural circuit breaker. Worker threads are non-daemon, so worst-case
# process shutdown is delayed by at most one occupied-slot lifetime,
# assuming no queued backlog beyond the pool at shutdown — a safe
# assumption here because every request cancels its still-queued checks on
# exit.
_EXISTENCE_POOL_WORKERS = 8
_existence_pool = ThreadPoolExecutor(
    max_workers=_EXISTENCE_POOL_WORKERS, thread_name_prefix="design-image-exists"
)


@dataclass(frozen=True)
class DesignImageUrls:
    """One issuance: all three URLs share a single declared expiry."""

    original_url: str
    original_download_url: str
    thumbnail_url: str
    expires_at: datetime


class S3DesignImageSigner:
    """A dedicated SigV4 presigner for design-image GET URLs.

    Deliberately separate from the storage backend's own client so the
    browser-facing signing endpoint can differ from the internal storage
    endpoint, and so no code path can accidentally use it for uploads."""

    def __init__(self):
        endpoint_url = settings.S3_SIGNED_URL_ENDPOINT_URL or None
        # Path-style addressing only for an explicitly configured local/custom
        # origin (MinIO serves path-style); the blank-endpoint production case
        # keeps boto3's default addressing for real S3.
        config_kwargs = {"signature_version": "s3v4"}
        if endpoint_url:
            config_kwargs["s3"] = {"addressing_style": "path"}
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name=settings.S3_REGION_NAME,
            config=BotoConfig(**config_kwargs),
        )

    def sign_get(
        self, key: str, *, ttl_seconds: int, filename: str, disposition: str = "inline"
    ) -> str:
        """Presign one GET (no network call). Response headers pin the content
        type to image/webp and a server-owned filename under the requested,
        allowlisted disposition (``inline`` or ``attachment``)."""
        if disposition not in _ALLOWED_DISPOSITIONS:
            raise ValueError(f"unsupported disposition: {disposition!r}")
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.S3_BUCKET_NAME,
                "Key": key,
                "ResponseContentType": "image/webp",
                "ResponseContentDisposition": f'{disposition}; filename="{filename}"',
            },
            ExpiresIn=ttl_seconds,
            HttpMethod="GET",
        )


def issue_design_image_urls(
    design_version,
    *,
    ttl_seconds=None,
    signer=None,
    storage=None,
    now=None,
) -> DesignImageUrls:
    """Issue short-lived signed GET URLs for one ingested DesignVersion.

    Raises :class:`DesignImageNotReady` when permanent provenance is
    incomplete (the API maps this to the controlled 409) and
    :class:`DesignImageDeliveryUnavailable` for the filesystem backend, a
    storage outage, or a missing object (503). URLs are returned, never
    persisted, cached or logged; both expire at the same declared instant."""
    if not design_version.has_permanent_image:
        raise DesignImageNotReady("this design version has no permanent image yet")
    if settings.DESIGN_IMAGE_STORAGE_BACKEND != "s3":
        # No proxy exists in Phase 11: filesystem storage has no safe
        # browser-delivery path, so this fails closed by design.
        raise DesignImageDeliveryUnavailable("image delivery is not available for this backend")

    store = storage if storage is not None else design_image_storage()
    # Both existence checks run CONCURRENTLY on the shared bounded pool
    # (boto3 clients are thread-safe) and the WHOLE phase is bounded by the
    # in-process EXISTENCE_DEADLINE_SECONDS: the worst case is one storage
    # round-trip or the deadline, whichever is smaller — never the sum of
    # two calls, and never botocore's own (ingest-sized) timeouts. On any
    # exit the request's futures are cancelled: queued work is dropped;
    # already-running work is abandoned to wind down on the client timeout
    # inside its bounded pool slot.
    keys = (design_version.image_storage_key, design_version.thumbnail_storage_key)
    futures = [_existence_pool.submit(store.exists, key) for key in keys]
    try:
        deadline = time.monotonic() + EXISTENCE_DEADLINE_SECONDS
        for future in futures:
            try:
                remaining = max(0.0, deadline - time.monotonic())
                present = bool(future.result(timeout=remaining))
            except Exception as exc:  # noqa: BLE001 - transport errors AND the deadline
                raise DesignImageDeliveryUnavailable(
                    "image storage is temporarily unavailable"
                ) from exc
            if not present:
                # Metadata says ingested but the private object is gone — an
                # integrity problem surfaced as unavailability, never a 404
                # that would contradict the ownership-checked design lookup.
                raise DesignImageDeliveryUnavailable("a stored image object is unavailable")
    finally:
        for future in futures:
            future.cancel()

    ttl = int(ttl_seconds) if ttl_seconds else settings.DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS
    issued_at = now if now is not None else timezone.now()
    # Signer construction and presigning are LOCAL computations, but they run
    # inside botocore, whose exception surface is broad and version-dependent
    # (param validation, region resolution, data loading). The endpoint's
    # documented failure taxonomy is exactly 404/409/503, and no project-wide
    # JSON exception handler exists — so every signing failure must classify
    # into the controlled unavailability here, never escape as an unhandled
    # exception (which would surface as a non-JSON 500).
    try:
        active_signer = signer if signer is not None else S3DesignImageSigner()
        original_url = active_signer.sign_get(
            design_version.image_storage_key, ttl_seconds=ttl, filename=_ORIGINAL_FILENAME
        )
        original_download_url = active_signer.sign_get(
            design_version.image_storage_key,
            ttl_seconds=ttl,
            filename=_DOWNLOAD_FILENAME,
            disposition="attachment",
        )
        thumbnail_url = active_signer.sign_get(
            design_version.thumbnail_storage_key, ttl_seconds=ttl, filename=_THUMBNAIL_FILENAME
        )
    except Exception as exc:  # noqa: BLE001 - botocore raises varied types
        raise DesignImageDeliveryUnavailable("image delivery is temporarily unavailable") from exc
    return DesignImageUrls(
        original_url=original_url,
        original_download_url=original_download_url,
        thumbnail_url=thumbnail_url,
        expires_at=issued_at + timedelta(seconds=ttl),
    )
