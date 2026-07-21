"""Demo source-image sanitisation for pack installation (Phase 15 Part A).

Uses the repository's shared sanitising-decode primitives
(:mod:`sitara.image_sanitize` — Pillow-only decode, decoded-format
allowlist, animated/multi-frame rejection, EXIF orientation then full
metadata strip via re-encode), the same ones used for staff catalogue
uploads (``sitara.catalogue.image_processing``) and permanent design images
(``sitara.media.image_processing``). This module never resizes: a curated
demo source asset is expected to already be at its final portrait
dimensions; installation only sanitises and verifies it."""

import hashlib
from dataclasses import dataclass

from sitara.image_sanitize import (
    ImageSanitizeRejected,
    encode_clean_webp,
    flatten_to_rgb,
    load_and_orient,
    open_and_validate,
    verify_webp,
)

_WEBP_QUALITY = 85


class DemoAssetImageError(Exception):
    """The source image was rejected or could not be processed. Messages are
    generic and structural — never echo file bytes or paths."""


@dataclass(frozen=True)
class SanitisedDemoImage:
    image_bytes: bytes
    width: int
    height: int
    size_bytes: int
    sha256: str


def _reject(reason: str) -> None:
    raise DemoAssetImageError(reason)


def sanitise_demo_source_image(
    data: bytes, *, max_bytes: int, max_pixels: int
) -> SanitisedDemoImage:
    """Decode, verify and re-encode one curated source image.

    Rejects anything outside a small allowlist (single-frame JPEG/PNG/WebP,
    within byte and pixel bounds); strips all metadata by re-encoding a bare
    RGB WebP copy; verifies the result by reopening it."""
    if len(data) == 0:
        _reject("The source file is empty.")
    if len(data) > max_bytes:
        _reject("The source file exceeds the maximum allowed size.")

    try:
        image = open_and_validate(data)

        width, height = image.size
        if width < 1 or height < 1 or width * height > max_pixels:
            raise ImageSanitizeRejected("The image dimensions are outside the accepted bounds.")

        image = load_and_orient(image)
        flattened = flatten_to_rgb(image)

        encoded = encode_clean_webp(flattened, quality=_WEBP_QUALITY, method=4)
        produced_size = verify_webp(encoded)
    except ImageSanitizeRejected as exc:
        _reject(str(exc))

    return SanitisedDemoImage(
        image_bytes=encoded,
        width=produced_size[0],
        height=produced_size[1],
        size_bytes=len(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )
