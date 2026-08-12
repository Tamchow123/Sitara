"""A refined canonical selection is only valid if the questionnaire says so.

Phase 23 §5, ADR 0028. These tests are the guardrail that makes the canonical
refinement safe: without them a model could answer ``"fabrics": ["unobtainium"]``
or set a neckline the design's own coverage answers forbid, and the deterministic
prompt builder would render it faithfully.
"""

import copy

import pytest
from pydantic import ValidationError

from sitara.designs.models import DesignVersion
from sitara.generation.design_spec import validate_design_spec
from sitara.generation.refinement import normalise_refinement_request
from sitara.generation.refinement_selections import (
    RefinedSelectionsInvalid,
    RefinementQuestionnaireUnavailable,
    assert_refined_selections_are_answerable,
    selections_as_answers,
)
from sitara.generation.refinement_service import (
    RefinementGenerationFailed,
    generate_refined_design_spec_for_design,
)
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.rules import questions_by_id

from .factory import (
    make_active_v4,
    make_complete_v4_design,
    make_source_version,
    v1_schema,
    v3_schema,
    v4_schema,
)
from .fakes import SequenceProvider, payload_result
from .test_refinement_prompt_effect import SOURCE_SPEC

pytestmark = pytest.mark.django_db

# Every ``source_selections`` field name, across every supported DesignSpec
# version. Used only by the structural invariant below.
_SELECTION_FIELDS = frozenset(SOURCE_SPEC["source_selections"]) | {
    "colour_palette",
    "coverage_preferences",
}


def _v4_design_and_source():
    """A v4 design whose version-1 concept is the fully-answered v3 spec."""
    design = make_complete_v4_design()
    source = make_source_version(design, copy.deepcopy(SOURCE_SPEC), design_spec_schema_version=3)
    return design, source


def _refined(**selection_changes) -> dict:
    payload = copy.deepcopy(SOURCE_SPEC)
    payload["source_selections"].update(selection_changes)
    return payload


class TestTheAssumptionThisRestsOn:
    """Selections alone are a complete answer set — pinned, not hoped for.

    ``assert_refined_selections_are_answerable`` validates the selections with
    ``require_complete=True`` and nothing else, which is exact only while every
    required question is a selection field. If a future questionnaire version
    breaks that, this test says so rather than letting refinement start
    rejecting valid concepts for an unrelated reason."""

    @pytest.mark.parametrize(
        "schema_name, schema_factory",
        [("v1", v1_schema), ("v3", v3_schema), ("v4", v4_schema)],
    )
    def test_every_required_question_is_a_selection_field(self, schema_name, schema_factory):
        schema = schema_factory()
        required = {
            question_id
            for question_id, question in questions_by_id(schema).items()
            if question.get("required")
        }
        required |= {
            rule["then"]["question_id"]
            for rule in schema.get("rules", [])
            if rule["then"]["action"] == "require"
        }
        assert required <= _SELECTION_FIELDS, schema_name


class TestSelectionsAsAnswers:
    def test_unanswered_scalars_and_empty_lists_are_absent_not_empty(self):
        answers = selections_as_answers(
            {"silhouette": "flared_lehenga", "saree_drape": None, "custom_colours": []}
        )
        assert answers == {"silhouette": "flared_lehenga"}

    def test_a_real_answer_survives_verbatim(self):
        answers = selections_as_answers({"fabrics": ["silk"], "midriff": "bare_midriff"})
        assert answers == {"fabrics": ["silk"], "midriff": "bare_midriff"}


class TestValidationAgainstThePinnedVersion:
    def test_the_unchanged_selections_are_accepted(self):
        design, _source = _v4_design_and_source()
        assert_refined_selections_are_answerable(design, validate_design_spec(SOURCE_SPEC))

    def test_a_real_option_of_that_question_is_accepted(self):
        design, _source = _v4_design_and_source()
        refined = validate_design_spec(_refined(fabrics=["silk", "organza"]))
        assert_refined_selections_are_answerable(design, refined)

    def test_an_invented_option_value_is_rejected(self):
        design, _source = _v4_design_and_source()
        refined = validate_design_spec(_refined(fabrics=["unobtainium"]))
        with pytest.raises(RefinedSelectionsInvalid) as excinfo:
            assert_refined_selections_are_answerable(design, refined)
        assert excinfo.value.fields == ["fabrics"]

    def test_a_value_that_breaks_a_compatibility_rule_is_rejected(self):
        # questionnaire v4 ships `covered_midriff_excludes_deep_v_neck`: a
        # covered midriff forbids the plunging deep V. The source concept has a
        # semi-sheer midriff, so set BOTH — a covered midriff and a deep V — and
        # the pair must be refused even though each value exists on its own.
        design, _source = _v4_design_and_source()
        refined = validate_design_spec(
            _refined(midriff="covered_midriff", neckline_style="deep_v_neck")
        )
        with pytest.raises(RefinedSelectionsInvalid) as excinfo:
            assert_refined_selections_are_answerable(design, refined)
        assert "neckline_style" in excinfo.value.fields

    def test_a_silhouette_belonging_to_another_garment_is_rejected(self):
        # The garment-dependent silhouette restriction, which no DesignSpec
        # bound could ever catch: `classic_gharara` is a real, declared option,
        # just not one a lehenga may take.
        design, _source = _v4_design_and_source()
        refined = validate_design_spec(_refined(silhouette="classic_gharara"))
        with pytest.raises(RefinedSelectionsInvalid) as excinfo:
            assert_refined_selections_are_answerable(design, refined)
        assert excinfo.value.fields == ["silhouette"]

    def test_a_multi_select_beyond_its_item_ceiling_is_rejected(self):
        # `fabrics` allows at most 3 in the questionnaire; the DesignSpec bound
        # is 8, so only the questionnaire can catch this.
        design, _source = _v4_design_and_source()
        refined = validate_design_spec(_refined(fabrics=["silk", "organza", "velvet", "net"]))
        with pytest.raises(RefinedSelectionsInvalid) as excinfo:
            assert_refined_selections_are_answerable(design, refined)
        assert excinfo.value.fields == ["fabrics"]

    def test_an_exclusive_value_combined_with_another_is_rejected(self):
        design, _source = _v4_design_and_source()
        refined = validate_design_spec(_refined(embellishment_styles=["none", "zardozi"]))
        with pytest.raises(RefinedSelectionsInvalid) as excinfo:
            assert_refined_selections_are_answerable(design, refined)
        assert excinfo.value.fields == ["embellishment_styles"]

    def test_an_optional_single_choice_refined_to_absent_is_accepted(self):
        design, _source = _v4_design_and_source()
        refined = validate_design_spec(_refined(back_coverage=None))
        assert_refined_selections_are_answerable(design, refined)

    @pytest.mark.parametrize(
        "field, absent_value",
        [
            ("garment_type", None),
            ("ceremony", None),
            ("silhouette", None),
            ("embellishment_styles", []),
        ],
    )
    def test_a_questionnaire_required_selection_cannot_be_absent_at_all(self, field, absent_value):
        # The two layers agree, so "refined to absent" is unreachable for a
        # required question rather than merely rejected: the DesignSpec contract
        # makes every questionnaire-required selection non-optional, and the
        # questionnaire would refuse it a second time if it ever got through.
        # Asserted per field so a future version that relaxes one is caught.
        payload = _refined()
        payload["source_selections"][field] = absent_value
        with pytest.raises(ValidationError):
            validate_design_spec(payload)

    def test_a_design_with_no_questionnaire_version_fails_closed(self):
        design, _source = _v4_design_and_source()
        design.questionnaire_version = None
        with pytest.raises(RefinementQuestionnaireUnavailable):
            assert_refined_selections_are_answerable(design, validate_design_spec(SOURCE_SPEC))


class TestThePinnedVersionNotTheActiveOne:
    def test_a_value_valid_only_in_the_ACTIVE_version_is_rejected(self):
        # The design is pinned to a v4 questionnaire whose `fabrics` question
        # does not offer `khaddar`. A NEWER version is then activated that does.
        # The refinement must still be judged against the design's own version.
        design, _source = _v4_design_and_source()
        pinned = design.questionnaire_version
        pinned.status = QuestionnaireVersion.Status.RETIRED
        pinned.save(update_fields=["status"])

        newer_schema = v4_schema()
        for step in newer_schema["steps"]:
            for question in step["questions"]:
                if question["id"] == "fabrics":
                    question["options"].append({"value": "khaddar", "label": "Khaddar"})
        QuestionnaireVersion.objects.create(version=9, status="active", schema=newer_schema)

        refined = validate_design_spec(_refined(fabrics=["khaddar"]))
        with pytest.raises(RefinedSelectionsInvalid) as excinfo:
            assert_refined_selections_are_answerable(design, refined)
        assert excinfo.value.fields == ["fabrics"]

    def test_a_design_pinned_to_a_retired_version_is_still_refinable(self):
        design, source = _v4_design_and_source()
        pinned = design.questionnaire_version
        pinned.status = QuestionnaireVersion.Status.RETIRED
        pinned.save(update_fields=["status"])
        make_active_v4(version=9)

        refined = _refined(fabrics=["silk", "organza"])
        provider = SequenceProvider([payload_result(refined)])
        version = generate_refined_design_spec_for_design(
            design,
            source,
            normalise_refinement_request(
                {"schema_version": 1, "change_type": "fabric_and_texture"}
            ),
            provider=provider,
        )
        assert version.design_spec["source_selections"]["fabrics"] == ["silk", "organza"]


class TestNothingIsPersistedOnRejection:
    def test_an_invented_value_persists_nothing_and_leaves_the_source_untouched(self):
        design, source = _v4_design_and_source()
        before = copy.deepcopy(source.design_spec)
        bad = payload_result(_refined(fabrics=["unobtainium"]))
        provider = SequenceProvider([bad, payload_result(_refined(fabrics=["unobtainium"]))])

        with pytest.raises(RefinementGenerationFailed) as excinfo:
            generate_refined_design_spec_for_design(
                design,
                source,
                normalise_refinement_request(
                    {"schema_version": 1, "change_type": "fabric_and_texture"}
                ),
                provider=provider,
            )

        # Bounded retries, then a controlled terminal error. Never a partial
        # accept.
        assert excinfo.value.attempts == 2
        assert provider.calls == 2
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()
        source.refresh_from_db()
        assert source.design_spec == before
