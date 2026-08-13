"""The deterministic demo refinement engine (Phase 15 Part B)."""

import copy

import pytest

from sitara.generation.demo import phrases, refinement_engine
from sitara.generation.demo.design_spec_engine import build_demo_design_spec
from sitara.generation.demo.refinement_engine import (
    DEMO_REFINEMENT_TEMPLATE_VERSION,
    DemoRefinementInert,
    build_demo_refined_spec,
)
from sitara.generation.design_spec import (
    DESIGN_SPEC_SCHEMA_VERSION,
    DesignSpec,
    validate_design_spec,
)
from sitara.generation.input_safety import scan_design_spec
from sitara.generation.prompt_builder import build_image_prompt
from sitara.generation.refinement import (
    REFINEMENT_CHANGE_TYPES,
    REFINEMENT_IMMUTABLE_ROOTS,
    RefinementRequest,
    canonical_refinement_fields,
    diff_design_spec_paths,
    path_is_allowed,
    refinement_allowed_paths,
)

from .demo_context_utils import a_context


class TestANoteNamesACanonicalValue:
    """`_note_machine_value` — the lookup that decides whether a customer's note
    reaches the SELECTION, and which was wrong in both directions until now.

    `_FIELD_PHRASES` maps machine value to English phrase. Reading a hint out of
    it with `_note_keyword` returns the phrase, so the hint could never match a
    machine value — except for most colours, where `COLOUR_PHRASES["emerald"]
    == "emerald"` makes key and value identical. A colour was the only field the
    old test covered, so the bug was invisible: a note naming a fabric, a
    density, a drape or a silhouette silently lost its hint.

    Every case below is a NON-colour field, deliberately, because the colour
    case is the one that would pass either way.
    """

    @pytest.mark.parametrize(
        ("note", "field", "expected"),
        [
            ("could it be velvet instead", "fabrics", "velvet"),
            ("please keep it minimal", "embellishment_density", "minimal"),
            ("I would like a boat_neck", "neckline_style", "boat_neck"),
            ("try one_shoulder please", "dupatta_style", "one_shoulder"),
            ("a mermaid_lehenga would suit her", "silhouette", "mermaid_lehenga"),
        ],
    )
    def test_a_named_value_is_returned_as_its_MACHINE_value(self, note, field, expected):
        assert refinement_engine._note_machine_value(note, field) == expected

    def test_the_colour_case_that_hid_the_defect_still_works(self):
        hinted = refinement_engine._note_machine_value("please make it emerald", "colour_palette")
        assert hinted == "emerald"

    def test_a_note_naming_nothing_returns_none(self):
        assert refinement_engine._note_machine_value("just something prettier", "fabrics") is None

    def test_an_unknown_field_returns_none_rather_than_raising(self):
        assert refinement_engine._note_machine_value("velvet", "not_a_field") is None


def _source_spec_dict() -> dict:
    payload = build_demo_design_spec(a_context())
    return DesignSpec.model_validate(payload).model_dump(mode="json")


def _request(change_type: str, note: str = "") -> RefinementRequest:
    return RefinementRequest.model_validate(
        {"schema_version": 1, "change_type": change_type, "note": note}
    )


# What production always supplies: the canonical values this design's own
# questionnaire would legally accept, in the shape
# `answerable_selection_alternatives` returns them — a ONE-ITEM LIST for a
# multi-select question, a bare value otherwise.
#
# Before ADR 0028 these tests called the engine with none, which exercised the
# narrative-only fallback — a path that for six of the seven categories writes a
# field the prompt builder does not render, so its output could never move the
# concept. The engine now refuses that rather than returning it, so these tests
# supply alternatives the way every production caller does.
#
# Every value below differs from the demo fixture design's own answers
# (ivory/gold, silk/organza, balanced, full_sleeves/high_neckline, head_drape,
# flared_lehenga) and is a real questionnaire option this module's phrase tables
# can name. `neckline` is absent: a version-1 spec has no `neckline_style` field
# at all, so the category owns nothing on it.
_ALTERNATIVES = {
    "colour_story": {"colour_palette": (["emerald"], ["navy"])},
    "fabric_and_texture": {"fabrics": (["velvet"], ["brocade"])},
    "embellishment": {"embellishment_density": ("heavy", "minimal")},
    "sleeves_and_coverage": {"coverage_preferences": (["elbow_sleeves"], ["short_sleeves"])},
    "dupatta_or_saree_drape": {"dupatta_style": ("one_shoulder", "both_shoulders")},
    "silhouette_detail": {"silhouette": ("a_line_lehenga", "mermaid_lehenga")},
}

# The categories a version-1 spec can actually be refined in. Derived from the
# production dispatch, never hand-listed, so a future version that gives
# `neckline` a canonical field on version 1 turns this on by itself.
V1_REFINABLE_CHANGE_TYPES = tuple(
    change_type
    for change_type in REFINEMENT_CHANGE_TYPES
    if canonical_refinement_fields(change_type, DESIGN_SPEC_SCHEMA_VERSION)
)


def _alternatives(change_type: str) -> dict:
    return {field: tuple(values) for field, values in _ALTERNATIVES[change_type].items()}


def test_the_alternatives_table_covers_exactly_the_refinable_categories():
    # A table that silently fell behind the dispatch would quietly stop
    # exercising a category rather than fail.
    assert sorted(_ALTERNATIVES) == sorted(V1_REFINABLE_CHANGE_TYPES)


class TestAllCategories:
    @pytest.mark.parametrize("change_type", V1_REFINABLE_CHANGE_TYPES)
    def test_produces_a_genuine_allowed_change(self, change_type):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source, _request(change_type), selection_alternatives=_alternatives(change_type)
        )
        DesignSpec.model_validate(refined)  # revalidates against the full contract

        changed = diff_design_spec_paths(source, refined)
        assert changed, "refinement must change at least one field"
        for path in changed:
            root = path.split(".", 1)[0].split("[", 1)[0]
            assert root not in REFINEMENT_IMMUTABLE_ROOTS
        # The production allowlist for this SPEC VERSION — narrative paths plus
        # the canonical selections the category owns. Asserting against the
        # narrative half alone (`REFINEMENT_ALLOWED_PATHS`) would reject the very
        # canonical move ADR 0028 requires.
        allowed = refinement_allowed_paths(change_type, DESIGN_SPEC_SCHEMA_VERSION)
        assert all(path_is_allowed(p, allowed) for p in changed)

    @pytest.mark.parametrize("change_type", V1_REFINABLE_CHANGE_TYPES)
    def test_passes_the_safety_scan(self, change_type):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source, _request(change_type), selection_alternatives=_alternatives(change_type)
        )
        scan_design_spec(DesignSpec.model_validate(refined))  # does not raise

    @pytest.mark.parametrize("change_type", V1_REFINABLE_CHANGE_TYPES)
    def test_every_selection_OUTSIDE_the_category_is_preserved_exactly(self, change_type):
        # ADR 0028 narrowed this from "no selection moves" to "no selection the
        # category does not own moves". The category's own field moving is the
        # whole point — it is the only thing the image prompt renders.
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source, _request(change_type), selection_alternatives=_alternatives(change_type)
        )
        owned = set(_alternatives(change_type))
        for field, value in source["source_selections"].items():
            if field not in owned:
                assert refined["source_selections"][field] == value, field

    @pytest.mark.parametrize("change_type", V1_REFINABLE_CHANGE_TYPES)
    def test_the_category_own_canonical_field_moves(self, change_type):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source, _request(change_type), selection_alternatives=_alternatives(change_type)
        )
        moved = [
            field
            for field in _alternatives(change_type)
            if refined["source_selections"][field] != source["source_selections"][field]
        ]
        assert moved, f"{change_type} left every canonical field it owns untouched"

    @pytest.mark.parametrize("change_type", V1_REFINABLE_CHANGE_TYPES)
    def test_the_rendered_image_prompt_actually_moves(self, change_type):
        # The assertion the whole phase turns on, and the one every one of these
        # tests was missing while a live refinement changed nothing. A spec-level
        # difference is not the claim; the customer sees the PROMPT's output.
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source, _request(change_type), selection_alternatives=_alternatives(change_type)
        )
        assert build_image_prompt(validate_design_spec(refined)) != build_image_prompt(
            validate_design_spec(source)
        )


class TestInertRefusal:
    def test_a_category_that_owns_nothing_on_this_spec_is_refused(self):
        # `neckline` owns no canonical field on a version-1 spec, so all this
        # engine could write is `coverage_and_drape.neckline` — which builder 8.x
        # does not render. Returning that would hand the pipeline an inert
        # concept its own guard then rejects twice, deterministically, for a
        # permanent failure. Refusing here is the honest answer.
        source = _source_spec_dict()
        with pytest.raises(DemoRefinementInert) as excinfo:
            build_demo_refined_spec(source, _request("neckline"), selection_alternatives={})
        assert excinfo.value.change_type == "neckline"

    def test_the_refusal_carries_the_category_and_nothing_else(self):
        # It is raised across a boundary that logs; it must not carry spec
        # content or the customer's note.
        marker = "xyzzy-unique-marker-should-never-appear-verbatim"
        source = _source_spec_dict()
        with pytest.raises(DemoRefinementInert) as excinfo:
            build_demo_refined_spec(
                source, _request("neckline", note=marker), selection_alternatives={}
            )
        text = str(excinfo.value)
        assert marker not in text
        assert "lehenga" not in text.lower()

    @pytest.mark.parametrize(
        "change_type", [c for c in V1_REFINABLE_CHANGE_TYPES if c != "colour_story"]
    )
    def test_with_no_legal_alternative_an_inert_category_is_refused(self, change_type):
        # Without alternatives every one of these falls back to narrative the
        # builder drops. Before ADR 0028's follow-up the engine returned that
        # happily, which is exactly the defect the live path had.
        source = _source_spec_dict()
        with pytest.raises(DemoRefinementInert):
            build_demo_refined_spec(source, _request(change_type), selection_alternatives={})

    def test_colour_story_narrative_alone_is_NOT_refused(self):
        # The converse, and the reason this guard compares prompts rather than
        # demanding a canonical move: builder 8.x renders `colour_story` when the
        # canonical colour roles are absent, so a narrative-only colour
        # refinement of this concept is genuine and must still succeed.
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source, _request("colour_story"), selection_alternatives={}
        )
        assert build_image_prompt(validate_design_spec(refined)) != build_image_prompt(
            validate_design_spec(source)
        )


class TestNoteHandling:
    def test_recognised_colour_keyword_is_honoured(self):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source,
            _request("colour_story", note="please make it emerald"),
            selection_alternatives=_alternatives("colour_story"),
        )
        assert "emerald" in refined["colour_story"]["palette_summary"].lower()

    def test_a_recognised_keyword_biases_the_CANONICAL_choice(self):
        # Since ADR 0028 a recognised keyword no longer picks prose directly: it
        # selects among the legal canonical values, and the prose is then written
        # from whichever one was chosen. That is what keeps the selection and the
        # sentence about it from describing different garments.
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source,
            _request("embellishment", note="please keep it minimal"),
            selection_alternatives=_alternatives("embellishment"),
        )
        assert refined["source_selections"]["embellishment_density"] == "minimal"
        assert "minimal" in refined["embellishment_plan"]["density"].lower()

    def test_an_unrecognised_tone_keyword_still_yields_a_legal_value(self):
        # "softer" is not a questionnaire value, so it cannot select one. The
        # engine must fall back to a deterministic legal pick rather than invent
        # a density the design's questionnaire never offered.
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source,
            _request("embellishment", note="please make it softer"),
            selection_alternatives=_alternatives("embellishment"),
        )
        assert refined["source_selections"]["embellishment_density"] in {"heavy", "minimal"}

    def test_unrecognised_note_selects_a_safe_deterministic_variant(self):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source,
            _request("colour_story", note="asdkjaslkdj"),
            selection_alternatives=_alternatives("colour_story"),
        )
        DesignSpec.model_validate(refined)

    def test_empty_note_still_produces_a_change(self):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source,
            _request("silhouette_detail", note=""),
            selection_alternatives=_alternatives("silhouette_detail"),
        )
        assert diff_design_spec_paths(source, refined)

    def test_raw_note_text_is_never_copied(self):
        marker = "xyzzy-unique-marker-should-never-appear-verbatim"
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source,
            _request("silhouette_detail", note=marker),
            selection_alternatives=_alternatives("silhouette_detail"),
        )
        assert marker not in str(refined)

    def test_no_designer_or_brand_name_is_introduced(self):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source,
            _request("fabric_and_texture", note="silk please"),
            selection_alternatives=_alternatives("fabric_and_texture"),
        )
        # A crude but effective proxy: the safety scan's denylist already
        # covers this; assert it still passes after a note-influenced edit.
        scan_design_spec(DesignSpec.model_validate(refined))


class TestTheAlternateSalt:
    """The retry between the first candidate and the refusal.

    It is a defensive branch — a canonical move is essentially never inert — so
    it cannot be reached through the public entry point on a realistic fixture
    without contriving one. What is testable, and what the branch's whole value
    rests on, is that the alternate salt genuinely explores different ground
    rather than recomputing the first answer. If it did not, the retry would be
    two identical attempts at the same wall and the refusal below it would be
    doing all the work.
    """

    def test_the_alternate_salt_reaches_a_different_candidate(self):
        source = _source_spec_dict()
        request = _request("colour_story")
        fingerprint = refinement_engine._fingerprint(source, request.change_type, "")
        # Every colour this concept has not already used — a pool wide enough
        # that two salts landing on the same value would be a real collision,
        # not a small-sample artefact.
        alternatives = {
            "colour_palette": tuple(
                [key] for key in phrases.COLOUR_PHRASES if key not in {"ivory", "gold"}
            )
        }

        first = refinement_engine._canonical_choice(alternatives, "", fingerprint)
        alternate = refinement_engine._canonical_choice(
            alternatives, "", fingerprint + ":alternate"
        )

        assert first != alternate

    def test_both_salts_still_choose_only_LEGAL_values(self):
        # The alternate must widen the search, never the permission: a retry that
        # reached outside the offered values would put a selection on the design
        # its own questionnaire never offered.
        source = _source_spec_dict()
        request = _request("silhouette_detail")
        fingerprint = refinement_engine._fingerprint(source, request.change_type, "")
        alternatives = _alternatives("silhouette_detail")

        for salt in ("", ":alternate"):
            field, value = refinement_engine._canonical_choice(alternatives, "", fingerprint + salt)
            assert field == "silhouette"
            assert value in alternatives["silhouette"]


class TestNoChangeAvoidance:
    def test_a_note_naming_the_colour_already_chosen_still_changes_something(self):
        # "ivory" is already this design's lead colour, so the hint names no
        # legal alternative. The refinement must still move, not quietly return
        # the source.
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source,
            _request("colour_story", note="ivory please"),
            selection_alternatives=_alternatives("colour_story"),
        )
        assert (
            refined["source_selections"]["colour_palette"]
            != source["source_selections"]["colour_palette"]
        )
        assert build_image_prompt(validate_design_spec(refined)) != build_image_prompt(
            validate_design_spec(source)
        )

    def test_repeated_calls_with_identical_inputs_are_byte_identical(self):
        source = _source_spec_dict()
        first = build_demo_refined_spec(
            source,
            _request("silhouette_detail"),
            selection_alternatives=_alternatives("silhouette_detail"),
        )
        second = build_demo_refined_spec(
            source,
            _request("silhouette_detail"),
            selection_alternatives=_alternatives("silhouette_detail"),
        )
        assert first == second


class TestOriginalUnchanged:
    def test_source_dict_is_not_mutated(self):
        source = _source_spec_dict()
        snapshot = copy.deepcopy(source)
        build_demo_refined_spec(
            source, _request("colour_story"), selection_alternatives=_alternatives("colour_story")
        )
        assert source == snapshot


class TestTemplateVersion:
    def test_template_version_is_pinned(self):
        # 2.0.0 since ADR 0028: the engine now changes a canonical selection,
        # not narrative alone, so a spec it produced before this phase and one
        # it produces now are not the same kind of artefact.
        assert DEMO_REFINEMENT_TEMPLATE_VERSION == "2.0.0"
