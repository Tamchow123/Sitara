"""The deterministic demo-asset selector."""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from sitara.generation.demo.manifest import DemoManifest
from sitara.generation.demo.selector import (
    DEMO_SELECTOR_VERSION,
    DemoAssetSelection,
    DemoAssetUnavailable,
    _tie_break_key,
    select_demo_asset,
)
from sitara.generation.demo.synthetic_pack import build_synthetic_demo_pack
from sitara.generation.design_spec import DesignSpec
from sitara.generation.prompt_builder import build_image_prompt

from .utils import a_valid_spec_dict, mutate


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _asset_dict(asset_id: str, **overrides) -> dict:
    base = {
        "asset_id": asset_id,
        "filename": f"{asset_id}.webp",
        "sha256": _sha(asset_id),
        "size_bytes": 50_000,
        "width": 768,
        "height": 1024,
        "alt_text": f"Placeholder alt text for {asset_id}, well over the minimum length.",
        "garment_types": ["lehenga"],
        "ceremonies": ["baraat"],
        "silhouettes": ["flared_lehenga"],
        "colours": ["red"],
        "fabrics": ["silk"],
        "embellishment_styles": ["zardozi"],
        "embellishment_densities": ["heavy"],
        "coverage_preferences": ["full_sleeves"],
        "dupatta_styles": ["head_drape"],
        "saree_drapes": [],
        "regional_styles": [],
        "provenance_status": "synthetic_development_placeholder",
    }
    base.update(overrides)
    return base


def _two_lehenga_manifest() -> DemoManifest:
    return DemoManifest.model_validate(
        {
            "schema_version": 1,
            "pack_id": "test-selector-pack",
            "assets": [
                _asset_dict(
                    "lehenga-a",
                    ceremonies=["baraat"],
                    colours=["red", "gold"],
                    fabrics=["silk"],
                ),
                _asset_dict(
                    "lehenga-b",
                    ceremonies=["reception"],
                    colours=["blue", "silver"],
                    fabrics=["georgette"],
                ),
            ],
        }
    )


def _spec_for(**overrides) -> DesignSpec:
    data = mutate(a_valid_spec_dict(), **{})
    if overrides:
        data["source_selections"] = mutate(data["source_selections"], **overrides)
    return DesignSpec.model_validate(data)


def _saree_spec() -> DesignSpec:
    return _spec_for(
        garment_type="saree",
        silhouette="classic_saree_drape",
        saree_drape="nivi_drape",
        dupatta_style=None,
    )


class TestHardFiltering:
    def test_exact_garment_compatibility_is_required(self):
        manifest, _images = build_synthetic_demo_pack()
        spec = _saree_spec()
        prompt = build_image_prompt(spec)
        selection = select_demo_asset(spec, prompt, manifest)
        assert selection.asset_id == "saree-nikah-dev-002"

    def test_incompatible_garment_never_falls_back(self):
        manifest, _images = build_synthetic_demo_pack()
        data = manifest.model_dump(mode="json")
        data["assets"] = [a for a in data["assets"] if "lehenga" not in a["garment_types"]]
        stripped_manifest = DemoManifest.model_validate(data)
        spec = _spec_for(garment_type="lehenga", silhouette="flared_lehenga")
        prompt = build_image_prompt(spec)
        with pytest.raises(DemoAssetUnavailable):
            select_demo_asset(spec, prompt, stripped_manifest)

    def test_no_compatible_asset_raises_controlled_error(self):
        manifest = DemoManifest.model_validate(
            {
                "schema_version": 1,
                "pack_id": "test-empty-garment-pack",
                "assets": [_asset_dict("lehenga-only", garment_types=["lehenga"])],
            }
        )
        spec = _saree_spec()
        prompt = build_image_prompt(spec)
        with pytest.raises(DemoAssetUnavailable):
            select_demo_asset(spec, prompt, manifest)


class TestScoring:
    def test_better_matching_asset_is_selected(self):
        manifest = _two_lehenga_manifest()
        spec = _spec_for(
            ceremony="reception",
            colour_palette=["blue", "silver"],
            fabrics=["georgette"],
        )
        prompt = build_image_prompt(spec)
        selection = select_demo_asset(spec, prompt, manifest)
        assert selection.asset_id == "lehenga-b"

    def test_other_asset_wins_with_opposite_preferences(self):
        manifest = _two_lehenga_manifest()
        spec = _spec_for(
            ceremony="baraat",
            colour_palette=["red", "gold"],
            fabrics=["silk"],
        )
        prompt = build_image_prompt(spec)
        selection = select_demo_asset(spec, prompt, manifest)
        assert selection.asset_id == "lehenga-a"


class TestDeterminism:
    def test_same_input_selects_same_asset_repeatedly(self):
        manifest, _images = build_synthetic_demo_pack()
        spec = _spec_for()
        prompt = build_image_prompt(spec)
        first = select_demo_asset(spec, prompt, manifest)
        second = select_demo_asset(spec, prompt, manifest)
        assert first == second

    def test_selection_provenance_shape(self):
        manifest, _images = build_synthetic_demo_pack()
        spec = _spec_for()
        prompt = build_image_prompt(spec)
        selection = select_demo_asset(spec, prompt, manifest)
        assert isinstance(selection, DemoAssetSelection)
        assert selection.selector_version == DEMO_SELECTOR_VERSION
        assert selection.manifest_schema_version == manifest.schema_version
        assert len(selection.manifest_hash) == 64

    def test_manifest_change_alters_selection_fingerprint(self):
        manifest, _images = build_synthetic_demo_pack()
        spec = _spec_for()
        prompt = build_image_prompt(spec)
        first = select_demo_asset(spec, prompt, manifest)

        data = manifest.model_dump(mode="json")
        data["assets"][0]["alt_text"] = data["assets"][0]["alt_text"] + " Changed."
        changed_manifest = DemoManifest.model_validate(data)
        second = select_demo_asset(spec, prompt, changed_manifest)
        assert first.manifest_hash != second.manifest_hash

    def test_same_input_selects_same_asset_across_separate_processes(self):
        # PYTHONHASHSEED varies Python's per-process string-hash randomisation
        # (used by dict/set iteration order); the selector must be immune to
        # it, in any process, on any run.
        spec_json = json.dumps(a_valid_spec_dict())
        script = (
            "import django, os\n"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n"
            "django.setup()\n"
            "import json\n"
            "from sitara.generation.demo.synthetic_pack import build_synthetic_demo_pack\n"
            "from sitara.generation.demo.selector import select_demo_asset\n"
            "from sitara.generation.design_spec import DesignSpec\n"
            "from sitara.generation.prompt_builder import build_image_prompt\n"
            "manifest, _images = build_synthetic_demo_pack()\n"
            f"spec = DesignSpec.model_validate(json.loads({spec_json!r}))\n"
            "prompt = build_image_prompt(spec)\n"
            "selection = select_demo_asset(spec, prompt, manifest)\n"
            "print(selection.asset_id, selection.manifest_hash)\n"
        )
        results = []
        for seed in ("0", "3971593614"):
            completed = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONHASHSEED": seed},
                # The subprocess needs the same Django project root pytest
                # itself is already running from (os.getcwd()) — never a
                # hardcoded path, which only happens to exist inside the
                # local Docker container's /app and breaks in CI, where the
                # workflow's own working-directory is elsewhere.
                cwd=os.getcwd(),
                timeout=60,
            )
            assert completed.returncode == 0, completed.stderr
            results.append(completed.stdout.strip())
        assert results[0] == results[1]
        assert results[0]  # non-empty: the subprocess actually produced a selection


class TestTieBreaking:
    def _tied_manifest(self, *, reversed_order: bool = False) -> DemoManifest:
        # Identical asset content on every scoring dimension -> a genuine tie.
        assets = [_asset_dict("lehenga-tied-a"), _asset_dict("lehenga-tied-b")]
        if reversed_order:
            assets = list(reversed(assets))
        return DemoManifest.model_validate(
            {"schema_version": 1, "pack_id": "test-tie-pack", "assets": assets}
        )

    def _expected_tie_break_winner(self, spec, manifest, manifest_hash: str) -> str:
        """Independently recompute the documented SHA-256 tie-break recipe."""
        source_selections_json = json.dumps(
            spec.source_selections.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        candidate_keys = {
            asset.asset_id: _tie_break_key(
                manifest_hash=manifest_hash,
                source_selections_json=source_selections_json,
                asset_id=asset.asset_id,
            )
            for asset in manifest.assets
        }
        return min(candidate_keys, key=candidate_keys.get)

    def test_tied_score_is_broken_deterministically_and_stably(self):
        manifest = self._tied_manifest()
        spec = _spec_for(ceremony="baraat", colour_palette=["red"], fabrics=["silk"])
        prompt = build_image_prompt(spec)
        first = select_demo_asset(spec, prompt, manifest)
        second = select_demo_asset(spec, prompt, manifest)
        assert first.asset_id == second.asset_id

    def test_tie_break_winner_matches_the_documented_sha256_recipe(self):
        manifest = self._tied_manifest()
        spec = _spec_for(ceremony="baraat", colour_palette=["red"], fabrics=["silk"])
        prompt = build_image_prompt(spec)
        selection = select_demo_asset(spec, prompt, manifest)
        expected_winner = self._expected_tie_break_winner(spec, manifest, selection.manifest_hash)
        assert selection.asset_id == expected_winner

    def test_tie_break_still_follows_the_recipe_with_assets_listed_in_reverse(self):
        # Reordering the manifest's asset list changes its canonical JSON and
        # therefore its manifest_hash (an intentional property: "manifest
        # changes alter the selector fingerprint") — so the reversed-order
        # manifest is legitimately a different pack and may pick a different
        # winner. What must NOT happen is a first-in-list bias: the winner
        # must still be whichever asset the documented SHA-256 recipe picks,
        # not simply "the first one in the (possibly reversed) list."
        spec = _spec_for(ceremony="baraat", colour_palette=["red"], fabrics=["silk"])
        prompt = build_image_prompt(spec)
        manifest = self._tied_manifest(reversed_order=True)
        selection = select_demo_asset(spec, prompt, manifest)
        expected_winner = self._expected_tie_break_winner(spec, manifest, selection.manifest_hash)
        assert selection.asset_id == expected_winner
