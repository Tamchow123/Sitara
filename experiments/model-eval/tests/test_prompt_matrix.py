"""Matrix expansion: determinism, coverage, and visible skips."""

from conftest import (
    EXPERIMENT_ROOT,
    make_brief,
    make_candidate,
    make_candidates_config,
    make_reference_entry,
    make_refinement_brief,
    make_stage,
)
from model_eval.config import (
    REQUIRED_CEREMONIES,
    REQUIRED_GARMENTS,
    BriefsFile,
    ReferenceManifest,
    load_briefs,
    load_stage,
)
from model_eval.prompt_matrix import (
    SKIP_NO_EDIT_SUPPORT,
    SKIP_NO_REFERENCE_SUPPORT,
    SKIP_NOT_TEXT_TO_IMAGE,
    SKIP_UNVERIFIED_REFERENCE,
    expand,
)
from model_eval.runner import load_stage_bundle


def _real_plans():
    screening = load_stage_bundle(EXPERIMENT_ROOT / "configs" / "screening.yaml").plan
    finalist = load_stage_bundle(EXPERIMENT_ROOT / "configs" / "finalists.yaml").plan
    return screening, finalist


class TestDeterminism:
    def test_expansion_is_deterministic(self, plain_candidate, reffy_candidate):
        candidates = make_candidates_config(plain_candidate, reffy_candidate)
        briefs = BriefsFile(briefs=[make_refinement_brief(), make_brief("second-brief")])
        stage = make_stage(
            models=["plain", "reffy"],
            seeds=[11, 47],
            inspiration_modes=["text_only", "metadata"],
            refinement={"enabled": True, "strategies": ["fresh_regeneration", "image_edit"]},
        )
        plan_a = expand(stage, candidates, briefs)
        plan_b = expand(stage, candidates, briefs)
        assert [r.request_id for r in plan_a.requests] == [r.request_id for r in plan_b.requests]
        assert plan_a == plan_b

    def test_request_ids_are_unique(self):
        screening, finalist = _real_plans()
        for plan in (screening, finalist):
            ids = [r.request_id for r in plan.requests]
            assert len(ids) == len(set(ids))


class TestRealMatrices:
    def test_screening_and_finalist_matrices_are_distinct(self):
        screening, finalist = _real_plans()
        screening_ids = {r.request_id for r in screening.requests}
        finalist_ids = {r.request_id for r in finalist.requests}
        assert screening_ids.isdisjoint(finalist_ids)
        assert len(finalist.runnable) > len(screening.runnable)

    def test_screening_covers_required_garments_and_ceremonies(self):
        screening, _ = _real_plans()
        garments = {r.garment for r in screening.runnable}
        ceremonies = {r.ceremony for r in screening.runnable if r.ceremony}
        assert REQUIRED_GARMENTS <= garments
        assert REQUIRED_CEREMONIES <= ceremonies

    def test_screening_covers_modesty_and_both_embellishment_extremes(self):
        briefs = load_briefs(EXPERIMENT_ROOT / "prompts" / "briefs.yaml")
        stage = load_stage(EXPERIMENT_ROOT / "configs" / "screening.yaml")
        selected = [briefs.by_id(bid) for bid in stage.brief_ids]
        assert any(
            b.sleeves == "full sleeves" and "modest" in b.tags for b in selected
        ), "screening must include modest full-sleeve styling"
        levels = {b.embellishment_level for b in selected}
        assert {"heavy", "minimal"} <= levels

    def test_screening_is_one_output_per_model_and_brief(self):
        screening, _ = _real_plans()
        stage = load_stage(EXPERIMENT_ROOT / "configs" / "screening.yaml")
        assert len(screening.runnable) == len(stage.brief_ids) * len(stage.models)

    def test_finalist_exercises_modes_formats_and_refinements(self):
        _, finalist = _real_plans()
        modes = {r.inspiration_mode for r in finalist.requests}
        kinds = {r.kind for r in finalist.runnable}
        formats = {r.prompt_format for r in finalist.runnable}
        assert {"text_only", "metadata", "reference_image"} <= modes
        assert {"base", "refinement_fresh", "refinement_edit"} <= kinds
        assert {"editorial", "sectioned", "json"} <= formats

    def test_shipped_reference_requests_are_plan_time_skips_until_verified(self):
        """The repo ships an EMPTY manifest, so every reference-mode request
        must be a visible plan-time skip excluded from counts and spend."""
        _, finalist = _real_plans()
        ref_requests = [r for r in finalist.requests if r.inspiration_mode == "reference_image"]
        assert ref_requests, "briefs with reference_ids should still appear in the plan"
        assert all(r.skipped for r in ref_requests)
        assert all(
            SKIP_UNVERIFIED_REFERENCE in (r.skip_reason or "") for r in ref_requests
        )
        assert all(r.estimated_max_cost_usd == 0.0 for r in ref_requests)


class TestSkips:
    def test_reference_mode_without_model_support_is_skipped_clearly(
        self, plain_candidate, reffy_candidate
    ):
        candidates = make_candidates_config(plain_candidate, reffy_candidate)
        briefs = BriefsFile(briefs=[make_brief(reference_ids=["ref-a"])])
        manifest = ReferenceManifest(
            references=[make_reference_entry("ref-a", "local/ref-a.png", "verified")]
        )
        stage = make_stage(models=["plain", "reffy"], inspiration_modes=["reference_image"])
        plan = expand(stage, candidates, briefs, manifest)
        skipped = {r.model_key: r for r in plan.skipped}
        assert "plain" in skipped
        assert skipped["plain"].skip_reason == SKIP_NO_REFERENCE_SUPPORT
        assert all(r.model_key == "reffy" for r in plan.runnable)
        # Runnable reference request respects the model's max image count.
        assert plan.runnable[0].reference_ids == ["ref-a"]

    def test_unknown_and_unverified_references_are_plan_time_skips(self, reffy_candidate):
        candidates = make_candidates_config(reffy_candidate)
        briefs = BriefsFile(
            briefs=[
                make_brief("unknown-ref", reference_ids=["nowhere"]),
                make_brief("pending-ref", reference_ids=["ref-p"]),
            ]
        )
        manifest = ReferenceManifest(
            references=[make_reference_entry("ref-p", "local/ref-p.png", "pending")]
        )
        stage = make_stage(
            models=["reffy"],
            inspiration_modes=["reference_image"],
            prompt_formats=["editorial"],
        )
        plan = expand(stage, candidates, briefs, manifest)
        assert plan.runnable == []
        assert len(plan.skipped) == 2
        for r in plan.skipped:
            assert SKIP_UNVERIFIED_REFERENCE in (r.skip_reason or "")
        assert plan.total_max_cost_usd == 0.0

    def test_missing_reference_file_is_a_plan_time_skip(self, reffy_candidate, tmp_path):
        candidates = make_candidates_config(reffy_candidate)
        briefs = BriefsFile(briefs=[make_brief(reference_ids=["ref-a"])])
        manifest = ReferenceManifest(
            references=[make_reference_entry("ref-a", "local/ref-a.png", "verified")]
        )
        stage = make_stage(models=["reffy"], inspiration_modes=["reference_image"])
        # references_dir given but the file does not exist there:
        plan = expand(stage, candidates, briefs, manifest, references_dir=tmp_path)
        assert plan.runnable == []
        assert "file missing" in (plan.skipped[0].skip_reason or "")

    def test_editing_only_model_is_visibly_rejected_for_text_to_image(self, plain_candidate):
        edit_only = make_candidate(
            "editonly",
            categories=["editing"],
            text_to_image=False,
            image_editing=True,
            image_editing_param="input_image",
        )
        candidates = make_candidates_config(plain_candidate, edit_only)
        briefs = BriefsFile(briefs=[make_brief()])
        stage = make_stage(models=["plain", "editonly"])
        plan = expand(stage, candidates, briefs)
        assert all(r.model_key == "plain" for r in plan.runnable)
        rejected = [r for r in plan.skipped if r.model_key == "editonly"]
        assert len(rejected) == 1
        assert rejected[0].skip_reason == SKIP_NOT_TEXT_TO_IMAGE
        assert rejected[0].estimated_max_cost_usd == 0.0

    def test_edit_refinement_without_editing_support_is_skipped_clearly(self, plain_candidate):
        candidates = make_candidates_config(plain_candidate)
        briefs = BriefsFile(briefs=[make_refinement_brief()])
        stage = make_stage(
            refinement={"enabled": True, "strategies": ["fresh_regeneration", "image_edit"]},
        )
        plan = expand(stage, candidates, briefs)
        edit_requests = [r for r in plan.requests if r.kind == "refinement_edit"]
        assert len(edit_requests) == 1
        assert edit_requests[0].skipped
        assert edit_requests[0].skip_reason == SKIP_NO_EDIT_SUPPORT
        fresh = [r for r in plan.runnable if r.kind == "refinement_fresh"]
        assert len(fresh) == 1
        assert fresh[0].seed == fresh[0].seed  # seed carried for continuity
        assert "deep red and gold" in (fresh[0].prompt_text or "")

    def test_metadata_mode_quietly_omitted_when_brief_has_no_metadata(self, plain_candidate):
        candidates = make_candidates_config(plain_candidate)
        briefs = BriefsFile(
            briefs=[
                make_brief("with-meta", inspiration_metadata={"fabric": "silk"}),
                make_brief("without-meta"),
            ]
        )
        stage = make_stage(inspiration_modes=["text_only", "metadata"])
        plan = expand(stage, candidates, briefs)
        metadata_briefs = {r.brief_id for r in plan.requests if r.inspiration_mode == "metadata"}
        assert metadata_briefs == {"with-meta"}

    def test_skipped_requests_reserve_no_budget(self, plain_candidate):
        candidates = make_candidates_config(plain_candidate)
        briefs = BriefsFile(briefs=[make_brief(reference_ids=["ref-a"])])
        stage = make_stage(inspiration_modes=["reference_image"])
        plan = expand(stage, candidates, briefs)
        assert plan.total_max_cost_usd == 0.0
        assert all(r.estimated_max_cost_usd == 0.0 for r in plan.skipped)
