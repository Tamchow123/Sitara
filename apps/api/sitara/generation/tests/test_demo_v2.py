"""Phase 16B demo-engine support for the expanded questionnaire taxonomy.

The deterministic demo engine must produce the SAME DesignSpec schema version
and canonical source selections as the live structured provider for the v3
questionnaire (satin, Anand Karaj, the dedicated neckline, expanded colours,
corrected head/midriff semantics), and the selector must fail closed rather than
show a misleading image for a covered-head, full-midriff or Anand Karaj request.
"""

import hashlib

import pytest

from sitara.generation.context import GenerationContext
from sitara.generation.demo.design_spec_engine import build_demo_design_spec
from sitara.generation.demo.manifest import DemoManifest
from sitara.generation.demo.selector import DemoAssetUnavailable, select_demo_asset
from sitara.generation.demo.synthetic_pack import build_synthetic_demo_pack
from sitara.generation.design_spec import validate_design_spec
from sitara.generation.prompt_builder import build_image_prompt

from .demo_context_utils import EMPTY_INSPIRATION_SNAPSHOT, a_selections_dict


def _v2_context(**overrides) -> GenerationContext:
    fields = {
        "ceremony": "anand_karaj",
        "colour_palette": ["ruby", "gold"],
        "fabrics": ["satin", "organza"],
        "coverage_preferences": ["full_sleeves", "full_midriff", "head_drape_preferred"],
        "dupatta_style": "double_dupatta",
        "neckline_style": "high_neck",
        **overrides,
    }
    selections = a_selections_dict(**fields)
    return GenerationContext(
        source_selections=selections,
        trusted_answers=[],
        untrusted_texts=[],
        inspiration_context=EMPTY_INSPIRATION_SNAPSHOT,
        inspiration_cues=[],
        design_spec_schema_version=2,
    )


class TestDemoProducesV2:
    def test_demo_spec_matches_the_v2_contract(self):
        payload = build_demo_design_spec(_v2_context())
        spec = validate_design_spec(payload)
        assert spec.schema_version == 2
        assert spec.source_selections.neckline_style == "high_neck"
        assert spec.source_selections.ceremony == "anand_karaj"
        assert spec.source_selections.fabrics == ["satin", "organza"]
        assert spec.source_selections.colour_palette == ["ruby", "gold"]

    def test_canonical_neckline_drives_the_narrative(self):
        payload = build_demo_design_spec(_v2_context(neckline_style="sweetheart_neck"))
        assert "sweetheart" in payload["coverage_and_drape"]["neckline"].lower()

    def test_satin_is_phrased_distinctly_from_silk(self):
        payload = build_demo_design_spec(_v2_context())
        blob = " ".join(entry["fabric"] for entry in payload["fabrics_and_texture"]).lower()
        assert "satin" in blob

    def test_corrected_midriff_and_head_semantics(self):
        payload = build_demo_design_spec(_v2_context())
        cd = payload["coverage_and_drape"]
        assert "midriff" in cd["back_and_midriff"].lower()
        assert "no bare skin" in cd["back_and_midriff"].lower()
        assert "covered" in cd["head_covering"].lower()

    def test_deterministic(self):
        assert build_demo_design_spec(_v2_context()) == build_demo_design_spec(_v2_context())

    def test_a_new_colour_is_phrased(self):
        payload = build_demo_design_spec(_v2_context(colour_palette=["dusty_rose", "champagne"]))
        assert "dusty rose" in payload["colour_story"]["palette_summary"].lower()


class TestNecklineScoring:
    """The neckline is a scored (soft) dimension: a matching-neckline asset must
    beat an otherwise-identical asset that does not tag the selected neckline."""

    def _two_neckline_manifest(self) -> DemoManifest:
        base = {
            "asset_id": "",
            "filename": "",
            "sha256": "",
            "size_bytes": 50_000,
            "width": 768,
            "height": 1024,
            "alt_text": "Placeholder alt text well over the minimum length for testing.",
            "garment_types": ["lehenga"],
            "ceremonies": ["baraat"],
            "silhouettes": ["flared_lehenga"],
            "colours": ["red"],
            "fabrics": ["silk"],
            "embellishment_styles": ["zardozi"],
            "embellishment_densities": ["heavy"],
            "coverage_preferences": ["full_sleeves"],
            "necklines": [],
            "dupatta_styles": ["head_drape"],
            "saree_drapes": [],
            "regional_styles": [],
            "provenance_status": "synthetic_development_placeholder",
        }
        match = {
            **base,
            "asset_id": "lehenga-match",
            "filename": "lehenga-match.webp",
            "sha256": hashlib.sha256(b"match").hexdigest(),
            "necklines": ["v_neck"],
        }
        nomatch = {
            **base,
            "asset_id": "lehenga-nomatch",
            "filename": "lehenga-nomatch.webp",
            "sha256": hashlib.sha256(b"nomatch").hexdigest(),
            "necklines": ["boat_neck"],
        }
        return DemoManifest.model_validate(
            {"schema_version": 2, "pack_id": "neckline-pack", "assets": [match, nomatch]}
        )

    def test_matching_neckline_asset_is_preferred(self):
        manifest = self._two_neckline_manifest()
        spec = validate_design_spec(
            build_demo_design_spec(
                _v2_context(
                    garment_type="lehenga",
                    ceremony="baraat",
                    colour_palette=["red"],
                    fabrics=["silk"],
                    coverage_preferences=["full_sleeves"],
                    dupatta_style="head_drape",
                    neckline_style="v_neck",
                )
            )
        )
        prompt = build_image_prompt(spec)
        selection = select_demo_asset(spec, prompt, manifest)
        assert selection.asset_id == "lehenga-match"


class TestSelectorFailClosed:
    def _spec_and_prompt(self, **overrides):
        payload = build_demo_design_spec(_v2_context(**overrides))
        spec = validate_design_spec(payload)
        return spec, build_image_prompt(spec)

    def _without(self, manifest: DemoManifest, asset_id: str) -> DemoManifest:
        data = manifest.model_dump(mode="json")
        data["assets"] = [a for a in data["assets"] if a["asset_id"] != asset_id]
        return DemoManifest.model_validate(data)

    def test_anand_karaj_selects_the_approved_asset(self):
        manifest, _images = build_synthetic_demo_pack()
        spec, prompt = self._spec_and_prompt()
        selection = select_demo_asset(spec, prompt, manifest)
        assert selection.asset_id == "lehenga-anand-karaj-dev-007"

    def test_anand_karaj_fails_closed_without_an_approved_asset(self):
        manifest, _images = build_synthetic_demo_pack()
        reduced = self._without(manifest, "lehenga-anand-karaj-dev-007")
        spec, prompt = self._spec_and_prompt()
        with pytest.raises(DemoAssetUnavailable):
            select_demo_asset(spec, prompt, reduced)

    def test_covered_head_never_matches_an_uncovered_head_asset(self):
        # A baraat lehenga wanting a covered head: only assets that show a
        # covered head may match. Strip the covered-head assets and expect a
        # controlled failure rather than an uncovered-head fallback.
        manifest, _images = build_synthetic_demo_pack()
        spec, prompt = self._spec_and_prompt(
            ceremony="baraat",
            coverage_preferences=["full_sleeves", "head_drape_preferred"],
            dupatta_style="head_drape",
        )
        # Remove every lehenga asset that shows a covered head.
        data = manifest.model_dump(mode="json")
        data["assets"] = [
            a
            for a in data["assets"]
            if not (
                "lehenga" in a["garment_types"]
                and (
                    "head_drape_preferred" in a["coverage_preferences"]
                    or {"head_drape", "double_dupatta"} & set(a["dupatta_styles"])
                )
            )
        ]
        stripped = DemoManifest.model_validate(data)
        with pytest.raises(DemoAssetUnavailable):
            select_demo_asset(spec, prompt, stripped)

    def test_full_midriff_never_matches_an_exposed_midriff_asset(self):
        manifest, _images = build_synthetic_demo_pack()
        # A gharara wanting a fully covered midriff: no gharara asset in the
        # synthetic pack tags full_midriff, so selection must fail closed rather
        # than return the exposed-midriff gharara asset.
        spec, prompt = self._spec_and_prompt(
            garment_type="gharara",
            ceremony="mehndi",
            coverage_preferences=["three_quarter_sleeves", "full_midriff"],
            dupatta_style="one_shoulder",
            neckline_style="v_neck",
        )
        with pytest.raises(DemoAssetUnavailable):
            select_demo_asset(spec, prompt, manifest)

    def test_deterministic_selection_for_the_same_v2_input(self):
        manifest, _images = build_synthetic_demo_pack()
        spec, prompt = self._spec_and_prompt()
        first = select_demo_asset(spec, prompt, manifest)
        second = select_demo_asset(spec, prompt, manifest)
        assert first == second
