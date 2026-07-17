"""Shared helpers for questionnaire tests."""

import copy

from sitara.questionnaire.models import QuestionnaireVersion

QUESTIONNAIRE_ACTIVE_URL = "/api/v1/questionnaire/active/"

# A deliberately small but COMPLETE schema: every top-level key, one choice
# question, one capped text question, and one rule — enough to exercise the
# whole validator surface when mutated.
_VALID_SCHEMA = {
    "schema_version": 1,
    "key": "test_questionnaire",
    "title": "Test questionnaire",
    "steps": [
        {
            "id": "step_one",
            "title": "Step one",
            "questions": [
                {
                    "id": "garment_type",
                    "type": "single_choice",
                    "label": "Which garment?",
                    "required": True,
                    "options": [
                        {"value": "lehenga", "label": "Lehenga"},
                        {"value": "saree", "label": "Saree"},
                    ],
                },
                {
                    "id": "embellishments",
                    "type": "multi_choice",
                    "label": "Which embellishments?",
                    "required": False,
                    "options": [
                        {"value": "zardozi", "label": "Zardozi"},
                        {"value": "sequins", "label": "Sequins"},
                        {"value": "none", "label": "No embellishment"},
                    ],
                    "constraints": {
                        "min_items": 0,
                        "max_items": 2,
                        "exclusive_values": ["none"],
                    },
                },
                {
                    "id": "notes",
                    "type": "text",
                    "label": "Anything else?",
                    "required": False,
                    "constraints": {"min_length": 0, "max_length": 500},
                },
            ],
        }
    ],
    "rules": [
        {
            "id": "saree_restricts_embellishments",
            "when": {
                "question_id": "garment_type",
                "operator": "equals",
                "values": ["saree"],
            },
            "then": {
                "action": "restrict_options",
                "question_id": "embellishments",
                "values": ["zardozi", "none"],
            },
        }
    ],
}


def valid_schema() -> dict:
    """A fresh deep copy of the small valid schema, safe to mutate."""
    return copy.deepcopy(_VALID_SCHEMA)


def make_version(*, version: int = 1, status: str = "draft", schema: dict | None = None, **extra):
    return QuestionnaireVersion.objects.create(
        version=version,
        status=status,
        schema=valid_schema() if schema is None else schema,
        **extra,
    )
