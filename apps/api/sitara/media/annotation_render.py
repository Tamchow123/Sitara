"""Deterministic annotated-PNG composition (Phase 19 Part B).

One pure function turns an owned ``DesignVersion``'s canonical private image plus
its validated annotation document into a flattened PNG: the image with numbered
marks drawn over it, and a note legend below. Nothing is persisted — the original
object is read, never written, and the composite exists only as bytes handed back
to the caller.

**Determinism.** Identical input bytes and an identical document produce identical
output bytes under the pinned Pillow and the pinned vendored font.
``DESIGN_ANNOTATION_RENDERER_VERSION`` names that exact behaviour; changing any
observable output requires a version bump plus a reviewed golden-bytes update.
Everything that could vary is fixed here: no timestamps, no PNG text chunks, no
locale, no client-supplied sizes or colours. The palette, stroke widths, marker
radii, page metrics and font are allowlisted constants — a request can choose
*which* allowlisted palette entry a mark uses, never a value.

**The halo is not decoration.** ``--color-accent`` measures 3.03:1 on cream and
far less on a maroon lehenga, so every stroke, ring and badge is drawn twice: a
white halo underneath, then the palette colour on top. It is what makes a mark
legible over arbitrary photography, and it applies in the export exactly as in
the browser.

**Text has a hard limit, stated honestly.** The vendored Noto Sans covers Latin,
Greek, Cyrillic, punctuation and currency. It does not cover Devanagari, Arabic,
Bengali or Gurmukhi, and adding those families would not fix it: the pinned
Pillow wheel reports ``raqm: False``/``harfbuzz: False``/``fribidi: False``, so
conjuncts would break apart, matras would detach, Arabic would not join and
right-to-left runs would come out reversed. Rendering a bride's Devanagari note
as broken glyphs is worse than declining, so a note containing characters this
font cannot draw is replaced by one honest line pointing the owner back to the
workspace, where their own system fonts render it correctly. See
``fonts/README.md``.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from .exceptions import DesignAnnotationRenderError
from .ingest import design_image_storage

logger = logging.getLogger(__name__)

# The exact composition behaviour. Nothing persists it, so a bump needs no
# migration — but it does need a reviewed golden-bytes update.
#
# It stayed at 1.0.0 through the review rounds that built this module, on the
# reasoning that the version records SHIPPED behaviour and nothing had shipped
# yet — the golden manifest was simply regenerated each time instead. That
# reasoning expired the moment this file was first committed. From here on any
# change to the rendered output bumps this version in the same commit as the
# regenerated manifest, with no "it was never really shipped" exception.
DESIGN_ANNOTATION_RENDERER_VERSION = "1.0.0"

# Server-owned, fixed. Never the design title, never a client value.
ANNOTATED_FILENAME = "sitara-concept-annotations.png"
PLAIN_FILENAME = "sitara-concept.png"
RENDER_CONTENT_TYPE = "image/png"

# --- the vendored font ------------------------------------------------------

_FONT_DIR = __file__.rsplit("annotation_render.py", 1)[0] + "fonts/"
_FONT_PATH = _FONT_DIR + "NotoSans-Regular.ttf"
# Pinned so a swapped or corrupted font fails closed rather than silently
# changing every byte of the golden output.
_FONT_SHA256 = "478c558ea716033cd60c03438f628dfa75694dcf6b5f6d505a2f05fd2b4f3823"

# --- the fixed page ---------------------------------------------------------

PAGE_WIDTH = 1620  # the handoff's 2x export width
_MARGIN = 64
_HEADER_HEIGHT = 132
_LEGEND_TOP_GAP = 48
_LEGEND_TITLE_HEIGHT = 56
_LEGEND_ROW_GAP = 22
_LEGEND_COLUMNS = 2
_LEGEND_COLUMN_GAP = 48
_LEGEND_BADGE_WIDTH = 64
_LEGEND_LINE_HEIGHT = 34
_LEGEND_MAX_LINES = 3
_FOOTER_HEIGHT = 76

_TITLE_SIZE = 40
_SUBTITLE_SIZE = 22
_LEGEND_TITLE_SIZE = 24
_LEGEND_NOTE_SIZE = 24
_FOOTER_SIZE = 20

# --- the fixed palette (design tokens, not request values) ------------------

_CREAM = (245, 234, 216)
_WORKTABLE = (238, 231, 219)
_RULE = (220, 211, 196)
_INK = (46, 43, 37)
_MUTED = (100, 92, 80)
_WHITE = (255, 255, 255)

MARK_COLOURS = {
    "terracotta": (198, 113, 57),
    "sage": (122, 138, 94),
    "ink": (46, 43, 37),
}

# --- the fixed mark geometry ------------------------------------------------

_STROKE = 6
_HALO_EXTRA = 6  # the halo is drawn this much wider than the stroke, each side
_PIN_RADIUS = 26
_BADGE_RADIUS = 24
_ARROW_HEAD = 34
_BADGE_TEXT_SIZE = 26

# What replaces a note this font cannot draw. Fixed ASCII-safe wording, so the
# fallback can itself always be rendered.
UNRENDERABLE_NOTE = "(note uses a script this export cannot render - open the workspace to read it)"
EMPTY_NOTE = "(no note)"
_ELLIPSIS = "…"

# This module's OWN ceilings on what it will draw, deliberately duplicating what
# the annotation schema already enforces. Defence in depth: this code runs in a
# Celery worker, cannot see that validation, and would otherwise trust a contract
# it has no way to check. Both are comfortably above the schema's own limits, so
# a valid document never reaches them.
MAX_RENDERED_MARKS = 200
MAX_RENDERED_NOTE_LENGTH = 500

# ONE shared, bounded pool for every render's storage read, so the threads this
# path can hold is a fixed process-wide cap rather than a function of job rate.
# An expired deadline abandons its read: the running one occupies its slot until
# the storage client itself gives up, and a hung backend therefore SATURATES the
# pool instead of growing it. Sized smaller than delivery.py's pool because a
# render is a background job, not a request in a user's 5-second budget.
_read_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="annotation-render-read")


@dataclass(frozen=True)
class AnnotatedRender:
    """The finished composite. Bytes only — nothing is written to storage."""

    content: bytes
    filename: str
    content_type: str
    width: int
    height: int
    renderer_version: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------


def _font_bytes() -> bytes:
    try:
        with open(_FONT_PATH, "rb") as handle:
            raw = handle.read()
    except OSError:
        raise DesignAnnotationRenderError("the export font is unavailable") from None
    if hashlib.sha256(raw).hexdigest() != _FONT_SHA256:
        raise DesignAnnotationRenderError("the export font failed its integrity check")
    return raw


def _font(raw: bytes, size: int) -> ImageFont.FreeTypeFont:
    """A sized face from already-verified font bytes.

    The bytes are read and hashed ONCE per render and threaded to each size,
    rather than cached at import or re-read per size. That keeps the integrity
    check on the bytes actually used — a font swapped on disk is caught on the
    next render, not on the next worker restart — without paying for six reads
    and six SHA-256 passes over 621KB per composite."""
    try:
        return ImageFont.truetype(io.BytesIO(raw), size=size)
    except OSError:
        raise DesignAnnotationRenderError("the export font could not be loaded") from None


def _notdef_mask(font: ImageFont.FreeTypeFont) -> bytes:
    # U+E000 is private-use: no real font maps it, so its glyph IS .notdef.
    return bytes(font.getmask(""))


def _is_renderable(text: str, font: ImageFont.FreeTypeFont, notdef: bytes) -> bool:
    """Whether every character has a real glyph in this font.

    Compared against the .notdef mask rather than a codepoint allowlist, so the
    answer always reflects the font actually in use."""
    for char in text:
        if char.isspace():
            continue
        try:
            if bytes(font.getmask(char)) == notdef:
                return False
        except Exception as exc:  # noqa: BLE001 - any glyph failure means "cannot draw it"
            # Logged, not silent. Without this, a caller bug (the wrong font
            # threaded in by a later refactor) would make EVERY note decline as
            # unrenderable with no evidence an operator could search for.
            logger.warning(
                "annotation_render.glyph_check_failed",
                extra={"exception_type": type(exc).__name__},
            )
            return False
    return True


# ---------------------------------------------------------------------------
# Reading the original, bounded in size AND time
# ---------------------------------------------------------------------------


def _read_bytes(storage_key: str) -> bytes:
    """The blocking storage read, size-bounded. Runs on the pool, not inline."""
    limit = settings.ANNOTATION_RENDER_MAX_BYTES
    store = design_image_storage()
    chunks: list[bytes] = []
    total = 0
    with store.open(storage_key, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise DesignAnnotationRenderError("the original image is too large to annotate")
            chunks.append(chunk)
    return b"".join(chunks)


def _read_original(storage_key: str) -> bytes:
    """The canonical original's bytes, read through the storage boundary.

    No signed URL is ever accepted from a client — the key comes from the owned
    DesignVersion row and the bytes come from the private bucket.

    The read runs on a bounded pool and the deadline is enforced with
    ``future.result(timeout=...)``, the same mechanism ``delivery.py`` uses and
    for the same reason: a deadline polled between chunks cannot interrupt a
    single stalled ``read()``, so the real bound would silently become the
    storage client's own botocore timeouts — which are sized for the slower
    ingest path (roughly 30s across retries) and are not this consumer's budget.
    On expiry the future is abandoned to wind down inside its pool slot, so a
    hung backend saturates the pool rather than growing threads."""
    future = _read_pool.submit(_read_bytes, storage_key)
    try:
        return future.result(timeout=settings.ANNOTATION_RENDER_READ_DEADLINE_SECONDS)
    except DesignAnnotationRenderError:
        raise
    except FuturesTimeoutError:
        logger.warning("annotation_render.read_timeout")
        raise DesignAnnotationRenderError("reading the original image took too long") from None
    except Exception as exc:  # noqa: BLE001 - storage boundary; nothing else is safe to leak
        logger.warning(
            "annotation_render.read_failed", extra={"exception_type": type(exc).__name__}
        )
        raise DesignAnnotationRenderError("the original image could not be read") from None
    finally:
        future.cancel()


def _open_original(raw: bytes) -> Image.Image:
    """Decode to RGB, bounded by the pixel ceiling BEFORE any allocation."""
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            width, height = probe.size
            if width <= 0 or height <= 0:
                raise DesignAnnotationRenderError("the original image has no usable size")
            if width * height > settings.ANNOTATION_RENDER_MAX_PIXELS:
                raise DesignAnnotationRenderError("the original image is too large to annotate")
            probe.load()
            return probe.convert("RGB")
    except DesignAnnotationRenderError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow raises a wide family here
        logger.warning(
            "annotation_render.decode_failed", extra={"exception_type": type(exc).__name__}
        )
        raise DesignAnnotationRenderError("the original image could not be decoded") from None


# ---------------------------------------------------------------------------
# Drawing one mark — halo first, then the palette colour
# ---------------------------------------------------------------------------


def _point(geometry_point: dict, box: tuple[int, int, int, int]) -> tuple[float, float]:
    """A normalised point placed inside the image box.

    Coordinates are fractions of the image, never viewport pixels, so a mark
    lands in the same place whatever size the image was displayed at."""
    left, top, width, height = box
    return (left + geometry_point["x"] * width, top + geometry_point["y"] * height)


def _stroked_line(draw, points, colour, width):
    draw.line(points, fill=_WHITE, width=width + _HALO_EXTRA * 2, joint="curve")
    draw.line(points, fill=colour, width=width, joint="curve")


def _stroked_ellipse(draw, bounds, colour, width):
    draw.ellipse(bounds, outline=_WHITE, width=width + _HALO_EXTRA)
    draw.ellipse(bounds, outline=colour, width=width)


def _stroked_rectangle(draw, bounds, colour, width):
    draw.rectangle(bounds, outline=_WHITE, width=width + _HALO_EXTRA)
    draw.rectangle(bounds, outline=colour, width=width)


def _draw_arrow_head(draw, start, end, colour):
    """A filled triangular head, haloed like every other stroke."""
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    spread = math.radians(26)
    left = (
        end[0] - _ARROW_HEAD * math.cos(angle - spread),
        end[1] - _ARROW_HEAD * math.sin(angle - spread),
    )
    right = (
        end[0] - _ARROW_HEAD * math.cos(angle + spread),
        end[1] - _ARROW_HEAD * math.sin(angle + spread),
    )
    draw.polygon([end, left, right], fill=_WHITE, outline=_WHITE, width=_HALO_EXTRA * 2)
    draw.polygon([end, left, right], fill=colour)


def _draw_badge(draw, centre, number: int, colour, font):
    """The numbered badge tying a mark to its legend row."""
    x, y = centre
    bounds = (x - _BADGE_RADIUS, y - _BADGE_RADIUS, x + _BADGE_RADIUS, y + _BADGE_RADIUS)
    draw.ellipse(bounds, fill=_WHITE)
    inner = (bounds[0] + 4, bounds[1] + 4, bounds[2] - 4, bounds[3] - 4)
    draw.ellipse(inner, fill=colour)
    label = str(number)
    draw.text((x, y), label, font=font, fill=_WHITE, anchor="mm")


def _draw_mark(draw, mark: dict, number: int, box, badge_font) -> None:
    colour = MARK_COLOURS[mark["palette"]]
    geometry = mark["geometry"]
    kind = mark["type"]

    if kind == "pin":
        centre = _point(geometry["point"], box)
        bounds = (
            centre[0] - _PIN_RADIUS,
            centre[1] - _PIN_RADIUS,
            centre[0] + _PIN_RADIUS,
            centre[1] + _PIN_RADIUS,
        )
        _stroked_ellipse(draw, bounds, colour, _STROKE)
        badge_at = (centre[0], centre[1] - _PIN_RADIUS - _BADGE_RADIUS - 6)
    elif kind == "arrow":
        start = _point(geometry["start"], box)
        end = _point(geometry["end"], box)
        _stroked_line(draw, [start, end], colour, _STROKE)
        _draw_arrow_head(draw, start, end, colour)
        badge_at = start
    elif kind == "rectangle":
        left, top, _, _ = box
        _, _, width, height = box
        x0 = left + geometry["x"] * width
        y0 = top + geometry["y"] * height
        x1 = x0 + geometry["width"] * width
        y1 = y0 + geometry["height"] * height
        _stroked_rectangle(draw, (x0, y0, x1, y1), colour, _STROKE)
        badge_at = (x0, y0)
    elif kind == "freehand":
        points = [_point(point, box) for point in geometry["points"]]
        _stroked_line(draw, points, colour, _STROKE)
        badge_at = points[0]
    else:  # pragma: no cover - the schema allowlist makes this unreachable
        raise DesignAnnotationRenderError("unsupported mark type")

    _draw_badge(draw, badge_at, number, colour, badge_font)


# ---------------------------------------------------------------------------
# The note legend
# ---------------------------------------------------------------------------


def _wrap(text: str, font, max_width: int, max_lines: int) -> list[str]:
    """Greedy word wrap with a deterministic ellipsis truncation.

    A note that cannot fit its cell is truncated, never overflowed. Wrapping is
    by whitespace; a single word longer than the cell is broken by character so
    one pathological token cannot escape the column."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while font.getlength(word) > max_width and len(word) > 1:
            cut = len(word)
            while cut > 1 and font.getlength(word[:cut]) > max_width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
        if len(lines) > max_lines:
            break
    if current:
        lines.append(current)

    if len(lines) <= max_lines:
        return lines
    kept = lines[: max_lines - 1]
    last = lines[max_lines - 1]
    while last and font.getlength(last + _ELLIPSIS) > max_width:
        last = last[:-1]
    kept.append(last.rstrip() + _ELLIPSIS)
    return kept


def _legend_text(note: str, font, notdef) -> str:
    if not note:
        return EMPTY_NOTE
    # Clamped locally before any measurement. The annotation schema already caps
    # a note, but this module cannot see that validation, and _wrap's per-word
    # chop search is superlinear in the length of one unbroken token.
    if len(note) > MAX_RENDERED_NOTE_LENGTH:
        note = note[:MAX_RENDERED_NOTE_LENGTH]
    if not _is_renderable(note, font, notdef):
        return UNRENDERABLE_NOTE
    return note


def _legend_row_heights(cells: list[list[str]]) -> list[int]:
    """The pixel height of each legend row.

    A row is as tall as its tallest cell. The last row may be partly empty when
    the mark count is odd, so it is measured from the cells it actually has."""
    heights: list[int] = []
    for start in range(0, len(cells), _LEGEND_COLUMNS):
        row = cells[start : start + _LEGEND_COLUMNS]
        heights.append(max(len(cell) for cell in row) * _LEGEND_LINE_HEIGHT + _LEGEND_ROW_GAP)
    return heights


def _legend_height(cells: list[list[str]]) -> int:
    if not cells:
        return 0
    return _LEGEND_TOP_GAP + _LEGEND_TITLE_HEIGHT + sum(_legend_row_heights(cells))


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------


def _encode_png(canvas: Image.Image) -> bytes:
    """PNG bytes with every optional chunk suppressed.

    ``pnginfo`` is left empty and no ``tEXt``/``tIME`` chunk is added, so two runs
    over the same input are byte-identical.

    ``compress_level=6`` (zlib's default), not 9, and no ``optimize``. Profiling
    showed encoding to be ~99% of a whole render — the LANCZOS resize is ~34ms and
    all the text drawing ~17ms next to seconds of DEFLATE.

    Two canvases below, because the size cost depends on image entropy and quoting
    one number would misrepresent the other. The first is the ``one_pin`` golden
    fixture, so its bytes are checkable against ``annotation_golden_v1.json``; the
    second is a denser synthetic original. Both are 1620x2421 pages:

        canvas                    optimize + level 9        level 6
        one_pin golden fixture    1.27s    593 377 B        0.21s    668 812 B  (+12.7%)
        denser 768x1024 source    4.99s   ~1524 KiB         0.48s   ~1642 KiB   (+7.7%)

    ``optimize=True`` bought nothing at all over plain level 9 — for PNG, Pillow's
    ``optimize`` only forces the level up, so the old setting paid the slowest
    encode available for bytes identical to plain level 9. Level 6 is 6-10x faster
    for 8-13% more bytes, on a path that runs synchronously inside the delivery
    task. Both levels are equally deterministic; the level is simply one more thing
    the golden manifest pins, so changing it again means regenerating those hashes
    under review.

    Neither canvas is a real photograph — see ``compose_annotated_png`` for what a
    production-scale render actually weighs."""
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", compress_level=6)
    return buffer.getvalue()


def compose_annotated_png(
    *, storage_key: str, document: dict, annotated: bool = True
) -> AnnotatedRender:
    """Compose the flattened export for one design version.

    ``document`` must already be a validated annotation document — this function
    trusts its shape and re-derives nothing from the request. Pass
    ``annotated=False`` for the plain render, which draws no marks and no legend.

    Raises :class:`DesignAnnotationRenderError` for every failure, so a caller
    maps one controlled code and no storage detail escapes.

    **What the output actually weighs.** Nothing here bounds the encoded byte
    length directly; it is bounded only indirectly, by ``MAX_RENDERED_MARKS`` and
    ``ANNOTATION_RENDER_MAX_PIXELS`` capping how large the page can get. That
    indirect bound is loose on paper, so it was measured rather than reasoned
    about — a 1536x2048 photographic original, which is the production shape:

        12 marks x ~140 chars     1.7s    1620x3109   ( 5.0 MP)   3.06 MiB
        200 marks x ~140 chars   10.4s    1620x14765  (23.9 MP)   4.17 MiB
        200 marks x ~500 chars   17.9s    1620x14765  (23.9 MP)   4.19 MiB

    So a realistic export is ~3 MiB and the ceiling-hugging worst case is ~4.2 MiB
    — the 200-mark page lands at 23.9 MP, just under the 24 MP ceiling, which is
    what actually caps the bytes. A caller attaching this to a message should still
    check the length it got rather than trusting these figures: they are a measured
    expectation, not an enforced contract, and a different page width or ceiling
    moves them. The 10-18s upper end matters too — it has to fit inside whatever
    time limit the calling task declares."""
    original = _open_original(_read_original(storage_key))
    try:
        marks = list(document["items"]) if annotated else []
        marks.sort(key=lambda mark: mark["created_order"])

        if len(marks) > MAX_RENDERED_MARKS:
            # Defence in depth. The annotation schema already caps items, but
            # this module cannot see that validation and is about to run inside
            # a Celery worker, so it enforces its own ceiling rather than
            # trusting a contract it cannot check.
            raise DesignAnnotationRenderError("too many marks to render")

        image_width = PAGE_WIDTH - _MARGIN * 2
        scale = image_width / original.width
        image_height = max(1, round(original.height * scale))
        # BEFORE the resize, not after. The original's pixel COUNT is bounded in
        # _open_original, but its aspect ratio is not: a 8 x 2,999,999 original
        # is under the pixel ceiling yet scales to a 1492 x 559,499,813 target.
        # Checking only the finished page height would put this ceiling on the
        # far side of the largest allocation in the function.
        if image_width * image_height > settings.ANNOTATION_RENDER_MAX_PIXELS:
            raise DesignAnnotationRenderError("the image is too large to place on the page")
        resized = original.resize((image_width, image_height), Image.Resampling.LANCZOS)

        face = _font_bytes()  # read and integrity-checked once per render
        title_font = _font(face, _TITLE_SIZE)
        subtitle_font = _font(face, _SUBTITLE_SIZE)
        legend_title_font = _font(face, _LEGEND_TITLE_SIZE)
        note_font = _font(face, _LEGEND_NOTE_SIZE)
        badge_font = _font(face, _BADGE_TEXT_SIZE)
        footer_font = _font(face, _FOOTER_SIZE)
        notdef = _notdef_mask(note_font)

        column_width = (image_width - _LEGEND_COLUMN_GAP) // _LEGEND_COLUMNS
        note_width = column_width - _LEGEND_BADGE_WIDTH
        cells = [
            _wrap(
                _legend_text(mark["note"], note_font, notdef),
                note_font,
                note_width,
                _LEGEND_MAX_LINES,
            )
            for mark in marks
        ]

        legend_height = _legend_height(cells)
        page_height = _HEADER_HEIGHT + image_height + legend_height + _FOOTER_HEIGHT + _MARGIN
        if PAGE_WIDTH * page_height > settings.ANNOTATION_RENDER_MAX_PIXELS:
            raise DesignAnnotationRenderError("the annotated page would be too large to render")

        canvas = Image.new("RGB", (PAGE_WIDTH, page_height), _CREAM)
        draw = ImageDraw.Draw(canvas)

        draw.text((_MARGIN, 44), "Sitara", font=title_font, fill=_INK)
        draw.text(
            (_MARGIN, 96),
            "Concept design with your notes" if annotated else "Concept design",
            font=subtitle_font,
            fill=_MUTED,
        )

        image_top = _HEADER_HEIGHT
        draw.rectangle(
            (_MARGIN - 8, image_top - 8, _MARGIN + image_width + 8, image_top + image_height + 8),
            fill=_WORKTABLE,
        )
        canvas.paste(resized, (_MARGIN, image_top))

        box = (_MARGIN, image_top, image_width, image_height)
        for number, mark in enumerate(marks, start=1):
            _draw_mark(draw, mark, number, box, badge_font)

        if cells:
            legend_top = image_top + image_height + _LEGEND_TOP_GAP
            draw.text((_MARGIN, legend_top), "ANNOTATIONS", font=legend_title_font, fill=_MUTED)
            draw.line(
                (_MARGIN, legend_top + 38, _MARGIN + image_width, legend_top + 38),
                fill=_RULE,
                width=2,
            )
            cursor = legend_top + _LEGEND_TITLE_HEIGHT
            for row, height in enumerate(_legend_row_heights(cells)):
                for column in range(_LEGEND_COLUMNS):
                    index = row * _LEGEND_COLUMNS + column
                    if index >= len(cells):
                        continue
                    x = _MARGIN + column * (column_width + _LEGEND_COLUMN_GAP)
                    _draw_badge(
                        draw,
                        (x + _BADGE_RADIUS, cursor + _BADGE_RADIUS - 2),
                        index + 1,
                        MARK_COLOURS[marks[index]["palette"]],
                        badge_font,
                    )
                    for line_number, line in enumerate(cells[index]):
                        draw.text(
                            (x + _LEGEND_BADGE_WIDTH, cursor + line_number * _LEGEND_LINE_HEIGHT),
                            line,
                            font=note_font,
                            fill=_INK,
                        )
                cursor += height

        draw.text(
            (_MARGIN, page_height - _FOOTER_HEIGHT + 8),
            "Private concept visualisation. Not a sewing pattern or construction specification.",
            font=footer_font,
            fill=_MUTED,
        )

        content = _encode_png(canvas)
        return AnnotatedRender(
            content=content,
            filename=ANNOTATED_FILENAME if annotated else PLAIN_FILENAME,
            content_type=RENDER_CONTENT_TYPE,
            width=PAGE_WIDTH,
            height=page_height,
            renderer_version=DESIGN_ANNOTATION_RENDERER_VERSION,
        )
    except DesignAnnotationRenderError:
        raise
    except MemoryError:
        # The ceilings above should make this unreachable; if it happens anyway
        # it becomes the same controlled failure rather than killing the worker.
        raise DesignAnnotationRenderError("the annotated page could not be allocated") from None
    except Exception as exc:  # noqa: BLE001 - deliberate composition boundary
        logger.warning(
            "annotation_render.compose_failed", extra={"exception_type": type(exc).__name__}
        )
        raise DesignAnnotationRenderError("the annotated image could not be composed") from None
    finally:
        original.close()


__all__ = [
    "ANNOTATED_FILENAME",
    "DESIGN_ANNOTATION_RENDERER_VERSION",
    "EMPTY_NOTE",
    "MARK_COLOURS",
    "PLAIN_FILENAME",
    "RENDER_CONTENT_TYPE",
    "UNRENDERABLE_NOTE",
    "AnnotatedRender",
    "compose_annotated_png",
]
