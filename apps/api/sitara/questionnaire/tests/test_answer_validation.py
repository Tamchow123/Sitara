"""Authoritative questionnaire answer validation.

Covers the shared cross-language contract cases plus Python-specific
totality, normalisation and rule-semantics tests. The validator must never
raise an incidental TypeError/KeyError/ValueError — every malformed input
becomes a controlled QuestionnaireAnswerError.
"""

import pytest

from sitara.questionnaire.answer_validation import (
    TOP_LEVEL_ERROR_KEY,
    QuestionnaireAnswerError,
    validate_questionnaire_answers,
)
from sitara.questionnaire.schema_validation import validate_questionnaire_schema

from .contract import load_contract

_CONTRACT = load_contract()
_SCHEMA = _CONTRACT["schema"]
_CASES = _CONTRACT["cases"]


def _run(answers, *, require_complete):
    return validate_questionnaire_answers(_SCHEMA, answers, require_complete=require_complete)


class TestSharedContract:
    def test_embedded_schema_is_a_valid_questionnaire_schema(self):
        # The compact contract schema must itself be format-valid, so both
        # languages validate against a legal questionnaire.
        validate_questionnaire_schema(_SCHEMA)

    @pytest.mark.parametrize("case", _CASES, ids=lambda case: case["name"])
    def test_case(self, case):
        answers = case["answers"]
        require_complete = case["require_complete"]
        if case["valid"]:
            # Must not raise.
            _run(answers, require_complete=require_complete)
            return
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            _run(answers, require_complete=require_complete)
        errors = excinfo.value.errors
        for expected_key in case.get("error_questions", []):
            assert expected_key in errors, (case["name"], errors)


class TestTotalityOverArbitraryInput:
    @pytest.mark.parametrize(
        "answers",
        [
            None,
            "a string",
            123,
            True,
            ["a", "list"],
            {"garment_type": {"nested": "object"}},
            {"garment_type": 5},
            {"garment_type": None},
            {"colour_palette": {"not": "a list"}},
            {"colour_palette": [1, 2, 3]},
            {"colour_palette": [None]},
            {"final_notes": ["not", "text"]},
            {"final_notes": 12},
            {1: "int key"},
            {"": "empty key"},
        ],
    )
    def test_malformed_input_raises_controlled_error_only(self, answers):
        # No incidental TypeError/KeyError/ValueError — always the domain error.
        with pytest.raises(QuestionnaireAnswerError):
            _run(answers, require_complete=False)

    def test_top_level_non_object_uses_top_level_key(self):
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            _run(["garment_type"], require_complete=False)
        assert TOP_LEVEL_ERROR_KEY in excinfo.value.errors


class TestTextNormalisation:
    def test_crlf_and_cr_become_lf_and_outer_whitespace_trimmed(self):
        result = _run(
            {"garment_type": "lehenga", "final_notes": "  a\r\nb\rc  "},
            require_complete=False,
        )
        assert result["final_notes"] == "a\nb\nc"

    def test_internal_whitespace_is_preserved(self):
        result = _run(
            {"garment_type": "lehenga", "final_notes": "keep   inner  spaces"},
            require_complete=False,
        )
        assert result["final_notes"] == "keep   inner  spaces"


class TestPartialVersusComplete:
    def test_partial_allows_missing_required(self):
        # Draft mode: garment only, nothing else required.
        assert _run({"garment_type": "lehenga"}, require_complete=False) == {
            "garment_type": "lehenga"
        }

    def test_complete_requires_visible_required_questions(self):
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            _run({"garment_type": "lehenga"}, require_complete=True)
        errors = excinfo.value.errors
        # silhouette, colour_palette and embellishment_styles are required.
        assert {"silhouette", "colour_palette", "embellishment_styles"} <= set(errors)

    def test_min_items_only_enforced_when_complete(self):
        # An empty (but present) multi-choice passes draft, fails complete.
        _run(
            {"garment_type": "lehenga", "colour_palette": []},
            require_complete=False,
        )
        with pytest.raises(QuestionnaireAnswerError):
            _run(
                {
                    "garment_type": "lehenga",
                    "silhouette": "flared_lehenga",
                    "colour_palette": [],
                    "embellishment_styles": ["zardozi"],
                },
                require_complete=True,
            )


class TestRuleSemantics:
    def test_hidden_question_answer_rejected_in_both_modes(self):
        for require_complete in (False, True):
            with pytest.raises(QuestionnaireAnswerError) as excinfo:
                _run(
                    {"garment_type": "lehenga", "saree_drape": "nivi_drape"},
                    require_complete=require_complete,
                )
            assert "saree_drape" in excinfo.value.errors

    def test_restrict_options_rejects_out_of_scope_option(self):
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            _run(
                {"garment_type": "saree", "silhouette": "flared_lehenga"},
                require_complete=False,
            )
        assert "silhouette" in excinfo.value.errors

    def test_require_rule_semantics_via_base_required(self):
        # colour_palette is base-required and visible → required when complete.
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            _run(
                {
                    "garment_type": "lehenga",
                    "silhouette": "flared_lehenga",
                    "embellishment_styles": ["zardozi"],
                },
                require_complete=True,
            )
        assert "colour_palette" in excinfo.value.errors

    def test_multi_choice_preserves_submitted_order(self):
        result = _run(
            {"garment_type": "lehenga", "colour_palette": ["gold", "red"]},
            require_complete=False,
        )
        assert result["colour_palette"] == ["gold", "red"]

    def test_exclusive_value_alone_is_valid(self):
        result = _run(
            {"garment_type": "lehenga", "embellishment_styles": ["none"]},
            require_complete=False,
        )
        assert result["embellishment_styles"] == ["none"]
