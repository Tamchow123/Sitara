"""Fixture-integrity, taxonomy and answer tests for questionnaire_v4.

v4 is the design-handoff revision. It is a NEW draft version: v1 stays the
published active seed and v2/v3 stay untouched drafts (ADR 0005 immutability),
so every answer already persisted against an earlier version keeps being read
through that version's own schema. Nothing here migrates or rewrites history.

Two shapes change relative to v3, and both are the reason this file exists:

1. The single ``colour_palette`` (multi_choice, max 4) is replaced by three
   per-role ``colour_choice`` questions — fabric, embroidery, dupatta — plus a
   ``custom_colours`` palette of user-added hex colours.
2. The single ``coverage_preferences`` (multi_choice, max 6), which conflated
   sleeves, back, midriff and head covering into one list, is replaced by four
   independent ``single_choice`` questions.

Answer-level behaviour is validated directly against the fixture schema (no DB
needed); lifecycle behaviour uses the activation service like the v1/v3 tests.
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
    QuestionnaireSchemaError,
    validate_questionnaire_schema,
)
from sitara.questionnaire.services import activate_questionnaire_version

from .test_fixture_versions import V1_SCHEMA_FINGERPRINT, _fingerprint

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_V4_PATH = _FIXTURES / "questionnaire_v4.json"
_V3_PATH = _FIXTURES / "questionnaire_v3.json"


def _schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))[0]["fields"]["schema"]


@pytest.fixture(scope="module")
def v4() -> dict:
    return _schema(_V4_PATH)


def _questions(schema: dict) -> dict[str, dict]:
    return {q["id"]: q for step in schema["steps"] for q in step["questions"]}


def _values(question: dict) -> set[str]:
    return {option["value"] for option in question["options"]}


def _answer(schema, answers, *, complete=False):
    return validate_questionnaire_answers(schema, answers, require_complete=complete)


def _rejects(schema, answers, question_id, *, complete=False):
    """Assert these answers are rejected, and specifically on `question_id`."""
    with pytest.raises(QuestionnaireAnswerError) as caught:
        _answer(schema, answers, complete=complete)
    assert question_id in caught.value.errors, caught.value.errors
    return caught.value.errors


# --------------------------------------------------------------------------
# Fixture integrity
# --------------------------------------------------------------------------
class TestV4FixtureIntegrity:
    def test_fixture_is_format_valid(self, v4):
        validate_questionnaire_schema(v4)

    def test_declares_version_four_as_a_draft(self):
        record = json.loads(_V4_PATH.read_text(encoding="utf-8"))[0]
        assert record["fields"]["version"] == 4
        assert record["fields"]["status"] == "draft"

    def test_every_identifier_is_a_stable_machine_id(self, v4):
        for step in v4["steps"]:
            assert MACHINE_ID_PATTERN.fullmatch(step["id"])
            for question in step["questions"]:
                assert MACHINE_ID_PATTERN.fullmatch(question["id"])
                for option in question.get("options", []):
                    assert MACHINE_ID_PATTERN.fullmatch(option["value"]), option["value"]

    def test_leaves_v3_completely_untouched(self):
        # v4 is additive. If generating it ever mutated v3 in place, answers
        # already persisted against v3 would silently change meaning.
        v3 = _schema(_V3_PATH)
        validate_questionnaire_schema(v3)
        questions = _questions(v3)
        assert "colour_palette" in questions
        assert "coverage_preferences" in questions
        assert questions["colour_palette"]["constraints"]["max_items"] == 4


# --------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------
class TestV4Taxonomy:
    def test_carries_all_seven_ceremonies(self, v4):
        assert _values(_questions(v4)["ceremony"]) == {
            "nikah",
            "mehndi",
            "baraat",
            "walima",
            "pheras",
            "anand_karaj",
            "reception",
        }

    def test_carries_all_six_garments(self, v4):
        assert _values(_questions(v4)["garment_type"]) == {
            "lehenga",
            "saree",
            "gharara",
            "sharara",
            "anarkali",
            "shalwar_kameez",
        }

    def test_expands_silhouettes_and_keeps_them_garment_dependent(self, v4):
        silhouette = _questions(v4)["silhouette"]
        assert len(silhouette["options"]) == 23

        restrictions = {
            rule["when"]["values"][0]: set(rule["then"]["values"])
            for rule in v4["rules"]
            if rule["then"]["action"] == "restrict_options"
            and rule["then"]["question_id"] == "silhouette"
        }
        # Every garment restricts, every shape belongs to exactly one garment,
        # and the restrictions exactly partition the declared options.
        assert set(restrictions) == _values(_questions(v4)["garment_type"])
        union = set()
        for shapes in restrictions.values():
            assert not (union & shapes), "a silhouette is offered for two garments"
            union |= shapes
        assert union == _values(silhouette)

    def test_keeps_gharara_and_sharara_structurally_distinct(self, v4):
        restrictions = {
            rule["when"]["values"][0]: set(rule["then"]["values"])
            for rule in v4["rules"]
            if rule["then"]["action"] == "restrict_options"
            and rule["then"]["question_id"] == "silhouette"
        }
        # Different constructions: knee-flare vs waist-flare. Sharing a shape
        # would flatten exactly the distinction CLAUDE.md section 12 protects.
        assert not (restrictions["gharara"] & restrictions["sharara"])
        assert restrictions["gharara"] == {
            "classic_gharara",
            "farshi_gharara",
            "slim_modern_gharara",
        }
        assert restrictions["sharara"] == {
            "classic_sharara",
            "high_waisted_sharara",
            "farshi_sharara",
        }

    def test_keeps_saree_draping_separate_from_dupatta_styling(self, v4):
        questions = _questions(v4)
        assert not (_values(questions["saree_drape"]) & _values(questions["dupatta_style"]))

    def test_retains_satin_and_the_full_fabric_vocabulary(self, v4):
        assert "satin" in _values(_questions(v4)["fabrics"])

    def test_offers_no_designer_or_brand_names(self, v4):
        denied = {"sabyasachi", "manish", "malhotra", "hsy", "elan", "maria", "gucci", "dior"}
        for step in v4["steps"]:
            for question in step["questions"]:
                for option in question.get("options", []):
                    words = set(option["label"].lower().replace("-", " ").split())
                    assert not (words & denied), option


# --------------------------------------------------------------------------
# The colour split
# --------------------------------------------------------------------------
class TestV4ColourQuestions:
    def test_replaces_the_single_palette_with_three_role_questions(self, v4):
        questions = _questions(v4)
        assert "colour_palette" not in questions
        for question_id in ("fabric_colour", "embroidery_colour", "dupatta_colour"):
            assert questions[question_id]["type"] == "colour_choice"

    def test_each_role_offers_the_curated_grouped_palette(self, v4):
        questions = _questions(v4)
        assert len(questions["fabric_colour"]["options"]) == 28
        assert len(questions["embroidery_colour"]["options"]) == 28
        # Every curated colour carries a presentation group, so the frontend can
        # render them compactly instead of as one unusable scrolling list.
        groups = {option["group"] for option in questions["fabric_colour"]["options"]}
        assert len(groups) == 7

    def test_only_the_dupatta_may_match_the_fabric(self, v4):
        questions = _questions(v4)
        assert "match_fabric" in _values(questions["dupatta_colour"])
        assert "match_fabric" not in _values(questions["fabric_colour"])
        assert "match_fabric" not in _values(questions["embroidery_colour"])

    def test_declares_exactly_one_bounded_custom_palette(self, v4):
        questions = _questions(v4)
        palettes = [q for q in questions.values() if q["type"] == "colour_list"]
        assert len(palettes) == 1
        assert palettes[0]["id"] == "custom_colours"
        assert palettes[0]["constraints"]["max_items"] == 8
        assert "options" not in palettes[0]

    def test_a_saree_hides_the_dupatta_colour(self, v4):
        # A saree has no dupatta, so asking for its colour is incoherent.
        hides = {
            rule["then"]["question_id"]
            for rule in v4["rules"]
            if rule["then"]["action"] == "hide" and rule["when"]["values"] == ["saree"]
        }
        assert {"dupatta_colour", "dupatta_style"} <= hides

    def test_accepts_a_curated_swatch(self, v4):
        result = _answer(v4, {"garment_type": "lehenga", "fabric_colour": "scarlet"})
        assert result["fabric_colour"] == "scarlet"

    def test_rejects_a_colour_that_is_not_offered(self, v4):
        _rejects(v4, {"fabric_colour": "chartreuse"}, "fabric_colour")

    def test_rejects_a_non_string_colour(self, v4):
        _rejects(v4, {"fabric_colour": ["scarlet"]}, "fabric_colour")

    def test_accepts_a_custom_hex_the_bride_has_added(self, v4):
        result = _answer(v4, {"custom_colours": ["#c22b39"], "fabric_colour": "#c22b39"})
        assert result["fabric_colour"] == "#c22b39"
        assert result["custom_colours"] == ["#c22b39"]

    def test_normalises_custom_hex_to_lower_case(self, v4):
        # A native colour input may emit either case; persistence must not end
        # up holding two spellings of one colour.
        result = _answer(v4, {"custom_colours": ["#C22B39"], "fabric_colour": "#C22B39"})
        assert result["custom_colours"] == ["#c22b39"]
        assert result["fabric_colour"] == "#c22b39"

    def test_rejects_a_custom_hex_that_is_not_in_the_palette(self, v4):
        # The bride removed the colour but an answer still references it.
        _rejects(v4, {"custom_colours": ["#c22b39"], "fabric_colour": "#00ff00"}, "fabric_colour")

    def test_rejects_a_custom_hex_when_no_palette_was_supplied(self, v4):
        _rejects(v4, {"fabric_colour": "#c22b39"}, "fabric_colour")

    def test_resolves_the_palette_regardless_of_key_order(self, v4):
        # Dict order must not decide validity: the palette is resolved in a
        # pre-pass precisely so a colour answer listed first still validates.
        forwards = _answer(v4, {"custom_colours": ["#abcdef"], "fabric_colour": "#abcdef"})
        backwards = _answer(v4, {"fabric_colour": "#abcdef", "custom_colours": ["#abcdef"]})
        assert forwards == backwards

    @pytest.mark.parametrize(
        "bad",
        ["#fff", "c22b39", "#c22b3", "#c22b399", "rgb(1,2,3)", "red", "#ggghhh", "#c22b39 "],
    )
    def test_rejects_anything_that_is_not_six_digit_hex(self, v4, bad):
        _rejects(v4, {"custom_colours": [bad]}, "custom_colours")

    def test_rejects_a_duplicate_custom_colour(self, v4):
        _rejects(v4, {"custom_colours": ["#c22b39", "#C22B39"]}, "custom_colours")

    def test_enforces_the_custom_palette_ceiling(self, v4):
        too_many = [f"#0000{index:02x}" for index in range(9)]
        _rejects(v4, {"custom_colours": too_many}, "custom_colours")

    def test_rejects_a_non_list_palette(self, v4):
        _rejects(v4, {"custom_colours": "#c22b39"}, "custom_colours")


# --------------------------------------------------------------------------
# The coverage split
# --------------------------------------------------------------------------
class TestV4CoverageQuestions:
    def test_replaces_the_conflated_list_with_four_questions(self, v4):
        questions = _questions(v4)
        assert "coverage_preferences" not in questions
        for question_id in ("sleeves", "back_coverage", "midriff", "head_covering"):
            assert questions[question_id]["type"] == "single_choice"

    def test_each_body_area_is_independently_answerable(self, v4):
        result = _answer(
            v4,
            {
                "sleeves": "full_sleeve",
                "back_coverage": "modest_back",
                "midriff": "covered_midriff",
                "head_covering": "hijab",
            },
        )
        assert result["sleeves"] == "full_sleeve"
        assert result["head_covering"] == "hijab"

    def test_keeps_modest_coverage_represented_everywhere(self, v4):
        # CLAUDE.md section 12: modest options must survive every revision.
        questions = _questions(v4)
        assert "full_sleeve" in _values(questions["sleeves"])
        assert "modest_back" in _values(questions["back_coverage"])
        assert "covered_midriff" in _values(questions["midriff"])
        assert {"hijab", "dupatta_over_head"} <= _values(questions["head_covering"])
        assert "high_neck" in _values(questions["neckline_style"])

    def test_a_body_area_takes_only_one_answer(self, v4):
        _rejects(v4, {"sleeves": ["full_sleeve", "cap_sleeve"]}, "sleeves")

    def test_an_uncovered_head_cannot_also_be_framed_by_the_dupatta(self, v4):
        # "The dupatta stays on the shoulders" and "framing the face from the
        # crown" are the same piece of fabric in two places at once.
        _rejects(
            v4,
            {"head_covering": "uncovered", "dupatta_style": "head_drape"},
            "dupatta_style",
        )
        _rejects(
            v4,
            {"head_covering": "uncovered", "dupatta_style": "double_dupatta"},
            "dupatta_style",
        )

    def test_a_covered_head_cannot_take_a_non_head_drape(self, v4):
        # The requirement the coverage split has to keep: a non-head drape
        # must not contradict a covered-head selection when the DUPATTA is
        # what covers the head.
        _rejects(
            v4,
            {"head_covering": "dupatta_over_head", "dupatta_style": "one_shoulder"},
            "dupatta_style",
        )
        _rejects(
            v4,
            {"head_covering": "veil_style", "dupatta_style": "front_drape"},
            "dupatta_style",
        )

    @pytest.mark.parametrize(
        ("head_covering", "dupatta_style"),
        [
            ("dupatta_over_head", "head_drape"),
            ("dupatta_over_head", "double_dupatta"),
            ("veil_style", "head_drape"),
            # Pinned from the crown and trailing: the veil IS a trail drape.
            ("veil_style", "trail_dupatta"),
            ("uncovered", "one_shoulder"),
        ],
    )
    def test_accepts_a_coherent_head_and_dupatta_pairing(self, v4, head_covering, dupatta_style):
        result = _answer(v4, {"head_covering": head_covering, "dupatta_style": dupatta_style})
        assert result["dupatta_style"] == dupatta_style

    @pytest.mark.parametrize("dupatta_style", ["one_shoulder", "front_drape", "head_drape"])
    def test_a_hijab_leaves_the_dupatta_free(self, v4, dupatta_style):
        # A hijab covers the hair by itself. Restricting the dupatta here
        # would be the app inventing a rule no community holds — and it would
        # make the most modest head option the most constrained one.
        result = _answer(v4, {"head_covering": "hijab", "dupatta_style": dupatta_style})
        assert result["dupatta_style"] == dupatta_style

    def test_a_covered_midriff_cannot_take_a_plunging_neckline(self, v4):
        _rejects(
            v4,
            {"midriff": "covered_midriff", "neckline_style": "deep_v_neck"},
            "neckline_style",
        )

    @pytest.mark.parametrize("neckline", ["sweetheart_neck", "v_neck", "high_neck"])
    def test_a_covered_midriff_still_allows_every_other_neckline(self, v4, neckline):
        # v3 drew the line at the plunging V only; a sweetheart neckline above
        # a long bodice is an ordinary bridal look, not a contradiction.
        result = _answer(v4, {"midriff": "covered_midriff", "neckline_style": neckline})
        assert result["neckline_style"] == neckline

    def test_a_bare_midriff_leaves_every_neckline_open(self, v4):
        result = _answer(v4, {"midriff": "bare_midriff", "neckline_style": "deep_v_neck"})
        assert result["neckline_style"] == "deep_v_neck"

    def test_rejects_a_value_from_a_different_body_area(self, v4):
        _rejects(v4, {"sleeves": "modest_back"}, "sleeves")


# --------------------------------------------------------------------------
# Garment-dependent behaviour
# --------------------------------------------------------------------------
class TestV4GarmentDependence:
    def test_a_saree_gets_the_drape_question(self, v4):
        result = _answer(v4, {"garment_type": "saree", "saree_drape": "nivi_drape"})
        assert result["saree_drape"] == "nivi_drape"

    def test_a_saree_cannot_answer_the_dupatta_questions(self, v4):
        _rejects(v4, {"garment_type": "saree", "dupatta_style": "one_shoulder"}, "dupatta_style")
        _rejects(v4, {"garment_type": "saree", "dupatta_colour": "scarlet"}, "dupatta_colour")

    def test_a_lehenga_gets_the_dupatta_questions_but_not_the_drape(self, v4):
        result = _answer(
            v4,
            {
                "garment_type": "lehenga",
                "dupatta_style": "double_dupatta",
                "dupatta_colour": "match_fabric",
            },
        )
        assert result["dupatta_style"] == "double_dupatta"
        assert result["dupatta_colour"] == "match_fabric"
        _rejects(v4, {"garment_type": "lehenga", "saree_drape": "nivi_drape"}, "saree_drape")

    def test_a_silhouette_from_another_garment_is_rejected(self, v4):
        _rejects(v4, {"garment_type": "lehenga", "silhouette": "farshi_gharara"}, "silhouette")

    def test_each_garment_accepts_its_own_silhouettes(self, v4):
        pairs = [
            ("lehenga", "panelled_kali_lehenga"),
            ("saree", "half_saree"),
            ("gharara", "farshi_gharara"),
            ("sharara", "high_waisted_sharara"),
            ("anarkali", "jacket_style_anarkali"),
            ("shalwar_kameez", "angrakha_kameez"),
        ]
        for garment, silhouette in pairs:
            result = _answer(v4, {"garment_type": garment, "silhouette": silhouette})
            assert result["silhouette"] == silhouette


# --------------------------------------------------------------------------
# Schema-vocabulary guards for the new question types
# --------------------------------------------------------------------------
class TestColourTypeSchemaGuards:
    def _minimal(self, questions, rules=()):
        return {
            "schema_version": 1,
            "key": "guard_schema",
            "title": "Guard",
            "steps": [{"id": "only_step", "title": "Only", "questions": questions}],
            "rules": list(rules),
        }

    def _colour_choice(self, **constraints):
        return {
            "id": "fabric_colour",
            "type": "colour_choice",
            "label": "Fabric colour",
            "required": False,
            "options": [{"value": "scarlet", "label": "Scarlet"}],
            "constraints": constraints,
        }

    def _palette(self, max_items=4):
        return {
            "id": "custom_colours",
            "type": "colour_list",
            "label": "Your own colours",
            "required": False,
            "constraints": {"max_items": max_items},
        }

    def test_allow_custom_requires_a_palette_to_draw_from(self):
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(self._minimal([self._colour_choice(allow_custom=True)]))

    def test_allow_custom_is_valid_once_a_palette_exists(self):
        validate_questionnaire_schema(
            self._minimal([self._colour_choice(allow_custom=True), self._palette()])
        )

    def test_at_most_one_palette_per_schema(self):
        second = self._palette() | {"id": "other_colours"}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(self._minimal([self._palette(), second]))

    def test_a_palette_must_declare_a_ceiling(self):
        palette = self._palette()
        palette["constraints"] = {}
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(self._minimal([palette]))

    def test_a_palette_declares_no_options(self):
        palette = self._palette()
        palette["options"] = [{"value": "scarlet", "label": "Scarlet"}]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(self._minimal([palette]))

    def test_a_rule_may_not_read_a_colour_question(self):
        # A colour answer may be a custom hex that is not a declared option, so
        # it could never satisfy a condition — barring it keeps rule semantics
        # identical to the pre-colour vocabulary.
        rules = [
            {
                "id": "colour_gates_something",
                "when": {
                    "question_id": "fabric_colour",
                    "operator": "equals",
                    "values": ["scarlet"],
                },
                "then": {"action": "hide", "question_id": "custom_colours"},
            }
        ]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(
                self._minimal([self._colour_choice(), self._palette()], rules)
            )

    def test_a_rule_may_not_narrow_a_colour_question(self):
        garment = {
            "id": "garment_type",
            "type": "single_choice",
            "label": "Garment",
            "required": True,
            "options": [{"value": "saree", "label": "Saree"}],
        }
        rules = [
            {
                "id": "restrict_colour",
                "when": {"question_id": "garment_type", "operator": "equals", "values": ["saree"]},
                "then": {
                    "action": "restrict_options",
                    "question_id": "fabric_colour",
                    "values": ["scarlet"],
                },
            }
        ]
        with pytest.raises(QuestionnaireSchemaError):
            validate_questionnaire_schema(
                self._minimal([garment, self._colour_choice(), self._palette()], rules)
            )

    def test_a_rule_may_still_hide_a_colour_question(self):
        garment = {
            "id": "garment_type",
            "type": "single_choice",
            "label": "Garment",
            "required": True,
            "options": [{"value": "saree", "label": "Saree"}],
        }
        rules = [
            {
                "id": "saree_hides_colour",
                "when": {"question_id": "garment_type", "operator": "equals", "values": ["saree"]},
                "then": {"action": "hide", "question_id": "fabric_colour"},
            }
        ]
        validate_questionnaire_schema(
            self._minimal([garment, self._colour_choice(), self._palette()], rules)
        )


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------
class TestV4Lifecycle:
    pytestmark = pytest.mark.django_db

    @pytest.mark.django_db
    def test_loading_every_version_keeps_v1_active_and_v4_draft(self):
        call_command(
            "loaddata",
            "questionnaire_v1",
            "questionnaire_v2",
            "questionnaire_v3",
            "questionnaire_v4",
            verbosity=0,
        )
        assert QuestionnaireVersion.objects.count() == 4
        assert QuestionnaireVersion.objects.get(version=1).status == "active"
        assert QuestionnaireVersion.objects.get(version=4).status == "draft"

    @pytest.mark.django_db
    def test_activating_v4_atomically_retires_the_previous_active(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v4", verbosity=0)
        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=4))
        assert QuestionnaireVersion.objects.get(version=1).status == "retired"
        assert QuestionnaireVersion.objects.get(version=4).status == "active"
        assert (
            QuestionnaireVersion.objects.filter(status=QuestionnaireVersion.Status.ACTIVE).count()
            == 1
        )

    @pytest.mark.django_db
    def test_activating_v4_does_not_touch_an_earlier_schema(self):
        # Retiring a version must never rewrite it — the answers persisted
        # against v1 have to keep meaning exactly what they meant.
        call_command("loaddata", "questionnaire_v1", "questionnaire_v4", verbosity=0)
        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=4))
        assert (
            _fingerprint(QuestionnaireVersion.objects.get(version=1).schema)
            == V1_SCHEMA_FINGERPRINT
        )


# --------------------------------------------------------------------------
# Generated-fixture freshness (CLAUDE.md section 18)
# --------------------------------------------------------------------------
class TestV4IsGenerated:
    def test_committed_fixture_matches_a_fresh_render(self):
        # questionnaire_v4.json is GENERATED from build_v4.py. Without this
        # guard, "never hand-edit generated files" would rest on manual
        # diligence alone for a 1500-line JSON file: a direct edit would
        # survive until someone unrelated regenerated and silently reverted it.
        import importlib.util

        spec = importlib.util.spec_from_file_location("build_v4", _FIXTURES / "build_v4.py")
        build_v4 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_v4)

        assert build_v4.render() == _V4_PATH.read_text(encoding="utf-8"), (
            "questionnaire_v4.json is stale or was hand-edited — "
            "run python apps/api/sitara/questionnaire/fixtures/build_v4.py"
        )

    def test_rendering_twice_is_byte_identical(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("build_v4", _FIXTURES / "build_v4.py")
        build_v4 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_v4)
        assert build_v4.render() == build_v4.render()


# --------------------------------------------------------------------------
# The custom palette is bounded before it is walked
# --------------------------------------------------------------------------
class TestCustomPaletteIsBounded:
    """An oversized palette must be refused cheaply, not walked first.

    Every syntactically valid hex passes the per-item check, so unlike a
    multi_choice (which fails on its first value outside a small declared set)
    the caller would otherwise choose how much work validation does. These
    tests pin the bound down on both routes into the palette: the question's
    own answer, and the pre-pass that resolves it for a custom colour_choice.
    """

    def _huge(self, count):
        return [f"#{index:06x}" for index in range(count)]

    def test_refuses_an_oversized_palette(self, v4):
        _rejects(v4, {"custom_colours": self._huge(5_000)}, "custom_colours")

    def test_refuses_an_oversized_palette_promptly(self, v4):
        # A quadratic dedup over an attacker-sized array would take minutes;
        # bounded rejection is effectively instant. The threshold is loose so
        # the test measures the algorithm, not the machine.
        import time

        payload = {"custom_colours": self._huge(200_000)}
        started = time.monotonic()
        with pytest.raises(QuestionnaireAnswerError):
            _answer(v4, payload)
        assert time.monotonic() - started < 2.0

    def test_refuses_an_oversized_palette_through_the_pre_pass_too(self, v4):
        # _custom_palette runs on EVERY validation call, before the main pass,
        # so the bound has to hold there as well - including when no colour
        # answer accompanies the palette.
        import time

        payload = {
            "custom_colours": self._huge(200_000),
            "fabric_colour": "#000001",
        }
        started = time.monotonic()
        with pytest.raises(QuestionnaireAnswerError) as caught:
            _answer(v4, payload)
        assert time.monotonic() - started < 2.0
        assert "custom_colours" in caught.value.errors

    def test_still_accepts_a_palette_at_the_declared_ceiling(self, v4):
        result = _answer(v4, {"custom_colours": self._huge(8)})
        assert len(result["custom_colours"]) == 8

    def test_preserves_the_order_colours_were_added_in(self, v4):
        # Dedup switched to a set for cost reasons; insertion order is what the
        # review screen ranks by, so it must survive that change.
        added = ["#c22b39", "#1d6b4f", "#2b3d8f"]
        assert _answer(v4, {"custom_colours": added})["custom_colours"] == added
