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

Error messages are generic and structural — they never echo uploaded
bytes, filenames or decoded metadata.
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_DECODED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})

# Neutral studio-grey composite background for transparent uploads.
_COMPOSITE_BACKGROUND_RGB = (242, 242, 242)

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


def _open_upload(data: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(data))
    except Image.DecompressionBombError:
        # Pillow's own backstop for absurd header dimensions; our stricter
        # pixel bound below covers everything beneath it.
        _reject("The image dimensions are outside the accepted bounds.")
    except (UnidentifiedImageError, OSError, ValueError):
        _reject("The file is not a decodable image.")
    if image.format not in ALLOWED_DECODED_FORMATS:
        # SVG and PDF never decode; GIF and TIFF decode to a disallowed
        # format — the DECODED format decides, never the extension or the
        # claimed content type.
        _reject("Only JPEG, PNG and WebP images are accepted.")
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
        _reject("Animated or multi-frame images are not accepted.")
    return image


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Composite any transparency onto a neutral background; plain RGB out."""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, _COMPOSITE_BACKGROUND_RGB + (255,))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def _encode_webp(image: Image.Image) -> bytes:
    # A fresh RGB copy with an EMPTY info dict: Pillow's WebP encoder reads
    # exif/xmp/icc_profile from image.info, so an empty dict is what
    # guarantees no metadata survives into the output.
    clean = image.copy()
    clean.info = {}
    buffer = BytesIO()
    clean.save(buffer, format="WEBP", quality=_WEBP_QUALITY, method=4)
    return buffer.getvalue()


def _verify_webp(data: bytes) -> tuple[int, int]:
    """Reopen a produced file and prove it is a decodable RGB WebP."""
    try:
        produced = Image.open(BytesIO(data))
        if produced.format != "WEBP":
            raise ValueError
        produced.load()
    except (UnidentifiedImageError, OSError, ValueError):
        _reject("The processed image failed verification.")
    return produced.size


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

    image = _open_upload(data)

    width, height = image.size
    if width < 1 or height < 1 or width * height > max_pixels:
        # Header dimensions gate the decode: a decompression bomb is
        # rejected before its pixels are ever allocated.
        _reject("The image dimensions are outside the accepted bounds.")

    try:
        image.load()
        image = ImageOps.exif_transpose(image)
    except Image.DecompressionBombError:
        _reject("The image dimensions are outside the accepted bounds.")
    except (OSError, SyntaxError, ValueError):
        _reject("The file is not a decodable image.")

    flattened = _flatten_to_rgb(image)

    main = flattened.copy()
    # thumbnail() only ever downscales, preserving aspect ratio — small
    # uploads keep their native size.
    main.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    thumb = flattened.copy()
    thumb.thumbnail((thumbnail_edge, thumbnail_edge), Image.Resampling.LANCZOS)

    image_bytes = _encode_webp(main)
    thumbnail_bytes = _encode_webp(thumb)

    main_size = _verify_webp(image_bytes)
    _verify_webp(thumbnail_bytes)

    return ProcessedInspirationImage(
        image_bytes=image_bytes,
        thumbnail_bytes=thumbnail_bytes,
        width=main_size[0],
        height=main_size[1],
        size_bytes=len(image_bytes),
        sha256=hashlib.sha256(image_bytes).hexdigest(),
    )
