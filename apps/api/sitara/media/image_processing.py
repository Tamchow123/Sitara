"""Canonical design-image processing (Phase 11 Part A).

One pure function turns verified staged provider output into the two
permanent derivatives: a full-size WebP original and a square-bounded WebP
thumbnail. Decoding uses Pillow only; the DECODED format decides (never an
extension or content type); every piece of metadata (EXIF, GPS, comments,
XMP, ICC, provider fields) is dropped by re-encoding a bare RGB copy;
transparency is composited onto a documented neutral background; output is
never upscaled and never distorted or cropped.

Determinism: identical input bytes under the pinned Pillow version produce
identical output bytes (fixed resampling filter, fixed encoder parameters).
``DESIGN_IMAGE_PROCESSOR_VERSION`` names this exact behaviour — any change to
the observable output requires a version bump plus a reviewed golden-manifest
update (see the processor-golden tests).

The shared decode/flatten/encode/verify primitives live in
:mod:`sitara.image_sanitize` (also used by
``sitara.catalogue.image_processing`` and ``sitara.generation.demo.ingest``);
this module owns only the design-image-specific policy: byte/pixel gating,
two-derivative thumbnailing at this pipeline's fixed resampling/encoder
choices, and this app's own exception type.

Error messages are generic and structural — they never echo image bytes,
hashes or metadata.
"""

import hashlib
from dataclasses import dataclass

from PIL import Image

from sitara.image_sanitize import (
    ImageSanitizeRejected,
    encode_clean_webp,
    flatten_to_rgb,
    load_and_orient,
    open_and_validate,
    verify_webp,
)

from .exceptions import DesignImageProcessingError

# The exact processing behaviour version persisted onto every ingested
# DesignVersion. Bump ONLY with a reviewed golden-manifest update; a future
# processor version must create new DesignVersions, never rewrite existing
# permanent images.
DESIGN_IMAGE_PROCESSOR_VERSION = "1.0.0"

# Deterministic encoder/resampling choices — part of the versioned behaviour.
_RESAMPLING_FILTER = Image.Resampling.LANCZOS
_WEBP_METHOD = 6


@dataclass(frozen=True)
class ProcessedDesignImage:
    """The canonical derivatives of one staged design image."""

    original_bytes: bytes
    original_sha256: str
    original_width: int
    original_height: int
    thumbnail_bytes: bytes
    thumbnail_sha256: str
    thumbnail_width: int
    thumbnail_height: int


def _reject(reason: str) -> None:
    raise DesignImageProcessingError(reason)


def process_design_image(
    raw_bytes: bytes,
    *,
    max_edge: int,
    thumbnail_edge: int,
    full_quality: int,
    thumbnail_quality: int,
    max_bytes: int,
    max_pixels: int,
) -> ProcessedDesignImage:
    """Process verified staged bytes into the canonical original + thumbnail.

    Pure: everything it needs arrives as parameters (callers pass the
    DESIGN_IMAGE_* settings and the Phase 10 raw byte/pixel caps, enforced
    here AGAIN as defence in depth). Raises
    :class:`DesignImageProcessingError` with a generic, safe message."""
    if not raw_bytes:
        _reject("The staged image is empty.")
    if len(raw_bytes) > max_bytes:
        _reject("The staged image exceeds the maximum allowed size.")

    try:
        image = open_and_validate(raw_bytes)

        width, height = image.size
        if width < 1 or height < 1 or width * height > max_pixels:
            # Header dimensions gate the decode: a decompression bomb is
            # rejected before its pixels are ever allocated.
            raise ImageSanitizeRejected("The image dimensions are outside the accepted bounds.")

        image = load_and_orient(image)
        flattened = flatten_to_rgb(image)

        main = flattened.copy()
        # thumbnail() only ever downscales, preserving aspect ratio — input
        # at or below the cap keeps its native size (never upscaled).
        main.thumbnail((max_edge, max_edge), _RESAMPLING_FILTER)
        thumb = flattened.copy()
        thumb.thumbnail((thumbnail_edge, thumbnail_edge), _RESAMPLING_FILTER)

        original_bytes = encode_clean_webp(main, quality=full_quality, method=_WEBP_METHOD)
        thumbnail_bytes = encode_clean_webp(thumb, quality=thumbnail_quality, method=_WEBP_METHOD)

        original_size = verify_webp(original_bytes)
        thumbnail_size = verify_webp(thumbnail_bytes)
        if original_size != main.size or thumbnail_size != thumb.size:
            # The encoded files must carry exactly the dimensions being persisted.
            raise ImageSanitizeRejected("The processed image failed verification.")
    except ImageSanitizeRejected as exc:
        _reject(str(exc))

    return ProcessedDesignImage(
        original_bytes=original_bytes,
        original_sha256=hashlib.sha256(original_bytes).hexdigest(),
        original_width=original_size[0],
        original_height=original_size[1],
        thumbnail_bytes=thumbnail_bytes,
        thumbnail_sha256=hashlib.sha256(thumbnail_bytes).hexdigest(),
        thumbnail_width=thumbnail_size[0],
        thumbnail_height=thumbnail_size[1],
    )
