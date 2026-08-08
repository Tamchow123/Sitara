"""Strict, versioned annotation-document contract (Phase 19, ADR 0020).

One :class:`~sitara.designs.models.DesignAnnotationDocument` holds the editorial
marks its owner placed over one immutable generated ``DesignVersion`` image. This
module owns the shape of that document and nothing else: no persistence, no
ownership, no rendering, no HTTP.

Three properties matter more than the field list:

1. **Geometry is normalised, never pixels.** Every coordinate is a fraction of
   the image's own width/height in the closed range ``[0, 1]``, so a mark stays
   on the same seam whatever size the image is rendered at — a browser viewport,
   a zoomed canvas, or the server-side PNG composition. Storing viewport pixels
   would silently invalidate every mark the first time the layout changed.
2. **Validation is total over arbitrary JSON.** Anything a client can send —
   a string where a number belongs, ``NaN``, a nested list, ``null`` — becomes a
   controlled :class:`AnnotationDocumentInvalid`, never a raw ``TypeError`` or
   ``KeyError`` escaping as a 500. Pydantic v2 with ``extra="forbid"`` gives that
   for shape; the explicit coordinate validator below gives it for numbers,
   because Python's ``json.loads`` accepts ``NaN``/``Infinity`` even though
   neither is valid JSON.
3. **A note is inert text.** Notes are rendered as escaped text in the browser
   and as plain glyphs in the PNG — never HTML, never Markdown, never a followed
   URL. Normalisation strips both ASCII control characters (Unicode category
   ``Cc``) and the invisible *format* characters (category ``Cf``), which is the
   less obvious half: ``Cf`` carries the bidirectional overrides and isolates
   (``U+202A``–``U+202E``, ``U+2066``–``U+2069``) that let stored bytes render or
   be announced in a different order than they were typed. Escaping stops markup
   injection; it does not stop that. The same note reaches three separately
   trusted surfaces — an ``aria-label`` read aloud by assistive technology, the
   on-canvas chip, and pixel text in a PNG that leaves the system by email — so
   the reordering has to be removed at the point of storage.

Deliberately absent: any executable expression, any client-chosen colour, any
storage key, URL or file path, and any geometry the client expresses in pixels.
"""

import json
import math
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from sitara.content_safety import contains_markup, contains_url, strip_format_characters

# Versions the persisted DOCUMENT SHAPE. Bump only with a documented migration
# strategy for existing persisted rows.
ANNOTATION_SCHEMA_VERSION = 1

MAX_ANNOTATION_ITEMS = 100
ANNOTATION_NOTE_MAX_LENGTH = 140
FREEHAND_MIN_POINTS = 2
FREEHAND_MAX_POINTS = 500

# A cheap raw-length gate applied BEFORE the normalisation pipeline, which is
# linear in the input but not free (NFKC normalisation plus several full-string
# passes and the markup/URL denylist). The product limit is 140 characters
# post-normalisation; this is deliberately far above it so no real note is ever
# refused for a formatting quirk, while an attacker cannot make a megabyte of
# text be normalised only to be rejected for length afterwards.
ANNOTATION_NOTE_RAW_MAX_LENGTH = 2_000

# The longest run of consecutive combining marks a note may carry. Correctly
# composed Devanagari, Arabic and Urdu text stacks one to three marks on a base
# character; dozens is "Zalgo" text, which occupies a single grapheme position
# while defeating the fixed-size legend cell and the aria-label the same note is
# rendered into. Bounded here at the point of storage so neither the browser nor
# the PNG renderer has to reimplement the defence.
MAX_COMBINING_MARK_RUN = 4

# Ceiling on the CANONICAL serialisation (sorted keys, no whitespace), so the
# bound is a property of the content rather than of a client's formatting.
MAX_DOCUMENT_BYTES = 256 * 1024

# The complete, fixed mark palette. Not a colour picker: every value here is
# rendered with the mandatory white halo underneath (ADR 0020), which is what
# makes a mark legible on arbitrary bridal photography. An open colour space
# would let a user choose a mark invisible against their own render.
ANNOTATION_PALETTES = ("terracotta", "sage", "ink")

# The documented degenerate-geometry tolerance. A rectangle thinner than this
# on either axis, or an arrow shorter than this end to end, is a stray click
# rather than a mark: it cannot be seen, selected or nudged, so it is rejected
# at the contract instead of being stored as an invisible row. 0.005 is half a
# percent of the image edge — about 5px on a 1024px-wide render.
MIN_GEOMETRY_EXTENT = 0.005

# The complete set of mark types. Public so the OpenAPI serializers derive
# their choices from here rather than restating the list.
ANNOTATION_ITEM_TYPES = ("pin", "arrow", "rectangle", "freehand")

# A plain canonical UUID and nothing else. Bounded by construction (36 chars),
# so a client cannot smuggle a long or structured id into a persisted document.
_UUID_PATTERN = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE
)

_MODEL_CONFIG = ConfigDict(extra="forbid", validate_assignment=True)


class AnnotationDocumentInvalid(Exception):
    """The supplied annotation document is not a valid document.

    ``code`` is a stable machine code and ``message`` a safe, generic sentence.
    Neither ever echoes note text back — a validation failure must not become a
    reflection channel for the private content it rejected."""

    def __init__(
        self,
        message: str = "The annotation document is not valid.",
        *,
        code: str = "annotation_invalid",
    ):
        self.code = code
        self.message = message
        super().__init__(message)


def _coordinate(value: object) -> float:
    """One normalised coordinate: a real, finite number in ``[0, 1]``.

    Explicit rather than delegated to Pydantic's numeric coercion for two
    reasons. ``bool`` is a subclass of ``int`` in Python, so ``True`` would
    otherwise pass as ``1.0``; and ``json.loads`` accepts the non-standard
    ``NaN``/``Infinity`` literals, either of which would poison every downstream
    transform (an SVG path, a Pillow draw call) rather than failing here."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("must be finite")
    if number < 0.0 or number > 1.0:
        raise ValueError("must be between 0 and 1")
    return number


Coordinate = Annotated[float, BeforeValidator(_coordinate)]


def _strict_int(value: object) -> int:
    """A real integer, not something that merely coerces to one.

    Pydantic's default lax mode would accept the string ``"1024"`` as ``1024``.
    For a dimension that is checked against the server's own canonical value,
    and for an ordering key, that laxness is the wrong default: a client sending
    a string disagrees with the contract, and silently coercing hides it."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be an integer")
    return value


# Not named ``StrictInt``: Pydantic exports its own public ``StrictInt``, and a
# same-named local alias with different semantics is a trap for a reader.
NonCoercedInt = Annotated[int, BeforeValidator(_strict_int)]


def _longest_combining_run(text: str) -> int:
    """The longest run of consecutive combining marks (Mn/Mc/Me)."""
    longest = 0
    run = 0
    for char in text:
        if unicodedata.category(char) in ("Mn", "Mc", "Me"):
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


def _normalise_note(value: object) -> str:
    """Collapse a note to safe, inert, bounded text.

    Order matters. The cheap structural rejections come first, so hostile input
    is refused before any full-string work is paid for. Then the invisible
    characters are stripped (so a denylisted pattern cannot hide behind one, and
    a bidi override cannot survive), then whitespace is collapsed and trimmed,
    and only then is the length measured — so the 140-character limit counts
    characters the reader will actually see rather than the client's formatting."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("must be text")
    if len(value) > ANNOTATION_NOTE_RAW_MAX_LENGTH:
        # Before normalisation, not after: rejecting a megabyte of text must not
        # first cost a megabyte of NFKC normalisation and regex scanning.
        raise ValueError("is far longer than a note may be")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        # A lone surrogate is not encodable UTF-8. ``json.loads`` accepts the
        # "\ud800" escape without complaint, so without this check the character
        # survives every category filter below (its category is Cs, neither Cc
        # nor Cf) and only fails later, when the canonical bytes are produced —
        # as a raw UnicodeEncodeError rather than a controlled rejection. It is
        # also unstorable: PostgreSQL JSONB and the downstream Pillow/SVG
        # renderers all require genuinely valid Unicode.
        raise ValueError("must not contain unpaired surrogate characters")
    # NFKC-normalises and removes every Unicode Cf character.
    text = strip_format_characters(value)
    # Remove ASCII/C1 control characters, which Cf-stripping does not cover.
    # Whitespace becomes a space rather than vanishing, so "a\nb" stays two words.
    text = "".join(" " if char in "\t\n\r\v\f" else char for char in text)
    text = "".join(char for char in text if unicodedata.category(char) != "Cc")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > ANNOTATION_NOTE_MAX_LENGTH:
        raise ValueError(f"must be at most {ANNOTATION_NOTE_MAX_LENGTH} characters")
    if _longest_combining_run(text) > MAX_COMBINING_MARK_RUN:
        raise ValueError("stacks too many combining marks on one character")
    if contains_markup(text):
        raise ValueError("must not contain markup")
    if contains_url(text):
        raise ValueError("must not contain a URL")
    return text


Note = Annotated[str, BeforeValidator(_normalise_note)]


def _normalise_item_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a UUID string")
    if not _UUID_PATTERN.match(value):
        raise ValueError("must be a plain UUID")
    return value.lower()


ItemId = Annotated[str, BeforeValidator(_normalise_item_id)]


class Point(BaseModel):
    model_config = _MODEL_CONFIG

    x: Coordinate
    y: Coordinate


class PinGeometry(BaseModel):
    model_config = _MODEL_CONFIG

    point: Point


class ArrowGeometry(BaseModel):
    model_config = _MODEL_CONFIG

    start: Point
    end: Point

    def model_post_init(self, _context) -> None:
        span = math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)
        if span < MIN_GEOMETRY_EXTENT:
            raise ValueError("arrow is too short to be a mark")


class RectangleGeometry(BaseModel):
    """Origin plus extent rather than two corners, so a rectangle has exactly
    one representation — two corners would let the same shape arrive four ways
    and would put the normalisation burden on every reader."""

    model_config = _MODEL_CONFIG

    x: Coordinate
    y: Coordinate
    width: Coordinate
    height: Coordinate

    def model_post_init(self, _context) -> None:
        if self.width < MIN_GEOMETRY_EXTENT or self.height < MIN_GEOMETRY_EXTENT:
            raise ValueError("rectangle has no visible area")
        # The far edge must also stay inside the image: each field is
        # individually in [0, 1], but x + width could still run off the edge.
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("rectangle extends beyond the image")


class FreehandGeometry(BaseModel):
    model_config = _MODEL_CONFIG

    points: list[Point] = Field(min_length=FREEHAND_MIN_POINTS, max_length=FREEHAND_MAX_POINTS)


class AnnotationItem(BaseModel):
    """One mark. ``type`` selects which geometry shape is required — a pin may
    not carry an arrow's endpoints, and vice versa."""

    model_config = _MODEL_CONFIG

    id: ItemId
    type: Literal["pin", "arrow", "rectangle", "freehand"]
    geometry: PinGeometry | ArrowGeometry | RectangleGeometry | FreehandGeometry
    # An empty note is allowed: a rectangle around a hem sometimes says
    # everything. The list announces such a row as "no note" and the export
    # legend prints "(no note)", so the numbering stays continuous.
    note: Note = ""
    palette: Literal["terracotta", "sage", "ink"]
    created_order: NonCoercedInt = Field(gt=0)

    def model_post_init(self, _context) -> None:
        expected = {
            "pin": PinGeometry,
            "arrow": ArrowGeometry,
            "rectangle": RectangleGeometry,
            "freehand": FreehandGeometry,
        }[self.type]
        if not isinstance(self.geometry, expected):
            raise ValueError(f"{self.type} requires {self.type} geometry")


class AnnotationDocument(BaseModel):
    """The complete annotation overlay for one DesignVersion.

    ``image_width``/``image_height`` bind the document to the exact image it was
    drawn over. They are validated against the version's own canonical
    dimensions by the persistence layer and never trusted from the client — a
    document whose bound dimensions differed from the image would place every
    mark wrongly."""

    model_config = _MODEL_CONFIG

    schema_version: Literal[1]
    image_width: NonCoercedInt = Field(gt=0)
    image_height: NonCoercedInt = Field(gt=0)
    items: list[AnnotationItem] = Field(max_length=MAX_ANNOTATION_ITEMS)

    def model_post_init(self, _context) -> None:
        ids = [item.id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate annotation id")
        orders = [item.created_order for item in self.items]
        if len(set(orders)) != len(orders):
            raise ValueError("duplicate created_order")


def canonical_document_bytes(document: dict) -> bytes:
    """The canonical serialisation the size ceiling is measured against."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _canonical_length(document: object) -> int:
    """The canonical byte length, or a controlled rejection.

    Serialising is where an unencodable or non-JSON-serialisable value finally
    surfaces, and it must not surface as a raw ``UnicodeEncodeError`` (a
    ``ValueError``) or ``TypeError`` from a function whose contract promises a
    single failure mode. The individual field validators reject such values at
    source; this is the boundary that keeps the promise even if one ever
    slipped past.

    ``RecursionError`` is caught too, and for a subtler reason: this runs on the
    RAW payload before Pydantic has bounded its nesting, and ``json.dumps``
    recurses per level. A pathologically deep payload is small in bytes, so a
    size ceiling alone never stops it — and ``RecursionError`` is a
    ``RuntimeError``, so it would slip past a ValueError/TypeError guard."""
    try:
        return len(canonical_document_bytes(document))
    except (TypeError, ValueError, RecursionError):
        raise AnnotationDocumentInvalid() from None


def normalise_annotation_document(payload: object, *, image_width: int, image_height: int) -> dict:
    """Validate arbitrary JSON into a canonical annotation document.

    ``image_width``/``image_height`` are the SERVER's canonical values for the
    version. A payload declaring anything else is rejected rather than corrected,
    because a client that disagrees about the image it drew over is a client
    whose coordinates cannot be trusted.

    Returns a plain ``dict`` ready to persist. Raises
    :class:`AnnotationDocumentInvalid` for every invalid input — there is no
    other failure mode."""
    if not isinstance(payload, dict):
        raise AnnotationDocumentInvalid()

    # A cheap C-level size gate on the RAW payload, before any per-note Unicode
    # normalisation or regex work. Without it, a document that is structurally
    # legal under the per-field bounds (100 items x 500 freehand points) is fully
    # parsed and normalised before the ceiling below discards it — so the ceiling
    # would bound the ACCEPT decision but not the WORK. The authoritative check
    # is still the post-normalisation one, since normalisation can change size.
    if _canonical_length(payload) > MAX_DOCUMENT_BYTES:
        raise AnnotationDocumentInvalid("The annotation document is too large.")

    try:
        document = AnnotationDocument.model_validate(payload)
    except (PydanticValidationError, ValueError):
        # The exception text can quote the rejected note, so it is never
        # forwarded — only the generic, safe sentence.
        raise AnnotationDocumentInvalid() from None

    if document.image_width != image_width or document.image_height != image_height:
        raise AnnotationDocumentInvalid(
            "The annotation document does not match this image.",
            code="annotation_dimension_mismatch",
        )

    normalised = document.model_dump(mode="json")
    if _canonical_length(normalised) > MAX_DOCUMENT_BYTES:
        raise AnnotationDocumentInvalid("The annotation document is too large.")
    return normalised


def empty_annotation_document(*, image_width: int, image_height: int) -> dict:
    """The document a version has before anything is saved.

    Returned by GET with ``revision: 0`` so the client needs no separate
    never-saved branch."""
    return {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "image_width": image_width,
        "image_height": image_height,
        "items": [],
    }
