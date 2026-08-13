"""The deterministic demo refinement engine (Phase 15 Part B)."""

import copy

import pytest

from sitara.generation.demo import refinement_engine
from sitara.generation.demo.design_spec_engine import build_demo_design_spec
from sitara.generation.demo.refinement_engine import (
    DEMO_REFINEMENT_TEMPLATE_VERSION,
    build_demo_refined_spec,
)
from sitara.generation.design_spec import DesignSpec
from sitara.generation.input_safety import scan_design_spec
from sitara.generation.refinement import (
    REFINEMENT_ALLOWED_PATHS,
    REFINEMENT_CHANGE_TYPES,
    REFINEMENT_IMMUTABLE_ROOTS,
    RefinementRequest,
    diff_design_spec_paths,
    path_is_allowed,
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


class TestAllCategories:
    @pytest.mark.parametrize("change_type", REFINEMENT_CHANGE_TYPES)
    def test_produces_a_genuine_allowed_change(self, change_type):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(source, _request(change_type))
        DesignSpec.model_validate(refined)  # revalidates against the full contract

        changed = diff_design_spec_paths(source, refined)
        assert changed, "refinement must change at least one field"
        for path in changed:
            root = path.split(".", 1)[0].split("[", 1)[0]
            assert root not in REFINEMENT_IMMUTABLE_ROOTS
        allowed = REFINEMENT_ALLOWED_PATHS[change_type]
        assert all(path_is_allowed(p, allowed) for p in changed)

    @pytest.mark.parametrize("change_type", REFINEMENT_CHANGE_TYPES)
    def test_passes_the_safety_scan(self, change_type):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(source, _request(change_type))
        scan_design_spec(DesignSpec.model_validate(refined))  # does not raise

    @pytest.mark.parametrize("change_type", REFINEMENT_CHANGE_TYPES)
    def test_source_selections_are_preserved_exactly(self, change_type):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(source, _request(change_type))
        assert refined["source_selections"] == source["source_selections"]


class TestNoteHandling:
    def test_recognised_colour_keyword_is_honoured(self):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source, _request("colour_story", note="please make it emerald")
        )
        assert "emerald" in refined["colour_story"]["palette_summary"].lower()

    def test_recognised_tone_keyword_for_embellishment(self):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source, _request("embellishment", note="please make it softer")
        )
        assert "minimal" in refined["embellishment_plan"]["density"].lower()

    def test_unrecognised_note_selects_a_safe_deterministic_variant(self):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(source, _request("colour_story", note="asdkjaslkdj"))
        DesignSpec.model_validate(refined)

    def test_empty_note_still_produces_a_change(self):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(source, _request("silhouette_detail", note=""))
        assert diff_design_spec_paths(source, refined)

    def test_raw_note_text_is_never_copied(self):
        marker = "xyzzy-unique-marker-should-never-appear-verbatim"
        source = _source_spec_dict()
        refined = build_demo_refined_spec(source, _request("silhouette_detail", note=marker))
        assert marker not in str(refined)

    def test_no_designer_or_brand_name_is_introduced(self):
        source = _source_spec_dict()
        refined = build_demo_refined_spec(
            source, _request("fabric_and_texture", note="silk please")
        )
        # A crude but effective proxy: the safety scan's denylist already
        # covers this; assert it still passes after a note-influenced edit.
        scan_design_spec(DesignSpec.model_validate(refined))


class TestNoChangeAvoidance:
    def test_first_candidate_equal_to_source_selects_an_alternate(self):
        # Force the "already this colour" case: request colour_story with a
        # note naming the colour the source already leads with, and confirm
        # a genuine change still results (the engine excludes already-used
        # colours from its candidate pool).
        source = _source_spec_dict()
        refined = build_demo_refined_spec(source, _request("colour_story", note="ivory please"))
        assert diff_design_spec_paths(source, refined)

    def test_repeated_calls_with_identical_inputs_are_byte_identical(self):
        source = _source_spec_dict()
        first = build_demo_refined_spec(source, _request("neckline"))
        second = build_demo_refined_spec(source, _request("neckline"))
        assert first == second


class TestOriginalUnchanged:
    def test_source_dict_is_not_mutated(self):
        source = _source_spec_dict()
        snapshot = copy.deepcopy(source)
        build_demo_refined_spec(source, _request("colour_story"))
        assert source == snapshot


class TestTemplateVersion:
    def test_template_version_is_pinned(self):
        # 2.0.0 since ADR 0028: the engine now changes a canonical selection,
        # not narrative alone, so a spec it produced before this phase and one
        # it produces now are not the same kind of artefact.
        assert DEMO_REFINEMENT_TEMPLATE_VERSION == "2.0.0"
