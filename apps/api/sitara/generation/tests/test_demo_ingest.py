"""Demo source-image sanitisation used by the pack installer."""

import hashlib
import io

import pytest
from PIL import Image

from sitara.generation.demo.ingest import DemoAssetImageError, sanitise_demo_source_image


def _webp_bytes(width=768, height=1024, colour=(120, 40, 60)) -> bytes:
    image = Image.new("RGB", (width, height), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=80)
    return buffer.getvalue()


def _animated_webp_bytes() -> bytes:
    frame1 = Image.new("RGB", (100, 100), (255, 0, 0))
    frame2 = Image.new("RGB", (100, 100), (0, 255, 0))
    buffer = io.BytesIO()
    frame1.save(buffer, format="WEBP", save_all=True, append_images=[frame2], duration=100)
    return buffer.getvalue()


class TestSanitiseDemoSourceImage:
    def test_valid_webp_is_sanitised_and_verified(self):
        data = _webp_bytes()
        result = sanitise_demo_source_image(data, max_bytes=8_000_000, max_pixels=4096 * 4096)
        assert result.width == 768
        assert result.height == 1024
        assert result.sha256 == hashlib.sha256(result.image_bytes).hexdigest()

    def test_valid_png_is_accepted_and_reencoded_to_webp(self):
        image = Image.new("RGB", (768, 1024), (10, 20, 30))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        result = sanitise_demo_source_image(
            buffer.getvalue(), max_bytes=8_000_000, max_pixels=4096 * 4096
        )
        assert result.width == 768 and result.height == 1024

    def test_empty_bytes_are_rejected(self):
        with pytest.raises(DemoAssetImageError):
            sanitise_demo_source_image(b"", max_bytes=8_000_000, max_pixels=4096 * 4096)

    def test_corrupt_bytes_are_rejected(self):
        with pytest.raises(DemoAssetImageError):
            sanitise_demo_source_image(
                b"this is definitely not an image", max_bytes=8_000_000, max_pixels=4096 * 4096
            )

    def test_oversized_bytes_are_rejected(self):
        data = _webp_bytes()
        with pytest.raises(DemoAssetImageError):
            sanitise_demo_source_image(data, max_bytes=10, max_pixels=4096 * 4096)

    def test_excessive_pixel_count_is_rejected(self):
        data = _webp_bytes(width=2000, height=2667)
        with pytest.raises(DemoAssetImageError):
            sanitise_demo_source_image(data, max_bytes=8_000_000, max_pixels=1000)

    def test_animated_webp_is_rejected(self):
        data = _animated_webp_bytes()
        with pytest.raises(DemoAssetImageError):
            sanitise_demo_source_image(data, max_bytes=8_000_000, max_pixels=4096 * 4096)

    def test_gif_format_is_rejected(self):
        image = Image.new("RGB", (768, 1024), (5, 5, 5))
        buffer = io.BytesIO()
        image.save(buffer, format="GIF")
        with pytest.raises(DemoAssetImageError):
            sanitise_demo_source_image(
                buffer.getvalue(), max_bytes=8_000_000, max_pixels=4096 * 4096
            )

    def test_metadata_is_stripped(self):
        image = Image.new("RGB", (768, 1024), (77, 88, 99))
        buffer = io.BytesIO()
        # Embed an ICC profile / comment payload to prove it does not survive.
        image.save(buffer, format="WEBP", quality=80, icc_profile=b"fake-icc-profile-bytes")
        result = sanitise_demo_source_image(
            buffer.getvalue(), max_bytes=8_000_000, max_pixels=4096 * 4096
        )
        reopened = Image.open(io.BytesIO(result.image_bytes))
        assert not reopened.info.get("icc_profile")

    def test_transparency_is_composited(self):
        image = Image.new("RGBA", (768, 1024), (10, 10, 10, 0))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        result = sanitise_demo_source_image(
            buffer.getvalue(), max_bytes=8_000_000, max_pixels=4096 * 4096
        )
        reopened = Image.open(io.BytesIO(result.image_bytes))
        assert reopened.mode == "RGB"
