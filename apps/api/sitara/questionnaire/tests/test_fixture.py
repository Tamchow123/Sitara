"""Fixture-integrity and cultural-coverage tests for questionnaire_v1.

Django fixture loading bypasses normal model validation, so the seed data
is validated explicitly here: the schema passes the full validator, every
rule reference resolves, the required garments and ceremonies are covered,
gharara and sharara stay structurally distinct, free text is capped, and
no designer names, external URLs or provider configuration appear.
"""

import json
from pathlib import Path

import pytest
from django.core.management import call_command

from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.schema_validation import validate_questionnaire_schema

pytestmark = pytest.mark.django_db

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "questionnaire_v1.json"

REQUIRED_GARMENTS = {"lehenga", "saree", "gharara", "sharara", "anarkali", "shalwar_kameez"}
REQUIRED_CEREMONIES = {"nikah", "mehndi", "baraat", "walima", "pheras", "reception"}

# Small project denylist: designer and fashion-house names must never
# appear in the questionnaire (matching the input-safety direction of the
# proposal). Lower-case substring matching against the whole fixture.
DESIGNER_DENYLIST = (
    "sabyasachi",
    "manish malhotra",
    "tarun tahiliani",
    "anita dongre",
    "bunto kazmi",
    "nomi ansari",
    "faraz manan",
    "deepak perwani",
    "sana safinaz",
    "elan",
    "hsy",
)


@pytest.fixture
def loaded_version() -> QuestionnaireVersion:
    call_command("loaddata", "questionnaire_v1", verbosity=0)
    return QuestionnaireVersion.objects.get(version=1)


def _questions(schema: dict) -> dict[str, dict]:
    return {question["id"]: question for step in schema["steps"] for question in step["questions"]}


def _options(question: dict) -> dict[str, dict]:
    return {option["value"]: option for option in question.get("options", [])}


class TestFixtureLoads:
    def test_fixture_loads_as_the_active_version_one(self, loaded_version):
        assert loaded_version.status == QuestionnaireVersion.Status.ACTIVE
        assert str(loaded_version.pk) == "3f7a2b9e-4c1d-4e8a-9b6f-1a2b3c4d5e6f"
        assert QuestionnaireVersion.objects.count() == 1

    def test_fixture_schema_passes_the_full_validator(self, loaded_version):
        validate_questionnaire_schema(loaded_version.schema)

    def test_every_rule_reference_resolves(self, loaded_version):
        questions = _questions(loaded_version.schema)
        for rule in loaded_version.schema["rules"]:
            condition = questions[rule["when"]["question_id"]]
            condition_values = set(_options(condition))
            assert set(rule["when"]["values"]) <= condition_values
            target = questions[rule["then"]["question_id"]]
            if rule["then"]["action"] == "restrict_options":
                assert set(rule["then"]["values"]) <= set(_options(target))


class TestCulturalCoverage:
    def test_required_garments_are_distinct_machine_values(self, loaded_version):
        garment_values = set(_options(_questions(loaded_version.schema)["garment_type"]))
        assert REQUIRED_GARMENTS <= garment_values

    def test_required_ceremonies_are_covered(self, loaded_version):
        ceremony_values = set(_options(_questions(loaded_version.schema)["ceremony"]))
        assert REQUIRED_CEREMONIES <= ceremony_values

    def test_gharara_and_sharara_remain_structurally_distinct(self, loaded_version):
        schema = loaded_version.schema
        garments = _options(_questions(schema)["garment_type"])
        gharara = garments["gharara"]["description"].lower()
        sharara = garments["sharara"]["description"].lower()
        assert gharara != sharara
        # Gharara: fitted through the upper leg/knee before the lower flare.
        assert "knee" in gharara
        # Sharara: flares broadly from the waist or upper leg.
        assert "waist" in sharara

        restrictions = {
            rule["then"]["question_id"]: rule["then"]["values"]
            for rule in schema["rules"]
            if rule["then"]["action"] == "restrict_options"
            and rule["when"]["values"] == ["gharara"]
        }
        sharara_restrictions = {
            rule["then"]["question_id"]: rule["then"]["values"]
            for rule in schema["rules"]
            if rule["then"]["action"] == "restrict_options"
            and rule["when"]["values"] == ["sharara"]
        }
        # Each garment restricts the silhouette to ITS OWN construction.
        assert restrictions["silhouette"] == ["gharara_construction"]
        assert sharara_restrictions["silhouette"] == ["sharara_construction"]

    def test_saree_styling_is_distinct_from_lehenga(self, loaded_version):
        schema = loaded_version.schema
        rules_by_id = {rule["id"]: rule for rule in schema["rules"]}
        saree_silhouettes = set(rules_by_id["saree_silhouettes"]["then"]["values"])
        lehenga_silhouettes = set(rules_by_id["lehenga_silhouettes"]["then"]["values"])
        assert saree_silhouettes.isdisjoint(lehenga_silhouettes)
        # Sarees get drape styling; other garments get dupatta styling.
        assert rules_by_id["saree_shows_saree_drape"]["then"]["action"] == "show"
        assert rules_by_id["saree_hides_dupatta_style"]["then"]["action"] == "hide"
        assert rules_by_id["non_saree_hides_saree_drape"]["when"]["operator"] == "not_in"

    def test_no_regional_direction_is_offered_and_optional(self, loaded_version):
        regional = _questions(loaded_version.schema)["regional_style"]
        assert regional["required"] is False
        assert "no_specific_direction" in _options(regional)

    def test_modest_full_sleeve_styling_is_represented(self, loaded_version):
        coverage = _options(_questions(loaded_version.schema)["coverage_preferences"])
        assert "full_sleeves" in coverage
        assert "high_neckline" in coverage
        assert "head_drape_preferred" in coverage

    def test_none_is_exclusive_in_embellishments(self, loaded_version):
        question = _questions(loaded_version.schema)["embellishment_styles"]
        assert "none" in _options(question)
        assert question["constraints"]["exclusive_values"] == ["none"]

    def test_free_text_is_capped_at_500(self, loaded_version):
        notes = _questions(loaded_version.schema)["final_notes"]
        assert notes["type"] == "text"
        assert notes["constraints"]["max_length"] == 500

    def test_multi_selections_have_explicit_caps(self, loaded_version):
        questions = _questions(loaded_version.schema)
        assert questions["colour_palette"]["constraints"]["max_items"] == 4
        assert questions["embellishment_styles"]["constraints"]["max_items"] == 5


class TestFixtureHygiene:
    def test_no_designer_or_brand_names(self):
        text = FIXTURE_PATH.read_text(encoding="utf-8").lower()
        for name in DESIGNER_DENYLIST:
            assert name not in text

    def test_no_external_urls_or_provider_configuration(self):
        text = FIXTURE_PATH.read_text(encoding="utf-8").lower()
        for marker in (
            "http://",
            "https://",
            "replicate",
            "anthropic",
            "flux",
            "api_key",
            "secret",
        ):
            assert marker not in text

    def test_fixture_is_deterministic_json(self):
        # Parses cleanly and contains exactly one object with a fixed pk —
        # re-loading it can never mint a second row.
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        assert len(payload) == 1
        assert payload[0]["pk"] == "3f7a2b9e-4c1d-4e8a-9b6f-1a2b3c4d5e6f"
        assert payload[0]["fields"]["version"] == 1
