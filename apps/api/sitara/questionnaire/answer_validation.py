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

import re

from . import rules
from .schema_validation import MAX_CUSTOM_COLOURS_LIMIT

# Accepts what a native <input type="color"> emits, case-insensitively, and
# normalises to lower case. No 3-digit shorthand, no named colours, no
# rgb()/hsl(), no alpha — so nothing but a plain six-digit hex is ever
# persisted or handed to the prompt builder.
_HEX_COLOUR_INPUT = re.compile(r"^#[0-9a-fA-F]{6}$")

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
_WRONG_TYPE_COLOUR = "Choose a colour."
_WRONG_TYPE_COLOUR_LIST = "Your own colours must be a list of colours."
_BAD_HEX = "That is not a valid colour."
_DUPLICATE_COLOUR = "You have already added that colour."
_UNKNOWN_CUSTOM_COLOUR = "That colour is no longer in your own colours."
_CUSTOM_NOT_ALLOWED = "This question only takes one of the shown colours."

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


def _normalise_colour_list(value: object) -> tuple[list[str] | None, str | None]:
    """Validate a custom palette: unique six-digit hex, normalised to lower case.

    Bounded BEFORE any per-item work. Unlike a multi_choice — whose items are
    checked against a small declared-option set, so a hostile list fails on its
    first unknown value — every syntactically valid hex passes, which would let
    a caller choose how much work this loop does. The schema validator caps any
    colour_list's declared ``max_items`` at ``MAX_CUSTOM_COLOURS_LIMIT``, so
    refusing anything longer here costs nothing legitimate and keeps the cost
    of rejecting an oversized array constant. Dedup uses a set for the same
    reason; ``ordered`` preserves insertion order for the persisted answer.
    """
    if not isinstance(value, list):
        return None, _WRONG_TYPE_COLOUR_LIST
    if len(value) > MAX_CUSTOM_COLOURS_LIMIT:
        # Reported as a count violation, matching the per-question max_items
        # message the caller would otherwise have produced.
        return None, _TOO_MANY_ITEMS.format(count=MAX_CUSTOM_COLOURS_LIMIT)
    ordered: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not _HEX_COLOUR_INPUT.fullmatch(item):
            return None, _BAD_HEX
        lowered = item.lower()
        if lowered in seen:
            return None, _DUPLICATE_COLOUR
        seen.add(lowered)
        ordered.append(lowered)
    return ordered, None


def _custom_palette(index: dict[str, dict], answers: dict) -> frozenset[str]:
    """The design's own colours, resolved BEFORE the main structural pass.

    A ``colour_choice`` answer may be a custom hex, and whether that hex is
    legitimate depends on the sibling ``colour_list`` answer — which dict
    iteration order gives no guarantee of reaching first. Resolving it up
    front makes validation order-independent. A malformed palette yields an
    empty set here and is reported on its own question by the main pass, so a
    custom colour_choice referencing it fails too rather than being silently
    accepted.
    """
    for question_id, question in index.items():
        if question.get("type") != "colour_list":
            continue
        normalised, message = _normalise_colour_list(answers.get(question_id))
        return frozenset() if message is not None or normalised is None else frozenset(normalised)
    return frozenset()


def _structural_value(
    question: dict, value: object, custom_palette: frozenset[str]
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

    if question_type == "colour_choice":
        # Either a declared swatch or, when the question allows it, one of the
        # design's own colours. The selected-set is always None: a colour never
        # participates in rule evaluation (see rules._RULE_REFERENCEABLE_TYPES),
        # so a custom hex can never leak into a condition or a restriction.
        if not isinstance(value, str):
            return None, _WRONG_TYPE_COLOUR, None
        if value in declared:
            return value, None, None
        if not _HEX_COLOUR_INPUT.fullmatch(value):
            return None, _UNKNOWN_OPTION, None
        if not question.get("constraints", {}).get("allow_custom", False):
            return None, _CUSTOM_NOT_ALLOWED, None
        lowered = value.lower()
        if lowered not in custom_palette:
            # The bride removed this colour from her palette (or never added
            # it). Rejecting keeps the answer and the palette consistent.
            return None, _UNKNOWN_CUSTOM_COLOUR, None
        return lowered, None, None

    if question_type == "colour_list":
        normalised, message = _normalise_colour_list(value)
        if message is not None:
            return None, message, None
        return normalised, None, None

    # Unreachable for a format-valid schema.
    return None, _WRONG_TYPE_TEXT, None


def _is_answered(question: dict, normalised: object) -> bool:
    if question.get("type") in ("multi_choice", "colour_list"):
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
    # The design's own palette, resolved first so a custom colour_choice can be
    # checked against it regardless of key order.
    custom_palette = _custom_palette(index, answers)
    # Structural pass: type + declared-option membership only, so we can build
    # the selected-values view that rule evaluation needs.
    structural: dict[str, object] = {}
    selected: dict[str, set[str]] = {}
    for key, value in answers.items():
        question = index.get(key) if isinstance(key, str) else None
        if question is None:
            errors[str(key)] = [_UNKNOWN_QUESTION]
            continue
        structural_value, message, selected_set = _structural_value(question, value, custom_palette)
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
        elif question_type == "colour_list":
            max_items = constraints.get("max_items")
            if (
                isinstance(max_items, int)
                and not isinstance(max_items, bool)
                and len(structural_value) > max_items
            ):
                errors[key] = [_TOO_MANY_ITEMS.format(count=max_items)]
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
