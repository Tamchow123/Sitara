"""Image pipeline tests: acceptance, sanitisation, rejection.

Every test image is generated in memory — no third-party or real
photographic image exists anywhere in the repository.
"""

import hashlib
from io import BytesIO

import pytest
from PIL import Image

from sitara.catalogue.image_processing import (
    InspirationImageError,
    process_inspiration_upload,
)

from .utils import make_image_bytes, make_upload


def _jpeg_with_metadata(size=(800, 600), orientation=None, gps=False, description=None):
    image = Image.new("RGB", size, (10, 120, 90))
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    if description is not None:
        exif[0x010E] = description
    if gps:
        gps_ifd = exif.get_ifd(0x8825)
        gps_ifd[1] = "N"  # GPSLatitudeRef
        gps_ifd[2] = (51.0, 30.0, 0.0)  # GPSLatitude
    buffer = BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _reopen(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


class TestAcceptedFormats:
    @pytest.mark.parametrize(
        ("fmt", "content_type"),
        [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
    )
    def test_allowed_formats_are_ingested(self, fmt, content_type):
        data = make_image_bytes(fmt=fmt)
        processed = process_inspiration_upload(
            make_upload(data, name=f"test.{fmt.lower()}", content_type=content_type)
        )
        assert _reopen(processed.image_bytes).format == "WEBP"
        assert _reopen(processed.thumbnail_bytes).format == "WEBP"

    def test_output_is_single_frame_rgb(self):
        processed = process_inspiration_upload(make_upload(make_image_bytes()))
        produced = _reopen(processed.image_bytes)
        assert produced.mode == "RGB"
        assert getattr(produced, "n_frames", 1) == 1
        assert not getattr(produced, "is_animated", False)

    def test_sha256_matches_the_sanitised_main_image(self):
        processed = process_inspiration_upload(make_upload(make_image_bytes()))
        assert processed.sha256 == hashlib.sha256(processed.image_bytes).hexdigest()
        assert processed.size_bytes == len(processed.image_bytes)


class TestResizing:
    def test_maximum_edge_is_enforced_without_upscaling(self, settings):
        settings.INSPIRATION_OUTPUT_MAX_EDGE = 200
        settings.INSPIRATION_THUMBNAIL_EDGE = 50
        processed = process_inspiration_upload(make_upload(make_image_bytes(size=(400, 100))))
        # Aspect ratio 4:1 preserved at the bounded edge.
        assert (processed.width, processed.height) == (200, 50)
        thumb_width, thumb_height = _reopen(processed.thumbnail_bytes).size
        assert thumb_width == 50
        assert thumb_height in (12, 13)  # Pillow rounds the short edge

    def test_small_images_are_never_upscaled(self):
        processed = process_inspiration_upload(make_upload(make_image_bytes(size=(300, 180))))
        assert (processed.width, processed.height) == (300, 180)


class TestMetadataStripping:
    def test_exif_is_stripped(self):
        data = _jpeg_with_metadata(description="camera-owner-secret-marker")
        # Precondition: the INPUT really carries EXIF.
        assert dict(_reopen(data).getexif())
        processed = process_inspiration_upload(make_upload(data))
        produced = _reopen(processed.image_bytes)
        assert not dict(produced.getexif())
        for marker in ("exif", "xmp", "icc_profile", "comment"):
            assert marker not in produced.info

    def test_gps_metadata_is_stripped(self):
        data = _jpeg_with_metadata(gps=True)
        # Precondition: the INPUT really carries a GPS IFD.
        assert dict(_reopen(data).getexif().get_ifd(0x8825))
        processed = process_inspiration_upload(make_upload(data))
        produced = _reopen(processed.image_bytes)
        assert not dict(produced.getexif().get_ifd(0x8825))
        assert not dict(produced.getexif())

    def test_exif_orientation_is_applied_before_stripping(self):
        # Orientation 6 = rotate 90° clockwise to display: an 800x600
        # sensor image must come out 600x800 with the tag gone.
        data = _jpeg_with_metadata(size=(800, 600), orientation=6)
        processed = process_inspiration_upload(make_upload(data))
        assert (processed.width, processed.height) == (600, 800)
        assert not dict(_reopen(processed.image_bytes).getexif())


class TestTransparency:
    def test_transparent_pixels_composite_onto_a_neutral_background(self):
        data = make_image_bytes(fmt="PNG", mode="RGBA", color=(255, 0, 0, 0))
        processed = process_inspiration_upload(
            make_upload(data, name="t.png", content_type="image/png")
        )
        produced = _reopen(processed.image_bytes)
        assert produced.mode == "RGB"
        pixel = produced.getpixel((10, 10))
        # WebP at quality 85 is lossy; allow a small tolerance around the
        # neutral background value.
        for channel in pixel:
            assert abs(channel - 242) <= 10


class TestRejection:
    def _rejected(self, upload, match=None):
        with pytest.raises(InspirationImageError, match=match):
            process_inspiration_upload(upload)

    def test_gif_is_rejected(self):
        self._rejected(
            make_upload(make_image_bytes(fmt="GIF", mode="P"), name="a.gif"),
            match="Only JPEG, PNG and WebP",
        )

    def test_tiff_is_rejected(self):
        self._rejected(
            make_upload(make_image_bytes(fmt="TIFF"), name="a.tiff"),
            match="Only JPEG, PNG and WebP",
        )

    def test_svg_is_rejected(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        self._rejected(make_upload(svg, name="a.svg"), match="not a decodable image")

    def test_pdf_is_rejected(self):
        pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        self._rejected(make_upload(pdf, name="a.pdf"), match="not a decodable image")

    def test_spoofed_extension_and_content_type_are_ignored(self):
        # GIF bytes claiming to be JPEG: the DECODED format decides.
        gif = make_image_bytes(fmt="GIF", mode="P")
        self._rejected(
            make_upload(gif, name="totally-a-photo.jpg", content_type="image/jpeg"),
            match="Only JPEG, PNG and WebP",
        )

    def test_corrupt_data_is_rejected(self):
        self._rejected(
            make_upload(b"\x89PNG\r\n\x1a\n" + b"not-actually-a-png" * 20, name="a.png"),
            match="not a decodable image",
        )

    def test_animated_webp_is_rejected(self):
        frame_one = Image.new("RGB", (64, 64), (255, 0, 0))
        frame_two = Image.new("RGB", (64, 64), (0, 0, 255))
        buffer = BytesIO()
        frame_one.save(
            buffer, format="WEBP", save_all=True, append_images=[frame_two], duration=100
        )
        self._rejected(
            make_upload(buffer.getvalue(), name="a.webp", content_type="image/webp"),
            match="Animated or multi-frame",
        )

    def test_decompression_bomb_dimensions_are_rejected(self, settings):
        settings.INSPIRATION_MAX_IMAGE_PIXELS = 10_000
        self._rejected(
            make_upload(make_image_bytes(size=(200, 200))),
            match="dimensions are outside",
        )

    def test_upload_size_limit_is_enforced(self, settings):
        settings.INSPIRATION_MAX_UPLOAD_BYTES = 1_000
        data = make_image_bytes(fmt="PNG", size=(500, 500))
        assert len(data) > 1_000
        self._rejected(make_upload(data), match="maximum allowed size")

    def test_empty_upload_is_rejected(self):
        self._rejected(make_upload(b""), match="empty")
