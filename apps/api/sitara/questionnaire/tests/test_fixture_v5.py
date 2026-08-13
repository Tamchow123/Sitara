"""Fixture-integrity tests for questionnaire_v5 — v4 plus option descriptions.

Phase 23 Part C, an addendum to ADR 0018. ``ChoiceOptionCard`` mounts its info
trigger only for an option that carries a description, so a question where one
option has one and eleven do not renders a single unexplained "i". That was the
reported defect on ``fabrics``: satin arrived in Phase 16B with a description,
alongside eleven options inherited from v1 that never had one.

v5 fills three questions and deliberately leaves a fourth alone. Everything
else — questions, ids, options, order, rules, constraints — is v4 verbatim, so a
design pinned to v4 keeps its exact historical semantics and nothing about
generation, the DesignSpec or the image prompt changes.

The two claims this file exists to hold down:

- **v4 is untouched.** Published versions are immutable (ADR 0005), and a
  description lives inside the frozen ``schema`` JSON, so the rule applies whole
  even though a validator never reads the text. v5 is a new DRAFT.
- **A description never leaves the browser.** It is presentation metadata. It
  must not reach ``source_selections``, a DesignSpec, an image prompt or any
  provider payload — asserted as a negative, because that is the only way to
  assert it.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from sitara.designs.models import Design, DesignSession
from sitara.generation.context import build_generation_context
from sitara.generation.demo.design_spec_engine import build_demo_design_spec
from sitara.generation.design_spec import validate_design_spec
from sitara.generation.prompt_builder import build_image_prompt
from sitara.questionnaire.answer_validation import validate_questionnaire_answers
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.rules import declared_option_values, questions_by_id
from sitara.questionnaire.schema_validation import validate_questionnaire_schema
from sitara.questionnaire.services import activate_questionnaire_version

from .test_fixture_versions import V1_SCHEMA_FINGERPRINT, _fingerprint

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_V4_PATH = _FIXTURES / "questionnaire_v4.json"
_V5_PATH = _FIXTURES / "questionnaire_v5.json"

# The three questions Phase 23 fills, and the one it deliberately does not.
_DESCRIBED = ("fabrics", "embellishment_styles", "embellishment_density")
_UNDESCRIBED = "regional_style"


def _record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))[0]


def _schema(path: Path) -> dict:
    return _record(path)["fields"]["schema"]


def _build_v5():
    spec = importlib.util.spec_from_file_location("build_v5", _FIXTURES / "build_v5.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def v5() -> dict:
    return _schema(_V5_PATH)


@pytest.fixture(scope="module")
def v4() -> dict:
    return _schema(_V4_PATH)


class TestV5FixtureIntegrity:
    def test_fixture_is_format_valid(self, v5):
        validate_questionnaire_schema(v5)

    def test_declares_version_five_as_a_draft(self):
        # A DRAFT. Activation is an operator step; loaddata never activates.
        fields = _record(_V5_PATH)["fields"]
        assert fields["version"] == 5
        assert fields["status"] == "draft"

    def test_leaves_v4_byte_identical(self):
        # The whole reason v5 exists rather than an edit to v4. Compared as
        # BYTES, not as parsed JSON: a reordering or a whitespace change would
        # be a rewrite of a frozen artefact even though it parses the same.
        digest = hashlib.sha256(_lf(_V4_PATH.read_bytes())).hexdigest()
        assert digest == _V4_SHA256_AT_PHASE_START

    def test_is_v4_plus_descriptions_and_nothing_else(self, v4, v5):
        # Structural equality after stripping every description: same questions,
        # same ids, same options, same order, same rules, same constraints.
        assert _without_descriptions(v5) == _without_descriptions(v4)

    def test_carries_the_same_questions_in_the_same_order(self, v4, v5):
        assert list(questions_by_id(v5)) == list(questions_by_id(v4))


class TestEveryOptionExplainsItself:
    @pytest.mark.parametrize("question_id", _DESCRIBED)
    def test_every_option_of_a_filled_question_carries_a_description(self, v5, question_id):
        question = questions_by_id(v5)[question_id]
        undescribed = [
            option["value"] for option in question["options"] if not option.get("description")
        ]
        assert not undescribed, f"{question_id}: {undescribed}"

    @pytest.mark.parametrize("question_id", _DESCRIBED)
    def test_v4_left_at_least_one_option_of_it_unexplained(self, v4, question_id):
        # The defect, pinned. If this ever stops being true for v4, either v4
        # was edited (forbidden) or this question did not need filling.
        question = questions_by_id(v4)[question_id]
        assert any(not option.get("description") for option in question["options"])

    def test_satin_keeps_the_exact_description_adr_0018_shipped(self, v4, v5):
        # The one fabric that already had one. It is repeated verbatim in the
        # build script so the table is the whole answer for the question rather
        # than a patch over part of it.
        before = _option(v4, "fabrics", "satin")["description"]
        assert _option(v5, "fabrics", "satin")["description"] == before

    def test_regional_style_stays_at_zero_of_nine(self, v5):
        # The project owner's decision, and CLAUDE.md §12's requirement that
        # regional influences stay OPTIONAL AND NON-PRESCRIPTIVE. Nine short
        # sentences would be nine claims about how nine communities dress, and
        # nobody has reviewed them. Zero of nine is an internally consistent
        # "none"; seven of nine would not be.
        question = questions_by_id(v5)[_UNDESCRIBED]
        assert question["options"]
        assert not any(option.get("description") for option in question["options"])

    def test_no_colour_swatch_gains_a_description(self, v5):
        # "All or none" applies to the CARDS. A swatch is its own explanation,
        # and `match_fabric` is the one non-colour option in a colour list and
        # keeps the description it already had as the legitimate exception.
        for question_id, question in questions_by_id(v5).items():
            if not question_id.endswith("_colour"):
                continue
            described = [
                option["value"] for option in question["options"] if option.get("description")
            ]
            assert described in ([], ["match_fabric"]), (question_id, described)

    def test_no_description_names_a_community_or_a_region(self, v5):
        # The same restraint the undescribed question is protecting: a craft
        # description says what the technique LOOKS LIKE, never whose it is.
        # Naming provenance is a separate, reviewable claim.
        forbidden = (
            "punjabi",
            "rajasthani",
            "gujarati",
            "bengali",
            "pakistani",
            "bangladeshi",
            "lucknow",
            "kashmir",
            "north indian",
            "south indian",
            "hyderabadi",
        )
        # Scoped to the three questions v5 fills. v4's own `silhouette` copy
        # already names regions ("the langa-voni ... a South Indian favourite",
        # "timeless Lucknowi grace"), inherited here verbatim; v4 is published
        # and immutable (ADR 0005), so this rule binds what Phase 23 writes and
        # cannot retroactively bind what it is forbidden to edit. Recorded
        # rather than quietly narrowed: the precedent exists and is not the
        # standard the craft descriptions are held to.
        questions = questions_by_id(v5)
        for question_id in _DESCRIBED:
            for option in questions[question_id]["options"]:
                text = (option.get("description") or "").lower()
                for word in forbidden:
                    assert word not in text, (question_id, option["value"], word)


class TestADesignPinnedToV4IsUnaffected:
    """v5 existing changes nothing for a concept already pinned to v4."""

    @pytest.fixture
    def v4_answers(self) -> dict:
        from sitara.generation.tests.factory import COMPLETE_ANSWERS_V4

        return dict(COMPLETE_ANSWERS_V4)

    def test_the_same_answers_still_validate_against_v4(self, v4, v4_answers):
        validate_questionnaire_answers(v4, v4_answers, require_complete=True)

    def test_and_against_v5(self, v5, v4_answers):
        # Same options, so the same answers are still a complete answer set.
        validate_questionnaire_answers(v5, v4_answers, require_complete=True)

    @pytest.mark.django_db
    def test_it_still_generates_the_same_spec_and_the_same_prompt(self, v4_answers):
        # The strongest form: a v4 design's DesignSpec and image prompt are
        # byte-identical whether or not v5 exists, because a description is
        # never an input to either.
        design = _v4_design(v4_answers)
        before = _spec_and_prompt(design)

        call_command("loaddata", "questionnaire_v5", verbosity=0)
        assert QuestionnaireVersion.objects.filter(version=5).exists()

        assert _spec_and_prompt(design) == before


@pytest.mark.django_db
class TestADescriptionNeverLeavesTheBrowser:
    def test_it_reaches_no_design_spec_no_prompt_and_no_provider_payload(self):
        # Asserted as a NEGATIVE against the real generation path, because
        # "presentation metadata stays presentational" is only worth anything
        # if something checks it. The demo engine is used deliberately: it is
        # the one spec builder that runs locally, so this covers the whole
        # chain (answers -> context -> DesignSpec -> image prompt) without a
        # provider call.
        call_command("loaddata", "questionnaire_v5", verbosity=0)
        # The RETURNED row, not the pre-activation instance: assigning the stale
        # one caches `status="draft"` on the Design and generation refuses it.
        version = activate_questionnaire_version(QuestionnaireVersion.objects.get(version=5))

        from sitara.generation.tests.factory import COMPLETE_ANSWERS_V4

        design = Design.objects.create(
            design_session=DesignSession.objects.create(),
            questionnaire_version=version,
            answers=dict(COMPLETE_ANSWERS_V4),
        )
        spec_json, prompt = _spec_and_prompt(design)

        descriptions = _all_descriptions(version.schema)
        assert descriptions, "v5 must actually carry descriptions for this to prove anything"
        for text in descriptions:
            assert text not in spec_json
            assert text not in prompt
        # And no distinctive fragment of one leaks either — a substring check on
        # whole sentences alone could pass while a clause was copied through.
        for fragment in ("padded base", "coiled metallic wire", "natural slubs"):
            assert fragment not in spec_json
            assert fragment not in prompt

    def test_source_selections_carry_values_only(self):
        call_command("loaddata", "questionnaire_v5", verbosity=0)
        # The RETURNED row, not the pre-activation instance: assigning the stale
        # one caches `status="draft"` on the Design and generation refuses it.
        version = activate_questionnaire_version(QuestionnaireVersion.objects.get(version=5))

        from sitara.generation.tests.factory import COMPLETE_ANSWERS_V4

        design = Design.objects.create(
            design_session=DesignSession.objects.create(),
            questionnaire_version=version,
            answers=dict(COMPLETE_ANSWERS_V4),
        )
        context = build_generation_context(design)
        selections = json.dumps(context.source_selections, ensure_ascii=False)
        for text in _all_descriptions(version.schema):
            assert text not in selections


@pytest.mark.django_db
class TestActivation:
    def test_v5_loads_as_a_draft_and_activating_it_retires_v4(self):
        call_command("loaddata", "questionnaire_v4", "questionnaire_v5", verbosity=0)
        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=4))
        assert QuestionnaireVersion.objects.get(version=5).status == "draft"

        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=5))

        assert QuestionnaireVersion.objects.get(version=4).status == "retired"
        assert QuestionnaireVersion.objects.get(version=5).status == "active"
        assert (
            QuestionnaireVersion.objects.filter(status=QuestionnaireVersion.Status.ACTIVE).count()
            == 1
        )

    def test_activating_v5_does_not_touch_an_earlier_schema(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v5", verbosity=0)
        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=5))
        assert (
            _fingerprint(QuestionnaireVersion.objects.get(version=1).schema)
            == V1_SCHEMA_FINGERPRINT
        )


# --------------------------------------------------------------------------
# Generated-fixture freshness (CLAUDE.md §18)
# --------------------------------------------------------------------------
class TestV5IsGenerated:
    def test_committed_fixture_matches_a_fresh_render(self):
        assert _build_v5().render() == _V5_PATH.read_text(encoding="utf-8"), (
            "questionnaire_v5.json is stale or was hand-edited — "
            "run python apps/api/sitara/questionnaire/fixtures/build_v5.py"
        )

    def test_rendering_twice_is_byte_identical(self):
        build_v5 = _build_v5()
        assert build_v5.render() == build_v5.render()

    def test_the_description_tables_match_v4s_options_exactly(self, v4):
        # The build script raises rather than describing seven of twelve. This
        # asserts the tables are neither short nor over-full, so a future v4
        # option change is a loud failure rather than a silent gap.
        build_v5 = _build_v5()
        for question_id, table in build_v5.DESCRIPTIONS.items():
            assert set(declared_option_values(questions_by_id(v4)[question_id])) == set(table)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _option(schema: dict, question_id: str, value: str) -> dict:
    for option in questions_by_id(schema)[question_id]["options"]:
        if option["value"] == value:
            return option
    raise AssertionError(f"{question_id} has no option {value}")


def _without_descriptions(schema: dict) -> dict:
    stripped = json.loads(json.dumps(schema))
    for question in questions_by_id(stripped).values():
        for option in question.get("options", []):
            option.pop("description", None)
    return stripped


def _all_descriptions(schema: dict) -> list[str]:
    return [
        option["description"]
        for question in questions_by_id(schema).values()
        for option in question.get("options", [])
        if option.get("description")
    ]


# SHA-256 of questionnaire_v4.json's committed bytes at this phase's base commit
# (`main` @ 85b6bd4), taken with line endings normalised to LF. Hard-coded rather
# than read back through git: the test suite runs inside a container that has no
# git and no remote, and comparing the file to itself would prove nothing.
#
# Normalised because this fixture is not pinned in .gitattributes, so a Windows
# checkout with core.autocrlf holds CRLF while CI's Linux checkout holds LF. The
# digest must fail on a content edit and only on a content edit.
_V4_SHA256_AT_PHASE_START = "b170ce8eac882ac91f5645261aa071a1efc40ea62aa25766bdee8e849f873dc0"


def _lf(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n")


def _v4_design(answers: dict) -> Design:
    # The fixture ships v4 as a DRAFT, and a draft is not answerable: pinning a
    # design to it and generating raises `questionnaire_not_answerable`. Activate
    # it the way an operator would, so this exercises a real v4 concept.
    call_command("loaddata", "questionnaire_v4", verbosity=0)
    version = activate_questionnaire_version(QuestionnaireVersion.objects.get(version=4))
    return Design.objects.create(
        design_session=DesignSession.objects.create(),
        questionnaire_version=version,
        answers=dict(answers),
    )


def _spec_and_prompt(design: Design) -> tuple[str, str]:
    context = build_generation_context(design)
    spec = validate_design_spec(build_demo_design_spec(context))
    return json.dumps(spec.model_dump(mode="json"), sort_keys=True, ensure_ascii=False), (
        build_image_prompt(spec)
    )
