"""The annotation-document contract (Phase 19 Part A, ADR 0020).

Everything here is pure validation — no database, no HTTP, no storage. The
suite's job is to prove the contract is TOTAL over arbitrary JSON: every bad
input becomes ``AnnotationDocumentInvalid``, never a raw ``TypeError``,
``KeyError`` or ``ValidationError`` escaping to a caller.
"""

import json
import math
import uuid

import pytest

from sitara.designs.annotation_schema import (
    ANNOTATION_ITEM_TYPES,
    ANNOTATION_NOTE_MAX_LENGTH,
    ANNOTATION_NOTE_RAW_MAX_LENGTH,
    ANNOTATION_PALETTES,
    ANNOTATION_SCHEMA_VERSION,
    FREEHAND_MAX_POINTS,
    FREEHAND_MIN_POINTS,
    MAX_ANNOTATION_ITEMS,
    MAX_DOCUMENT_BYTES,
    MIN_GEOMETRY_EXTENT,
    AnnotationDocumentInvalid,
    canonical_document_bytes,
    empty_annotation_document,
    normalise_annotation_document,
)

WIDTH = 1024
HEIGHT = 1365


def item(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "type": "pin",
        "geometry": {"point": {"x": 0.5, "y": 0.175}},
        "note": "Raise the neckline about 2 cm.",
        "palette": "terracotta",
        "created_order": 1,
    }
    base.update(overrides)
    return base


# Distinguishes "caller did not pass items" from "caller passed items=None",
# which is itself one of the malformed shapes under test.
_DEFAULT = object()


def document(items=_DEFAULT, **overrides) -> dict:
    base = {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "image_width": WIDTH,
        "image_height": HEIGHT,
        "items": [item()] if items is _DEFAULT else items,
    }
    base.update(overrides)
    return base


def normalise(payload, *, width=WIDTH, height=HEIGHT) -> dict:
    return normalise_annotation_document(payload, image_width=width, image_height=height)


def assert_rejected(payload, *, width=WIDTH, height=HEIGHT):
    with pytest.raises(AnnotationDocumentInvalid):
        normalise_annotation_document(payload, image_width=width, image_height=height)


# --- the four geometry variants --------------------------------------------


def test_pin_geometry_round_trips():
    result = normalise(document())
    assert result["items"][0]["geometry"] == {"point": {"x": 0.5, "y": 0.175}}


def test_arrow_geometry_round_trips():
    geometry = {"start": {"x": 0.8, "y": 0.4}, "end": {"x": 0.65, "y": 0.5}}
    result = normalise(document([item(type="arrow", geometry=geometry)]))
    assert result["items"][0]["geometry"] == geometry


def test_rectangle_geometry_round_trips():
    geometry = {"x": 0.25, "y": 0.7, "width": 0.5, "height": 0.15}
    result = normalise(document([item(type="rectangle", geometry=geometry)]))
    assert result["items"][0]["geometry"] == geometry


def test_freehand_geometry_round_trips():
    points = [{"x": 0.1 + i / 100, "y": 0.2 + i / 100} for i in range(12)]
    result = normalise(document([item(type="freehand", geometry={"points": points})]))
    assert result["items"][0]["geometry"]["points"] == points


@pytest.mark.parametrize(
    ("declared", "geometry"),
    [
        ("pin", {"start": {"x": 0.1, "y": 0.1}, "end": {"x": 0.4, "y": 0.4}}),
        ("arrow", {"point": {"x": 0.5, "y": 0.5}}),
        ("rectangle", {"point": {"x": 0.5, "y": 0.5}}),
        ("freehand", {"point": {"x": 0.5, "y": 0.5}}),
    ],
)
def test_geometry_must_match_declared_type(declared, geometry):
    """A pin may not carry an arrow's endpoints — the type and the geometry
    have to agree, or a renderer would draw the wrong primitive."""
    assert_rejected(document([item(type=declared, geometry=geometry)]))


def test_unknown_item_type_rejected():
    assert_rejected(document([item(type="ellipse")]))


# --- coordinate bounds and numeric hostility --------------------------------


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_coordinates_rejected(value):
    """json.loads accepts NaN/Infinity even though neither is valid JSON, so
    the contract has to reject them rather than propagate a value that would
    poison every downstream SVG or Pillow transform."""
    assert_rejected(document([item(geometry={"point": {"x": value, "y": 0.5}})]))


@pytest.mark.parametrize("value", [-0.001, -1, 1.0001, 2, 1e9])
def test_out_of_range_coordinates_rejected(value):
    assert_rejected(document([item(geometry={"point": {"x": value, "y": 0.5}})]))


@pytest.mark.parametrize("value", [True, False])
def test_boolean_coordinates_rejected(value):
    """bool subclasses int in Python, so True would otherwise validate as 1.0."""
    assert_rejected(document([item(geometry={"point": {"x": value, "y": 0.5}})]))


@pytest.mark.parametrize("value", ["0.5", None, [], {}, [0.5]])
def test_non_numeric_coordinates_rejected(value):
    assert_rejected(document([item(geometry={"point": {"x": value, "y": 0.5}})]))


def test_boundary_coordinates_accepted():
    """0 and 1 are both inside the closed range — a mark on the very edge of
    the image is legitimate."""
    result = normalise(document([item(geometry={"point": {"x": 0.0, "y": 1.0}})]))
    assert result["items"][0]["geometry"]["point"] == {"x": 0.0, "y": 1.0}


# --- degenerate geometry ----------------------------------------------------


@pytest.mark.parametrize(
    "geometry",
    [
        {"x": 0.5, "y": 0.5, "width": 0.0, "height": 0.2},
        {"x": 0.5, "y": 0.5, "width": 0.2, "height": 0.0},
        {"x": 0.5, "y": 0.5, "width": 0.0, "height": 0.0},
        {"x": 0.5, "y": 0.5, "width": MIN_GEOMETRY_EXTENT / 2, "height": 0.2},
    ],
)
def test_zero_area_rectangles_rejected(geometry):
    assert_rejected(document([item(type="rectangle", geometry=geometry)]))


def test_rectangle_may_not_extend_past_the_image():
    """Each field is individually within [0, 1], but x + width must be too."""
    assert_rejected(
        document(
            [item(type="rectangle", geometry={"x": 0.8, "y": 0.1, "width": 0.5, "height": 0.2})]
        )
    )


def test_rectangle_flush_to_the_far_edge_accepted():
    geometry = {"x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5}
    assert (
        normalise(document([item(type="rectangle", geometry=geometry)]))["items"][0]["geometry"]
        == geometry
    )


@pytest.mark.parametrize(
    "geometry",
    [
        {"start": {"x": 0.5, "y": 0.5}, "end": {"x": 0.5, "y": 0.5}},
        {"start": {"x": 0.5, "y": 0.5}, "end": {"x": 0.5 + MIN_GEOMETRY_EXTENT / 3, "y": 0.5}},
    ],
)
def test_zero_length_arrows_rejected(geometry):
    assert_rejected(document([item(type="arrow", geometry=geometry)]))


def test_geometry_exactly_at_the_tolerance_is_accepted():
    """Pins the strict-inequality semantics at the literal boundary, so a later
    change from `<` to `<=` — or from hypot to a squared-distance comparison —
    cannot silently move the cutoff."""
    rectangle = {
        "x": 0.1,
        "y": 0.1,
        "width": MIN_GEOMETRY_EXTENT,
        "height": MIN_GEOMETRY_EXTENT,
    }
    assert normalise(document([item(type="rectangle", geometry=rectangle)]))
    # Anchored at exact zero, not 0.1: subtracting zero never rounds, so the
    # span the code computes is bit-identical to the constant. Building it as
    # (0.1 + X) - 0.1 would not be, since 0.1 is not representable in binary
    # floating point and (a + b) - a != b in general.
    arrow = {"start": {"x": 0.0, "y": 0.1}, "end": {"x": MIN_GEOMETRY_EXTENT, "y": 0.1}}
    assert normalise(document([item(type="arrow", geometry=arrow)]))


def test_arrow_just_past_the_tolerance_accepted():
    geometry = {
        "start": {"x": 0.5, "y": 0.5},
        "end": {"x": 0.5 + MIN_GEOMETRY_EXTENT * 2, "y": 0.5},
    }
    assert normalise(document([item(type="arrow", geometry=geometry)]))


def test_a_short_diagonal_arrow_is_measured_end_to_end_not_per_axis():
    """The tolerance is a straight-line distance, ``hypot(dx, dy)``.

    This diagonal is under the tolerance on BOTH axes individually (0.004 each)
    but over it end to end (~0.00566), so it is accepted. The case exists because
    a per-axis reading of the rule would reject it, and a client pre-validating
    from the published contract would then silently drop a mark the server would
    have kept — the contract's own wording is pinned to this behaviour."""
    short_leg = MIN_GEOMETRY_EXTENT * 0.8
    geometry = {"start": {"x": 0.0, "y": 0.0}, "end": {"x": short_leg, "y": short_leg}}
    assert math.hypot(short_leg, short_leg) > MIN_GEOMETRY_EXTENT
    assert short_leg < MIN_GEOMETRY_EXTENT
    assert normalise(document([item(type="arrow", geometry=geometry)]))


def test_the_published_contract_describes_the_arrow_rule_correctly():
    """The help_text is what a client author reads, so it must not describe a
    different rule from the one enforced above."""
    from sitara.designs.openapi import AnnotationArrowGeometrySerializer

    described = AnnotationArrowGeometrySerializer().fields["end"].help_text
    assert "straight-line" in described
    assert "not either axis alone" in described
    assert str(MIN_GEOMETRY_EXTENT) in described


# --- freehand point bounds --------------------------------------------------


def test_freehand_below_minimum_points_rejected():
    points = [{"x": 0.1, "y": 0.1}] * (FREEHAND_MIN_POINTS - 1)
    assert_rejected(document([item(type="freehand", geometry={"points": points})]))


def test_freehand_at_maximum_points_accepted():
    points = [{"x": i / FREEHAND_MAX_POINTS, "y": 0.5} for i in range(FREEHAND_MAX_POINTS)]
    assert (
        len(
            normalise(document([item(type="freehand", geometry={"points": points})]))["items"][0][
                "geometry"
            ]["points"]
        )
        == FREEHAND_MAX_POINTS
    )


def test_freehand_above_maximum_points_rejected():
    points = [{"x": 0.5, "y": 0.5}] * (FREEHAND_MAX_POINTS + 1)
    assert_rejected(document([item(type="freehand", geometry={"points": points})]))


# --- item, note and payload limits -----------------------------------------


def test_item_count_at_limit_accepted():
    items = [item(id=str(uuid.uuid4()), created_order=n + 1) for n in range(MAX_ANNOTATION_ITEMS)]
    assert len(normalise(document(items))["items"]) == MAX_ANNOTATION_ITEMS


def test_item_count_above_limit_rejected():
    items = [
        item(id=str(uuid.uuid4()), created_order=n + 1) for n in range(MAX_ANNOTATION_ITEMS + 1)
    ]
    assert_rejected(document(items))


def test_note_at_limit_accepted():
    note = "a" * ANNOTATION_NOTE_MAX_LENGTH
    assert normalise(document([item(note=note)]))["items"][0]["note"] == note


def test_note_above_limit_rejected():
    assert_rejected(document([item(note="a" * (ANNOTATION_NOTE_MAX_LENGTH + 1))]))


def test_note_length_is_measured_after_normalisation():
    """Whitespace collapse happens before the length check, so a note that is
    only over the limit because of runs of spaces is accepted, not rejected."""
    note = ("word" + " " * 20) * 6
    result = normalise(document([item(note=note)]))
    assert len(result["items"][0]["note"]) <= ANNOTATION_NOTE_MAX_LENGTH
    assert result["items"][0]["note"] == " ".join(["word"] * 6)


def test_oversized_payload_rejected():
    """The ceiling is on the canonical serialisation, so it cannot be evaded by
    sending compact JSON — and a document that passes every per-field bound can
    still be refused for total size."""
    items = [
        item(
            id=str(uuid.uuid4()),
            created_order=n + 1,
            type="freehand",
            geometry={
                "points": [
                    {"x": (i % 1000) / 1000, "y": (i % 997) / 1000}
                    for i in range(FREEHAND_MAX_POINTS)
                ]
            },
            note="n" * ANNOTATION_NOTE_MAX_LENGTH,
        )
        for n in range(MAX_ANNOTATION_ITEMS)
    ]
    payload = document(items)
    assert len(canonical_document_bytes(payload)) > MAX_DOCUMENT_BYTES
    assert_rejected(payload)


# --- identity and ordering --------------------------------------------------


def test_duplicate_item_ids_rejected():
    shared = str(uuid.uuid4())
    assert_rejected(document([item(id=shared, created_order=1), item(id=shared, created_order=2)]))


def test_duplicate_created_order_rejected():
    assert_rejected(
        document(
            [
                item(id=str(uuid.uuid4()), created_order=3),
                item(id=str(uuid.uuid4()), created_order=3),
            ]
        )
    )


@pytest.mark.parametrize("order", [0, -1, -100])
def test_non_positive_created_order_rejected(order):
    assert_rejected(document([item(created_order=order)]))


@pytest.mark.parametrize(
    "bad_id",
    [
        "not-a-uuid",
        "",
        "a" * 300,
        "12345678-1234-1234-1234-1234567890",
        "12345678123412341234123456789012",
        "../../etc/passwd",
        "<script>alert(1)</script>",
        123,
        None,
    ],
)
def test_item_id_must_be_a_plain_uuid(bad_id):
    """A bounded plain UUID and nothing else, so a client cannot smuggle a
    path, markup or an unbounded string into a persisted document."""
    assert_rejected(document([item(id=bad_id)]))


def test_item_id_is_normalised_to_lowercase():
    raw = str(uuid.uuid4()).upper()
    assert normalise(document([item(id=raw)]))["items"][0]["id"] == raw.lower()


# --- palette allowlist ------------------------------------------------------


@pytest.mark.parametrize("palette", ANNOTATION_PALETTES)
def test_allowlisted_palettes_accepted(palette):
    assert normalise(document([item(palette=palette)]))["items"][0]["palette"] == palette


@pytest.mark.parametrize(
    "palette", ["#ff0000", "red", "TERRACOTTA", "", None, "url(evil)", "rgb(1,2,3)"]
)
def test_palette_outside_the_allowlist_rejected(palette):
    assert_rejected(document([item(palette=palette)]))


# --- note safety ------------------------------------------------------------


def test_empty_note_allowed():
    """A purely visual mark is deliberately legal: a rectangle around a hem
    sometimes says everything."""
    assert normalise(document([item(note="")]))["items"][0]["note"] == ""


def test_missing_note_defaults_to_empty():
    payload = item()
    del payload["note"]
    assert normalise(document([payload]))["items"][0]["note"] == ""


@pytest.mark.parametrize(
    "note",
    [
        "<script>alert(1)</script>",
        "<b>bold</b>",
        "**markdown**",
        "[link](http://example.com)",
        "http://example.com",
        "see https://evil.example/path",
    ],
)
def test_markup_and_urls_rejected(note):
    """Notes render as escaped text in the browser and plain glyphs in the PNG;
    neither interprets markup, but a stored URL invites a future surface to."""
    assert_rejected(document([item(note=note)]))


def test_html_like_text_that_is_not_markup_stays_inert_text():
    """The rule rejects markup, not the characters — a note about a neckline
    being "< 2cm" is ordinary prose and must survive."""
    result = normalise(document([item(note="hem is < 2cm and > the brief")]))
    assert result["items"][0]["note"] == "hem is < 2cm and > the brief"


@pytest.mark.parametrize(
    "raw",
    [
        "safe‮evil",
        "safe‭evil",
        "safe⁦evil",
        "safe⁩evil",
        "safe‎evil",
        "safe‏evil",
        "safe​evil",
    ],
)
def test_bidi_and_invisible_format_characters_are_stripped(raw):
    """Escaping stops markup injection but not a presentation mismatch: a bidi
    override makes stored bytes render, and be read aloud, in a different order
    than they were typed. The same note reaches an aria-label, the canvas and
    pixel text in an emailed PNG, so it is removed at storage."""
    result = normalise(document([item(note=raw)]))
    stored = result["items"][0]["note"]
    assert stored == "safeevil"
    assert not any(ord(char) in range(0x202A, 0x202F) for char in stored)
    assert all(char.isprintable() or char == " " for char in stored)


@pytest.mark.parametrize("raw", ["a\x00b", "a\x07b", "a\x1bb", "a\x9fb"])
def test_ascii_and_c1_control_characters_are_stripped(raw):
    stored = normalise(document([item(note=raw)]))["items"][0]["note"]
    assert stored == "ab"


def test_newlines_and_tabs_become_single_spaces():
    stored = normalise(document([item(note="one\ntwo\t\tthree\r\nfour")]))["items"][0]["note"]
    assert stored == "one two three four"


def test_note_ends_are_trimmed():
    assert normalise(document([item(note="   padded   ")]))["items"][0]["note"] == "padded"


@pytest.mark.parametrize("codepoint", [0xD800, 0xDBFF, 0xDC00, 0xDFFF])
def test_unpaired_surrogates_rejected(codepoint):
    """``json.loads`` accepts a "\\ud800" escape without complaint, and a lone
    surrogate's category is Cs — neither Cc nor Cf — so it survives every other
    filter. Left in, it is unencodable UTF-8 and would surface much later as a
    raw UnicodeEncodeError from the canonical serialisation, breaking this
    module's promise of a single controlled failure mode. It is also unstorable:
    PostgreSQL JSONB and the Pillow/SVG renderers all require valid Unicode."""
    assert_rejected(document([item(note=chr(codepoint))]))


def test_a_surrogate_never_escapes_as_a_unicode_error():
    """The specific regression: a controlled rejection, not a leaked
    UnicodeEncodeError (which is a ValueError, so it would otherwise slip
    through a caller that only catches AnnotationDocumentInvalid)."""
    with pytest.raises(AnnotationDocumentInvalid):
        normalise(document([item(note="safe\ud800tail")]))


def test_raw_note_far_over_the_limit_is_rejected_before_normalisation():
    """Rejecting a megabyte of text must not first cost a megabyte of NFKC
    normalisation and regex scanning, so the raw gate precedes the pipeline."""
    assert_rejected(document([item(note="a" * (ANNOTATION_NOTE_RAW_MAX_LENGTH + 1))]))


def test_a_note_between_the_raw_and_product_limits_is_still_rejected():
    """The raw gate is a cost guard, not the product limit — a 500-character
    note is under the raw ceiling and still refused for length."""
    assert_rejected(document([item(note="a" * 500)]))


def test_whitespace_heavy_notes_survive_the_raw_gate():
    """A legitimate note padded with whitespace must not trip the raw ceiling
    before collapsing gets the chance to bring it under the product limit."""
    note = ("word" + " " * 40) * 20
    assert len(note) < ANNOTATION_NOTE_RAW_MAX_LENGTH
    assert normalise(document([item(note=note)]))["items"][0]["note"] == " ".join(["word"] * 20)


def test_excessive_combining_marks_rejected():
    """Zalgo text: one base character carrying dozens of stacked marks occupies a
    single grapheme position while defeating the fixed-size export legend cell
    and the aria-label the same note is rendered into."""
    assert_rejected(document([item(note="a" + "́" * 130)]))


def test_a_normal_combining_sequence_is_preserved():
    """The cap must not clip correctly composed multi-script text, which stacks
    one to three marks on a base character."""
    note = "café — दुपट्टा — نستعلیق"
    assert normalise(document([item(note=note)]))["items"][0]["note"] == note


def test_ordinary_multiscript_text_is_preserved():
    """The Cf/Cc strip is a denylist, never a script allowlist — a culturally
    accurate product cannot reject Urdu, Hindi or Arabic notes."""
    note = "गोटा पट्टी on the hem دوپٹه — softer"
    assert normalise(document([item(note=note)]))["items"][0]["note"] == note


# --- document-level shape ---------------------------------------------------


def test_unknown_document_field_rejected():
    assert_rejected(document(extra="nope"))


def test_unknown_item_field_rejected():
    assert_rejected(document([item(colour="#fff")]))


def test_unknown_geometry_field_rejected():
    assert_rejected(document([item(geometry={"point": {"x": 0.5, "y": 0.5}, "z": 0.1})]))


def test_unknown_point_field_rejected():
    assert_rejected(document([item(geometry={"point": {"x": 0.5, "y": 0.5, "z": 0.5}})]))


@pytest.mark.parametrize("payload", [None, [], "string", 42, True, 1.5])
def test_non_object_payload_rejected(payload):
    """Validation is total: no input shape reaches an unhandled exception."""
    assert_rejected(payload)


def test_pathologically_nested_payload_rejected():
    """Deep, not long. The raw size gate runs json.dumps before Pydantic has
    bounded the nesting, and json.dumps recurses per level — so a payload only a
    few kilobytes wide can exhaust the stack. RecursionError is a RuntimeError,
    so it would slip past a ValueError guard and escape as a 500."""
    nested: object = {}
    for _ in range(20_000):
        nested = {"items": nested}
    with pytest.raises(AnnotationDocumentInvalid):
        normalise(nested)


@pytest.mark.parametrize("version", [0, 2, -1, "1", None])
def test_unsupported_schema_version_rejected(version):
    assert_rejected(document(schema_version=version))


@pytest.mark.parametrize("items", [None, {}, "x", [None], [[]], ["item"]])
def test_malformed_items_collection_rejected(items):
    assert_rejected(document(items))


def test_empty_items_list_accepted():
    """A document that exists but holds nothing is legal — the user cleared
    their marks rather than never having made any."""
    assert normalise(document([]))["items"] == []


# --- dimension binding ------------------------------------------------------


def test_dimension_mismatch_rejected():
    """A client that disagrees about the image it drew over is a client whose
    coordinates cannot be trusted, so this is refused rather than corrected."""
    with pytest.raises(AnnotationDocumentInvalid) as excinfo:
        normalise(document(), width=WIDTH + 1)
    assert excinfo.value.code == "annotation_dimension_mismatch"


def test_both_dimensions_are_checked():
    assert_rejected(document(image_height=HEIGHT + 7))
    assert_rejected(document(image_width=WIDTH + 7))


@pytest.mark.parametrize("value", [0, -1, "1024", None, 10.5, True])
def test_invalid_dimensions_rejected(value):
    """Strict integers: the string "1024" is refused rather than coerced, so a
    client that disagrees with the contract is told, not silently accommodated."""
    assert_rejected(document(image_width=value))


# --- failure messages -------------------------------------------------------


def test_rejection_never_echoes_the_note_back():
    """A validation failure must not become a reflection channel for the
    private content it rejected."""
    secret = "the brides private measurement is 34 inches"
    with pytest.raises(AnnotationDocumentInvalid) as excinfo:
        normalise(document([item(note=secret, palette="#bad")]))
    rendered = f"{excinfo.value} {excinfo.value.message} {excinfo.value.code}"
    assert "34 inches" not in rendered
    assert "measurement" not in rendered


# --- the empty document -----------------------------------------------------


def test_empty_document_is_itself_valid():
    """The never-saved response the API returns must satisfy the same contract
    it would accept back, so a client can round-trip it without special-casing."""
    empty = empty_annotation_document(image_width=WIDTH, image_height=HEIGHT)
    assert empty == {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "image_width": WIDTH,
        "image_height": HEIGHT,
        "items": [],
    }
    assert normalise(empty) == empty


def test_canonical_bytes_are_stable_and_order_independent():
    """The size ceiling must not depend on key order or client formatting."""
    a = {"b": 1, "a": [1, 2], "c": {"y": 2, "x": 1}}
    b = json.loads(json.dumps({"c": {"x": 1, "y": 2}, "a": [1, 2], "b": 1}))
    assert canonical_document_bytes(a) == canonical_document_bytes(b)
    assert b" " not in canonical_document_bytes(a)


# --- the published contract -------------------------------------------------


def test_the_serializer_choices_match_the_taxonomy():
    """The OpenAPI serializers restate the taxonomy for documentation, so they
    must not drift from the contract they document."""
    from sitara.designs.openapi import AnnotationItemSerializer

    fields = AnnotationItemSerializer().fields
    assert list(fields["type"].choices) == list(ANNOTATION_ITEM_TYPES)
    assert list(fields["palette"].choices) == list(ANNOTATION_PALETTES)
