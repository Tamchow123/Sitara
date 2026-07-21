"""Shared image decode/sanitise/verify primitives (Phase 15).

A dependency-free leaf module at the package root (not inside any single
app) so any app may reuse it without an app-to-app import reversal — the
same placement rationale as :mod:`sitara.content_safety`. Three call sites
share this exact pipeline: the staff catalogue upload sanitiser
(``sitara.catalogue.image_processing``), the canonical permanent
design-image processor (``sitara.media.image_processing``), and the demo
asset-pack installer (``sitara.generation.demo.ingest``).

Decoding uses Pillow only (no ImageMagick, no libmagic, no external
services, no URL fetching); the DECODED format decides, never a filename or
claimed content type. Metadata (EXIF, GPS, XMP, ICC, comments) is dropped by
re-encoding a bare RGB copy with an empty ``info`` dict; transparency is
composited onto a neutral background. Callers own resizing/thumbnailing,
quality/method choices and byte/pixel-size gating — this module only
provides the shared decode -> validate -> flatten -> encode -> verify
building blocks, each raising the generic :class:`ImageSanitizeRejected`
with a message that is always safe to display or log (never image bytes,
filenames or decoded metadata).
"""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_DECODED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})

# Neutral studio-grey composite background for transparent input.
COMPOSITE_BACKGROUND_RGB = (242, 242, 242)


class ImageSanitizeRejected(Exception):
    """The image was rejected or could not be processed. Messages are
    generic and structural — callers should catch this and re-raise their
    own domain-specific exception type with the same message."""


def open_and_validate(data: bytes) -> Image.Image:
    """Decode ``data``, and reject anything outside the allowed format
    allowlist or an animated/multi-frame image. Raises
    :class:`ImageSanitizeRejected`."""
    try:
        image = Image.open(BytesIO(data))
    except Image.DecompressionBombError:
        # Pillow's own backstop for absurd header dimensions; a caller's
        # stricter pixel bound (checked separately, before full decode)
        # covers everything beneath it.
        raise ImageSanitizeRejected(
            "The image dimensions are outside the accepted bounds."
        ) from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise ImageSanitizeRejected("The file is not a decodable image.") from None
    if (image.format or "").upper() not in ALLOWED_DECODED_FORMATS:
        # SVG and PDF never decode; GIF and TIFF decode to a disallowed
        # format — the DECODED format decides, never the extension or the
        # claimed content type.
        raise ImageSanitizeRejected("Only JPEG, PNG and WebP images are accepted.")
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
        raise ImageSanitizeRejected("Animated or multi-frame images are not accepted.")
    return image


def load_and_orient(image: Image.Image) -> Image.Image:
    """Force a complete decode (to catch truncation) and apply EXIF
    orientation. Raises :class:`ImageSanitizeRejected`."""
    try:
        image.load()
        return ImageOps.exif_transpose(image)
    except Image.DecompressionBombError:
        raise ImageSanitizeRejected(
            "The image dimensions are outside the accepted bounds."
        ) from None
    except (OSError, SyntaxError, ValueError):
        raise ImageSanitizeRejected("The file is not a decodable image.") from None


def flatten_to_rgb(
    image: Image.Image, *, background_rgb: tuple[int, int, int] = COMPOSITE_BACKGROUND_RGB
) -> Image.Image:
    """Composite any transparency onto ``background_rgb``; plain RGB out.
    Raises :class:`ImageSanitizeRejected`."""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    try:
        if has_alpha:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, background_rgb + (255,))
            return Image.alpha_composite(background, rgba).convert("RGB")
        return image.convert("RGB")
    except (OSError, ValueError):
        raise ImageSanitizeRejected("The image colour mode cannot be safely converted.") from None


def encode_clean_webp(image: Image.Image, *, quality: int, method: int) -> bytes:
    """Re-encode ``image`` as WebP with all metadata stripped.

    A fresh RGB copy with an EMPTY ``info`` dict: Pillow's WebP encoder
    reads exif/xmp/icc_profile from ``image.info``, so an empty dict is what
    guarantees no metadata survives into the output."""
    clean = image.copy()
    clean.info = {}
    buffer = BytesIO()
    clean.save(buffer, format="WEBP", quality=quality, method=method)
    return buffer.getvalue()


def verify_webp(data: bytes) -> tuple[int, int]:
    """Reopen a produced file and prove it is a decodable, single-frame
    WebP. Raises :class:`ImageSanitizeRejected`."""
    try:
        produced = Image.open(BytesIO(data))
        if produced.format != "WEBP":
            raise ValueError
        if getattr(produced, "is_animated", False) or getattr(produced, "n_frames", 1) > 1:
            raise ValueError
        produced.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ImageSanitizeRejected("The processed image failed verification.") from None
    return produced.size
