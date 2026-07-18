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

Error messages are generic and structural — they never echo image bytes,
hashes or metadata.
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from .exceptions import DesignImageProcessingError

# The exact processing behaviour version persisted onto every ingested
# DesignVersion. Bump ONLY with a reviewed golden-manifest update; a future
# processor version must create new DesignVersions, never rewrite existing
# permanent images.
DESIGN_IMAGE_PROCESSOR_VERSION = "1.0.0"

# The staged formats Phase 10 verified and may hand to ingest.
_ALLOWED_DECODED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})

# Documented neutral studio-grey composite background for alpha-bearing
# input (same neutral tone the catalogue pipeline uses).
_COMPOSITE_BACKGROUND_RGB = (242, 242, 242)

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


def _open_staged(data: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(data))
    except Image.DecompressionBombError:
        _reject("The image dimensions are outside the accepted bounds.")
    except (UnidentifiedImageError, OSError, ValueError):
        _reject("The data is not a decodable image.")
    if (image.format or "").upper() not in _ALLOWED_DECODED_FORMATS:
        _reject("Only JPEG, PNG and WebP images are accepted.")
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
        _reject("Animated or multi-frame images are not accepted.")
    return image


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Composite any transparency onto the neutral background; plain RGB out."""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    try:
        if has_alpha:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, _COMPOSITE_BACKGROUND_RGB + (255,))
            return Image.alpha_composite(background, rgba).convert("RGB")
        return image.convert("RGB")
    except (OSError, ValueError):
        _reject("The image colour mode cannot be safely converted.")


def _encode_webp(image: Image.Image, quality: int) -> bytes:
    # A fresh copy with an EMPTY info dict: Pillow's WebP encoder reads
    # exif/xmp/icc_profile from image.info, so an empty dict guarantees no
    # metadata survives into the output.
    clean = image.copy()
    clean.info = {}
    buffer = BytesIO()
    clean.save(buffer, format="WEBP", quality=quality, method=_WEBP_METHOD)
    return buffer.getvalue()


def _verify_webp(data: bytes) -> tuple[int, int]:
    """Reopen a produced file and prove it is a decodable single-frame WebP."""
    try:
        produced = Image.open(BytesIO(data))
        if produced.format != "WEBP":
            raise ValueError
        if getattr(produced, "is_animated", False) or getattr(produced, "n_frames", 1) > 1:
            raise ValueError
        produced.load()
    except (UnidentifiedImageError, OSError, ValueError):
        _reject("The processed image failed verification.")
    return produced.size


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

    image = _open_staged(raw_bytes)

    width, height = image.size
    if width < 1 or height < 1 or width * height > max_pixels:
        # Header dimensions gate the decode: a decompression bomb is rejected
        # before its pixels are ever allocated.
        _reject("The image dimensions are outside the accepted bounds.")

    try:
        image.load()  # force a complete decode to catch truncation
        image = ImageOps.exif_transpose(image)
    except Image.DecompressionBombError:
        _reject("The image dimensions are outside the accepted bounds.")
    except (OSError, SyntaxError, ValueError):
        _reject("The data is not a decodable image.")

    flattened = _flatten_to_rgb(image)

    main = flattened.copy()
    # thumbnail() only ever downscales, preserving aspect ratio — input at or
    # below the cap keeps its native size (never upscaled).
    main.thumbnail((max_edge, max_edge), _RESAMPLING_FILTER)
    thumb = flattened.copy()
    thumb.thumbnail((thumbnail_edge, thumbnail_edge), _RESAMPLING_FILTER)

    original_bytes = _encode_webp(main, full_quality)
    thumbnail_bytes = _encode_webp(thumb, thumbnail_quality)

    original_size = _verify_webp(original_bytes)
    thumbnail_size = _verify_webp(thumbnail_bytes)
    if original_size != main.size or thumbnail_size != thumb.size:
        # The encoded files must carry exactly the dimensions being persisted.
        _reject("The processed image failed verification.")

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
