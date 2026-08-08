"""The regeneration guard for annotated-PNG composition.

Mirrors ``TestProcessorGolden``. The same-process determinism test next door
proves ``compose_annotated_png`` is repeatable with itself in one run; it cannot
catch the class of regression that actually threatens the contract — a Pillow,
zlib or libm change, or a different wheel build between a developer's host and
the environments CI and production run in. Only a hash committed to the
repository at one point in time and re-checked later can catch that.

**Two guards, because they are not equally portable.** This file originally
pinned only the sha256 of the *compressed* PNG, and that made it fail the moment
it first ran anywhere other than where the manifest was generated: identical
dimensions, identical layout, ~90 bytes different in a ~660 KB file. A PNG's
compressed bytes depend on the exact zlib the encoder is linked against, which is
not something this project controls or should pretend to. So:

* ``test_golden_pixels_are_reproduced_exactly`` pins the sha256 of the *decoded*
  pixels plus the page dimensions. This is the real contract — the picture a
  stylist receives — and it holds anywhere.
* ``test_golden_bytes_are_reproduced_exactly`` still pins the compressed bytes,
  but only when the running environment fingerprint matches the one recorded in
  the manifest. Elsewhere it SKIPS with the differing components named. A skip is
  visible in the suite output; it is never silently green.

The original docstring here claimed the manifest should be generated "inside the
container image CI and production use". That was wrong about CI, which installs
the same hash-verified lock directly on the runner rather than using the image —
which is precisely how the byte-level guard came to be unsatisfiable there.

**Expect the byte-level test to skip in CI, by design, indefinitely.** CI's install
path structurally differs from the container the manifest is regenerated in, so the
fingerprints will not match there. A routinely-skipped test is easy to misread as a
passing one, so: its skip message always names the differing components, and if it
ever begins skipping for a NEW reason — a zlib patch bump, say — that is worth
reading rather than scrolling past. It is meaningful when run inside the container,
which is where CLAUDE.md §20 already directs the authoritative local backend run.

Golden hashes are tied to ``DESIGN_ANNOTATION_RENDERER_VERSION``. If composition
behaviour changes without a version bump the PIXEL digests diverge and the suite
fails; a deliberate bump requires regenerating the manifest in review.

Regenerate with ``python -m sitara.media.tests.regenerate_annotation_golden``
from ``apps/api``, inside the container, and note that the byte digests it
records are only meaningful in an environment with the same fingerprint.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from PIL import Image

from sitara.media.annotation_render import (
    DESIGN_ANNOTATION_RENDERER_VERSION,
    PAGE_WIDTH,
    compose_annotated_png,
)

GOLDEN_MANIFEST = Path(__file__).with_name("annotation_golden_v1.json")

# Deliberately hardcoded, not read from settings: a test-time settings override
# must never be able to invalidate the golden hashes. A changed shipped default
# without a renderer-version bump and manifest review fails the guard below.
_BOUNDS = {"max_pixels": 24_000_000, "max_bytes": 20_000_000, "read_deadline_seconds": 10}

KEY = "design-images/golden/v1/original.webp"


@pytest.fixture(autouse=True)
def isolated_design_image_storage(settings):
    import copy

    configured = copy.deepcopy(settings.STORAGES)
    configured["design_images"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = configured


def golden_original() -> bytes:
    """A fixed synthetic original. Lossless so the stored bytes are exactly what
    this function computes, on any platform."""
    image = Image.new("RGB", (384, 512))
    pixels = image.load()
    for y in range(512):
        for x in range(384):
            pixels[x, y] = ((x * 5) % 256, (y * 3) % 256, ((x + y) * 7) % 256)
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", lossless=True, method=6)
    return buffer.getvalue()


def _mark(order: int, **overrides) -> dict:
    base = {
        "id": f"{order:08x}-0000-4000-8000-000000000001",
        "type": "pin",
        "geometry": {"point": {"x": 0.5, "y": 0.25}},
        "note": "Raise the neckline about 2 cm.",
        "palette": "terracotta",
        "created_order": order,
    }
    base.update(overrides)
    return base


def _document(items: list[dict]) -> dict:
    return {"schema_version": 1, "image_width": 384, "image_height": 512, "items": items}


# The fixtures the manifest pins. Each exercises a distinct part of the layout.
FIXTURES: dict[str, dict] = {
    "empty": {"document": _document([]), "annotated": True},
    "plain": {"document": _document([_mark(1)]), "annotated": False},
    "one_pin": {"document": _document([_mark(1)]), "annotated": True},
    "all_mark_types": {
        "document": _document(
            [
                _mark(1, type="pin", palette="terracotta"),
                _mark(
                    2,
                    type="arrow",
                    palette="sage",
                    geometry={"start": {"x": 0.2, "y": 0.2}, "end": {"x": 0.8, "y": 0.6}},
                ),
                _mark(
                    3,
                    type="rectangle",
                    palette="ink",
                    geometry={"x": 0.15, "y": 0.5, "width": 0.5, "height": 0.3},
                ),
                _mark(
                    4,
                    type="freehand",
                    palette="terracotta",
                    geometry={
                        "points": [
                            {"x": 0.2, "y": 0.8},
                            {"x": 0.45, "y": 0.7},
                            {"x": 0.7, "y": 0.85},
                        ]
                    },
                ),
            ]
        ),
        "annotated": True,
    },
    "empty_and_long_notes": {
        "document": _document(
            [
                _mark(1, note=""),
                _mark(2, note="Lengthen the champagne hem border and repeat the motif " * 3),
            ]
        ),
        "annotated": True,
    },
    "unrenderable_script": {
        "document": _document([_mark(1, note="दुपट्टा को थोड़ा लंबा करें")]),
        "annotated": True,
    },
    "odd_row_count": {
        "document": _document([_mark(index, note=f"Note {index}.") for index in range(1, 6)]),
        "annotated": True,
    },
}


def _render(name: str):
    storages["design_images"].save(f"{KEY}-{name}", ContentFile(golden_original()))
    fixture = FIXTURES[name]
    return compose_annotated_png(
        storage_key=f"{KEY}-{name}",
        document=fixture["document"],
        annotated=fixture["annotated"],
    )


def _pixels_sha256(content: bytes) -> str:
    """Hash the DECODED image, not the file.

    Mode and size go into the digest alongside the pixels so a mode change (say
    RGB to RGBA) cannot coincide with an identical pixel run and slip through."""
    with Image.open(io.BytesIO(content)) as opened:
        opened.load()
        digest = hashlib.sha256()
        digest.update(f"{opened.mode}:{opened.width}x{opened.height}:".encode())
        digest.update(opened.tobytes())
        return digest.hexdigest()


def _environment() -> dict:
    """The components whose build determines the compressed bytes.

    Pillow bundles zlib, libwebp and FreeType in its wheels, so these versions —
    not the host distribution's — are what the encoder actually used."""
    from PIL import __version__ as pillow_version
    from PIL import features

    return {
        "pillow": pillow_version,
        "zlib": features.version("zlib"),
        "freetype2": features.version("freetype2"),
        "webp": features.version("webp"),
    }


def _observed(name: str) -> dict:
    render = _render(name)
    return {
        "sha256": render.sha256,
        "pixels_sha256": _pixels_sha256(render.content),
        "width": render.width,
        "height": render.height,
        "size_bytes": len(render.content),
        "filename": render.filename,
    }


def _portable(observed: dict) -> dict:
    """The environment-independent part of an observation."""
    return {key: observed[key] for key in ("pixels_sha256", "width", "height", "filename")}


def _manifest() -> dict:
    with GOLDEN_MANIFEST.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_manifest_version_matches_the_renderer_version():
    assert _manifest()["renderer_version"] == DESIGN_ANNOTATION_RENDERER_VERSION


def test_manifest_records_the_font_the_hashes_were_produced_with():
    """The font is part of the deterministic contract, so the manifest pins it
    too — swapping the font without regenerating must fail here."""
    from sitara.media import annotation_render

    assert _manifest()["font_sha256"] == annotation_render._FONT_SHA256


def test_bounds_match_the_shipped_settings_defaults():
    """Silent-drift guard: _BOUNDS is hardcoded so a test override cannot
    invalidate the hashes, but it must track the SHIPPED defaults."""
    from django.conf import settings

    assert _BOUNDS == {
        "max_pixels": settings.ANNOTATION_RENDER_MAX_PIXELS,
        "max_bytes": settings.ANNOTATION_RENDER_MAX_BYTES,
        "read_deadline_seconds": settings.ANNOTATION_RENDER_READ_DEADLINE_SECONDS,
    }
    assert _manifest()["bounds"] == _BOUNDS


def test_manifest_covers_exactly_the_declared_fixtures():
    assert set(_manifest()["fixtures"]) == set(FIXTURES)


def test_the_page_width_is_pinned():
    assert _manifest()["page_width"] == PAGE_WIDTH


def test_the_manifest_records_the_environment_its_byte_hashes_came_from():
    """Without this the byte guard below could not tell "same environment" from
    "manifest predates the fingerprint", and would have to trust or skip
    blindly."""
    recorded = _manifest()["environment"]
    assert set(recorded) == set(_environment())
    assert all(isinstance(value, str) and value for value in recorded.values())


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_golden_pixels_are_reproduced_exactly(name):
    """The portable guard: the picture itself, on any platform.

    This is what DESIGN_ANNOTATION_RENDERER_VERSION actually promises. A layout,
    palette, font-metric or coordinate change lands here."""
    expected = _manifest()["fixtures"][name]
    observed = _observed(name)
    assert _portable(observed) == _portable(expected), (
        f"golden PIXEL mismatch for fixture {name!r}. The rendered image itself "
        f"changed, not merely its compression. Either composition changed (bump "
        f"DESIGN_ANNOTATION_RENDERER_VERSION and regenerate the manifest in "
        f"review) or a dependency changed underneath it — investigate before "
        f"regenerating, which is exactly what this guard exists to catch. "
        f"Manifest environment {_manifest()['environment']}, "
        f"running environment {_environment()}."
    )


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_golden_bytes_are_reproduced_exactly(name):
    """The strict guard, where it is meaningful.

    PNG compression is a property of the zlib the encoder was linked against, so
    this can only hold in an environment matching the manifest's. It is skipped —
    visibly, naming what differs — rather than deleted, because within one
    environment byte-identity is still the sharpest signal available."""
    manifest = _manifest()
    recorded, running = manifest["environment"], _environment()
    if recorded != running:
        differing = {
            component: f"manifest {recorded[component]!r} != running {running[component]!r}"
            for component in running
            if recorded.get(component) != running[component]
        }
        pytest.skip(
            f"byte-level golden check does not apply here: {differing}. The pixel "
            f"guard above still ran and is the portable contract."
        )

    expected = manifest["fixtures"][name]
    observed = _observed(name)
    assert (observed["sha256"], observed["size_bytes"]) == (
        expected["sha256"],
        expected["size_bytes"],
    ), (
        f"golden BYTE mismatch for fixture {name!r} in an environment whose "
        f"fingerprint matches the manifest ({running}). The pixels may still "
        f"match — check the pixel test above — in which case the encoder itself "
        f"changed without its reported version changing."
    )
