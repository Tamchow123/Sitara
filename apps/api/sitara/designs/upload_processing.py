"""Sanitising image pipeline for a user's own inspiration uploads (Phase 16B).

Uploaded bytes in, ONE clean WebP out — nothing else survives. Like the staff
catalogue pipeline it decodes with Pillow only (no ImageMagick, no libmagic, no
external service, no URL fetching), trusts the DECODED format rather than any
filename or content-type claim, and rejects everything outside a small
allowlist: single-frame JPEG, PNG and WebP within byte, pixel and dimension
bounds.

Sanitisation: EXIF orientation is applied, then every piece of metadata (EXIF,
GPS, XMP, ICC, comments) is dropped by re-encoding a bare RGB copy; transparency
is composited onto a neutral background; output never upscales. The original
upload is never stored and never logged.

The shared decode/flatten/encode/verify primitives live in
:mod:`sitara.image_sanitize` — the same ones the catalogue, permanent design
images and demo ingest use. This module owns only the USER-UPLOAD policy: byte
and pixel gating from settings, a single display-sized derivative (a user upload
has no catalogue thumbnail to serve), and this app's own exception type. It is
deliberately its own module rather than an import of the catalogue's processor:
a user upload is private user content, and nothing here may drift into implying
catalogue rights status.

Error messages are generic and structural — they never echo uploaded bytes,
filenames or decoded metadata. The two SIZE refusals are the exception to
"generic": since Phase 22 a reference is routinely a photograph taken on the
spot, and a modern phone sensor clears both ceilings at its default setting.
A bare "outside the accepted bounds" leaves someone holding a camera with
nothing to do about it, so those two say what to change. They still name only
the configured limit, never anything about the image itself.
"""

import hashlib
import logging
from dataclasses import dataclass

from django.conf import settings
from PIL import Image

from sitara.image_sanitize import (
    ImageSanitizeRejected,
    encode_clean_webp,
    flatten_to_rgb,
    load_and_orient,
    open_and_validate,
    verify_webp,
)

logger = logging.getLogger(__name__)

_WEBP_QUALITY = 85


def _too_many_bytes_message(limit_bytes: int) -> str:
    # Rounded down to whole megabytes: the point is to name a figure someone can
    # act on, not to be exact about a limit they cannot see anyway.
    return (
        f"That image file is larger than {limit_bytes // 1_000_000} MB. "
        "Most phones can send a smaller copy — choose a reduced size when you "
        "share it, or take the photo again at a lower resolution."
    )


def _too_many_pixels_message(limit_pixels: int) -> str:
    return (
        f"That photograph is larger than {limit_pixels // 1_000_000} megapixels. "
        "Your camera is probably set to its highest resolution — lower it and "
        "take the photo again, or send a smaller copy of it."
    )


class InspirationUploadRejected(Exception):
    """The upload was rejected or could not be processed. Messages are generic
    and safe to return to the user and to log."""


@dataclass(frozen=True)
class ProcessedUpload:
    """The sanitised derivative of one accepted user upload."""

    image_bytes: bytes
    width: int
    height: int
    size_bytes: int
    sha256: str


def process_user_inspiration_upload(uploaded_file) -> ProcessedUpload:
    """Validate and sanitise one user upload; raises
    :class:`InspirationUploadRejected`.

    Accepts an ``UploadedFile`` (or any object with ``size`` and ``read``).
    Reads it only after the byte-size gate, checks the declared dimensions
    BEFORE the full decode (decompression-bomb guard), then decodes, sanitises
    and re-encodes as described in the module docstring."""
    max_upload_bytes = settings.USER_UPLOAD_MAX_BYTES
    max_pixels = settings.USER_UPLOAD_MAX_IMAGE_PIXELS
    max_edge = settings.USER_UPLOAD_OUTPUT_MAX_EDGE

    size = getattr(uploaded_file, "size", None)
    if size is None or size <= 0:
        raise InspirationUploadRejected("The upload is empty.")
    if size > max_upload_bytes:
        raise InspirationUploadRejected(_too_many_bytes_message(max_upload_bytes))

    uploaded_file.seek(0)
    data = uploaded_file.read(max_upload_bytes + 1)
    if len(data) > max_upload_bytes:
        # A stated size can lie; the real read is what bounds memory.
        raise InspirationUploadRejected(_too_many_bytes_message(max_upload_bytes))

    try:
        image = open_and_validate(data)
        width, height = image.size
        if width < 1 or height < 1:
            raise ImageSanitizeRejected("The image dimensions are outside the accepted bounds.")
        if width * height > max_pixels:
            # Header dimensions gate the decode: a decompression bomb is
            # rejected before its pixels are ever allocated. This is also the
            # gate an ordinary 48-megapixel phone photograph meets, which is
            # why it answers with instructions rather than a bare refusal.
            raise ImageSanitizeRejected(_too_many_pixels_message(max_pixels))

        oriented = load_and_orient(image)
        flattened = flatten_to_rgb(oriented)
        # thumbnail() only ever downscales, preserving aspect ratio — a small
        # upload keeps its native size.
        flattened.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        image_bytes = encode_clean_webp(flattened, quality=_WEBP_QUALITY, method=4)
        out_width, out_height = verify_webp(image_bytes)
    except ImageSanitizeRejected as exc:
        raise InspirationUploadRejected(str(exc)) from None
    except Exception as exc:
        # A DELIBERATE catch-all boundary (CLAUDE.md 15). This is the only place
        # in the codebase that fully decodes ANONYMOUS, attacker-controlled
        # bytes, and the decode primitives catch an enumerated set of Pillow
        # exception types — a fuzzed codec-specific error outside that set must
        # still become a controlled 400, never an unhandled 500 with a Django
        # HTML error page. Only the exception TYPE is logged: never its text,
        # the filename or any uploaded byte.
        logger.warning(
            "user inspiration upload decode failed exception_type=%s", type(exc).__name__
        )
        raise InspirationUploadRejected("The file could not be read as an image.") from None

    return ProcessedUpload(
        image_bytes=image_bytes,
        width=out_width,
        height=out_height,
        size_bytes=len(image_bytes),
        sha256=hashlib.sha256(image_bytes).hexdigest(),
    )
