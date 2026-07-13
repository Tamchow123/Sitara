"""Configuration validation, including the real shipped config files."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import EXPERIMENT_ROOT, make_brief, make_candidate
from model_eval.config import (
    Capabilities,
    ModelCandidate,
    Pricing,
    RefinementChange,
    load_briefs,
    load_candidates,
    load_reference_manifest,
    load_stage,
)

TODAY_KWARGS = dict(checked_on="2026-07-13", source_url="https://example.com")


class TestCandidateValidation:
    def test_malformed_replicate_id_rejected(self):
        data = make_candidate("bad").model_dump(mode="json")
        data["replicate_id"] = "not-a-model-id"
        with pytest.raises(ValidationError, match="owner/model"):
            ModelCandidate.model_validate(data)

    def test_reference_category_requires_capability(self):
        with pytest.raises(ValidationError, match="reference"):
            make_candidate("bad", categories=["reference"])

    def test_editing_category_requires_capability(self):
        with pytest.raises(ValidationError, match="editing"):
            make_candidate("bad", categories=["editing"])

    def test_editing_only_model_cannot_fill_text_to_image_categories(self):
        for category in ("fast", "balanced", "highest_quality"):
            with pytest.raises(ValidationError, match="editing-only"):
                make_candidate(
                    "bad",
                    categories=[category],
                    text_to_image=False,
                )

    def test_negative_prompt_requires_param(self):
        with pytest.raises(ValidationError, match="negative_prompt_param"):
            Capabilities(negative_prompt=True)

    def test_reference_requires_param_and_count(self):
        with pytest.raises(ValidationError, match="reference_image_param"):
            Capabilities(reference_image=True)
        with pytest.raises(ValidationError, match="max_reference_images"):
            Capabilities(reference_image=True, reference_image_param="image")

    def test_max_cost_must_dominate_expected(self):
        with pytest.raises(ValidationError, match="max_cost_per_generation_usd"):
            Pricing(
                unit="per_image",
                usd_per_unit=0.04,
                expected_cost_per_generation_usd=0.05,
                max_cost_per_generation_usd=0.01,
                **TODAY_KWARGS,
            )

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            Pricing(
                unit="per_image",
                usd_per_unit=0.04,
                expected_cost_per_generation_usd=0.04,
                max_cost_per_generation_usd=0.1,
                surprise_field=1,
                **TODAY_KWARGS,
            )


class TestBriefValidation:
    def test_refinement_from_value_must_match_current(self):
        with pytest.raises(ValidationError, match="does not match"):
            make_brief(
                "bad-brief",
                palette="deep red and gold",
                refinement=RefinementChange(
                    id="wrong",
                    field="palette",
                    from_value="ivory",
                    to_value="green",
                ),
            )

    def test_refinement_description_is_derived_from_the_change(self):
        change = RefinementChange(
            id="ivory-to-red",
            field="palette",
            from_value="ivory",
            to_value="deep red",
        )
        assert change.description == "the colour palette from ivory to deep red"

    def test_refinement_must_actually_change_something(self):
        with pytest.raises(ValidationError, match="equals"):
            RefinementChange(id="noop", field="palette", from_value="red", to_value="red")

    def test_brief_id_must_be_slug(self):
        with pytest.raises(ValidationError, match="slug"):
            make_brief("Bad Brief Id!")


class TestShippedConfigs:
    """The committed YAML must always validate."""

    def test_candidates_file_valid(self):
        config = load_candidates(EXPERIMENT_ROOT / "configs" / "model_candidates.yaml")
        assert not config.requires_manual_verification
        categories = {c for cand in config.candidates for c in cand.categories}
        assert {"fast", "balanced", "highest_quality", "reference", "editing"} <= categories
        # At least one candidate can take reference images AND one can edit.
        assert any(c.capabilities.reference_image for c in config.candidates)
        assert any(c.capabilities.image_editing for c in config.candidates)

    def test_briefs_file_valid(self):
        briefs = load_briefs(EXPERIMENT_ROOT / "prompts" / "briefs.yaml")
        assert len(briefs.briefs) >= 24
        # Culturally uncertain briefs are flagged, not presented as verified.
        assert any(b.cultural_review for b in briefs.briefs)
        # No typo regressions in fabric wording.
        assert not any("kameex" in b.fabric for b in briefs.briefs)

    def test_shipped_candidates_are_all_text_to_image(self):
        config = load_candidates(EXPERIMENT_ROOT / "configs" / "model_candidates.yaml")
        assert all(c.capabilities.text_to_image for c in config.candidates), (
            "editing-only candidates need an editor-only experiment design, "
            "not a place in the text-to-image evaluation"
        )

    def test_smoke_config_plans_exactly_one_bounded_request(self):
        """The smoke config must be a genuine single-request live test:
        1 runnable request, 0 skips, flux-2-pro only, $0.12 ceiling."""
        from model_eval.runner import load_stage_bundle, plan_summary

        bundle = load_stage_bundle(EXPERIMENT_ROOT / "configs" / "smoke.yaml")
        summary = plan_summary(bundle, 0.12)
        assert summary["planned_requests"] == 1
        assert summary["skipped_requests"] == 0
        assert summary["models"] == {"flux-2-pro": 1}
        assert summary["conservative_max_spend_usd"] == pytest.approx(0.12)
        assert summary["within_budget"] is True
        assert summary["preflight_warnings"] == []
        request = bundle.plan.runnable[0]
        assert request.brief_id == "scr-shalwar-kameez-nikah-modest"
        assert request.prompt_format == "editorial"
        assert request.inspiration_mode == "text_only"
        assert request.seed == 11
        assert request.aspect_ratio == "3:4"
        assert request.kind == "base"

    def test_stage_configs_valid_and_consistent(self):
        for name in ("screening.yaml", "finalists.yaml", "seed_stability.yaml", "smoke.yaml"):
            stage = load_stage(EXPERIMENT_ROOT / "configs" / name)
            candidates = load_candidates(
                EXPERIMENT_ROOT / "configs" / stage.candidates_file
            )
            briefs = load_briefs((EXPERIMENT_ROOT / "configs" / stage.briefs_file).resolve())
            for key in stage.models:
                candidates.by_key(key)
            if stage.brief_ids != "all":
                for bid in stage.brief_ids:
                    briefs.by_id(bid)

    def test_reference_manifest_valid_and_ships_empty(self):
        manifest = load_reference_manifest(EXPERIMENT_ROOT / "references" / "manifest.yaml")
        assert manifest.references == []
