"""The questionnaire v4 answers carried end to end (Phase 16B, T6).

Covers the whole path a v4-answered design takes — generation context ->
DesignSpec v3 -> deterministic image prompt -> demo DesignSpec engine -> demo
asset selection — plus the regression that v1 and v3 questionnaires keep
producing exactly the DesignSpec versions they always did. No provider call, no
network and no paid AI usage anywhere in this module.
"""

import hashlib
import json

import pytest
from django.core.management import call_command
from pydantic import ValidationError

from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.generation.context import (
    _build_source_selections,
    _target_design_spec_version,
    build_generation_context,
)
from sitara.generation.demo.design_spec_engine import build_demo_design_spec
from sitara.generation.demo.manifest import DemoManifest, coverage_tags_for_selections
from sitara.generation.demo.selector import DemoAssetUnavailable, select_demo_asset
from sitara.generation.design_spec import (
    DesignSpecV3,
    SourceSelectionsV3,
    validate_design_spec,
)
from sitara.generation.pipeline import PipelineConfig, run_generation_attempt
from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION, build_image_prompt
from sitara.generation.selection_semantics import (
    colour_role_values,
    coverage_area_values,
    covered_midriff_requested,
    custom_colour_values,
    explicit_head_covering_decision,
    head_covering_answer,
    is_per_area_coverage,
    is_per_role_colour,
    legacy_coverage_values,
    ordered_colour_values,
)

from . import factory
from .demo_context_utils import a_selections_dict, a_v3_context, a_v3_selections_dict


def _v3_spec(**selection_overrides) -> DesignSpecV3:
    """A validated DesignSpec v3 built by the deterministic demo engine."""
    context = a_v3_context(selections=a_v3_selections_dict(**selection_overrides))
    return validate_design_spec(build_demo_design_spec(context))


def _lehenga_asset(asset_id: str, **overrides) -> dict:
    base = {
        "asset_id": asset_id,
        "filename": f"{asset_id}.webp",
        "sha256": hashlib.sha256(asset_id.encode("utf-8")).hexdigest(),
        "size_bytes": 50_000,
        "width": 768,
        "height": 1024,
        "alt_text": f"Placeholder alt text for {asset_id}, well over the minimum length.",
        "garment_types": ["lehenga"],
        "ceremonies": ["nikah"],
        "silhouettes": ["panelled_kali_lehenga"],
        "colours": ["deep_maroon"],
        "fabrics": ["satin"],
        "embellishment_styles": ["zardozi"],
        "embellishment_densities": ["balanced"],
        "coverage_preferences": ["full_sleeves"],
        "necklines": ["high_neck"],
        "dupatta_styles": ["double_dupatta"],
        "saree_drapes": [],
        "regional_styles": ["pakistani"],
        "provenance_status": "synthetic_development_placeholder",
    }
    base.update(overrides)
    return base


def _manifest(*assets: dict) -> DemoManifest:
    return DemoManifest.model_validate(
        {"schema_version": 2, "pack_id": "test-v4-pack", "assets": list(assets)}
    )


class TestQuestionnaireVersionTargetsSpecVersion:
    def test_v4_questionnaire_targets_design_spec_version_three(self):
        assert _target_design_spec_version(factory.v4_schema()) == 3

    def test_v3_questionnaire_still_targets_version_two(self):
        assert _target_design_spec_version(factory.v3_schema()) == 2

    def test_v1_questionnaire_still_targets_version_one(self):
        assert _target_design_spec_version(factory.v1_schema()) == 1

    def test_a_partially_migrated_schema_does_not_claim_version_three(self):
        # Only some of the v4 replacement questions present -> still version 2,
        # so a half-migrated schema fails the contract check rather than
        # producing half a version-3 brief.
        schema = factory.v4_schema()
        for step in schema["steps"]:
            step["questions"] = [q for q in step["questions"] if q["id"] != "midriff"]
        assert _target_design_spec_version(schema) == 2


@pytest.mark.django_db
class TestGenerationContextForV4:
    def test_context_carries_the_v3_contract_and_no_retired_fields(self):
        design = factory.make_complete_v4_design()
        context = build_generation_context(design)

        assert context.design_spec_schema_version == 3
        selections = context.source_selections
        assert "colour_palette" not in selections
        assert "coverage_preferences" not in selections
        assert selections["fabric_colour"] == "deep_maroon"
        assert selections["embroidery_colour"] == "antique_gold"
        assert selections["dupatta_colour"] == "#c8b273"
        assert selections["custom_colours"] == ["#c8b273"]
        assert selections["sleeves"] == "full_sleeve"
        assert selections["back_coverage"] == "modest_back"
        assert selections["midriff"] == "covered_midriff"
        assert selections["head_covering"] == "dupatta_over_head"
        # The contract the provider stage will hold the model to.
        SourceSelectionsV3.model_validate(selections)

    def test_unanswered_optional_questions_stay_null_never_defaulted(self):
        answers = dict(factory.COMPLETE_ANSWERS_V4)
        for optional in ("fabric_colour", "embroidery_colour", "sleeves", "head_covering"):
            answers.pop(optional)
        design = factory.make_complete_v4_design(answers=answers)

        selections = build_generation_context(design).source_selections

        assert selections["fabric_colour"] is None
        assert selections["embroidery_colour"] is None
        assert selections["sleeves"] is None
        assert selections["head_covering"] is None

    def test_a_saree_echoes_its_drape_and_no_dupatta_colour(self):
        # The v4 rules hide dupatta_colour and dupatta_style for a saree.
        answers = dict(factory.COMPLETE_ANSWERS_V4)
        answers.update(
            garment_type="saree",
            silhouette="classic_saree_drape",
            saree_drape="nivi_drape",
        )
        answers.pop("dupatta_style")
        answers.pop("dupatta_colour")
        design = factory.make_complete_v4_design(answers=answers)

        selections = build_generation_context(design).source_selections

        assert selections["dupatta_colour"] is None
        assert selections["dupatta_style"] is None
        assert selections["saree_drape"] == "nivi_drape"

    def test_the_echo_follows_the_schema_version_not_the_answer_keys(self):
        # The echo is built for the version the SCHEMA declares, never for
        # whatever keys happen to be in the answers blob.
        selections = _build_source_selections(
            dict(factory.COMPLETE_ANSWERS_V4),
            visibility=dict.fromkeys(factory.COMPLETE_ANSWERS_V4, True),
            version=2,
        )
        assert "fabric_colour" not in selections
        assert selections["colour_palette"] == []

    def test_a_hidden_answer_is_never_echoed(self):
        # Defence in depth: questionnaire validation already rejects an answer
        # to a hidden question, so this guards the echo itself.
        selections = _build_source_selections(
            dict(factory.COMPLETE_ANSWERS_V4),
            visibility=dict.fromkeys(factory.COMPLETE_ANSWERS_V4, True)
            | {"dupatta_colour": False, "custom_colours": False},
            version=3,
        )
        assert selections["dupatta_colour"] is None
        assert selections["custom_colours"] == []


class TestSourceSelectionsV3Contract:
    def test_a_bride_supplied_hex_is_accepted_for_a_role(self):
        payload = a_v3_selections_dict(fabric_colour="#a1b2c3")
        assert SourceSelectionsV3.model_validate(payload).fabric_colour == "#a1b2c3"

    @pytest.mark.parametrize(
        "bad", ["#ABC123", "#abc", "#a1b2c3z", "", "#a1b2c", "rgb(1,2,3)", "Deep_Maroon", "#"]
    )
    def test_a_malformed_colour_is_rejected(self, bad):
        with pytest.raises(ValidationError):
            SourceSelectionsV3.model_validate(a_v3_selections_dict(fabric_colour=bad))

    def test_a_retired_field_is_rejected_outright(self):
        payload = a_v3_selections_dict()
        payload["colour_palette"] = ["ivory"]
        with pytest.raises(ValidationError):
            SourceSelectionsV3.model_validate(payload)

    def test_custom_colours_is_bounded_and_hex_only(self):
        with pytest.raises(ValidationError):
            SourceSelectionsV3.model_validate(a_v3_selections_dict(custom_colours=["ivory"]))
        with pytest.raises(ValidationError):
            SourceSelectionsV3.model_validate(
                a_v3_selections_dict(custom_colours=[f"#00000{n}" for n in range(9)])
            )


class TestSelectionSemantics:
    def test_version_three_colours_are_role_ordered_deduplicated_and_match_free(self):
        selections = a_v3_selections_dict(
            fabric_colour="deep_maroon",
            embroidery_colour="deep_maroon",
            dupatta_colour="match_fabric",
        )
        assert ordered_colour_values(selections) == ("deep_maroon",)
        assert [field for field, _value in colour_role_values(selections)] == [
            "fabric_colour",
            "embroidery_colour",
            "dupatta_colour",
        ]

    def test_a_version_one_palette_passes_straight_through(self):
        selections = a_selections_dict()
        assert ordered_colour_values(selections) == ("ivory", "gold")
        assert colour_role_values(selections) == ()
        assert custom_colour_values(selections) == ()
        assert not is_per_role_colour(selections)
        assert not is_per_area_coverage(selections)

    def test_coverage_accessors_agree_across_both_contracts(self):
        v1 = a_selections_dict(coverage_preferences=["full_midriff", "head_drape_preferred"])
        v3 = a_v3_selections_dict(midriff="covered_midriff", head_covering="veil_style")

        assert explicit_head_covering_decision(v1) is True
        assert explicit_head_covering_decision(v3) is True
        assert covered_midriff_requested(v1) and covered_midriff_requested(v3)
        assert legacy_coverage_values(v3) == ()
        assert coverage_area_values(v1) == ()
        assert head_covering_answer(v1) is None
        assert head_covering_answer(v3) == "veil_style"

    def test_a_semi_sheer_midriff_is_not_a_covered_midriff(self):
        assert not covered_midriff_requested(a_v3_selections_dict(midriff="semi_sheer_midriff"))

    def test_an_uncovered_head_is_a_decision_not_an_absence(self):
        # Version 3 answers the question directly, so "uncovered" is False —
        # never None, which would hand the decision back to dupatta inference.
        assert explicit_head_covering_decision(a_v3_selections_dict(head_covering="uncovered")) is (
            False
        )

    def test_an_unanswered_head_question_leaves_the_decision_to_the_caller(self):
        assert explicit_head_covering_decision(a_v3_selections_dict(head_covering=None)) is None
        # A version-1/2 multi-select cannot express "uncovered" at all, so its
        # absence must stay None and never become a decision.
        assert explicit_head_covering_decision(a_selections_dict(coverage_preferences=[])) is None

    def test_the_accessors_are_total_over_a_model_instance_too(self):
        model = SourceSelectionsV3.model_validate(a_v3_selections_dict())
        assert ordered_colour_values(model) == ("deep_maroon", "antique_gold")
        assert explicit_head_covering_decision(model) is True
        assert covered_midriff_requested(model)


class TestDemoDesignSpecEngineForV3:
    def test_it_builds_a_valid_version_three_spec(self):
        spec = _v3_spec()
        assert isinstance(spec, DesignSpecV3)
        assert spec.schema_version == 3
        assert spec.source_selections.fabric_colour == "deep_maroon"

    def test_it_is_deterministic(self):
        context = a_v3_context()
        first = build_demo_design_spec(context)
        second = build_demo_design_spec(context)
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_the_colour_narrative_names_each_role(self):
        spec = _v3_spec()
        placement = spec.colour_story.placement
        assert "the main fabric" in placement
        assert "the embroidery and surface work" in placement
        assert "takes the main fabric's colour" in placement

    def test_a_bride_supplied_hex_reaches_the_brief(self):
        spec = _v3_spec(fabric_colour="#a1b2c3", custom_colours=["#a1b2c3"])
        assert "#a1b2c3" in spec.colour_story.placement

    def test_each_body_area_answer_reaches_the_coverage_narrative(self):
        spec = _v3_spec(
            sleeves="elbow_sleeve",
            back_coverage="open_back",
            midriff="semi_sheer_midriff",
            head_covering="hijab",
        )
        coverage = spec.coverage_and_drape
        assert "elbow-length sleeves" in coverage.sleeves
        assert "left open at the back" in coverage.back_and_midriff
        assert "veiled in sheer fabric" in coverage.back_and_midriff
        assert "hijab" in coverage.head_covering

    def test_an_explicit_uncovered_head_wins_over_a_head_draped_dupatta(self):
        spec = _v3_spec(head_covering="uncovered", dupatta_style="head_drape")
        assert "left uncovered" in spec.coverage_and_drape.head_covering


class TestImagePromptForV3:
    def test_the_prompt_names_a_colour_per_role_and_not_a_palette(self):
        prompt = build_image_prompt(_v3_spec())
        assert "The chosen colours are the main fabric in deep maroon" in prompt
        assert "the embroidery and surface work in antique gold" in prompt
        assert "the dupatta in the same colour as the main fabric" in prompt
        assert "colour palette, in order" not in prompt

    def test_a_bride_supplied_hex_is_rendered_as_a_literal_colour_code(self):
        prompt = build_image_prompt(_v3_spec(fabric_colour="#a1b2c3", custom_colours=["#a1b2c3"]))
        assert "the main fabric in the exact colour code #a1b2c3" in prompt

    def test_an_unused_custom_colour_is_never_rendered_as_a_requirement(self):
        prompt = build_image_prompt(_v3_spec(custom_colours=["#0f0f0f"]))
        assert "#0f0f0f" not in prompt

    def test_every_answered_body_area_becomes_a_visual_requirement(self):
        prompt = build_image_prompt(_v3_spec())
        directive = prompt.split("\n\n")[1]
        assert directive.startswith("Coverage requirements that must be clearly visible")
        assert "full-length sleeves reaching the wrists" in directive
        assert "a covered back that is not left open" in directive
        assert "the midriff kept covered" in directive
        assert "over the head like a veil" in directive

    def test_a_less_covered_answer_is_stated_rather_than_silently_dropped(self):
        prompt = build_image_prompt(
            _v3_spec(sleeves="sleeveless", back_coverage="open_back", midriff="bare_midriff")
        )
        directive = prompt.split("\n\n")[1]
        assert "a sleeveless bodice" in directive
        assert "an open back" in directive
        assert "a bare midriff" in directive

    def test_a_canonically_answered_area_suppresses_its_generated_narrative(self):
        prompt = build_image_prompt(_v3_spec())
        assert "Sleeves:" not in prompt
        assert "Head covering:" not in prompt
        assert "Back and midriff:" not in prompt

    def test_an_unanswered_area_keeps_its_generated_narrative(self):
        prompt = build_image_prompt(_v3_spec(sleeves=None))
        assert "Sleeves:" in prompt

    def test_a_hijab_renders_its_own_covering_rather_than_a_dupatta(self):
        prompt = build_image_prompt(_v3_spec(head_covering="hijab"))
        assert "a hijab completely covering the hair and neck" in prompt

    def test_an_explicit_uncovered_head_is_honoured_over_a_head_draped_dupatta(self):
        prompt = build_image_prompt(_v3_spec(head_covering="uncovered", dupatta_style="head_drape"))
        assert "over the head like a veil" not in prompt
        assert "the head covered with no hair visible" not in prompt

    def test_the_closing_reinforcement_restates_only_the_coverage_worth_defending(self):
        prompt = build_image_prompt(
            _v3_spec(sleeves="sleeveless", back_coverage="open_back", midriff="bare_midriff")
        )
        closing = prompt.rsplit("\n\n", 1)[-1]
        assert "a sleeveless bodice" not in closing
        assert "an open back" not in closing


class TestDemoAssetSelectionForV3:
    def test_coverage_tags_project_onto_the_manifest_vocabulary(self):
        tags = coverage_tags_for_selections(a_v3_selections_dict())
        assert tags == ("full_sleeves", "full_back", "full_midriff", "head_drape_preferred")

    def test_an_answer_with_no_equivalent_tag_projects_to_nothing(self):
        tags = coverage_tags_for_selections(
            a_v3_selections_dict(
                sleeves=None,
                back_coverage="open_back",
                midriff="bare_midriff",
                head_covering="uncovered",
            )
        )
        assert tags == ()

    def test_a_covered_head_selection_never_takes_an_uncovered_head_asset(self):
        spec = _v3_spec(head_covering="veil_style", dupatta_style="front_drape")
        manifest = _manifest(
            _lehenga_asset(
                "bare-head-lehenga",
                coverage_preferences=["full_sleeves", "full_midriff"],
                dupatta_styles=["front_drape"],
            )
        )
        with pytest.raises(DemoAssetUnavailable):
            select_demo_asset(spec, build_image_prompt(spec), manifest)

    def test_an_explicitly_uncovered_head_is_not_narrowed_by_a_double_dupatta(self):
        # REL-001 regression: the selector's fail-closed head filter must honour
        # the same authority rule as the prompt builder. Before the fix a v3
        # design that explicitly chose an uncovered head was still narrowed to
        # covered-head assets by its dupatta styling alone — asking the image for
        # the opposite of the prompt, and failing the whole demo job closed when
        # no covered-head asset existed for that garment and ceremony.
        spec = _v3_spec(head_covering="uncovered", dupatta_style="double_dupatta")
        # The only asset shows neither a head-covering tag nor a head-draped
        # dupatta, so it survives only if the explicit "uncovered" answer beats
        # the design's own double-dupatta styling.
        manifest = _manifest(
            _lehenga_asset(
                "bare-head-lehenga",
                coverage_preferences=["full_sleeves", "full_midriff"],
                dupatta_styles=["front_drape"],
            )
        )
        prompt = build_image_prompt(spec)
        assert "the head covered with no hair visible" not in prompt
        assert select_demo_asset(spec, prompt, manifest).asset_id == "bare-head-lehenga"

    def test_a_head_draped_dupatta_still_covers_when_the_question_is_unanswered(self):
        spec = _v3_spec(head_covering=None, dupatta_style="head_drape")
        # An asset that shows neither a head-covering tag nor a head-draped
        # dupatta, so only the hard filter can exclude it.
        manifest = _manifest(
            _lehenga_asset(
                "bare-head-lehenga",
                coverage_preferences=["full_sleeves", "full_midriff"],
                dupatta_styles=["front_drape"],
            )
        )
        with pytest.raises(DemoAssetUnavailable):
            select_demo_asset(spec, build_image_prompt(spec), manifest)

    def test_a_covered_midriff_selection_never_takes_an_exposed_midriff_asset(self):
        spec = _v3_spec(head_covering="uncovered", dupatta_style="front_drape")
        manifest = _manifest(
            _lehenga_asset(
                "bare-midriff-lehenga",
                coverage_preferences=["full_sleeves"],
                dupatta_styles=["front_drape"],
            )
        )
        with pytest.raises(DemoAssetUnavailable):
            select_demo_asset(spec, build_image_prompt(spec), manifest)

    def test_a_v4_design_never_matches_an_incompatible_ceremony_asset(self):
        spec = _v3_spec(ceremony="anand_karaj")
        manifest = _manifest(
            _lehenga_asset(
                "nikah-only-lehenga",
                ceremonies=["nikah"],
                coverage_preferences=["full_sleeves", "full_midriff", "head_drape_preferred"],
            )
        )
        with pytest.raises(DemoAssetUnavailable):
            select_demo_asset(spec, build_image_prompt(spec), manifest)

    def test_a_compatible_asset_is_selected_deterministically(self):
        spec = _v3_spec()
        manifest = _manifest(
            _lehenga_asset(
                "covered-lehenga",
                coverage_preferences=["full_sleeves", "full_midriff", "head_drape_preferred"],
                dupatta_styles=["double_dupatta"],
            )
        )
        prompt = build_image_prompt(spec)
        first = select_demo_asset(spec, prompt, manifest)
        second = select_demo_asset(spec, prompt, manifest)
        assert first.asset_id == second.asset_id == "covered-lehenga"


@pytest.mark.django_db
@pytest.mark.usefixtures("inmemory_storage")
class TestV4DesignThroughTheWholeDemoPipeline:
    def test_a_v4_answered_design_generates_end_to_end_with_no_provider_call(self):
        # The acceptance evidence for T6: a design answered on questionnaire v4
        # goes context -> DesignSpec v3 -> deterministic prompt -> demo asset ->
        # permanent image through the SAME pipeline as every other design, with
        # no SDK client constructed anywhere (the generation suite's socket
        # guard proves the absence of network use).
        call_command("install_demo_asset_pack", "--dev-synthetic")
        design = factory.make_complete_v4_design()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        attempt = GenerationAttempt.objects.create(
            design=design, status=GenerationAttempt.Status.QUEUED, is_demo=True
        )

        result = run_generation_attempt(
            attempt.id, config=PipelineConfig(poll_interval_seconds=0.0, poll_max_attempts=10)
        )

        assert result.status == GenerationAttempt.Status.SUCCEEDED
        assert result.error_code == ""
        version = DesignVersion.objects.get(pk=result.design_version_id)
        assert version.design_spec_schema_version == 3
        assert version.prompt_builder_version == PROMPT_BUILDER_VERSION
        assert version.has_permanent_image
        # The v4 selections survived into the persisted, immutable audit record.
        assert version.design_spec["source_selections"]["fabric_colour"] == "deep_maroon"
        assert version.design_spec["source_selections"]["head_covering"] == "dupatta_over_head"
        assert "colour_palette" not in version.design_spec["source_selections"]
        # ...and the prompt names the roles rather than a palette.
        assert "The chosen colours are the main fabric in deep maroon" in version.image_prompt
