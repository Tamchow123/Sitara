"""Pure evaluation of the allowlisted questionnaire compatibility rules.

This is the single Python definition of the rule SEMANTICS described in ADR
0005 and Phase 7 §6; the frontend mirrors exactly these semantics from the
same schema data (``apps/web/src/features/questionnaire/rules.ts``). Neither
language hard-codes any individual fixture rule — both interpret the
allowlisted ``when``/``then`` vocabulary generically.

Given a schema (already format-validated on activation and on serve) and the
user's currently-selected choice values, these helpers derive: which
questions are visible, which are required, and — for choice questions —
which option values remain allowed. Rule conditions only ever reference
choice questions (enforced by the schema validator), so a controlling
answer is always a set of selected option values.

Semantics (Phase 7 §6):

- A condition whose question has no current answer evaluates ``False``.
- A scalar (single-choice) answer is treated as one selected value; a
  multi-choice answer as a set of selected values.
- ``equals``  → the selected values exactly equal the condition values.
- ``in``      → at least one selected value occurs in the condition values.
- ``not_in``  → an answer exists AND none of its selected values occurs in
  the condition values.
- Questions targeted by at least one ``show`` rule are hidden by default;
  all others are visible by default. A matching ``show`` reveals a target;
  a matching ``hide`` hides it; ``hide`` wins when both match.
- Base ``required`` applies only while a question is visible; a matching
  ``require`` rule makes a visible question required.
- Matching ``restrict_options`` rules intersect their allowed value sets;
  with no matching restriction every declared option remains allowed.
"""

from __future__ import annotations


def questions_by_id(schema: dict) -> dict[str, dict]:
    """Index every question in the schema by its stable id."""
    index: dict[str, dict] = {}
    for step in schema.get("steps", []):
        for question in step.get("questions", []):
            index[question["id"]] = question
    return index


def declared_option_values(question: dict) -> list[str]:
    """Declared option values for a choice question, in schema order."""
    return [option["value"] for option in question.get("options", [])]


def _condition_met(when: dict, selected: dict[str, set[str]]) -> bool:
    question_id = when["question_id"]
    chosen = selected.get(question_id)
    # No current answer for the controlling question → the condition is false.
    if not chosen:
        return False
    values = set(when["values"])
    operator = when["operator"]
    if operator == "equals":
        return chosen == values
    if operator == "in":
        return bool(chosen & values)
    if operator == "not_in":
        # An answer exists (chosen is truthy) and none of its values match.
        return not (chosen & values)
    # Unknown operators cannot occur in a format-validated schema; treat as
    # unmet rather than raising, keeping evaluation total.
    return False


def _matching_targets(schema: dict, selected: dict[str, set[str]], action: str) -> set[str]:
    return {
        rule["then"]["question_id"]
        for rule in schema.get("rules", [])
        if rule["then"]["action"] == action and _condition_met(rule["when"], selected)
    }


def visible_questions(schema: dict, selected: dict[str, set[str]]) -> dict[str, bool]:
    """Map every question id to whether it is currently visible."""
    index = questions_by_id(schema)
    hidden_by_default = {
        rule["then"]["question_id"]
        for rule in schema.get("rules", [])
        if rule["then"]["action"] == "show"
    }
    shown = _matching_targets(schema, selected, "show")
    hidden = _matching_targets(schema, selected, "hide")
    visibility: dict[str, bool] = {}
    for question_id in index:
        visible = question_id not in hidden_by_default
        if question_id in shown:
            visible = True
        # Hide wins over a conflicting show.
        if question_id in hidden:
            visible = False
        visibility[question_id] = visible
    return visibility


def required_questions(
    schema: dict, selected: dict[str, set[str]], visibility: dict[str, bool]
) -> dict[str, bool]:
    """Map every question id to whether it is currently required.

    A question is required only while visible, either by its base
    ``required`` flag or a matching ``require`` rule."""
    index = questions_by_id(schema)
    required_targets = _matching_targets(schema, selected, "require")
    result: dict[str, bool] = {}
    for question_id, question in index.items():
        base = bool(question.get("required", False))
        result[question_id] = (base or question_id in required_targets) and visibility[question_id]
    return result


def allowed_options(schema: dict, selected: dict[str, set[str]]) -> dict[str, set[str]]:
    """Map each choice question id to its currently-allowed option values.

    Every matching ``restrict_options`` rule intersects its value set into
    the allowed set; with no matching restriction all declared options
    remain allowed. An empty result means the current answers make the
    question unsatisfiable — the caller treats that as a validation failure.
    """
    index = questions_by_id(schema)
    allowed: dict[str, set[str]] = {}
    for question_id, question in index.items():
        if question.get("type") == "text":
            continue
        declared = set(declared_option_values(question))
        for rule in schema.get("rules", []):
            then = rule["then"]
            if (
                then["action"] == "restrict_options"
                and then["question_id"] == question_id
                and _condition_met(rule["when"], selected)
            ):
                declared &= set(then.get("values", []))
        allowed[question_id] = declared
    return allowed
