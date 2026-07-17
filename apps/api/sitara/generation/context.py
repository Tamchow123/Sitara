"""Deterministic, trusted generation context from a complete Design (Phase 8).

Everything Anthropic sees is built here, from validated data only. Before any
context is assembled every pre-spend gate is checked (a linked answerable
questionnaire, complete authoritative validation, still-eligible inspirations,
and no existing initial DesignVersion), so an incomplete or unsafe design is
rejected BEFORE a provider is ever selected.

What is sent: the canonical machine-value ``source_selections`` echo, and —
for currently VISIBLE, validated, non-text answers — the question label, the
canonical machine value(s) and the resolved option label(s). Free-text answers
are treated as UNTRUSTED preference data (scanned, capped, JSON-encoded and
placed in a delimited section by the prompt builder).

What is NEVER sent: the full questionnaire schema, hidden answers, rights
records/evidence, catalogue storage keys, image bytes, selected-inspiration
metadata (deferred to Phase 13), user/session identifiers, email addresses,
timestamps or internal database metadata.
"""

from dataclasses import dataclass

from django.conf import settings

from sitara.designs.services import design_completion_errors
from sitara.questionnaire.answer_validation import normalise_text
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.rules import build_selected, questions_by_id, visible_questions

from .input_safety import scan_user_text

# The DesignSpec.source_selections fields map 1:1 onto questionnaire question
# ids of the same name. This is a fixed CONTRACT mapping, not conditional UI
# logic — the DesignSpec is the structured brief for exactly this questionnaire.
_SCALAR_SELECTION_FIELDS = (
    "garment_type",
    "ceremony",
    "regional_style",
    "silhouette",
    "embellishment_density",
    "dupatta_style",
    "saree_drape",
)
_LIST_SELECTION_FIELDS = (
    "colour_palette",
    "fabrics",
    "embellishment_styles",
    "coverage_preferences",
)


class DesignNotReady(Exception):
    """A pre-spend gate failed; no provider is selected and nothing is spent.

    ``code`` is a stable machine code and ``field_errors`` (when present)
    carries the completion errors. Messages are safe to surface and log; raw
    answers are never included."""

    def __init__(self, code: str, message: str, *, field_errors: dict | None = None):
        self.code = code
        self.message = message
        self.field_errors = field_errors
        super().__init__(message)


@dataclass(frozen=True)
class GenerationContext:
    source_selections: dict
    trusted_answers: list[dict]
    untrusted_texts: list[dict]


def _option_labels(schema: dict) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    for question in questions_by_id(schema).values():
        labels[question["id"]] = {
            option["value"]: option["label"] for option in question.get("options", [])
        }
    return labels


def _selected_values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def build_generation_context(design) -> GenerationContext:
    """Validate every pre-spend gate and build the trusted context.

    Raises :class:`DesignNotReady` (before any provider selection) or
    :class:`~sitara.generation.input_safety.UnsafeUserTextError` (unsafe free
    text)."""
    version = design.questionnaire_version
    if version is None:
        raise DesignNotReady("questionnaire_missing", "The design has no questionnaire version.")
    if version.status not in (
        QuestionnaireVersion.Status.ACTIVE,
        QuestionnaireVersion.Status.RETIRED,
    ):
        raise DesignNotReady(
            "questionnaire_not_answerable",
            "The linked questionnaire version is not answerable.",
        )
    # One initial DesignVersion per design in Phase 8 — never regenerate.
    if design.versions.exists():
        raise DesignNotReady("already_generated", "This design already has a generated version.")
    errors = design_completion_errors(design)
    if errors:
        raise DesignNotReady("incomplete", "The design is not complete.", field_errors=errors)

    schema = version.schema
    answers = design.answers or {}
    index = questions_by_id(schema)
    visibility = visible_questions(schema, build_selected(schema, answers))
    option_labels = _option_labels(schema)

    source_selections = _build_source_selections(answers, visibility)

    trusted_answers: list[dict] = []
    untrusted_texts: list[dict] = []
    total_untrusted_chars = 0
    for step in schema.get("steps", []):
        for question in step.get("questions", []):
            question_id = question["id"]
            if not visibility.get(question_id):
                continue  # hidden answers are never sent
            value = answers.get(question_id)
            if question["type"] == "text":
                text = normalise_text(value) if isinstance(value, str) else ""
                if not text:
                    continue
                total_untrusted_chars += len(text)
                if total_untrusted_chars > settings.DESIGN_SPEC_MAX_INPUT_CHARS:
                    raise DesignNotReady(
                        "input_too_large", "The free-text input is too large to process."
                    )
                # Reject unsafe content BEFORE any provider/client is selected.
                scan_user_text(text)
                untrusted_texts.append(
                    {
                        "question_id": question_id,
                        "question_label": index[question_id]["label"],
                        "value": text,
                    }
                )
                continue
            values = _selected_values(value)
            if not values:
                continue
            trusted_answers.append(
                {
                    "question_id": question_id,
                    "question_label": index[question_id]["label"],
                    "values": [
                        {
                            "machine_value": machine_value,
                            "option_label": option_labels.get(question_id, {}).get(
                                machine_value, machine_value
                            ),
                        }
                        for machine_value in values
                    ],
                }
            )

    return GenerationContext(
        source_selections=source_selections,
        trusted_answers=trusted_answers,
        untrusted_texts=untrusted_texts,
    )


def _build_source_selections(answers: dict, visibility: dict) -> dict:
    """The canonical machine-value echo — only currently-visible answers, with
    optional questions null/empty as appropriate."""
    selections: dict = {}
    for field in _SCALAR_SELECTION_FIELDS:
        value = answers.get(field)
        selections[field] = value if (visibility.get(field) and isinstance(value, str)) else None
    for field in _LIST_SELECTION_FIELDS:
        value = answers.get(field)
        if visibility.get(field) and isinstance(value, list):
            selections[field] = [item for item in value if isinstance(item, str)]
        else:
            selections[field] = []
    return selections
