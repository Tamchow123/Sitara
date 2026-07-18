"""Trusted generation-context construction and pre-spend gates."""

import copy

import pytest

from sitara.designs.models import Design, DesignSession
from sitara.generation.context import DesignNotReady, build_generation_context
from sitara.generation.services import generate_design_spec_for_design
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.services import activate_questionnaire_version

from . import fakes
from .factory import make_active_v1, make_complete_design, v1_schema

pytestmark = pytest.mark.django_db


class TestSourceSelections:
    def test_echoes_the_validated_machine_values(self):
        design = make_complete_design()
        context = build_generation_context(design)
        ss = context.source_selections
        assert ss["garment_type"] == "lehenga"
        assert ss["ceremony"] == "nikah"
        assert ss["silhouette"] == "flared_lehenga"
        assert ss["colour_palette"] == ["ivory", "gold"]
        assert ss["embellishment_styles"] == ["zardozi", "dabka"]

    def test_hidden_optional_answers_are_null(self):
        design = make_complete_design()
        ss = build_generation_context(design).source_selections
        # saree_drape is hidden for a lehenga → null, never sent as an answer.
        assert ss["saree_drape"] is None


class TestTrustedAndUntrusted:
    def test_labels_are_resolved_and_text_is_untrusted(self):
        design = make_complete_design()
        context = build_generation_context(design)
        labelled = {a["question_id"]: a for a in context.trusted_answers}
        assert labelled["garment_type"]["values"][0]["option_label"] == "Lehenga"
        # final_notes is text → untrusted section, not trusted_answers.
        assert "final_notes" not in labelled
        assert any(t["question_id"] == "final_notes" for t in context.untrusted_texts)

    def test_context_carries_no_forbidden_data(self):
        design = make_complete_design()
        context = build_generation_context(design)
        blob = (
            str(context.source_selections)
            + str(context.trusted_answers)
            + str(context.untrusted_texts)
        )
        assert str(design.design_session_id) not in blob
        assert str(design.id) not in blob


class TestPreSpendGates:
    def test_missing_questionnaire_is_rejected(self):
        from sitara.designs.models import Design, DesignSession

        design = Design.objects.create(design_session=DesignSession.objects.create())
        with pytest.raises(DesignNotReady) as excinfo:
            build_generation_context(design)
        assert excinfo.value.code == "questionnaire_missing"

    def test_draft_questionnaire_is_not_answerable(self):
        draft = make_active_v1(version=5, status="draft")
        design = make_complete_design(questionnaire=draft)
        with pytest.raises(DesignNotReady) as excinfo:
            build_generation_context(design)
        assert excinfo.value.code == "questionnaire_not_answerable"

    def test_retired_questionnaire_still_generates(self):
        active = make_active_v1(version=1)
        design = make_complete_design(questionnaire=active)
        # Retire version 1 by activating version 2.
        draft = make_active_v1(version=2, status="draft")
        activate_questionnaire_version(draft)
        active.refresh_from_db()
        # Design still points at the now-retired version and remains usable.
        context = build_generation_context(design)
        assert context.source_selections["garment_type"] == "lehenga"

    def test_incomplete_design_is_rejected(self):
        design = make_complete_design(answers={"garment_type": "lehenga"})
        with pytest.raises(DesignNotReady) as excinfo:
            build_generation_context(design)
        assert excinfo.value.code == "incomplete"
        assert "silhouette" in (excinfo.value.field_errors or {})

    def test_unavailable_inspiration_is_rejected(self, inmemory_storage):
        from sitara.catalogue.services import retire_inspiration_asset
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        retire_inspiration_asset(asset)
        with pytest.raises(DesignNotReady) as excinfo:
            build_generation_context(design)
        assert excinfo.value.code == "incomplete"
        assert "inspiration_asset_ids" in (excinfo.value.field_errors or {})


def _questions(schema: dict) -> dict:
    return {q["id"]: q for step in schema["steps"] for q in step["questions"]}


def _remove_question(schema: dict, question_id: str) -> None:
    for step in schema["steps"]:
        step["questions"] = [q for q in step["questions"] if q["id"] != question_id]


def _design_for(schema: dict, answers: dict) -> Design:
    version = QuestionnaireVersion.objects.create(version=1, status="active", schema=schema)
    return Design.objects.create(
        design_session=DesignSession.objects.create(),
        questionnaire_version=version,
        answers=answers,
    )


# Minimal answers that satisfy the v1 completion gate (only required questions).
_BASE = {
    "garment_type": "lehenga",
    "ceremony": "nikah",
    "silhouette": "flared_lehenga",
    "colour_palette": ["ivory"],
    "embellishment_styles": ["zardozi"],
}


class TestUnsupportedQuestionnaireContract:
    """A structurally usable questionnaire that cannot satisfy the DesignSpec
    source-selection contract is refused BEFORE any provider call, with a
    controlled code — never a Pydantic traceback."""

    def _assert_unsupported(self, design):
        provider = fakes.SequenceProvider([])
        with pytest.raises(DesignNotReady) as excinfo:
            generate_design_spec_for_design(design, provider=provider)
        assert excinfo.value.code == "unsupported_questionnaire_contract"
        assert provider.calls == 0

    def test_omitted_garment_type(self):
        schema = copy.deepcopy(v1_schema())
        _remove_question(schema, "garment_type")
        answers = {k: v for k, v in _BASE.items() if k != "garment_type"}
        self._assert_unsupported(_design_for(schema, answers))

    def test_renamed_ceremony(self):
        schema = copy.deepcopy(v1_schema())
        _questions(schema)  # touch for clarity
        for step in schema["steps"]:
            for question in step["questions"]:
                if question["id"] == "ceremony":
                    question["id"] = "event"
        answers = {k: v for k, v in _BASE.items() if k != "ceremony"}
        answers["event"] = "nikah"
        self._assert_unsupported(_design_for(schema, answers))

    def test_hidden_required_source_field(self):
        schema = copy.deepcopy(v1_schema())
        # Hide silhouette (a required source field) whenever garment_type=lehenga.
        schema["rules"].append(
            {
                "id": "hide_silhouette_for_lehenga",
                "when": {
                    "question_id": "garment_type",
                    "operator": "equals",
                    "values": ["lehenga"],
                },
                "then": {"action": "hide", "question_id": "silhouette"},
            }
        )
        answers = {k: v for k, v in _BASE.items() if k != "silhouette"}
        self._assert_unsupported(_design_for(schema, answers))

    def test_incompatible_field_type(self):
        schema = copy.deepcopy(v1_schema())
        # colour_palette becomes a single-choice, so it can never satisfy the
        # contract's non-empty list.
        _questions(schema)["colour_palette"]["type"] = "single_choice"
        answers = dict(_BASE)
        answers["colour_palette"] = "ivory"
        self._assert_unsupported(_design_for(schema, answers))


def test_v1_schema_has_the_source_selection_fields():
    ids = {q["id"] for step in v1_schema()["steps"] for q in step["questions"]}
    for field in (
        "garment_type",
        "ceremony",
        "silhouette",
        "colour_palette",
        "embellishment_styles",
    ):
        assert field in ids
