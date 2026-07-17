"""Schema-format validator tests.

Each test mutates one aspect of a known-valid schema and expects a
QuestionnaireSchemaError; the valid schema itself must pass unchanged.
"""

import pytest

from sitara.questionnaire.schema_validation import (
    MAX_SCHEMA_BYTES,
    QuestionnaireSchemaError,
    validate_questionnaire_schema,
)

from .utils import valid_schema


def _question(schema: dict, question_id: str) -> dict:
    for step in schema["steps"]:
        for question in step["questions"]:
            if question["id"] == question_id:
                return question
    raise AssertionError(f"question {question_id} not in schema")


class TestTopLevel:
    def test_valid_schema_passes(self):
        validate_questionnaire_schema(valid_schema())

    @pytest.mark.parametrize("bad", [None, [], "schema", 42])
    def test_non_object_schema_is_rejected(self, bad):
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(bad)

    @pytest.mark.parametrize("missing", ["schema_version", "key", "title", "steps", "rules"])
    def test_missing_required_top_level_key(self, missing):
        schema = valid_schema()
        del schema[missing]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_unknown_top_level_key_is_rejected(self):
        schema = valid_schema()
        schema["extensions"] = {"custom": True}
        with pytest.raises(QuestionnaireSchemaError, match="unsupported keys"):
            validate_questionnaire_schema(schema)

    def test_unsupported_schema_version(self):
        schema = valid_schema()
        schema["schema_version"] = 2
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    @pytest.mark.parametrize("bad", [True, False, "1", 1.0, None])
    def test_schema_version_must_be_exactly_the_int_one(self, bad):
        # bool is an int subclass (True == 1) and 1.0 == 1 — neither may
        # slip through equality; only the exact int 1 is accepted.
        schema = valid_schema()
        schema["schema_version"] = bad
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    @pytest.mark.parametrize("bad_key", ["Bad-Key", "1starts_with_digit", "UPPER", "a", ""])
    def test_key_must_be_a_machine_identifier(self, bad_key):
        schema = valid_schema()
        schema["key"] = bad_key
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_oversized_schema_is_rejected(self):
        schema = valid_schema()
        schema["padding"] = "x" * (MAX_SCHEMA_BYTES + 1)
        # Size is checked before the key allowlist, so the size limit is
        # what rejects this.
        with pytest.raises(QuestionnaireSchemaError, match="at most"):
            validate_questionnaire_schema(schema)

    def test_empty_steps_are_rejected(self):
        schema = valid_schema()
        schema["steps"] = []
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)


class TestStepsAndQuestions:
    def test_duplicate_step_ids(self):
        schema = valid_schema()
        step = dict(schema["steps"][0])
        step["questions"] = [
            {
                "id": "another_question",
                "type": "text",
                "label": "More?",
                "required": False,
                "constraints": {"max_length": 100},
            }
        ]
        schema["steps"].append(step)
        with pytest.raises(QuestionnaireSchemaError, match="duplicate step id"):
            validate_questionnaire_schema(schema)

    def test_duplicate_question_ids_across_steps(self):
        schema = valid_schema()
        schema["steps"].append(
            {
                "id": "step_two",
                "title": "Step two",
                "questions": [
                    {
                        "id": "garment_type",
                        "type": "text",
                        "label": "Again?",
                        "required": False,
                        "constraints": {"max_length": 100},
                    }
                ],
            }
        )
        with pytest.raises(QuestionnaireSchemaError, match="duplicate question id"):
            validate_questionnaire_schema(schema)

    def test_step_without_questions(self):
        schema = valid_schema()
        schema["steps"][0]["questions"] = []
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_unsupported_question_type(self):
        schema = valid_schema()
        _question(schema, "notes")["type"] = "date"
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_unknown_question_key_is_rejected(self):
        schema = valid_schema()
        _question(schema, "notes")["on_submit"] = "__import__('os')"
        with pytest.raises(QuestionnaireSchemaError, match="unsupported keys"):
            validate_questionnaire_schema(schema)

    def test_question_id_must_match_pattern(self):
        schema = valid_schema()
        _question(schema, "notes")["id"] = "Notes-Field"
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_duplicate_option_values(self):
        schema = valid_schema()
        _question(schema, "garment_type")["options"].append(
            {"value": "saree", "label": "Saree again"}
        )
        with pytest.raises(QuestionnaireSchemaError, match="duplicate option value"):
            validate_questionnaire_schema(schema)

    def test_choice_question_requires_options(self):
        schema = valid_schema()
        _question(schema, "garment_type")["options"] = []
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_text_question_must_not_declare_options(self):
        schema = valid_schema()
        _question(schema, "notes")["options"] = [{"value": "a", "label": "A"}]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_overlong_label_is_rejected(self):
        schema = valid_schema()
        _question(schema, "notes")["label"] = "x" * 201
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)


class TestConstraints:
    def test_text_question_requires_max_length(self):
        schema = valid_schema()
        _question(schema, "notes")["constraints"] = {"min_length": 0}
        with pytest.raises(QuestionnaireSchemaError, match="max_length"):
            validate_questionnaire_schema(schema)

    def test_text_max_length_has_a_ceiling(self):
        schema = valid_schema()
        _question(schema, "notes")["constraints"] = {"max_length": 100_000}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_min_length_may_not_exceed_max_length(self):
        schema = valid_schema()
        _question(schema, "notes")["constraints"] = {"min_length": 10, "max_length": 5}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_min_items_may_not_exceed_max_items(self):
        schema = valid_schema()
        _question(schema, "embellishments")["constraints"] = {"min_items": 3, "max_items": 1}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_max_items_may_not_exceed_the_option_count(self):
        schema = valid_schema()
        _question(schema, "embellishments")["constraints"] = {"max_items": 99}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    @pytest.mark.parametrize("bad", [-1, True, "2", 1.5])
    def test_item_bounds_must_be_non_negative_integers(self, bad):
        schema = valid_schema()
        _question(schema, "embellishments")["constraints"] = {"max_items": bad}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_exclusive_values_must_exist_in_the_options(self):
        schema = valid_schema()
        _question(schema, "embellishments")["constraints"] = {"exclusive_values": ["ghost"]}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    @pytest.mark.parametrize("bad_item", [{"poison": "value"}, ["none"], 3, True, None])
    def test_non_string_exclusive_values_are_rejected(self, bad_item):
        schema = valid_schema()
        _question(schema, "embellishments")["constraints"] = {"exclusive_values": [bad_item]}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_duplicate_exclusive_values_are_rejected(self):
        schema = valid_schema()
        _question(schema, "embellishments")["constraints"] = {"exclusive_values": ["none", "none"]}
        with pytest.raises(QuestionnaireSchemaError, match="duplicate"):
            validate_questionnaire_schema(schema)

    def test_single_choice_takes_no_constraints(self):
        schema = valid_schema()
        _question(schema, "garment_type")["constraints"] = {"max_items": 1}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)


class TestRules:
    def test_rule_condition_question_must_exist(self):
        schema = valid_schema()
        schema["rules"][0]["when"]["question_id"] = "ghost_question"
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_rule_condition_values_must_exist_on_the_question(self):
        schema = valid_schema()
        schema["rules"][0]["when"]["values"] = ["ghost_value"]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_rule_target_question_must_exist(self):
        schema = valid_schema()
        schema["rules"][0]["then"]["question_id"] = "ghost_question"
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_operator_is_allowlisted(self):
        schema = valid_schema()
        schema["rules"][0]["when"]["operator"] = "matches_regex"
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_action_is_allowlisted(self):
        schema = valid_schema()
        schema["rules"][0]["then"] = {"action": "execute", "question_id": "notes"}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_equals_takes_exactly_one_value(self):
        schema = valid_schema()
        schema["rules"][0]["when"]["values"] = ["saree", "lehenga"]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_restrict_options_requires_values(self):
        schema = valid_schema()
        del schema["rules"][0]["then"]["values"]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_restrict_options_values_must_exist_on_the_target(self):
        schema = valid_schema()
        schema["rules"][0]["then"]["values"] = ["ghost_value"]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_show_takes_no_values(self):
        schema = valid_schema()
        schema["rules"][0]["then"] = {
            "action": "show",
            "question_id": "embellishments",
            "values": ["zardozi"],
        }
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_conditions_may_not_reference_text_questions(self):
        schema = valid_schema()
        schema["rules"][0]["when"] = {
            "question_id": "notes",
            "operator": "equals",
            "values": ["anything"],
        }
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_duplicate_rule_ids(self):
        schema = valid_schema()
        schema["rules"].append(dict(schema["rules"][0]))
        with pytest.raises(QuestionnaireSchemaError, match="duplicate rule id"):
            validate_questionnaire_schema(schema)

    def test_arbitrary_rule_keys_are_rejected(self):
        schema = valid_schema()
        schema["rules"][0]["expression"] = "answers['x'] > 1"
        with pytest.raises(QuestionnaireSchemaError, match="unsupported keys"):
            validate_questionnaire_schema(schema)


class TestRuleValueTypes:
    """The validator must be total: every JSON-compatible shape inside rule
    values raises QuestionnaireSchemaError — never TypeError from an
    unhashable set-membership lookup."""

    @pytest.mark.parametrize("bad_item", [{"poison": "value"}, ["saree"], 1, True, False, None])
    def test_non_string_when_values_are_rejected(self, bad_item):
        schema = valid_schema()
        schema["rules"][0]["when"]["values"] = [bad_item]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_duplicate_when_values_are_rejected(self):
        schema = valid_schema()
        schema["rules"][0]["when"]["operator"] = "in"
        schema["rules"][0]["when"]["values"] = ["saree", "saree"]
        with pytest.raises(QuestionnaireSchemaError, match="duplicate"):
            validate_questionnaire_schema(schema)

    @pytest.mark.parametrize("bad_values", [None, "zardozi", {}, 0])
    def test_when_values_must_be_a_list(self, bad_values):
        schema = valid_schema()
        schema["rules"][0]["when"]["values"] = bad_values
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    @pytest.mark.parametrize("bad_item", [{"poison": "value"}, ["zardozi"], 1, True, None])
    def test_non_string_restrict_options_values_are_rejected(self, bad_item):
        schema = valid_schema()
        schema["rules"][0]["then"]["values"] = [bad_item]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(schema)

    def test_duplicate_restrict_options_values_are_rejected(self):
        schema = valid_schema()
        schema["rules"][0]["then"]["values"] = ["zardozi", "zardozi"]
        with pytest.raises(QuestionnaireSchemaError, match="duplicate"):
            validate_questionnaire_schema(schema)
