"""Fixture-integrity, taxonomy and lifecycle tests for questionnaire_v3.

Phase 16B introduces questionnaire v3 as a NEW draft version (v1 stays the
published active seed, v2 stays an untouched draft — ADR 0005 immutability).
v3 adds: satin, the Anand Karaj ceremony, a dedicated single-choice neckline
question (migrating the old ``high_neckline`` coverage value out), an expanded
grouped colour vocabulary, option presentation metadata (``visual_key`` /
``group``), and coverage/neckline/dupatta consistency rules.

Answer-level behaviour is validated directly against the fixture schema (no DB
needed); lifecycle behaviour uses the activation service like the v1/v2 tests.
"""

import json
from pathlib import Path

import pytest
from django.core.management import call_command

from sitara.questionnaire.answer_validation import (
    QuestionnaireAnswerError,
    validate_questionnaire_answers,
)
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.schema_validation import (
    MACHINE_ID_PATTERN,
    validate_questionnaire_schema,
)
from sitara.questionnaire.services import activate_questionnaire_version

from .test_fixture_versions import V1_SCHEMA_FINGERPRINT, _fingerprint

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_V3_PATH = _FIXTURES / "questionnaire_v3.json"
_V3_PK = "c3d4e5f6-a7b8-4c9d-8e0f-2a3b4c5d6e7f"

# Every ceremony that existed before this phase — Anand Karaj must be a NEW,
# distinct value, never a rename or alias of any of these.
_PRE_EXISTING_CEREMONIES = {"nikah", "mehndi", "baraat", "walima", "pheras", "reception"}

_EXPECTED_NECKLINES = {
    "classic_crew",
    "curved_scoop",
    "v_neck",
    "deep_v_neck",
    "boat_neck",
    "square_neck",
    "sweetheart_neck",
    "high_neck",
    "band_collar",
}

_NEW_COLOURS = {
    "ruby",
    "burgundy",
    "coral",
    "rose",
    "dusty_rose",
    "mauve",
    "lavender",
    "lilac",
    "plum",
    "sage",
    "mint",
    "olive",
    "forest_green",
    "turquoise",
    "powder_blue",
    "royal_blue",
    "bronze",
    "copper",
    "taupe",
}


def _v3_schema() -> dict:
    return json.loads(_V3_PATH.read_text(encoding="utf-8"))[0]["fields"]["schema"]


def _questions(schema: dict) -> dict[str, dict]:
    return {q["id"]: q for step in schema["steps"] for q in step["questions"]}


def _options(question: dict) -> dict[str, dict]:
    return {o["value"]: o for o in question.get("options", [])}


def _base_answers(**overrides) -> dict:
    answers = {
        "garment_type": "lehenga",
        "ceremony": "nikah",
        "silhouette": "flared_lehenga",
        "colour_palette": ["ruby", "gold"],
        "embellishment_styles": ["zardozi"],
    }
    answers.update(overrides)
    return answers


class TestV3IsADistinctDraft:
    def test_fixture_is_a_single_deterministic_v3_draft(self):
        payload = json.loads(_V3_PATH.read_text(encoding="utf-8"))
        assert len(payload) == 1
        assert payload[0]["pk"] == _V3_PK
        assert payload[0]["fields"]["version"] == 3
        assert payload[0]["fields"]["status"] == "draft"
        assert payload[0]["fields"]["activated_at"] is None

    def test_schema_passes_the_full_validator(self):
        validate_questionnaire_schema(_v3_schema())

    def test_pk_is_unique_to_v3(self):
        assert _V3_PK != "3f7a2b9e-4c1d-4e8a-9b6f-1a2b3c4d5e6f"  # v1
        assert _V3_PK != "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"  # v2


class TestTaxonomyAdditions:
    def test_satin_is_added_and_distinct_from_silk(self):
        fabrics = _options(_questions(_v3_schema())["fabrics"])
        assert "satin" in fabrics
        assert "silk" in fabrics
        assert "raw_silk" in fabrics
        # Not synonyms: distinct machine values AND distinct labels.
        assert fabrics["satin"]["label"] != fabrics["silk"]["label"]
        assert _questions(_v3_schema())["fabrics"]["constraints"]["max_items"] == 3

    def test_anand_karaj_is_a_new_distinct_ceremony(self):
        ceremonies = _options(_questions(_v3_schema())["ceremony"])
        assert "anand_karaj" in ceremonies
        # Every pre-existing ceremony survives; anand_karaj is genuinely new.
        assert _PRE_EXISTING_CEREMONIES <= set(ceremonies)
        assert "anand_karaj" not in _PRE_EXISTING_CEREMONIES
        label = ceremonies["anand_karaj"]["label"].lower()
        assert label == "anand karaj"
        assert "sikh" in ceremonies["anand_karaj"]["description"].lower()

    def test_dedicated_neckline_question_is_optional_single_choice(self):
        neckline = _questions(_v3_schema())["neckline_style"]
        assert neckline["type"] == "single_choice"
        assert neckline["required"] is False
        assert set(_options(neckline)) == _EXPECTED_NECKLINES
        for option in neckline["options"]:
            assert option["group"] == "necklines"
            assert option["visual_key"].startswith("neckline_")

    def test_high_neckline_is_migrated_out_of_coverage(self):
        coverage = _options(_questions(_v3_schema())["coverage_preferences"])
        # The old competing coverage value is gone — neckline is authoritative now.
        assert "high_neckline" not in coverage
        # Other modest coverage options remain.
        assert {"full_sleeves", "full_back", "full_midriff", "head_drape_preferred"} <= set(
            coverage
        )

    def test_expanded_grouped_colours(self):
        colours_q = _questions(_v3_schema())["colour_palette"]
        colours = _options(colours_q)
        # New colours added, current ones preserved, still capped at four leads.
        assert _NEW_COLOURS <= set(colours)
        assert {"ivory", "red", "gold", "emerald", "navy", "multicolour"} <= set(colours)
        assert colours_q["constraints"]["max_items"] == 4
        assert len(colours) <= 50
        allowed_groups = {
            "neutrals",
            "reds",
            "pinks",
            "yellows_metallics",
            "greens",
            "blues_teals",
            "purples",
        }
        for option in colours_q["options"]:
            assert option["group"] in allowed_groups
            assert option["visual_key"] == f"colour_{option['value']}"

    def test_all_presentation_metadata_is_bounded_machine_ids(self):
        for question in _questions(_v3_schema()).values():
            for option in question.get("options", []):
                for field in ("visual_key", "group"):
                    if field in option:
                        assert MACHINE_ID_PATTERN.fullmatch(option[field])


class TestConsistencyRules:
    """v3 answer validation is server-authoritative for the new consistency
    constraints; these run directly against the fixture schema."""

    def test_satin_is_accepted(self):
        result = validate_questionnaire_answers(
            _v3_schema(), _base_answers(fabrics=["satin"]), require_complete=False
        )
        assert result["fabrics"] == ["satin"]

    def test_unknown_fabric_is_rejected(self):
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            validate_questionnaire_answers(
                _v3_schema(), _base_answers(fabrics=["polyester"]), require_complete=False
            )
        assert "fabrics" in excinfo.value.errors

    def test_anand_karaj_is_accepted(self):
        result = validate_questionnaire_answers(
            _v3_schema(), _base_answers(ceremony="anand_karaj"), require_complete=False
        )
        assert result["ceremony"] == "anand_karaj"

    def test_neckline_is_optional(self):
        # Absent neckline is valid (no preference); the required check never fires.
        result = validate_questionnaire_answers(
            _v3_schema(), _base_answers(), require_complete=True
        )
        assert "neckline_style" not in result

    def test_neckline_rejects_a_list(self):
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            validate_questionnaire_answers(
                _v3_schema(), _base_answers(neckline_style=["v_neck"]), require_complete=False
            )
        assert "neckline_style" in excinfo.value.errors

    def test_high_neckline_is_no_longer_a_coverage_value(self):
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            validate_questionnaire_answers(
                _v3_schema(),
                _base_answers(coverage_preferences=["high_neckline"]),
                require_complete=False,
            )
        assert "coverage_preferences" in excinfo.value.errors

    def test_covered_head_requires_a_head_compatible_dupatta(self):
        for dupatta in ("head_drape", "double_dupatta"):
            result = validate_questionnaire_answers(
                _v3_schema(),
                _base_answers(coverage_preferences=["head_drape_preferred"], dupatta_style=dupatta),
                require_complete=False,
            )
            assert result["dupatta_style"] == dupatta

    @pytest.mark.parametrize(
        "dupatta", ["one_shoulder", "both_shoulders", "front_drape", "cape_drape", "arm_drape"]
    )
    def test_covered_head_rejects_a_non_head_dupatta(self, dupatta):
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            validate_questionnaire_answers(
                _v3_schema(),
                _base_answers(coverage_preferences=["head_drape_preferred"], dupatta_style=dupatta),
                require_complete=False,
            )
        assert "dupatta_style" in excinfo.value.errors

    def test_full_midriff_rejects_a_plunging_neckline(self):
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            validate_questionnaire_answers(
                _v3_schema(),
                _base_answers(coverage_preferences=["full_midriff"], neckline_style="deep_v_neck"),
                require_complete=False,
            )
        assert "neckline_style" in excinfo.value.errors

    @pytest.mark.parametrize("neckline", ["v_neck", "sweetheart_neck", "high_neck", "boat_neck"])
    def test_full_midriff_allows_non_plunging_necklines(self, neckline):
        result = validate_questionnaire_answers(
            _v3_schema(),
            _base_answers(coverage_preferences=["full_midriff"], neckline_style=neckline),
            require_complete=False,
        )
        assert result["neckline_style"] == neckline

    @pytest.mark.parametrize("neckline", ["deep_v_neck", "sweetheart_neck", "v_neck"])
    def test_covered_head_does_not_restrict_the_neckline(self, neckline):
        # Head covering and neckline are INDEPENDENT decisions (a covered head
        # with a sweetheart or V choli is a canonical bridal look). The two
        # enforced consistency rules are head-covering -> dupatta and
        # full_midriff -> neckline; there is deliberately no head-covering ->
        # neckline restriction, so any neckline coexists with a covered head.
        result = validate_questionnaire_answers(
            _v3_schema(),
            _base_answers(
                coverage_preferences=["head_drape_preferred"],
                dupatta_style="head_drape",
                neckline_style=neckline,
            ),
            require_complete=False,
        )
        assert result["neckline_style"] == neckline


class TestHistoricalCoverageAnswers:
    """Historical v1/v2 answers using the old ``high_neckline`` coverage value
    stay valid against their OWN schema — v3 does not rewrite history."""

    @pytest.mark.django_db
    def test_v1_still_accepts_the_old_high_neckline_coverage(self):
        call_command("loaddata", "questionnaire_v1", verbosity=0)
        v1_schema = QuestionnaireVersion.objects.get(version=1).schema
        result = validate_questionnaire_answers(
            v1_schema,
            {
                "garment_type": "lehenga",
                "ceremony": "nikah",
                "silhouette": "flared_lehenga",
                "colour_palette": ["red"],
                "embellishment_styles": ["zardozi"],
                "coverage_preferences": ["high_neckline"],
            },
            require_complete=False,
        )
        assert result["coverage_preferences"] == ["high_neckline"]


class TestV3Lifecycle:
    pytestmark = pytest.mark.django_db

    @pytest.mark.django_db
    def test_loading_all_versions_keeps_v1_active_and_v3_draft(self):
        call_command(
            "loaddata", "questionnaire_v1", "questionnaire_v2", "questionnaire_v3", verbosity=0
        )
        assert QuestionnaireVersion.objects.count() == 3
        assert QuestionnaireVersion.objects.get(version=1).status == "active"
        assert QuestionnaireVersion.objects.get(version=3).status == "draft"

    @pytest.mark.django_db
    def test_activating_v3_atomically_retires_v1(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v3", verbosity=0)
        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=3))
        assert QuestionnaireVersion.objects.get(version=1).status == "retired"
        assert QuestionnaireVersion.objects.get(version=3).status == "active"
        assert (
            QuestionnaireVersion.objects.filter(status=QuestionnaireVersion.Status.ACTIVE).count()
            == 1
        )

    @pytest.mark.django_db
    def test_v1_fingerprint_survives_v3_activation(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v3", verbosity=0)
        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=3))
        assert (
            _fingerprint(QuestionnaireVersion.objects.get(version=1).schema)
            == V1_SCHEMA_FINGERPRINT
        )


class TestV3Hygiene:
    def test_no_external_urls_or_provider_configuration(self):
        text = _V3_PATH.read_text(encoding="utf-8").lower()
        markers = ("http://", "https://", "replicate", "anthropic", "flux", "api_key", "secret")
        for marker in markers:
            assert marker not in text
