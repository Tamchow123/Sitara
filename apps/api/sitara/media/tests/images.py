"""Deterministic, locally generated test images (zero network, no binaries
committed). Every builder is a pure function of its arguments, so the same
call always yields the same bytes under the pinned Pillow — the golden
processor manifest depends on that.
"""

import io

from PIL import Image


def _checker_rgb(width: int, height: int, block: int = 64) -> Image.Image:
    image = Image.new("RGB", (width, height), (198, 160, 122))
    for x in range(0, width, block):
        for y in range(0, height, block):
            if (x // block + y // block) % 2 == 0:
                for i in range(x, min(x + block, width)):
                    for j in range(y, min(y + block, height)):
                        image.putpixel((i, j), (120, 40, 60))
    return image


def _gradient_rgb(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height))
    pixels = [
        ((x * 255) // max(width - 1, 1), (y * 255) // max(height - 1, 1), 96)
        for y in range(height)
        for x in range(width)
    ]
    image.putdata(pixels)
    return image


def _encode(image: Image.Image, image_format: str, **params) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **params)
    return buffer.getvalue()


def webp_portrait_bytes(width: int = 768, height: int = 1024) -> bytes:
    """A 3:4 RGB WebP shaped like real staged FLUX output."""
    return _encode(_checker_rgb(width, height), "WEBP", quality=80, method=4)


def png_large_landscape_bytes(width: int = 3000, height: int = 2000) -> bytes:
    """An RGB PNG larger than DESIGN_IMAGE_MAX_EDGE (exercises downscaling)."""
    return _encode(_gradient_rgb(width, height), "PNG")


def png_small_bytes(width: int = 300, height: int = 400) -> bytes:
    """An RGB PNG smaller than every output bound (exercises no-upscale)."""
    return _encode(_gradient_rgb(width, height), "PNG")


def png_rgba_bytes(width: int = 640, height: int = 800) -> bytes:
    """An RGBA PNG with a semi-transparent half (exercises compositing)."""
    image = Image.new("RGBA", (width, height), (10, 200, 30, 255))
    for x in range(width // 2, width):
        for y in range(height):
            image.putpixel((x, y), (10, 200, 30, 0))  # fully transparent
    return _encode(image, "PNG")


def jpeg_plain_bytes(width: int = 800, height: int = 600) -> bytes:
    return _encode(_checker_rgb(width, height), "JPEG", quality=90)


def jpeg_with_metadata_bytes(width: int = 800, height: int = 600, *, gps: bool = False) -> bytes:
    """A JPEG carrying EXIF (orientation 6 + camera fields), a comment and a
    fake ICC profile — everything the processor must strip. Orientation 6
    means the rendered output is rotated 90° (dimensions swap). With
    ``gps=True`` a GPS sub-IFD (latitude) is embedded too, so the strip test
    can positively prove location data is removed."""
    image = _checker_rgb(width, height)
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW
    exif[0x010F] = "SitaraTestCamera"  # Make
    exif[0x0110] = "MetadataModel"  # Model
    if gps:
        gps_ifd = exif.get_ifd(0x8825)
        gps_ifd[1] = "N"  # GPSLatitudeRef
        gps_ifd[2] = (51.0, 30.0, 0.0)  # GPSLatitude
    return _encode(
        image,
        "JPEG",
        quality=90,
        exif=exif.tobytes(),
        comment=b"sitara-test-comment",
        icc_profile=b"sitara-fake-icc-profile-bytes",
    )


def animated_webp_bytes(width: int = 64, height: int = 64) -> bytes:
    first = Image.new("RGB", (width, height), (255, 0, 0))
    second = Image.new("RGB", (width, height), (0, 0, 255))
    buffer = io.BytesIO()
    first.save(buffer, format="WEBP", save_all=True, append_images=[second], duration=100)
    return buffer.getvalue()


def animated_gif_bytes(width: int = 64, height: int = 64) -> bytes:
    first = Image.new("P", (width, height), 0)
    second = Image.new("P", (width, height), 1)
    buffer = io.BytesIO()
    first.save(buffer, format="GIF", save_all=True, append_images=[second], duration=100)
    return buffer.getvalue()


def truncated_webp_bytes() -> bytes:
    data = webp_portrait_bytes()
    return data[: len(data) // 2]
