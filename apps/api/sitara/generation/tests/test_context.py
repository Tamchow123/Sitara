"""Trusted generation-context construction and pre-spend gates."""

import pytest

from sitara.generation.context import DesignNotReady, build_generation_context
from sitara.questionnaire.services import activate_questionnaire_version

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
