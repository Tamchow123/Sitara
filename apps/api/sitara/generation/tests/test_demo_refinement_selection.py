"""A demo refinement changes a canonical selection, legally (Phase 23 §8).

Two halves, and the seam between them is the point:

- :func:`answerable_selection_alternatives` decides WHICH values are legal, from
  the design's own pinned questionnaire, using the same validator that will
  judge the refined spec afterwards.
- the demo engine only CHOOSES among them, deterministically.

The engine must never pick a canonical value itself. Its phrase vocabularies are
deliberate supersets of any one questionnaire — ``COLOUR_PHRASES`` has 57
entries where questionnaire v4 offers far fewer — so a blind pick would produce
values the customer was never offered, and the questionnaire guardrail (ADR
0028 §5) would then reject the demo's own output. Zero provider calls anywhere
in this module, as for every demo test.
"""

import copy
import json
import pathlib

import pytest

from sitara.generation.demo import phrases, refinement_engine
from sitara.generation.demo.refinement_engine import build_demo_refined_spec
from sitara.generation.design_spec import (
    COLOUR_MATCH_FABRIC,
    SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS,
    validate_design_spec,
)
from sitara.generation.refinement import (
    REFINEMENT_CHANGE_TYPES,
    canonical_refinement_fields,
    normalise_refinement_request,
)
from sitara.generation.refinement_selections import (
    RefinementQuestionnaireUnavailable,
    answerable_selection_alternatives,
    assert_refined_selections_are_answerable,
)
from sitara.generation.selection_semantics import COLOUR_ROLE_FIELDS
from sitara.questionnaire.rules import declared_option_values, questions_by_id

from .factory import (
    make_complete_design,
    make_complete_v4_design,
    v1_schema,
    v3_schema,
    v4_schema,
)
from .test_refinement_prompt_effect import SOURCE_SPEC

pytestmark = pytest.mark.django_db


def _v4_design_and_spec(**selection_overrides):
    """A v4-pinned design and its validated version-3 source DesignSpec."""
    design = make_complete_v4_design()
    payload = copy.deepcopy(SOURCE_SPEC)
    payload["source_selections"].update(selection_overrides)
    return design, validate_design_spec(payload)


def _request(change_type: str, note: str = ""):
    return normalise_refinement_request(
        {"schema_version": 1, "change_type": change_type, "note": note}
    )


class TestWhichValuesAreOffered:
    def test_only_the_categorys_own_canonical_fields_are_offered(self):
        design, spec = _v4_design_and_spec()
        assert set(answerable_selection_alternatives(design, spec, "fabric_and_texture")) == {
            "fabrics"
        }
        assert set(answerable_selection_alternatives(design, spec, "neckline")) == {
            "neckline_style"
        }
        assert set(answerable_selection_alternatives(design, spec, "colour_story")) <= set(
            COLOUR_ROLE_FIELDS
        )

    def test_a_multi_select_is_offered_as_a_one_item_list_and_a_single_choice_bare(self):
        # The shape matters: it is assigned straight into ``source_selections``,
        # where a bare string in a list field would fail DesignSpec validation.
        design, spec = _v4_design_and_spec()
        for value in answerable_selection_alternatives(design, spec, "fabric_and_texture")[
            "fabrics"
        ]:
            assert isinstance(value, list) and len(value) == 1
        for value in answerable_selection_alternatives(design, spec, "neckline")["neckline_style"]:
            assert isinstance(value, str)

    def test_the_value_already_chosen_is_never_offered(self):
        # "Refined" to what it already was is the defect this phase exists to
        # fix, so it must not even be a candidate.
        design, spec = _v4_design_and_spec()
        offered = answerable_selection_alternatives(design, spec, "neckline")["neckline_style"]
        assert spec.source_selections.neckline_style not in offered

    def test_every_offered_value_survives_the_real_guardrail(self):
        # The strongest form of the claim: take each candidate, substitute it,
        # and put the result through the SAME check a live provider's output
        # faces. Anything the engine could choose is therefore acceptable.
        design, spec = _v4_design_and_spec()
        for change_type in ("colour_story", "fabric_and_texture", "embellishment", "neckline"):
            for field, values in answerable_selection_alternatives(
                design, spec, change_type
            ).items():
                for value in values:
                    payload = spec.model_dump(mode="json")
                    payload["source_selections"][field] = copy.deepcopy(value)
                    assert_refined_selections_are_answerable(design, validate_design_spec(payload))

    def test_a_value_forbidden_by_a_compatibility_rule_is_not_offered(self):
        # questionnaire v4 ships `covered_midriff_excludes_deep_v_neck`. Both
        # values are real declared options; the pair is not, and only the
        # questionnaire knows that.
        design, spec = _v4_design_and_spec(midriff="covered_midriff")
        offered = answerable_selection_alternatives(design, spec, "neckline")["neckline_style"]
        assert "deep_v_neck" not in offered
        assert offered  # and the question is still refinable to something

    def test_a_silhouette_belonging_to_another_garment_is_not_offered(self):
        design, spec = _v4_design_and_spec()
        offered = answerable_selection_alternatives(design, spec, "silhouette_detail")["silhouette"]
        assert "classic_gharara" not in offered
        assert all(value.endswith("lehenga") for value in offered), offered

    def test_a_category_with_no_canonical_field_on_this_version_offers_nothing(self):
        # A version-1 spec has no neckline_style. Nothing to offer, and nothing
        # invented: the refusal itself lives at the enqueue boundary.
        design = make_complete_design()
        spec = validate_design_spec(_v1_spec_payload())
        assert answerable_selection_alternatives(design, spec, "neckline") == {}

    def test_a_design_with_no_pinned_questionnaire_fails_closed(self):
        design, spec = _v4_design_and_spec()
        design.questionnaire_version = None
        with pytest.raises(RefinementQuestionnaireUnavailable):
            answerable_selection_alternatives(design, spec, "fabric_and_texture")


def _v1_spec_payload() -> dict:
    """The committed version-1 DesignSpec fixture, as a plain payload."""
    path = pathlib.Path(__file__).resolve().parent / "fixtures" / "nikah_lehenga.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class TestTheEngineOnlyChoosesAmongThem:
    def test_the_canonical_selection_actually_changes(self):
        design, spec = _v4_design_and_spec()
        payload = spec.model_dump(mode="json")
        alternatives = answerable_selection_alternatives(design, spec, "fabric_and_texture")

        refined = build_demo_refined_spec(
            payload, _request("fabric_and_texture"), selection_alternatives=alternatives
        )

        assert refined["source_selections"]["fabrics"] != payload["source_selections"]["fabrics"]
        assert refined["source_selections"]["fabrics"] in alternatives["fabrics"]

    def test_nothing_outside_the_categorys_own_group_moves(self):
        design, spec = _v4_design_and_spec()
        payload = spec.model_dump(mode="json")
        alternatives = answerable_selection_alternatives(design, spec, "neckline")

        refined = build_demo_refined_spec(
            payload, _request("neckline"), selection_alternatives=alternatives
        )

        moved = {
            field
            for field, value in refined["source_selections"].items()
            if value != payload["source_selections"][field]
        }
        assert moved == {"neckline_style"}

    def test_the_brief_names_the_value_that_was_chosen(self):
        # The manual checkpoint's requirement, pinned: the selection and the
        # narrative must describe the same garment, never two different ones.
        design, spec = _v4_design_and_spec()
        payload = spec.model_dump(mode="json")
        alternatives = answerable_selection_alternatives(design, spec, "fabric_and_texture")

        refined = build_demo_refined_spec(
            payload, _request("fabric_and_texture"), selection_alternatives=alternatives
        )

        chosen = refined["source_selections"]["fabrics"][0]
        assert phrases.FABRIC_PHRASES[chosen].lower() in (
            refined["fabrics_and_texture"][0]["fabric"].lower()
        )

    def test_it_is_deterministic(self):
        design, spec = _v4_design_and_spec()
        payload = spec.model_dump(mode="json")
        alternatives = answerable_selection_alternatives(design, spec, "colour_story")
        first = build_demo_refined_spec(
            payload, _request("colour_story"), selection_alternatives=alternatives
        )
        second = build_demo_refined_spec(
            payload, _request("colour_story"), selection_alternatives=alternatives
        )
        assert first == second

    def test_with_no_legal_alternative_it_changes_narrative_only(self):
        # Honest rather than fabricated: if the questionnaire offers nothing
        # else, the demo says something new about the same selection instead of
        # inventing an option that was never on the screen.
        design, spec = _v4_design_and_spec()
        payload = spec.model_dump(mode="json")

        refined = build_demo_refined_spec(
            payload, _request("fabric_and_texture"), selection_alternatives={}
        )

        assert refined["source_selections"] == payload["source_selections"]
        assert refined != payload

    def test_the_refined_spec_passes_the_guardrail_it_will_face(self):
        design, spec = _v4_design_and_spec()
        payload = spec.model_dump(mode="json")
        for change_type in ("colour_story", "fabric_and_texture", "embellishment", "neckline"):
            alternatives = answerable_selection_alternatives(design, spec, change_type)
            refined = build_demo_refined_spec(
                payload, _request(change_type), selection_alternatives=alternatives
            )
            assert_refined_selections_are_answerable(design, validate_design_spec(refined))


class TestTheSourceIsNeverMutated:
    def test_building_a_refinement_leaves_the_input_untouched(self):
        design, spec = _v4_design_and_spec()
        payload = spec.model_dump(mode="json")
        snapshot = copy.deepcopy(payload)
        alternatives = answerable_selection_alternatives(design, spec, "fabric_and_texture")
        build_demo_refined_spec(
            payload, _request("fabric_and_texture"), selection_alternatives=alternatives
        )
        assert payload == snapshot

    def test_a_candidate_list_handed_in_is_not_aliased_into_the_result(self):
        # The engine deep-copies what it chooses. Without that, a caller reusing
        # the alternatives mapping could find its own data mutated through the
        # refined spec.
        design, spec = _v4_design_and_spec()
        payload = spec.model_dump(mode="json")
        alternatives = answerable_selection_alternatives(design, spec, "fabric_and_texture")
        refined = build_demo_refined_spec(
            payload, _request("fabric_and_texture"), selection_alternatives=alternatives
        )
        refined["source_selections"]["fabrics"].append("velvet")
        for value in alternatives["fabrics"]:
            assert len(value) == 1


class TestTheTwoVocabulariesStayInStep:
    """The demo engine picks a value from the QUESTIONNAIRE and describes it
    from :mod:`sitara.generation.demo.phrases`. Those are two separately
    maintained tables, so nothing but this test stops them drifting apart — and
    a drift would write a canonical selection the brief describes as something
    else, which is exactly the defect ADR 0028 exists to remove.

    Fails LOUDLY on the next questionnaire version that adds an option before
    the phrase tables catch up. ``_describable_alternatives`` is the runtime
    backstop underneath it, so a drift degrades to a narrative-only refinement
    rather than a mismatch — but the fix is to add the phrase, not to rely on
    the backstop."""

    # Values a canonical field may LEGALLY take that this engine deliberately
    # cannot put into words, and therefore never chooses. Each needs a reason,
    # and the test below proves each is still genuinely undescribable — so
    # adding the phrase later means deleting the entry, not leaving dead config.
    UNDESCRIBABLE_BY_DESIGN = {
        ("dupatta_colour", COLOUR_MATCH_FABRIC): (
            "Not a colour but a relationship - 'the same as the main fabric'. "
            "Every demo colour sentence is templated around a colour "
            "noun-phrase, and COLOUR_PHRASES must stay a colour vocabulary "
            "because the asset selector and the demo brief read it too."
        ),
    }

    @staticmethod
    def _canonical_fields() -> set[str]:
        fields = set()
        for change_type in REFINEMENT_CHANGE_TYPES:
            for schema_version in SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS:
                fields.update(canonical_refinement_fields(change_type, schema_version))
        return fields

    @pytest.mark.parametrize(
        "name, schema_factory",
        [("v1", v1_schema), ("v3", v3_schema), ("v4", v4_schema)],
    )
    def test_every_declared_option_of_every_canonical_field_can_be_described(
        self, name, schema_factory
    ):
        questions = questions_by_id(schema_factory())
        missing = []
        for field in sorted(self._canonical_fields()):
            question = questions.get(field)
            if question is None:
                continue  # this version has no such question; nothing to describe
            table = refinement_engine._FIELD_PHRASES.get(field)
            if table is None:
                missing.append(f"{field}: no phrase table at all")
                continue
            for value in declared_option_values(question):
                if value in table or (field, value) in self.UNDESCRIBABLE_BY_DESIGN:
                    continue
                missing.append(f"{field}.{value}")
        assert not missing, f"{name} options with no demo phrase: {missing}"

    def test_each_documented_exception_is_still_genuinely_undescribable(self):
        # Keeps the exception list honest in the other direction: once a phrase
        # exists, the entry above is stale and must go, or it would silently
        # excuse a future drift on the same field.
        for (field, value), reason in self.UNDESCRIBABLE_BY_DESIGN.items():
            assert value not in refinement_engine._FIELD_PHRASES[field], (field, value, reason)

    def test_every_canonical_field_has_a_phrase_table(self):
        # The table is keyed by field, so a canonical field added to
        # selection_semantics without a matching entry would silently make that
        # whole category narrative-only in demo mode.
        assert self._canonical_fields() <= set(refinement_engine._FIELD_PHRASES)

    def test_an_undescribable_value_is_not_chosen_at_all(self, monkeypatch):
        # The runtime backstop, driven directly: with the phrase table emptied
        # for the only field this category owns, the engine must decline to
        # change the selection rather than change it and describe something
        # else. Narrative-only is the honest degradation.
        design, spec = _v4_design_and_spec()
        payload = spec.model_dump(mode="json")
        alternatives = answerable_selection_alternatives(design, spec, "fabric_and_texture")
        monkeypatch.setitem(refinement_engine._FIELD_PHRASES, "fabrics", {})

        refined = build_demo_refined_spec(
            payload, _request("fabric_and_texture"), selection_alternatives=alternatives
        )

        assert refined["source_selections"] == payload["source_selections"]
        assert refined != payload
