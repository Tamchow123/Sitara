"""Pure-Python validation of the questionnaire schema FORMAT.

This validates the questionnaire *definition* — not user answers (answer
validation against the active schema is a later phase). The format is
deliberately small and declarative: three question types, bounded
constraints, and an allowlisted compatibility-rule vocabulary. There is no
expression language, no eval, no imports from schema data and no arbitrary
JSON Schema extension mechanism — anything outside the allowlists is
rejected, never interpreted.

Every check raises :class:`QuestionnaireSchemaError` with a message built
from validated machine identifiers and structural positions only, so error
text is safe to surface in admin messages and test output.
"""

import json
import re

from django.core.exceptions import ValidationError

SUPPORTED_SCHEMA_VERSION = 1

# Stable machine identifiers: persisted answers will reference these forever.
MACHINE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

QUESTION_TYPES = frozenset({"single_choice", "multi_choice", "text"})
RULE_OPERATORS = frozenset({"equals", "in", "not_in"})
RULE_ACTIONS = frozenset({"show", "hide", "require", "restrict_options"})

# Structural bounds. Generous for a real questionnaire, tight enough that a
# malformed or hostile schema cannot balloon storage or responses.
MAX_SCHEMA_BYTES = 262_144  # 256 KiB serialised
MAX_STEPS = 20
MAX_QUESTIONS_PER_STEP = 20
MAX_OPTIONS_PER_QUESTION = 50
MAX_RULES = 100
MAX_TITLE_LENGTH = 200
MAX_LABEL_LENGTH = 200
MAX_HELP_TEXT_LENGTH = 500
MAX_OPTION_DESCRIPTION_LENGTH = 300
MAX_TEXT_LENGTH_LIMIT = 2_000  # ceiling for any text question's max_length

_TOP_LEVEL_KEYS = frozenset({"schema_version", "key", "title", "steps", "rules"})
_STEP_KEYS = frozenset({"id", "title", "description", "questions"})
_QUESTION_KEYS = frozenset(
    {"id", "type", "label", "help_text", "required", "options", "constraints"}
)
_OPTION_KEYS = frozenset({"value", "label", "description"})
_RULE_KEYS = frozenset({"id", "when", "then"})
_WHEN_KEYS = frozenset({"question_id", "operator", "values"})
_THEN_KEYS = frozenset({"action", "question_id", "values"})
_MULTI_CHOICE_CONSTRAINT_KEYS = frozenset({"min_items", "max_items", "exclusive_values"})
_TEXT_CONSTRAINT_KEYS = frozenset({"min_length", "max_length"})


class QuestionnaireSchemaError(ValidationError):
    """The questionnaire schema is not valid. Messages are safe to show."""


def _fail(path: str, reason: str) -> None:
    raise QuestionnaireSchemaError(f"{path}: {reason}")


def _require_dict(value, path: str) -> dict:
    if not isinstance(value, dict):
        _fail(path, "must be a JSON object")
    return value


def _require_known_keys(mapping: dict, allowed: frozenset, path: str) -> None:
    if set(mapping) - allowed:
        # Unknown keys are raw malformed input — never echoed back, so the
        # message stays safe for admin display and logs.
        _fail(path, "contains unsupported keys")


def _require_machine_id(value, path: str) -> str:
    if not isinstance(value, str) or not MACHINE_ID_PATTERN.fullmatch(value):
        _fail(path, "must be a lower-case machine identifier matching ^[a-z][a-z0-9_]{1,63}$")
    return value


def _require_machine_id_list(
    value,
    path: str,
    *,
    require_non_empty: bool = True,
    exact_length: int | None = None,
) -> list[str]:
    """A JSON list whose every item is a unique, valid machine identifier.

    This is the single gate through which rule values and exclusive values
    pass before any membership check — booleans, numbers, objects, nested
    lists and null all raise :class:`QuestionnaireSchemaError` here instead
    of surfacing later as ``TypeError`` from an unhashable set lookup.
    """
    if not isinstance(value, list):
        _fail(path, "must be a list of option values")
    if require_non_empty and not value:
        _fail(path, "must be a non-empty list of option values")
    if exact_length is not None and len(value) != exact_length:
        _fail(path, f"must contain exactly {exact_length} value(s)")
    validated: list[str] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        _require_machine_id(item, item_path)
        if item in validated:
            _fail(item_path, "duplicate value")
        validated.append(item)
    return validated


def _require_allowed_string(value, path: str, allowed: frozenset) -> str:
    """An enum field: exactly a string drawn from a fixed allowlist.

    The type check runs before the membership test — a dict or list here
    would otherwise raise ``TypeError`` from the frozenset hash lookup.
    The message names only the allowed constants, never the supplied value.
    """
    if not isinstance(value, str) or value not in allowed:
        _fail(path, f"must be one of {sorted(allowed)}")
    return value


def _require_text(value, path: str, *, max_length: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if not allow_empty and not value.strip():
        _fail(path, "must not be empty")
    if len(value) > max_length:
        _fail(path, f"must be at most {max_length} characters")
    return value


def _require_bounded_int(value, path: str, *, maximum: int) -> int:
    # bool is an int subclass; a schema saying true where a count belongs
    # is a mistake, not a zero or a one.
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "must be a non-negative integer")
    if value < 0:
        _fail(path, "must be a non-negative integer")
    if value > maximum:
        _fail(path, f"must be at most {maximum}")
    return value


def _validate_options(question: dict, path: str) -> list[str]:
    options = question.get("options")
    if not isinstance(options, list) or not options:
        _fail(path, "choice questions require a non-empty options list")
    if len(options) > MAX_OPTIONS_PER_QUESTION:
        _fail(path, f"must have at most {MAX_OPTIONS_PER_QUESTION} options")
    values: list[str] = []
    for index, option in enumerate(options):
        option_path = f"{path}.options[{index}]"
        _require_dict(option, option_path)
        _require_known_keys(option, _OPTION_KEYS, option_path)
        value = _require_machine_id(option.get("value"), f"{option_path}.value")
        _require_text(option.get("label"), f"{option_path}.label", max_length=MAX_LABEL_LENGTH)
        if "description" in option:
            _require_text(
                option["description"],
                f"{option_path}.description",
                max_length=MAX_OPTION_DESCRIPTION_LENGTH,
            )
        if value in values:
            _fail(f"{option_path}.value", f"duplicate option value '{value}'")
        values.append(value)
    return values


def _validate_multi_choice_constraints(constraints: dict, option_values: list[str], path: str):
    _require_known_keys(constraints, _MULTI_CHOICE_CONSTRAINT_KEYS, path)
    min_items = None
    max_items = None
    if "min_items" in constraints:
        min_items = _require_bounded_int(
            constraints["min_items"], f"{path}.min_items", maximum=len(option_values)
        )
    if "max_items" in constraints:
        max_items = _require_bounded_int(
            constraints["max_items"], f"{path}.max_items", maximum=len(option_values)
        )
        if max_items < 1:
            _fail(f"{path}.max_items", "must be at least 1")
    if min_items is not None and max_items is not None and min_items > max_items:
        _fail(f"{path}.min_items", "must not exceed max_items")
    if "exclusive_values" in constraints:
        exclusive = _require_machine_id_list(
            constraints["exclusive_values"], f"{path}.exclusive_values"
        )
        for index, value in enumerate(exclusive):
            if value not in option_values:
                _fail(
                    f"{path}.exclusive_values[{index}]",
                    "references an option value that does not exist on this question",
                )


def _validate_text_constraints(constraints: dict, path: str) -> None:
    _require_known_keys(constraints, _TEXT_CONSTRAINT_KEYS, path)
    if "max_length" not in constraints:
        # Free text is ALWAYS capped — an uncapped text question could not
        # be safely persisted or passed toward generation later.
        _fail(f"{path}.max_length", "text questions must declare a max_length")
    max_length = _require_bounded_int(
        constraints["max_length"], f"{path}.max_length", maximum=MAX_TEXT_LENGTH_LIMIT
    )
    if max_length < 1:
        _fail(f"{path}.max_length", "must be at least 1")
    if "min_length" in constraints:
        min_length = _require_bounded_int(
            constraints["min_length"], f"{path}.min_length", maximum=MAX_TEXT_LENGTH_LIMIT
        )
        if min_length > max_length:
            _fail(f"{path}.min_length", "must not exceed max_length")


def _validate_question(question, path: str, seen_question_ids: set[str]) -> tuple[str, dict]:
    """Validate one question; returns (question_id, question)."""
    _require_dict(question, path)
    _require_known_keys(question, _QUESTION_KEYS, path)

    question_id = _require_machine_id(question.get("id"), f"{path}.id")
    if question_id in seen_question_ids:
        _fail(f"{path}.id", f"duplicate question id '{question_id}'")

    question_type = _require_allowed_string(question.get("type"), f"{path}.type", QUESTION_TYPES)

    _require_text(question.get("label"), f"{path}.label", max_length=MAX_LABEL_LENGTH)
    if "help_text" in question:
        _require_text(
            question["help_text"],
            f"{path}.help_text",
            max_length=MAX_HELP_TEXT_LENGTH,
            allow_empty=True,
        )
    if not isinstance(question.get("required"), bool):
        _fail(f"{path}.required", "must be true or false")

    constraints = question.get("constraints", {})
    _require_dict(constraints, f"{path}.constraints")

    if question_type == "text":
        if question.get("options"):
            _fail(f"{path}.options", "text questions must not declare options")
        _validate_text_constraints(constraints, f"{path}.constraints")
    else:
        option_values = _validate_options(question, path)
        if question_type == "single_choice":
            # A single choice is constrained BY its declared options.
            if constraints:
                _fail(f"{path}.constraints", "single_choice questions take no constraints")
        else:
            _validate_multi_choice_constraints(constraints, option_values, f"{path}.constraints")
    return question_id, question


def _option_values(question: dict) -> set[str]:
    return {option["value"] for option in question.get("options", [])}


def _validate_rule(rule, path: str, questions_by_id: dict[str, dict], seen_rule_ids: set[str]):
    _require_dict(rule, path)
    _require_known_keys(rule, _RULE_KEYS, path)

    rule_id = _require_machine_id(rule.get("id"), f"{path}.id")
    if rule_id in seen_rule_ids:
        _fail(f"{path}.id", f"duplicate rule id '{rule_id}'")
    seen_rule_ids.add(rule_id)

    when = _require_dict(rule.get("when"), f"{path}.when")
    _require_known_keys(when, _WHEN_KEYS, f"{path}.when")
    condition_question_id = _require_machine_id(when.get("question_id"), f"{path}.when.question_id")
    condition_question = questions_by_id.get(condition_question_id)
    if condition_question is None:
        _fail(f"{path}.when.question_id", "references a question that does not exist")
    if condition_question["type"] == "text":
        _fail(f"{path}.when.question_id", "conditions may only reference choice questions")

    operator = _require_allowed_string(
        when.get("operator"), f"{path}.when.operator", RULE_OPERATORS
    )

    values = _require_machine_id_list(
        when.get("values"),
        f"{path}.when.values",
        exact_length=1 if operator == "equals" else None,
    )
    condition_options = _option_values(condition_question)
    for index, value in enumerate(values):
        if value not in condition_options:
            _fail(
                f"{path}.when.values[{index}]",
                "references an option value that does not exist on the condition question",
            )

    then = _require_dict(rule.get("then"), f"{path}.then")
    _require_known_keys(then, _THEN_KEYS, f"{path}.then")
    action = _require_allowed_string(then.get("action"), f"{path}.then.action", RULE_ACTIONS)

    target_question_id = _require_machine_id(then.get("question_id"), f"{path}.then.question_id")
    target_question = questions_by_id.get(target_question_id)
    if target_question is None:
        _fail(f"{path}.then.question_id", "references a question that does not exist")

    if action == "restrict_options":
        if target_question["type"] == "text":
            _fail(f"{path}.then.question_id", "restrict_options must target a choice question")
        restricted = _require_machine_id_list(then.get("values"), f"{path}.then.values")
        target_options = _option_values(target_question)
        for index, value in enumerate(restricted):
            if value not in target_options:
                _fail(
                    f"{path}.then.values[{index}]",
                    "references an option value that does not exist on the target question",
                )
    elif "values" in then:
        _fail(f"{path}.then.values", f"'{action}' does not take values")


def validate_questionnaire_schema(schema: object) -> None:
    """Validate a complete questionnaire schema; raises on the first defect.

    Raises :class:`QuestionnaireSchemaError` (a Django ``ValidationError``)
    with a safe, structural message. Returns None on success.
    """
    _require_dict(schema, "schema")

    try:
        serialised_size = len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        _fail("schema", "must be JSON-serialisable")
    if serialised_size > MAX_SCHEMA_BYTES:
        _fail("schema", f"must serialise to at most {MAX_SCHEMA_BYTES} bytes")

    _require_known_keys(schema, _TOP_LEVEL_KEYS, "schema")
    for key in sorted(_TOP_LEVEL_KEYS):
        if key not in schema:
            _fail("schema", f"missing required key '{key}'")

    schema_version = schema["schema_version"]
    # Strictly the int 1: bool is an int subclass (True == 1) and 1.0 == 1,
    # so equality alone would accept both — require the exact type first.
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        _fail("schema.schema_version", f"must be the integer {SUPPORTED_SCHEMA_VERSION}")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        _fail("schema.schema_version", f"must be {SUPPORTED_SCHEMA_VERSION}")
    _require_machine_id(schema["key"], "schema.key")
    _require_text(schema["title"], "schema.title", max_length=MAX_TITLE_LENGTH)

    steps = schema["steps"]
    if not isinstance(steps, list) or not steps:
        _fail("schema.steps", "must be a non-empty list")
    if len(steps) > MAX_STEPS:
        _fail("schema.steps", f"must have at most {MAX_STEPS} steps")

    seen_step_ids: set[str] = set()
    questions_by_id: dict[str, dict] = {}
    for step_index, step in enumerate(steps):
        step_path = f"schema.steps[{step_index}]"
        _require_dict(step, step_path)
        _require_known_keys(step, _STEP_KEYS, step_path)
        step_id = _require_machine_id(step.get("id"), f"{step_path}.id")
        if step_id in seen_step_ids:
            _fail(f"{step_path}.id", f"duplicate step id '{step_id}'")
        seen_step_ids.add(step_id)
        _require_text(step.get("title"), f"{step_path}.title", max_length=MAX_TITLE_LENGTH)
        if "description" in step:
            _require_text(
                step["description"],
                f"{step_path}.description",
                max_length=MAX_HELP_TEXT_LENGTH,
                allow_empty=True,
            )
        questions = step.get("questions")
        if not isinstance(questions, list) or not questions:
            _fail(f"{step_path}.questions", "must be a non-empty list")
        if len(questions) > MAX_QUESTIONS_PER_STEP:
            _fail(
                f"{step_path}.questions",
                f"must have at most {MAX_QUESTIONS_PER_STEP} questions",
            )
        for question_index, question in enumerate(questions):
            question_path = f"{step_path}.questions[{question_index}]"
            question_id, validated = _validate_question(
                question, question_path, set(questions_by_id)
            )
            questions_by_id[question_id] = validated

    rules = schema["rules"]
    if not isinstance(rules, list):
        _fail("schema.rules", "must be a list")
    if len(rules) > MAX_RULES:
        _fail("schema.rules", f"must have at most {MAX_RULES} rules")
    seen_rule_ids: set[str] = set()
    for rule_index, rule in enumerate(rules):
        _validate_rule(rule, f"schema.rules[{rule_index}]", questions_by_id, seen_rule_ids)
