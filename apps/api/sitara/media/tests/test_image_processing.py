"""Canonical design-image processing tests (Phase 11 Part A, spec §6/§9/§13).

Everything runs on deterministic, locally generated images — no network, no
committed binaries. The golden-manifest test at the bottom is the processor
REGENERATION GUARD: changed output bytes with an unchanged processor version
fail; a deliberate version bump requires a reviewed manifest update.
"""

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from sitara.media.exceptions import DesignImageProcessingError
from sitara.media.image_processing import (
    DESIGN_IMAGE_PROCESSOR_VERSION,
    process_design_image,
)

from . import images

# The reviewed default bounds the goldens were computed under. Deliberately
# EXPLICIT (not read from Django settings) so a settings override can never
# silently invalidate the golden guard.
_BOUNDS = {
    "max_edge": 2048,
    "thumbnail_edge": 512,
    "full_quality": 90,
    "thumbnail_quality": 82,
    "max_bytes": 20_000_000,
    "max_pixels": 40_000_000,
}

_GOLDEN_MANIFEST = Path(__file__).resolve().parent / "processor_golden_v1.json"


def _process(data: bytes, **overrides):
    return process_design_image(data, **{**_BOUNDS, **overrides})


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


class TestFormatsAndOutput:
    @pytest.mark.parametrize(
        "builder",
        [images.webp_portrait_bytes, images.png_small_bytes, images.jpeg_plain_bytes],
        ids=["webp", "png", "jpeg"],
    )
    def test_all_accepted_inputs_become_rgb_webp(self, builder):
        processed = _process(builder())
        for data in (processed.original_bytes, processed.thumbnail_bytes):
            image = _open(data)
            assert image.format == "WEBP"
            assert image.mode == "RGB"
            assert getattr(image, "n_frames", 1) == 1

    def test_recorded_dimensions_and_hashes_match_encoded_bytes(self):
        processed = _process(images.webp_portrait_bytes())
        original = _open(processed.original_bytes)
        thumbnail = _open(processed.thumbnail_bytes)
        assert original.size == (processed.original_width, processed.original_height)
        assert thumbnail.size == (processed.thumbnail_width, processed.thumbnail_height)
        assert processed.original_sha256 == hashlib.sha256(processed.original_bytes).hexdigest()
        assert processed.thumbnail_sha256 == hashlib.sha256(processed.thumbnail_bytes).hexdigest()


class TestOrientationAndMetadata:
    def test_exif_orientation_is_applied(self):
        # Orientation 6 rotates the 800x600 source: rendered output is 600x800.
        processed = _process(images.jpeg_with_metadata_bytes())
        assert (processed.original_width, processed.original_height) == (600, 800)

    def test_all_metadata_is_stripped(self):
        processed = _process(images.jpeg_with_metadata_bytes())
        for data in (processed.original_bytes, processed.thumbnail_bytes):
            image = _open(data)
            image.load()
            assert dict(image.getexif()) == {}
            for marker in ("exif", "icc_profile", "xmp", "comment"):
                assert marker not in image.info


class TestRejection:
    def test_animated_webp_is_rejected(self):
        with pytest.raises(DesignImageProcessingError):
            _process(images.animated_webp_bytes())

    def test_animated_gif_is_rejected(self):
        with pytest.raises(DesignImageProcessingError):
            _process(images.animated_gif_bytes())

    def test_truncated_bytes_are_rejected(self):
        with pytest.raises(DesignImageProcessingError):
            _process(images.truncated_webp_bytes())

    def test_non_image_bytes_are_rejected(self):
        with pytest.raises(DesignImageProcessingError):
            _process(b"definitely not an image payload")

    def test_empty_bytes_are_rejected(self):
        with pytest.raises(DesignImageProcessingError):
            _process(b"")

    def test_byte_cap_is_enforced(self):
        data = images.webp_portrait_bytes()
        with pytest.raises(DesignImageProcessingError):
            _process(data, max_bytes=len(data) - 1)

    def test_pixel_cap_is_enforced_before_full_decode(self):
        with pytest.raises(DesignImageProcessingError):
            _process(images.png_small_bytes(), max_pixels=300 * 400 - 1)

    def test_rejection_messages_are_generic(self):
        data = images.webp_portrait_bytes()
        for bad_input, overrides in [
            (b"definitely not an image payload", {}),
            (data, {"max_bytes": 10}),
        ]:
            with pytest.raises(DesignImageProcessingError) as exc:
                _process(bad_input, **overrides)
            message = str(exc.value)
            assert "sha" not in message.lower()
            assert "key" not in message.lower()


class TestGeometry:
    def test_oversized_input_downscales_to_max_edge_preserving_aspect(self):
        processed = _process(images.png_large_landscape_bytes(3000, 2000))
        assert max(processed.original_width, processed.original_height) == 2048
        source_ratio = 3000 / 2000
        output_ratio = processed.original_width / processed.original_height
        assert abs(source_ratio - output_ratio) < 0.01

    def test_small_input_is_never_upscaled(self):
        processed = _process(images.png_small_bytes(300, 400))
        assert (processed.original_width, processed.original_height) == (300, 400)
        # Already inside the thumbnail square: kept at native size.
        assert (processed.thumbnail_width, processed.thumbnail_height) == (300, 400)

    def test_thumbnail_fits_the_square_without_distortion(self):
        processed = _process(images.webp_portrait_bytes(768, 1024))
        assert max(processed.thumbnail_width, processed.thumbnail_height) == 512
        source_ratio = 768 / 1024
        thumb_ratio = processed.thumbnail_width / processed.thumbnail_height
        assert abs(source_ratio - thumb_ratio) < 0.01

    def test_thumbnail_never_exceeds_the_original(self):
        processed = _process(images.webp_portrait_bytes())
        assert processed.thumbnail_width <= processed.original_width
        assert processed.thumbnail_height <= processed.original_height


class TestDeterminism:
    def test_identical_input_produces_identical_bytes_and_hashes(self):
        data = images.png_rgba_bytes()
        first = _process(data)
        second = _process(data)
        assert first.original_bytes == second.original_bytes
        assert first.thumbnail_bytes == second.thumbnail_bytes
        assert first.original_sha256 == second.original_sha256
        assert first.thumbnail_sha256 == second.thumbnail_sha256

    def test_alpha_compositing_is_deterministic_onto_the_neutral_background(self):
        processed = _process(images.png_rgba_bytes())
        image = _open(processed.original_bytes)
        # The fully transparent right half composites onto neutral grey
        # (242,242,242); WebP is lossy so allow a small encoder tolerance.
        r, g, b = image.getpixel((image.width - 5, image.height // 2))
        assert all(abs(channel - 242) <= 6 for channel in (r, g, b))
        # The opaque left half keeps its own colour, not the background.
        r, g, b = image.getpixel((5, image.height // 2))
        assert abs(r - 10) <= 12 and abs(g - 200) <= 12


class TestProcessorGolden:
    """The regeneration guard (spec §9): golden output hashes are tied to
    DESIGN_IMAGE_PROCESSOR_VERSION. If processing behaviour changes without a
    version bump, these hashes diverge and the suite fails; a deliberate bump
    requires regenerating the manifest in review."""

    def _manifest(self) -> dict:
        with _GOLDEN_MANIFEST.open(encoding="utf-8") as handle:
            return json.load(handle)

    def test_manifest_version_matches_the_processor_version(self):
        assert self._manifest()["processor_version"] == DESIGN_IMAGE_PROCESSOR_VERSION

    def test_manifest_bounds_match_the_reviewed_defaults(self):
        assert self._manifest()["bounds"] == _BOUNDS

    def test_bounds_match_the_shipped_settings_defaults(self):
        # Silent-drift guard: _BOUNDS is deliberately hardcoded (so a test
        # settings override can never invalidate the golden hashes), but it
        # must track the SHIPPED defaults — a changed default without a
        # processor-version bump and manifest review must fail here.
        from django.conf import settings

        assert _BOUNDS == {
            "max_edge": settings.DESIGN_IMAGE_MAX_EDGE,
            "thumbnail_edge": settings.DESIGN_IMAGE_THUMBNAIL_EDGE,
            "full_quality": settings.DESIGN_IMAGE_WEBP_QUALITY,
            "thumbnail_quality": settings.DESIGN_IMAGE_THUMBNAIL_QUALITY,
            "max_bytes": settings.GENERATION_RAW_MAX_BYTES,
            "max_pixels": settings.GENERATION_RAW_MAX_PIXELS,
        }

    def test_golden_hashes_are_reproduced_exactly(self):
        manifest = self._manifest()
        builders = {
            "webp_portrait": images.webp_portrait_bytes,
            "png_large_landscape": images.png_large_landscape_bytes,
            "png_rgba": images.png_rgba_bytes,
            "jpeg_with_metadata": images.jpeg_with_metadata_bytes,
        }
        assert set(manifest["fixtures"]) == set(builders)
        for name, builder in builders.items():
            processed = _process(builder())
            expected = manifest["fixtures"][name]
            observed = {
                "original_sha256": processed.original_sha256,
                "original_width": processed.original_width,
                "original_height": processed.original_height,
                "original_size_bytes": len(processed.original_bytes),
                "thumbnail_sha256": processed.thumbnail_sha256,
                "thumbnail_width": processed.thumbnail_width,
                "thumbnail_height": processed.thumbnail_height,
                "thumbnail_size_bytes": len(processed.thumbnail_bytes),
            }
            assert observed == expected, f"golden mismatch for fixture {name!r}"
