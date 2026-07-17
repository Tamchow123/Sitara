"""Authoritative validation of submitted questionnaire ANSWERS.

Django is the source of truth for whether a set of answers is valid; the
frontend's Zod validation is a derived convenience only. This module
validates a JSON answer object against a (format-valid) questionnaire schema
and returns a normalised mapping that is safe to persist.

Totality (CLAUDE.md §12): the validator is total over arbitrary
JSON-compatible input. Any malformed shape — a list where an object is
expected, a number where a string belongs, an unknown question id, an
unhashable value — becomes a controlled :class:`QuestionnaireAnswerError`,
never an incidental ``TypeError``/``KeyError``/``ValueError`` or traceback.

Two modes:

- ``require_complete=False`` (draft autosave): structurally validate every
  supplied value, enforce option allowlists, active restrictions,
  exclusivity and maximum counts/lengths — but do NOT require missing
  answers or enforce minimum counts/lengths. This permits secure partial
  saves.
- ``require_complete=True`` (final validation): additionally require every
  visible required question to be answered and enforce minimum item counts
  and minimum text lengths.

Rule semantics (visibility / required / restrictions) live in
:mod:`sitara.questionnaire.rules`; this module only applies them to answers.
"""

from __future__ import annotations

from . import rules

# Error codes / messages are safe to surface: they never echo the raw
# submitted value, only which question failed and why.
_UNKNOWN_QUESTION = "This question is not part of the questionnaire."
_NOT_APPLICABLE = "This question does not apply to your current answers."
_WRONG_TYPE_SINGLE = "Choose one of the available options."
_WRONG_TYPE_MULTI = "Select from the available options."
_WRONG_TYPE_TEXT = "This answer must be text."
_UNKNOWN_OPTION = "That option is not available."
_DUPLICATE_OPTION = "The same option was selected more than once."
_TOO_FEW_ITEMS = "Please select at least {count}."
_TOO_MANY_ITEMS = "Please select at most {count}."
_EXCLUSIVE = "That option cannot be combined with any other."
_NO_OPTIONS = "No options are available for your current answers."
_TOO_SHORT = "Please use at least {count} characters."
_TOO_LONG = "Please use at most {count} characters."
_REQUIRED = "This question is required."

# Special error keys (not question ids, which are lower-case machine ids).
TOP_LEVEL_ERROR_KEY = "__all__"


class QuestionnaireAnswerError(Exception):
    """Answers failed validation.

    ``errors`` maps question id (or :data:`TOP_LEVEL_ERROR_KEY`) to a list of
    safe, human-readable messages."""

    def __init__(self, errors: dict[str, list[str]]):
        self.errors = errors
        super().__init__("questionnaire answers failed validation")


def normalise_text(value: str) -> str:
    # CRLF/CR → LF, trim OUTER whitespace, preserve meaningful internal
    # whitespace. The value is never interpreted as HTML or Markdown.
    unified = value.replace("\r\n", "\n").replace("\r", "\n")
    return unified.strip()


def _structural_value(
    question: dict, value: object
) -> tuple[object | None, str | None, set | None]:
    """Validate a value's basic SHAPE against its question type.

    Returns ``(structural_value, error_message, selected_set)``. On success
    ``error_message`` is None; for choice questions ``selected_set`` is the
    set of chosen option values (used for rule evaluation). Membership in the
    currently-restricted allow-set, counts and lengths are checked later.
    """
    question_type = question.get("type")
    declared = set(rules.declared_option_values(question))

    if question_type == "single_choice":
        if not isinstance(value, str):
            return None, _WRONG_TYPE_SINGLE, None
        if value not in declared:
            return None, _UNKNOWN_OPTION, None
        return value, None, {value}

    if question_type == "multi_choice":
        # bool is a str? no — but a plain str/dict must be rejected as a list.
        if not isinstance(value, list):
            return None, _WRONG_TYPE_MULTI, None
        seen: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return None, _WRONG_TYPE_MULTI, None
            if item not in declared:
                return None, _UNKNOWN_OPTION, None
            if item in seen:
                return None, _DUPLICATE_OPTION, None
            seen.append(item)
        return list(seen), None, set(seen)

    if question_type == "text":
        if not isinstance(value, str):
            return None, _WRONG_TYPE_TEXT, None
        return normalise_text(value), None, None

    # Unreachable for a format-valid schema.
    return None, _WRONG_TYPE_TEXT, None


def _is_answered(question: dict, normalised: object) -> bool:
    if question.get("type") == "multi_choice":
        return isinstance(normalised, list) and len(normalised) > 0
    if question.get("type") == "text":
        return isinstance(normalised, str) and normalised != ""
    return normalised is not None and normalised != ""


def validate_questionnaire_answers(
    schema: dict,
    answers: object,
    *,
    require_complete: bool,
) -> dict:
    """Validate ``answers`` against ``schema`` and return a normalised dict.

    Raises :class:`QuestionnaireAnswerError` (with per-question messages) on
    any violation. The returned mapping contains only the supplied answers,
    normalised (text trimmed and newline-unified, multi-choice as an ordered
    unique list); unanswered optional questions are simply absent.
    """
    index = rules.questions_by_id(schema)

    if not isinstance(answers, dict):
        raise QuestionnaireAnswerError({TOP_LEVEL_ERROR_KEY: ["Answers must be an object."]})

    errors: dict[str, list[str]] = {}
    # Structural pass: type + declared-option membership only, so we can build
    # the selected-values view that rule evaluation needs.
    structural: dict[str, object] = {}
    selected: dict[str, set[str]] = {}
    for key, value in answers.items():
        question = index.get(key) if isinstance(key, str) else None
        if question is None:
            errors[str(key)] = [_UNKNOWN_QUESTION]
            continue
        structural_value, message, selected_set = _structural_value(question, value)
        if message is not None:
            errors[key] = [message]
            continue
        structural[key] = structural_value
        if selected_set is not None:
            selected[key] = selected_set

    visibility = rules.visible_questions(schema, selected)
    required = rules.required_questions(schema, selected, visibility)
    allowed = rules.allowed_options(schema, selected)

    normalised: dict[str, object] = {}
    for key, structural_value in structural.items():
        question = index[key]
        if not visibility.get(key, False):
            # An answer to a hidden question is always rejected — in both
            # draft and complete modes.
            errors[key] = [_NOT_APPLICABLE]
            continue
        question_type = question["type"]
        constraints = question.get("constraints", {}) or {}
        if question_type in ("single_choice", "multi_choice"):
            allow_set = allowed.get(key, set())
            if not allow_set:
                errors[key] = [_NO_OPTIONS]
                continue
            chosen = selected.get(key, set())
            if chosen - allow_set:
                errors[key] = [_UNKNOWN_OPTION]
                continue
        if question_type == "multi_choice":
            message = _validate_multi(structural_value, constraints, require_complete)
            if message is not None:
                errors[key] = [message]
                continue
        elif question_type == "text":
            message = _validate_text(structural_value, constraints, require_complete)
            if message is not None:
                errors[key] = [message]
                continue
        normalised[key] = structural_value

    if require_complete:
        for question_id, is_required in required.items():
            if not is_required:
                continue
            if question_id in errors:
                continue
            if not _is_answered(index[question_id], normalised.get(question_id)):
                errors[question_id] = [_REQUIRED]

    if errors:
        raise QuestionnaireAnswerError(errors)
    return normalised


def _validate_multi(values: list, constraints: dict, require_complete: bool) -> str | None:
    exclusive = set(constraints.get("exclusive_values", []) or [])
    if exclusive and len(values) > 1 and exclusive.intersection(values):
        return _EXCLUSIVE
    max_items = constraints.get("max_items")
    if isinstance(max_items, int) and not isinstance(max_items, bool) and len(values) > max_items:
        return _TOO_MANY_ITEMS.format(count=max_items)
    if require_complete:
        min_items = constraints.get("min_items")
        if (
            isinstance(min_items, int)
            and not isinstance(min_items, bool)
            and len(values) < min_items
        ):
            return _TOO_FEW_ITEMS.format(count=min_items)
    return None


def _validate_text(value: str, constraints: dict, require_complete: bool) -> str | None:
    max_length = constraints.get("max_length")
    if isinstance(max_length, int) and not isinstance(max_length, bool) and len(value) > max_length:
        return _TOO_LONG.format(count=max_length)
    if require_complete:
        min_length = constraints.get("min_length")
        if (
            isinstance(min_length, int)
            and not isinstance(min_length, bool)
            and len(value) < min_length
        ):
            return _TOO_SHORT.format(count=min_length)
    return None
