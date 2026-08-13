"""Shared fixture builders for the generation test suite.

Builds the objects a generation or refinement test needs before it can start: a
published ``QuestionnaireVersion`` (v1, v3 or v4) from the real committed
fixture, a complete ``Design`` answering it, and a refinable version-1
``DesignVersion`` for the design. Using the real schemas means every
``source_selections`` field exists exactly as production would supply it, rather
than as a hand-written approximation of one."""

import json
from pathlib import Path

from django.utils import timezone

from sitara.designs.models import Design, DesignSession, DesignVersion
from sitara.generation.design_spec import DESIGN_SPEC_SCHEMA_VERSION, SPEC_TEMPLATE_VERSION
from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION
from sitara.questionnaire.models import QuestionnaireVersion

_FIXTURES = Path(__file__).resolve().parents[2] / "questionnaire" / "fixtures"
_V1_FIXTURE = _FIXTURES / "questionnaire_v1.json"
_V3_FIXTURE = _FIXTURES / "questionnaire_v3.json"
_V4_FIXTURE = _FIXTURES / "questionnaire_v4.json"

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

# A complete answer set for the Phase 16B v3 questionnaire (satin, Anand Karaj,
# a dedicated neckline, an expanded colour). Targets DesignSpec schema v2.
COMPLETE_ANSWERS_V3 = {
    "garment_type": "lehenga",
    "ceremony": "anand_karaj",
    "regional_style": "punjabi",
    "silhouette": "flared_lehenga",
    "colour_palette": ["ruby", "gold"],
    "fabrics": ["satin", "organza"],
    "embellishment_styles": ["zardozi", "dabka"],
    "embellishment_density": "balanced",
    "coverage_preferences": ["full_sleeves", "full_midriff", "head_drape_preferred"],
    "neckline_style": "high_neck",
    "dupatta_style": "double_dupatta",
    "final_notes": "Please keep the overall look elegant and balanced.",
}


# A complete answer set for the Phase 16B v4 questionnaire (a colour per garment
# role including one bride-supplied hex, and a coverage answer per body area).
# Targets DesignSpec schema v3.
COMPLETE_ANSWERS_V4 = {
    "garment_type": "lehenga",
    "ceremony": "anand_karaj",
    "regional_style": "punjabi",
    "silhouette": "panelled_kali_lehenga",
    "fabric_colour": "deep_maroon",
    "embroidery_colour": "antique_gold",
    "dupatta_colour": "#c8b273",
    "custom_colours": ["#c8b273"],
    "fabrics": ["satin", "organza"],
    "embellishment_styles": ["zardozi", "dabka"],
    "embellishment_density": "balanced",
    "neckline_style": "high_neck",
    "sleeves": "full_sleeve",
    "back_coverage": "modest_back",
    "midriff": "covered_midriff",
    "head_covering": "dupatta_over_head",
    "dupatta_style": "double_dupatta",
    "final_notes": "Please keep the overall look elegant and balanced.",
}


def v1_schema() -> dict:
    with _V1_FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)[0]["fields"]["schema"]


def v3_schema() -> dict:
    with _V3_FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)[0]["fields"]["schema"]


def v4_schema() -> dict:
    with _V4_FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)[0]["fields"]["schema"]


def committed_questionnaire_schemas() -> dict[str, dict]:
    """Every questionnaire version committed to the repository, by file stem.

    Discovered from the fixtures directory rather than listed, because a test
    that lists its own coverage stops covering whatever the next author forgets
    to add to the list — and does so silently, staying green. That is the exact
    shape of defect this phase has now hit twice. Adding
    ``questionnaire_v6.json`` puts v6 into every caller of this function with no
    further wiring; forgetting to write a phrase for one of its options fails
    loudly, which is the whole point of the checks that read it."""
    schemas = {}
    for path in sorted(_FIXTURES.glob("questionnaire_v*.json")):
        with path.open(encoding="utf-8") as handle:
            schemas[path.stem.removeprefix("questionnaire_")] = json.load(handle)[0]["fields"][
                "schema"
            ]
    return schemas


def make_active_v1(version: int = 1, status: str = "active") -> QuestionnaireVersion:
    return QuestionnaireVersion.objects.create(version=version, status=status, schema=v1_schema())


def make_active_v3(version: int = 3, status: str = "active") -> QuestionnaireVersion:
    return QuestionnaireVersion.objects.create(version=version, status=status, schema=v3_schema())


def make_complete_v3_design(*, answers=None) -> Design:
    """A complete design on the v3 questionnaire (targets DesignSpec v2)."""
    session = DesignSession.objects.create()
    return Design.objects.create(
        design_session=session,
        questionnaire_version=make_active_v3(),
        answers=dict(COMPLETE_ANSWERS_V3 if answers is None else answers),
    )


def make_active_v4(version: int = 4, status: str = "active") -> QuestionnaireVersion:
    return QuestionnaireVersion.objects.create(version=version, status=status, schema=v4_schema())


def make_complete_v4_design(*, answers=None) -> Design:
    """A complete design on the v4 questionnaire (targets DesignSpec v3)."""
    session = DesignSession.objects.create()
    return Design.objects.create(
        design_session=session,
        questionnaire_version=make_active_v4(),
        answers=dict(COMPLETE_ANSWERS_V4 if answers is None else answers),
    )


def make_complete_design(*, questionnaire=None, answers=None) -> Design:
    questionnaire = questionnaire or make_active_v1()
    session = DesignSession.objects.create()
    return Design.objects.create(
        design_session=session,
        questionnaire_version=questionnaire,
        answers=dict(COMPLETE_ANSWERS if answers is None else answers),
    )


def make_source_version(design: Design, spec_payload: dict, **overrides) -> DesignVersion:
    """A complete, refinable version-1 DesignVersion for ``design``.

    Every field ``validate_source_version`` inspects is populated — the spec, its
    schema/template versions and complete permanent-image provenance — so a test
    that wants a *different* source shape overrides only the field it is about.
    Shared by every refinement test rather than restated per module."""
    fields = {
        "design": design,
        "version_number": 1,
        "design_spec": spec_payload,
        "design_spec_schema_version": DESIGN_SPEC_SCHEMA_VERSION,
        "design_spec_template_version": SPEC_TEMPLATE_VERSION,
        "design_spec_provider": "fixture",
        "design_spec_model": "fixture-model",
        "design_spec_generated_at": timezone.now(),
        "image_prompt": "A deterministic placeholder prompt.",
        "prompt_builder_version": PROMPT_BUILDER_VERSION,
        "image_storage_key": f"design-images/{design.id}/v1/original.webp",
        "image_sha256": "a" * 64,
        "image_size_bytes": 100_000,
        "image_width": 900,
        "image_height": 1200,
        "thumbnail_storage_key": f"design-images/{design.id}/v1/thumbnail.webp",
        "thumbnail_sha256": "b" * 64,
        "thumbnail_size_bytes": 5_000,
        "thumbnail_width": 200,
        "thumbnail_height": 260,
        "image_processor_version": "1.0.0",
        "image_ingested_at": timezone.now(),
    }
    fields.update(overrides)
    return DesignVersion.objects.create(**fields)
