"""Build a complete, generation-ready Design for the Part B tests.

Uses the real seeded v1 questionnaire schema so every source_selections field
exists, and answers it completely for a lehenga/nikah concept."""

import json
from pathlib import Path

from sitara.designs.models import Design, DesignSession
from sitara.questionnaire.models import QuestionnaireVersion

_V1_FIXTURE = (
    Path(__file__).resolve().parents[2] / "questionnaire" / "fixtures" / "questionnaire_v1.json"
)

COMPLETE_ANSWERS = {
    "garment_type": "lehenga",
    "ceremony": "nikah",
    "regional_style": "pakistani",
    "silhouette": "flared_lehenga",
    "colour_palette": ["ivory", "gold"],
    "fabrics": ["silk", "organza"],
    "embellishment_styles": ["zardozi", "dabka"],
    "embellishment_density": "balanced",
    "coverage_preferences": ["full_sleeves", "high_neckline"],
    "dupatta_style": "head_drape",
    "final_notes": "Please keep the overall look elegant and balanced.",
}


def v1_schema() -> dict:
    with _V1_FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)[0]["fields"]["schema"]


def make_active_v1(version: int = 1, status: str = "active") -> QuestionnaireVersion:
    return QuestionnaireVersion.objects.create(version=version, status=status, schema=v1_schema())


def make_complete_design(*, questionnaire=None, answers=None) -> Design:
    questionnaire = questionnaire or make_active_v1()
    session = DesignSession.objects.create()
    return Design.objects.create(
        design_session=session,
        questionnaire_version=questionnaire,
        answers=dict(COMPLETE_ANSWERS if answers is None else answers),
    )
