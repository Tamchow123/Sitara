"""Deterministic crop-and-encode shared by the two asset build scripts.

`images/` at the repository root is a BUILD INPUT, never served: it holds the
project's own AI-generated source photography at full size. Both build scripts
select named sources from it, cut each to an exact target shape, downscale and
re-encode as a metadata-free WebP under `apps/web/public/`, and record a SHA-256
so a test can prove the served bytes are the reviewed bytes.

Everything here is pure and integer-arithmetic where it can be: the same source
and target always produce the same bytes, so a rebuild that changes a hash means
a source image or a mapping entry changed and must be re-reviewed. CI rebuilds
with the Pillow pinned in `apps/api/requirements.txt` and fails on any drift,
which is what actually enforces that rather than merely asserting it.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image

WEBP_QUALITY = 82
WEBP_METHOD = 6


def crop_box(size: tuple[int, int], aspect: float, focus: float) -> tuple[int, int, int, int]:
    """Largest centred box of `aspect` (w/h) inside `size`, anchored by `focus`.

    `focus` is the vertical anchor: 0.0 keeps the top of the frame, 1.0 the
    bottom. Integer arithmetic only, so the same input always yields the same
    box.
    """
    width, height = size
    if width / height > aspect:
        # Source is wider than the target: trim the sides, keeping the centre.
        new_width = round(height * aspect)
        left = (width - new_width) // 2
        return (left, 0, left + new_width, height)
    # Source is taller than the target: trim top/bottom, biased by `focus`.
    new_height = round(width / aspect)
    top = round((height - new_height) * focus)
    return (0, top, width, top + new_height)


def render(source_path: Path, target: tuple[int, int], focus: float) -> bytes:
    """Crop, downscale and encode one image; returns the WebP bytes."""
    target_width, target_height = target
    with Image.open(source_path) as image:
        # A source could arrive in any mode (palette, greyscale, RGBA); render
        # on an opaque white ground so the encoded output is always plain RGB.
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.split()[3])
            prepared = flattened
        else:
            prepared = image.convert("RGB")
        box = crop_box(prepared.size, target_width / target_height, focus)
        cropped = prepared.resize((target_width, target_height), resample=Image.LANCZOS, box=box)
    # Copy the pixels into a bare image so no source EXIF/ICC/XMP rides along.
    clean = Image.new("RGB", cropped.size)
    clean.putdata(list(cropped.getdata()))
    buffer = BytesIO()
    clean.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)
    return buffer.getvalue()


def digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()
