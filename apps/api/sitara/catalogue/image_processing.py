"""Sanitising image pipeline for staff catalogue uploads (Phase 5B).

Uploaded bytes in, two clean WebP derivatives out — nothing else survives.
The pipeline decodes with Pillow only (no ImageMagick, no libmagic, no
external services, no URL fetching), trusts the DECODED format rather than
any filename or content-type claim, and rejects everything outside a small
allowlist: single-frame JPEG, PNG and WebP within byte, pixel and
dimension bounds.

Sanitisation: EXIF orientation is applied, then every piece of metadata
(EXIF, GPS, XMP, ICC, comments) is dropped by re-encoding a bare RGB copy;
transparency is composited onto a neutral background; output never
upscales. The original upload is never stored and never logged.

The shared decode/flatten/encode/verify primitives live in
:mod:`sitara.image_sanitize` (also used by ``sitara.media.image_processing``
and ``sitara.generation.demo.ingest``); this module owns only the
catalogue-specific policy: byte/pixel gating from settings, two-derivative
thumbnailing, and this app's own exception type.

Error messages are generic and structural — they never echo uploaded
bytes, filenames or decoded metadata.
"""

import hashlib
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

_WEBP_QUALITY = 85


class InspirationImageError(Exception):
    """The upload was rejected or could not be processed. Messages are
    generic and safe to show to staff and to log."""


@dataclass(frozen=True)
class ProcessedInspirationImage:
    """The sanitised derivatives of one accepted upload."""

    image_bytes: bytes
    thumbnail_bytes: bytes
    width: int
    height: int
    size_bytes: int
    sha256: str


def _reject(reason: str) -> None:
    raise InspirationImageError(reason)


def process_inspiration_upload(uploaded_file) -> ProcessedInspirationImage:
    """Validate and sanitise one staff upload; raises InspirationImageError.

    Accepts an UploadedFile (or any object with ``size`` and ``read``);
    reads it only after the byte-size gate, checks declared dimensions
    BEFORE full decode (decompression-bomb guard), then decodes, sanitises
    and re-encodes as described in the module docstring.
    """
    max_upload_bytes = settings.INSPIRATION_MAX_UPLOAD_BYTES
    max_pixels = settings.INSPIRATION_MAX_IMAGE_PIXELS
    max_edge = settings.INSPIRATION_OUTPUT_MAX_EDGE
    thumbnail_edge = settings.INSPIRATION_THUMBNAIL_EDGE

    size = getattr(uploaded_file, "size", None)
    if size is None or size <= 0:
        _reject("The upload is empty.")
    if size > max_upload_bytes:
        _reject("The upload exceeds the maximum allowed size.")

    uploaded_file.seek(0)
    data = uploaded_file.read(max_upload_bytes + 1)
    if len(data) > max_upload_bytes:
        _reject("The upload exceeds the maximum allowed size.")

    try:
        image = open_and_validate(data)

        width, height = image.size
        if width < 1 or height < 1 or width * height > max_pixels:
            # Header dimensions gate the decode: a decompression bomb is
            # rejected before its pixels are ever allocated.
            raise ImageSanitizeRejected("The image dimensions are outside the accepted bounds.")

        image = load_and_orient(image)
        flattened = flatten_to_rgb(image)

        main = flattened.copy()
        # thumbnail() only ever downscales, preserving aspect ratio — small
        # uploads keep their native size.
        main.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        thumb = flattened.copy()
        thumb.thumbnail((thumbnail_edge, thumbnail_edge), Image.Resampling.LANCZOS)

        image_bytes = encode_clean_webp(main, quality=_WEBP_QUALITY, method=4)
        thumbnail_bytes = encode_clean_webp(thumb, quality=_WEBP_QUALITY, method=4)

        main_size = verify_webp(image_bytes)
        verify_webp(thumbnail_bytes)
    except ImageSanitizeRejected as exc:
        _reject(str(exc))

    return ProcessedInspirationImage(
        image_bytes=image_bytes,
        thumbnail_bytes=thumbnail_bytes,
        width=main_size[0],
        height=main_size[1],
        size_bytes=len(image_bytes),
        sha256=hashlib.sha256(image_bytes).hexdigest(),
    )
