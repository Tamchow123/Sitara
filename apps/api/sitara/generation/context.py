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

Selected-inspiration metadata (Phase 13) is sent only as the curated,
versioned ``curated_inspiration_cues`` built by
:mod:`sitara.generation.inspiration_context` — garment type, visual
description and cultural context ONLY, re-validated and safety-scanned here,
strictly before any provider is selected.

What is NEVER sent: the full questionnaire schema, hidden answers, rights
records/evidence, catalogue storage keys, image bytes, inspiration asset
UUIDs/titles/attribution, user/session identifiers, email addresses,
timestamps or internal database metadata.
"""

from dataclasses import dataclass

from django.conf import settings
from pydantic import ValidationError

from sitara.designs.services import design_completion_errors
from sitara.questionnaire.answer_validation import normalise_text
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.rules import build_selected, questions_by_id, visible_questions

from .design_spec import SourceSelections, SourceSelectionsV2
from .input_safety import scan_user_text
from .inspiration_context import (
    InspirationAssetIneligible,
    InspirationContextSnapshot,
    InspirationMetadataUnavailable,
    build_inspiration_context_snapshot,
    provider_inspiration_cues,
)

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

# The dedicated canonical neckline (Phase 16B). It exists only in questionnaire
# versions that declare a ``neckline_style`` question; when present the design
# targets DesignSpec schema version 2 (which carries the field), otherwise the
# design targets version 1 exactly as before. This is the single, explicit
# capability check that chooses the DesignSpec version — never a generic
# framework (ADR 0009 / Phase 16B).
_NECKLINE_FIELD = "neckline_style"


def _questionnaire_declares_neckline(schema: dict) -> bool:
    return _NECKLINE_FIELD in questions_by_id(schema)


def _target_design_spec_version(schema: dict) -> int:
    return 2 if _questionnaire_declares_neckline(schema) else 1


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
    inspiration_context: InspirationContextSnapshot
    inspiration_cues: list[dict]
    # The DesignSpec structure this questionnaire targets: 1 (default) or 2
    # (Phase 16B, when the questionnaire declares a dedicated neckline). The
    # provider stages, demo engine and persistence read this to produce and
    # store the correct version.
    design_spec_schema_version: int = 1


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

    target_version = _target_design_spec_version(schema)
    include_neckline = target_version == 2
    source_selections = _build_source_selections(
        answers, visibility, include_neckline=include_neckline
    )
    # The DesignSpec contract must be satisfiable by this questionnaire BEFORE
    # any provider is selected or any client is constructed. A schema that does
    # not supply the required source fields (or supplies an unusable shape) is a
    # controlled DesignNotReady, never a Pydantic traceback — and neither the
    # Pydantic input nor questionnaire contents are surfaced. The version-2
    # contract additionally requires the dedicated neckline field.
    selections_model = SourceSelectionsV2 if include_neckline else SourceSelections
    try:
        selections_model.model_validate(source_selections)
    except ValidationError:
        raise DesignNotReady(
            "unsupported_questionnaire_contract",
            "This questionnaire does not provide a supported design contract.",
        ) from None

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

    # Selected-inspiration eligibility and metadata safety, re-validated
    # strictly before any provider is selected (design_completion_errors
    # above already checked coarse eligibility; this is the precise,
    # defence-in-depth recheck that also builds the versioned snapshot).
    try:
        inspiration_context = build_inspiration_context_snapshot(design)
    except InspirationAssetIneligible:
        raise DesignNotReady(
            "inspiration_unavailable",
            "A selected inspiration is no longer available.",
        ) from None
    except InspirationMetadataUnavailable:
        raise DesignNotReady(
            "inspiration_metadata_unavailable",
            "Selected inspiration metadata is unavailable.",
        ) from None

    return GenerationContext(
        source_selections=source_selections,
        trusted_answers=trusted_answers,
        untrusted_texts=untrusted_texts,
        inspiration_context=inspiration_context,
        inspiration_cues=provider_inspiration_cues(inspiration_context),
        design_spec_schema_version=target_version,
    )


def _build_source_selections(answers: dict, visibility: dict, *, include_neckline: bool) -> dict:
    """The canonical machine-value echo — only currently-visible answers, with
    optional questions null/empty as appropriate.

    ``include_neckline`` adds the dedicated ``neckline_style`` scalar (Phase
    16B / DesignSpec v2); it is null when unanswered, hidden or not a string."""
    selections: dict = {}
    for field in _SCALAR_SELECTION_FIELDS:
        value = answers.get(field)
        selections[field] = value if (visibility.get(field) and isinstance(value, str)) else None
    if include_neckline:
        value = answers.get(_NECKLINE_FIELD)
        selections[_NECKLINE_FIELD] = (
            value if (visibility.get(_NECKLINE_FIELD) and isinstance(value, str)) else None
        )
    for field in _LIST_SELECTION_FIELDS:
        value = answers.get(field)
        if visibility.get(field) and isinstance(value, list):
            selections[field] = [item for item in value if isinstance(item, str)]
        else:
            selections[field] = []
    return selections
