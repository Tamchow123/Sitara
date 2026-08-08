"""The regeneration guard for annotated-PNG composition.

Mirrors ``TestProcessorGolden``. The same-process determinism test next door
proves ``compose_annotated_png`` is repeatable with itself in one run; it cannot
catch the class of regression that actually threatens the contract — a Pillow,
zlib or libm change, or a different wheel build between a developer's host and
the environments CI and production run in. Only a hash committed to the
repository at one point in time and re-checked later can catch that.

**What travels between builds, and what does not.** Established empirically, in two
wrong steps, because the wrong answers are instructive:

1. This file originally pinned only the sha256 of the *compressed* PNG. It failed
   the moment it first ran anywhere other than where the manifest was generated:
   identical dimensions, ~90 bytes different in ~660 KB. Diagnosis: PNG
   compression belongs to whichever zlib the encoder is linked against.
2. So the digest was moved to the *decoded pixels*, on the reasoning that the
   picture itself must surely be portable. **That was also wrong.** CI printed the
   two fingerprints and they differed in exactly one component — ``zlib 1.2.13``
   against ``zlib 1.3``, with Pillow, FreeType and libwebp identical — and yet the
   pixel digests differed too. zlib cannot touch decoded pixels, so the real cause
   is that the two Pillow 11.3.0 *wheels are different builds*: the LANCZOS resize
   of the original lands on marginally different rounding between them. Every
   fixture embeds that resized image, which is why even ``plain`` — no marks, text
   only — differed.

So neither digest travels, and the split is now by what genuinely does:

* ``test_golden_dimensions_and_filenames_are_portable`` runs **everywhere**. Layout
  is integer maths over allowlisted constants, so page size and the server-owned
  filename are reproducible across builds.
* ``test_golden_digests_are_reproduced_exactly`` pins pixels AND bytes, but only
  where the fingerprint matches, and SKIPS elsewhere naming the differing
  components. A skip is visible in the suite output; it is never silently green.

Be honest about what that costs: in CI this module verifies layout, not
appearance. The substantive appearance guards there are the behavioural tests next
door — mark placement per type, the white halo over dark photography, numbering by
``created_order``, legend growth, and same-run byte determinism — none of which
depend on a committed digest. This manifest is a same-build regeneration guard,
which is the most it can honestly be.

The original docstring here claimed the manifest should be generated "inside the
container image CI and production use". That was wrong about CI, which installs
the same hash-verified lock directly on the runner rather than using the image —
which is precisely how the byte-level guard came to be unsatisfiable there.

**Expect the digest test to skip in CI, by design, indefinitely.** CI's install path
structurally differs from the container the manifest is regenerated in, so the
fingerprints will not match there. A routinely-skipped test is easy to misread as a
passing one, so: its skip message always names the differing components, and if it
ever begins skipping for a NEW reason that is worth reading rather than scrolling
past. It is meaningful when run inside the container, which is where CLAUDE.md §20
already directs the authoritative local backend run.

**Do not try to make the digests portable again.** Two attempts are recorded above;
the blocker is that the same pinned Pillow version ships as different builds. The
option deliberately not taken is a manifest keyed by several observed-good
fingerprints, so CI could assert its own recorded digests — rejected for now
because recording digests scraped from a failing run makes the guard assert
"whatever that environment produced", which is circular unless someone has actually
reviewed that environment's output. If it is ever wanted, review the render there
first.

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


def test_the_manifest_records_the_environment_its_digests_came_from():
    """Without this the digest guard could not tell "same environment" from
    "manifest predates the fingerprint", and would have to trust or skip
    blindly."""
    recorded = _manifest()["environment"]
    assert set(recorded) == set(_environment())
    assert all(isinstance(value, str) and value for value in recorded.values())


def _differing_components() -> dict:
    """Which fingerprint components disagree with the manifest's, if any."""
    recorded, running = _manifest()["environment"], _environment()
    return {
        component: f"manifest {recorded.get(component)!r} != running {running[component]!r}"
        for component in running
        if recorded.get(component) != running[component]
    }


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_golden_dimensions_and_filenames_are_portable(name):
    """What actually travels between builds, asserted everywhere.

    Layout arithmetic is integer maths over allowlisted constants and font
    metrics, so page size and the server-owned filename are reproducible even
    where the rendered pixels are not. Narrow, but real: a layout regression
    (a legend that stops growing the page, a wrong page width, a filename
    leaking a design title) fails here in every environment, including CI."""
    expected = _manifest()["fixtures"][name]
    observed = _observed(name)
    assert (observed["width"], observed["height"], observed["filename"]) == (
        expected["width"],
        expected["height"],
        expected["filename"],
    ), f"golden LAYOUT mismatch for fixture {name!r} — this part is portable and must not drift."


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_golden_digests_are_reproduced_exactly(name):
    """The strict guard: exact pixels AND exact bytes, in a matching build.

    Gated on the fingerprint because neither digest travels. That was established
    the hard way — see the module docstring — and the gate covers the pixel digest
    for the same reason it covers the byte digest, not as a convenience.

    Skipped visibly rather than deleted: within one build this is the sharpest
    regeneration guard available, and it is the reason a Pillow bump cannot
    silently change the export."""
    differing = _differing_components()
    if differing:
        pytest.skip(
            f"golden digest check does not apply to this build: {differing}. Neither "
            f"pixels nor compressed bytes are reproducible across builds of the "
            f"imaging stack; the portable layout guard above still ran. Regenerate "
            f"the manifest inside the container to assert digests locally."
        )

    expected = _manifest()["fixtures"][name]
    observed = _observed(name)
    assert (observed["pixels_sha256"], observed["sha256"], observed["size_bytes"]) == (
        expected["pixels_sha256"],
        expected["sha256"],
        expected["size_bytes"],
    ), (
        f"golden DIGEST mismatch for fixture {name!r} in a build whose fingerprint "
        f"MATCHES the manifest ({_environment()}). Nothing environmental explains "
        f"this: either composition changed (bump DESIGN_ANNOTATION_RENDERER_VERSION "
        f"and regenerate the manifest in review) or a dependency moved without its "
        f"reported version changing. Investigate before regenerating — that is "
        f"exactly what this guard exists to catch."
    )
