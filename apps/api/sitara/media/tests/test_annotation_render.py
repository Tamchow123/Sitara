"""Annotated PNG composition (Phase 19 Part B).

Determinism is the load-bearing property: the same original bytes and the same
document must produce the same PNG bytes, because that is what
``DESIGN_ANNOTATION_RENDERER_VERSION`` promises and what makes the emailed export
reproducible. The rest of the suite exists to prove the composition never
allocates unbounded, never writes to storage, never leaks a storage detail, and
never silently emits glyphs it cannot actually draw.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from PIL import Image

from sitara.media.annotation_render import (
    DESIGN_ANNOTATION_RENDERER_VERSION,
    EMPTY_NOTE,
    MARK_COLOURS,
    PAGE_WIDTH,
    UNRENDERABLE_NOTE,
    compose_annotated_png,
)
from sitara.media.exceptions import DesignAnnotationRenderError

KEY = "design-images/test/v1/original.webp"


@pytest.fixture(autouse=True)
def isolated_design_image_storage(settings):
    """In-memory design-image storage — CI has no MinIO and no test may touch a
    real bucket."""
    import copy

    configured = copy.deepcopy(settings.STORAGES)
    configured["design_images"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = configured


def synthetic_original(width: int = 768, height: int = 1024) -> bytes:
    """A deterministic synthetic image — never a downloaded or licensed one."""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(0, width, 8):
            shade = ((x * 7 + y * 3) // 8) % 256
            for dx in range(min(8, width - x)):
                pixels[x + dx, y] = (shade, (shade * 2) % 256, (shade * 3) % 256)
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=90, method=6)
    return buffer.getvalue()


def store_original(content: bytes | None = None, key: str = KEY) -> str:
    storages["design_images"].save(key, ContentFile(content or synthetic_original()))
    return key


def mark(**overrides) -> dict:
    base = {
        "id": "3f2a1c8e-0000-4000-8000-000000000001",
        "type": "pin",
        "geometry": {"point": {"x": 0.5, "y": 0.25}},
        "note": "Raise the neckline about 2 cm.",
        "palette": "terracotta",
        "created_order": 1,
    }
    base.update(overrides)
    return base


def document(items=None) -> dict:
    return {
        "schema_version": 1,
        "image_width": 768,
        "image_height": 1024,
        "items": [mark()] if items is None else items,
    }


# --- determinism ------------------------------------------------------------


def test_the_same_input_produces_byte_identical_output():
    """The golden-bytes property. If this fails, the renderer version is stale."""
    key = store_original()
    first = compose_annotated_png(storage_key=key, document=document())
    second = compose_annotated_png(storage_key=key, document=document())
    assert first.content == second.content
    assert first.sha256 == second.sha256


def test_a_different_document_produces_different_output():
    """Guards against the previous test passing because nothing is drawn."""
    key = store_original()
    one = compose_annotated_png(storage_key=key, document=document())
    two = compose_annotated_png(
        storage_key=key,
        document=document([mark(geometry={"point": {"x": 0.9, "y": 0.9}})]),
    )
    assert one.content != two.content


def test_the_renderer_version_is_reported():
    key = store_original()
    render = compose_annotated_png(storage_key=key, document=document())
    assert render.renderer_version == DESIGN_ANNOTATION_RENDERER_VERSION


# --- output identity --------------------------------------------------------


def test_the_filename_is_fixed_and_server_owned():
    """Never the design title, never a client value."""
    key = store_original()
    annotated = compose_annotated_png(storage_key=key, document=document())
    plain = compose_annotated_png(storage_key=key, document=document(), annotated=False)
    assert annotated.filename == "sitara-concept-annotations.png"
    assert plain.filename == "sitara-concept.png"
    assert annotated.content_type == plain.content_type == "image/png"


def test_the_output_is_a_png_at_the_fixed_page_width():
    key = store_original()
    render = compose_annotated_png(storage_key=key, document=document())
    with Image.open(io.BytesIO(render.content)) as opened:
        assert opened.format == "PNG"
        assert opened.width == PAGE_WIDTH == render.width
        assert opened.height == render.height


def test_the_output_carries_no_metadata():
    """No EXIF, no text chunks, no timestamp chunk — all of which would also
    break byte-determinism."""
    key = store_original()
    render = compose_annotated_png(storage_key=key, document=document())
    with Image.open(io.BytesIO(render.content)) as opened:
        assert not opened.info.get("exif")
        assert not opened.text
        assert "tIME" not in render.content[:4096].decode("latin-1", "ignore")
    assert b"tEXt" not in render.content
    assert b"iTXt" not in render.content
    assert b"tIME" not in render.content


# --- the original is never touched ------------------------------------------


def test_the_stored_original_is_byte_identical_afterwards():
    content = synthetic_original()
    key = store_original(content)
    compose_annotated_png(storage_key=key, document=document())
    with storages["design_images"].open(key, "rb") as handle:
        assert handle.read() == content


def test_the_composite_is_never_written_back_to_storage():
    key = store_original()
    before = set(storages["design_images"].listdir("design-images/test/v1")[1])
    compose_annotated_png(storage_key=key, document=document())
    after = set(storages["design_images"].listdir("design-images/test/v1")[1])
    assert after == before


# --- marks ------------------------------------------------------------------


@pytest.mark.parametrize(
    "geometry,kind",
    [
        ({"point": {"x": 0.5, "y": 0.5}}, "pin"),
        ({"start": {"x": 0.2, "y": 0.2}, "end": {"x": 0.8, "y": 0.7}}, "arrow"),
        ({"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.4}, "rectangle"),
        (
            {"points": [{"x": 0.2, "y": 0.2}, {"x": 0.4, "y": 0.5}, {"x": 0.6, "y": 0.3}]},
            "freehand",
        ),
    ],
)
def test_every_mark_type_composes(geometry, kind):
    key = store_original()
    render = compose_annotated_png(
        storage_key=key, document=document([mark(type=kind, geometry=geometry)])
    )
    assert render.content


@pytest.mark.parametrize("palette", sorted(MARK_COLOURS))
def test_every_palette_entry_composes_and_differs(palette):
    key = store_original()
    render = compose_annotated_png(storage_key=key, document=document([mark(palette=palette)]))
    other = compose_annotated_png(storage_key=key, document=document([mark(palette="terracotta")]))
    assert (
        render.content == other.content
        if palette == "terracotta"
        else render.content != other.content
    )


def test_a_mark_lands_where_its_normalised_coordinates_say():
    """Two pins at opposite corners must differ, and each must differ from the
    plain render — proving the coordinates are actually used at all.

    Note the fixture points are on the x=y diagonal, so this cannot detect an
    x/y transposition; ``test_a_mark_lands_in_the_half_of_the_image_its_
    coordinates_name`` below is the test that does."""
    key = store_original()
    plain = compose_annotated_png(storage_key=key, document=document(), annotated=False)
    top_left = compose_annotated_png(
        storage_key=key, document=document([mark(geometry={"point": {"x": 0.05, "y": 0.05}})])
    )
    bottom_right = compose_annotated_png(
        storage_key=key, document=document([mark(geometry={"point": {"x": 0.95, "y": 0.95}})])
    )
    assert top_left.content != bottom_right.content
    assert top_left.content != plain.content


def dark_original(key: str) -> str:
    """A flat near-black original with NO white pixels anywhere, so any white
    found inside the image area can only have been drawn on top of it."""
    solid = Image.new("RGB", (768, 1024), (10, 20, 30))
    buffer = io.BytesIO()
    solid.save(buffer, format="WEBP", lossless=True)
    return store_original(buffer.getvalue(), key=key)


def image_area_colours(render) -> set:
    """The distinct colours inside the pasted image region only.

    Derived from the module's own page constants rather than hardcoded, so the
    assertion cannot silently start sampling the cream page background if the
    layout ever moves."""
    from sitara.media import annotation_render as module

    left = module._MARGIN
    top = module._HEADER_HEIGHT
    width = module.PAGE_WIDTH - module._MARGIN * 2
    with Image.open(io.BytesIO(render.content)) as page:
        height = min(round(1024 * width / 768), page.height - top)
        return set(page.crop((left, top, left + width, top + height)).convert("RGB").getdata())


@pytest.mark.parametrize(
    "kind,geometry",
    [
        ("pin", {"point": {"x": 0.5, "y": 0.5}}),
        ("arrow", {"start": {"x": 0.3, "y": 0.3}, "end": {"x": 0.7, "y": 0.7}}),
        ("rectangle", {"x": 0.2, "y": 0.2, "width": 0.5, "height": 0.4}),
        (
            "freehand",
            {"points": [{"x": 0.2, "y": 0.3}, {"x": 0.5, "y": 0.5}, {"x": 0.8, "y": 0.4}]},
        ),
    ],
)
def test_every_mark_type_is_drawn_over_a_white_halo(kind, geometry):
    """Not styling. The accent measures 3.03:1 on cream and less on a maroon
    render, so the halo is what makes a mark legible over arbitrary photography
    — the handoff calls it out explicitly and it applies to every mark type."""
    key = dark_original(f"design-images/test/v1/dark-{kind}.webp")
    render = compose_annotated_png(
        storage_key=key, document=document([mark(type=kind, geometry=geometry)])
    )
    assert (255, 255, 255) in image_area_colours(render)


def drawn_centroid(render) -> tuple[float, float]:
    """Where the drawn marks sit, as fractions of the pasted image area.

    Only meaningful over :func:`dark_original`, which contains no white pixel
    anywhere, so every white pixel inside the image area is one the compositor
    drew — the halo every mark type is stroked with."""
    from sitara.media import annotation_render as module

    left = module._MARGIN
    top = module._HEADER_HEIGHT
    width = module.PAGE_WIDTH - module._MARGIN * 2
    with Image.open(io.BytesIO(render.content)) as page:
        height = min(round(1024 * width / 768), page.height - top)
        area = page.crop((left, top, left + width, top + height)).convert("RGB")
        pixels = list(area.getdata())
    hits = [index for index, pixel in enumerate(pixels) if pixel == (255, 255, 255)]
    assert hits, "no drawn pixels found inside the image area"
    return (
        sum(index % width for index in hits) / len(hits) / width,
        sum(index // width for index in hits) / len(hits) / height,
    )


@pytest.mark.parametrize(
    "kind,geometry",
    [
        ("pin", {"point": {"x": 0.2, "y": 0.8}}),
        ("rectangle", {"x": 0.08, "y": 0.62, "width": 0.28, "height": 0.3}),
        ("arrow", {"start": {"x": 0.1, "y": 0.7}, "end": {"x": 0.3, "y": 0.9}}),
        (
            "freehand",
            {"points": [{"x": 0.1, "y": 0.7}, {"x": 0.2, "y": 0.8}, {"x": 0.3, "y": 0.9}]},
        ),
    ],
)
def test_a_mark_lands_in_the_half_of_the_image_its_coordinates_name(kind, geometry):
    """The transposition guard, which a diagonal fixture cannot be.

    Every point here is deliberately asymmetric — x well below 0.5, y well above
    it — and the original is 768x1024, so denormalising y against the width (or x
    against the height) moves the mark to the opposite side of the page instead of
    landing on the same pixel it started from. CLAUDE.md §14 singles out this
    class of coordinate bug precisely because the output still looks plausible;
    asserting only that two renders differ would not notice it. A compositor that
    ignored the coordinates entirely and drew at the centre also fails here."""
    key = dark_original(f"design-images/test/v1/dark-half-{kind}.webp")
    render = compose_annotated_png(
        storage_key=key, document=document([mark(type=kind, geometry=geometry)])
    )

    x, y = drawn_centroid(render)
    assert x < 0.45, f"{kind} drawn at x={x:.3f}; its coordinates put it in the left half"
    assert y > 0.55, f"{kind} drawn at y={y:.3f}; its coordinates put it in the lower half"


def test_the_image_area_has_no_white_without_a_mark():
    """The control for the halo tests: white in the image area really does come
    from the mark, not from the page or the original."""
    key = dark_original("design-images/test/v1/dark-plain.webp")
    render = compose_annotated_png(storage_key=key, document=document([]), annotated=False)
    assert (255, 255, 255) not in image_area_colours(render)


def test_marks_are_numbered_by_created_order_not_list_order():
    key = store_original()
    forward = compose_annotated_png(
        storage_key=key,
        document=document(
            [
                mark(id="a" * 8 + "-0000-4000-8000-000000000001", created_order=1, note="One."),
                mark(id="b" * 8 + "-0000-4000-8000-000000000002", created_order=2, note="Two."),
            ]
        ),
    )
    reversed_list = compose_annotated_png(
        storage_key=key,
        document=document(
            [
                mark(id="b" * 8 + "-0000-4000-8000-000000000002", created_order=2, note="Two."),
                mark(id="a" * 8 + "-0000-4000-8000-000000000001", created_order=1, note="One."),
            ]
        ),
    )
    assert forward.content == reversed_list.content


# --- the legend -------------------------------------------------------------


def test_the_plain_render_has_no_marks_and_no_legend():
    key = store_original()
    plain = compose_annotated_png(storage_key=key, document=document(), annotated=False)
    annotated = compose_annotated_png(storage_key=key, document=document())
    assert plain.height < annotated.height
    assert plain.content != annotated.content


def test_an_empty_document_still_renders_the_image():
    key = store_original()
    render = compose_annotated_png(storage_key=key, document=document([]))
    assert render.content
    with Image.open(io.BytesIO(render.content)) as opened:
        assert opened.width == PAGE_WIDTH


def test_more_notes_make_a_taller_page():
    """The legend grows the canvas rather than overflowing it."""
    key = store_original()
    one = compose_annotated_png(storage_key=key, document=document([mark()]))
    many = compose_annotated_png(
        storage_key=key,
        document=document(
            [
                mark(
                    id=f"{index:08x}-0000-4000-8000-000000000001",
                    created_order=index,
                    note=f"Note number {index}.",
                )
                for index in range(1, 11)
            ]
        ),
    )
    assert many.height > one.height


def test_an_empty_note_renders_the_no_note_placeholder():
    """Numbering must stay continuous, so a blank note still occupies a row."""
    key = store_original()
    blank = compose_annotated_png(storage_key=key, document=document([mark(note="")]))
    filled = compose_annotated_png(storage_key=key, document=document([mark(note=EMPTY_NOTE)]))
    # Rendering an empty note produces the same page as literally writing the
    # placeholder — i.e. the placeholder really is what gets drawn.
    assert blank.content == filled.content


def test_a_long_note_is_truncated_not_overflowed():
    """A note at the schema's 140-character ceiling must still fit its cell, so
    the page height matches a short note's."""
    key = store_original()
    short = compose_annotated_png(storage_key=key, document=document([mark(note="Short.")]))
    longest = compose_annotated_png(
        storage_key=key, document=document([mark(note="Lengthen the hem border " * 5 + "end.")])
    )
    assert longest.height <= short.height + 200
    assert longest.content != short.content


def test_a_single_unbroken_token_cannot_escape_its_column():
    """One pathological word must be broken by character rather than overflow."""
    key = store_original()
    render = compose_annotated_png(storage_key=key, document=document([mark(note="W" * 140)]))
    assert render.content


# --- honest Unicode handling ------------------------------------------------


@pytest.mark.parametrize(
    "note",
    ["दुपट्टा को थोड़ा लंबा करें", "نستعلیق", "শাড়ির আঁচল", "ਦੁਪੱਟਾ"],
)
def test_a_note_the_font_cannot_draw_is_declined_not_boxed(note):
    """The vendored Noto Sans has no Devanagari/Arabic/Bengali/Gurmukhi, and the
    pinned Pillow has no raqm, so adding those families would still produce
    broken conjuncts and unjoined Arabic. Emitting an honest line beats emitting
    tofu boxes or wrong glyphs."""
    key = store_original()
    actual = compose_annotated_png(storage_key=key, document=document([mark(note=note)]))
    honest = compose_annotated_png(
        storage_key=key, document=document([mark(note=UNRENDERABLE_NOTE)])
    )
    assert actual.content == honest.content


def test_accented_latin_and_punctuation_do_render():
    """The reason a font was vendored at all: Pillow's bundled fallback cannot
    draw these, and they occur in this product's ordinary copy."""
    key = store_original()
    accented = compose_annotated_png(
        storage_key=key, document=document([mark(note="Café — champagne piping ₹4,500")])
    )
    declined = compose_annotated_png(
        storage_key=key, document=document([mark(note=UNRENDERABLE_NOTE)])
    )
    assert accented.content != declined.content


# --- bounds and failure modes -----------------------------------------------


def test_an_image_above_the_pixel_ceiling_is_refused(settings):
    settings.ANNOTATION_RENDER_MAX_PIXELS = 1000
    key = store_original()
    with pytest.raises(DesignAnnotationRenderError):
        compose_annotated_png(storage_key=key, document=document())


def test_an_image_above_the_byte_ceiling_is_refused(settings):
    settings.ANNOTATION_RENDER_MAX_BYTES = 64
    key = store_original()
    with pytest.raises(DesignAnnotationRenderError):
        compose_annotated_png(storage_key=key, document=document())


def test_a_missing_original_is_a_controlled_failure():
    with pytest.raises(DesignAnnotationRenderError):
        compose_annotated_png(
            storage_key="design-images/absent/v1/original.webp", document=document()
        )


def test_undecodable_bytes_are_a_controlled_failure():
    key = store_original(b"not an image at all", key="design-images/test/v1/broken.webp")
    with pytest.raises(DesignAnnotationRenderError):
        compose_annotated_png(storage_key=key, document=document())


def test_a_storage_outage_is_a_controlled_failure(monkeypatch):
    def explode(*args, **kwargs):
        raise OSError("backend unreachable at http://minio:9000/sitara-private")

    monkeypatch.setattr(storages["design_images"], "open", explode)
    with pytest.raises(DesignAnnotationRenderError):
        compose_annotated_png(storage_key=KEY, document=document())


def test_no_failure_message_leaks_a_storage_detail():
    """Messages are safe to log and safe to map onto an API code."""
    secret_key = "design-images/9f1c/v1/original.webp"
    with pytest.raises(DesignAnnotationRenderError) as raised:
        compose_annotated_png(storage_key=secret_key, document=document())
    message = str(raised.value)
    for leaked in (secret_key, "9f1c", "minio", "http", "bucket", "sitara-private"):
        assert leaked not in message


def test_a_tampered_font_fails_closed(monkeypatch):
    """The font is part of the deterministic contract, so a swapped file must
    stop the render rather than silently change every byte."""
    import sitara.media.annotation_render as renderer

    monkeypatch.setattr(renderer, "_FONT_SHA256", "0" * 64)
    key = store_original()
    with pytest.raises(DesignAnnotationRenderError):
        compose_annotated_png(storage_key=key, document=document())


# --- no network -------------------------------------------------------------


def test_rendering_opens_no_socket(monkeypatch):
    """No font download, no provider call, nothing."""
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("composition attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    key = store_original()
    assert compose_annotated_png(storage_key=key, document=document()).content


# --- the guards added after review ------------------------------------------


def test_an_extreme_aspect_ratio_is_refused_before_the_resize(monkeypatch):
    """The original's pixel COUNT is bounded, but its aspect ratio is not. An
    8 x 2,999,999 original is under the ceiling yet scales to a 1492 x 559M
    target, so the check must precede the resize, not follow it. Without the
    pre-check, Pillow is asked for roughly 2.5TB of RGB buffer."""
    # PNG, not WebP: WebP caps at 16 383 pixels per side, and the whole point of
    # this fixture is a side the format would refuse.
    sliver = Image.new("RGB", (8, 60_000), (10, 20, 30))
    buffer = io.BytesIO()
    sliver.save(buffer, format="PNG")
    key = store_original(buffer.getvalue(), key="design-images/test/v1/sliver.png")
    # Under the pixel ceiling as an original (480 000 px), catastrophic once
    # scaled to the fixed page width.
    assert 8 * 60_000 < 24_000_000

    def forbidden(*args, **kwargs):
        raise AssertionError("resize was attempted before the ceiling check")

    monkeypatch.setattr(Image.Image, "resize", forbidden)
    with pytest.raises(DesignAnnotationRenderError):
        compose_annotated_png(storage_key=key, document=document())


def test_more_marks_than_the_render_ceiling_are_refused():
    """Defence in depth. The schema caps items at 100, but this module cannot
    see that validation and runs inside a Celery worker."""
    from sitara.media.annotation_render import MAX_RENDERED_MARKS

    key = store_original()
    too_many = [
        mark(id=f"{index:08x}-0000-4000-8000-000000000001", created_order=index)
        for index in range(1, MAX_RENDERED_MARKS + 2)
    ]
    with pytest.raises(DesignAnnotationRenderError):
        compose_annotated_png(storage_key=key, document=document(too_many))


def test_a_note_longer_than_the_render_ceiling_is_clamped_not_wrapped_forever():
    """_wrap's per-word chop search is superlinear in one unbroken token, so the
    clamp must apply before any measurement. The schema caps notes at 140; this
    proves the renderer does not depend on that."""
    key = store_original()
    render = compose_annotated_png(storage_key=key, document=document([mark(note="W" * 20_000)]))
    assert render.content


def test_a_stalled_storage_read_is_abandoned_at_the_deadline(settings, monkeypatch):
    """The deadline is enforced with future.result(timeout=...), not polled
    between chunks — a single hung read() cannot outlast it.

    The stall is an Event this test releases once it has measured, not a fixed
    sleep. ``future.cancel()`` cannot interrupt an already-running task, and
    ``concurrent.futures`` registers an atexit hook that joins every worker
    thread a pool ever started — so a thread left sleeping here would stall the
    whole pytest process at exit, long after the last reported result. The
    ``wait`` timeout is only a backstop against this test itself failing to
    release; the ``finally`` is what normally frees the worker.
    """
    import threading
    import time as _time

    settings.ANNOTATION_RENDER_READ_DEADLINE_SECONDS = 1
    release = threading.Event()

    class StalledFile:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            release.wait(timeout=30)
            return b""

    monkeypatch.setattr(storages["design_images"], "open", lambda *a, **k: StalledFile())

    started = _time.monotonic()
    try:
        with pytest.raises(DesignAnnotationRenderError):
            compose_annotated_png(storage_key=KEY, document=document())
        elapsed = _time.monotonic() - started
    finally:
        release.set()
    assert elapsed < 10, f"the deadline did not bound the read (took {elapsed:.1f}s)"


def test_a_glyph_check_failure_is_logged_not_silent(caplog):
    """Without a log line, a caller bug threading the wrong font would make
    EVERY note decline as unrenderable with no evidence to search for."""
    import logging as _logging

    from sitara.media import annotation_render

    def explode(self, *args, **kwargs):
        raise RuntimeError("freetype exploded")

    key = store_original()
    with caplog.at_level(_logging.WARNING, logger=annotation_render.__name__):
        from PIL import ImageFont as _ImageFont

        original_getmask = _ImageFont.FreeTypeFont.getmask
        calls = {"n": 0}

        def flaky(self, text, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] > 2 and len(text) == 1:
                raise RuntimeError("freetype exploded")
            return original_getmask(self, text, *args, **kwargs)

        _ImageFont.FreeTypeFont.getmask = flaky
        try:
            compose_annotated_png(storage_key=key, document=document())
        finally:
            _ImageFont.FreeTypeFont.getmask = original_getmask

    assert any("glyph_check_failed" in record.message for record in caplog.records)
