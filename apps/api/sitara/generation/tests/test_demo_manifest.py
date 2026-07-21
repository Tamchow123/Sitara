"""The strict demo-manifest Pydantic contract and cultural/coverage rules."""

import pytest
from pydantic import ValidationError

from sitara.generation.demo.manifest import (
    DEMO_MANIFEST_SCHEMA_VERSION,
    DemoManifest,
    ManifestCoverageError,
    assert_production_content_ready,
    canonical_manifest_json,
    manifest_sha256,
    validate_manifest_coverage,
)

from .demo_utils import a_valid_manifest_dict, mutate


class TestValidManifest:
    def test_synthetic_pack_parses_and_validates(self):
        manifest = DemoManifest.model_validate(a_valid_manifest_dict())
        assert manifest.schema_version == DEMO_MANIFEST_SCHEMA_VERSION
        validate_manifest_coverage(manifest)

    def test_roundtrip_is_stable(self):
        manifest = DemoManifest.model_validate(a_valid_manifest_dict())
        again = DemoManifest.model_validate(manifest.model_dump(mode="json"))
        assert again.model_dump(mode="json") == manifest.model_dump(mode="json")


class TestStrictness:
    def test_extra_top_level_field_is_rejected(self):
        data = mutate(a_valid_manifest_dict(), unexpected="nope")
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_extra_asset_field_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["unexpected"] = "nope"
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_unsupported_schema_version_is_rejected(self):
        data = mutate(a_valid_manifest_dict(), schema_version=2)
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_boolean_schema_version_is_rejected(self):
        data = mutate(a_valid_manifest_dict(), schema_version=True)
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_empty_assets_list_is_rejected(self):
        data = mutate(a_valid_manifest_dict(), assets=[])
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)


class TestUniqueness:
    def test_duplicate_asset_id_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][1]["asset_id"] = data["assets"][0]["asset_id"]
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_duplicate_filename_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][1]["filename"] = data["assets"][0]["filename"]
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_duplicate_sha256_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][1]["sha256"] = data["assets"][0]["sha256"]
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)


class TestAssetFieldValidation:
    def test_malformed_hash_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["sha256"] = "not-a-hash"
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_short_hash_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["sha256"] = "ab" * 30
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_uppercase_hash_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["sha256"] = data["assets"][0]["sha256"].upper()
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    @pytest.mark.parametrize(
        "bad_filename",
        [
            "../escape.webp",
            "sub/dir.webp",
            "sub\\dir.webp",
            "no-extension",
            "image.png",
            "image.jpeg",
            ".hidden.webp",
            "https://example.com/lehenga-baraat-001.webp",
        ],
    )
    def test_unsafe_or_non_webp_filename_is_rejected(self, bad_filename):
        data = a_valid_manifest_dict()
        data["assets"][0]["filename"] = bad_filename
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_absolute_path_filename_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["filename"] = "/etc/passwd.webp"
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    @pytest.mark.parametrize("width,height", [(1000, 1000), (768, 1000), (100, 133)])
    def test_non_portrait_3_4_dimensions_are_rejected(self, width, height):
        data = a_valid_manifest_dict()
        data["assets"][0]["width"] = width
        data["assets"][0]["height"] = height
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_undersized_dimensions_are_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["width"] = 3
        data["assets"][0]["height"] = 4
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_unknown_taxonomy_value_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["garment_types"] = ["not_a_real_garment"]
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_unknown_provenance_status_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["provenance_status"] = "trust_me"
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_gharara_and_sharara_together_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["garment_types"] = ["gharara", "sharara"]
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_too_many_garment_types_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["garment_types"] = ["lehenga", "saree", "anarkali"]
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_incompatible_silhouette_for_garment_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"][0]["garment_types"] = ["lehenga"]
        data["assets"][0]["silhouettes"] = ["gharara_construction"]
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_saree_asset_without_saree_drape_is_rejected(self):
        data = a_valid_manifest_dict()
        saree_asset = next(a for a in data["assets"] if "saree" in a["garment_types"])
        saree_asset["saree_drapes"] = []
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_non_saree_asset_with_saree_drape_is_rejected(self):
        data = a_valid_manifest_dict()
        non_saree = next(a for a in data["assets"] if "saree" not in a["garment_types"])
        non_saree["saree_drapes"] = ["nivi_drape"]
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)

    def test_saree_asset_with_dupatta_style_is_rejected(self):
        data = a_valid_manifest_dict()
        saree_asset = next(a for a in data["assets"] if "saree" in a["garment_types"])
        saree_asset["dupatta_styles"] = ["head_drape"]
        with pytest.raises(ValidationError):
            DemoManifest.model_validate(data)


class TestCoverageValidation:
    def test_missing_garment_coverage_is_rejected(self):
        data = a_valid_manifest_dict()
        data["assets"] = [a for a in data["assets"] if "lehenga" not in a["garment_types"]]
        manifest = DemoManifest.model_validate(data)
        with pytest.raises(ManifestCoverageError):
            validate_manifest_coverage(manifest)

    def test_missing_ceremony_coverage_is_rejected(self):
        data = a_valid_manifest_dict()
        for asset in data["assets"]:
            # "walima" appears on two synthetic assets, each with a second
            # ceremony too, so removing it never empties a ceremonies list.
            asset["ceremonies"] = [c for c in asset["ceremonies"] if c != "walima"]
        manifest = DemoManifest.model_validate(data)
        with pytest.raises(ManifestCoverageError):
            validate_manifest_coverage(manifest)

    def test_missing_embellishment_density_range_is_rejected(self):
        data = a_valid_manifest_dict()
        for asset in data["assets"]:
            asset["embellishment_densities"] = [
                d for d in asset["embellishment_densities"] if d != "heavy"
            ]
        manifest = DemoManifest.model_validate(data)
        with pytest.raises(ManifestCoverageError):
            validate_manifest_coverage(manifest)

    def test_missing_modest_coverage_representation_is_rejected(self):
        data = a_valid_manifest_dict()
        modest_tags = {
            "full_sleeves",
            "high_neckline",
            "full_back",
            "full_midriff",
            "head_drape_preferred",
        }
        for asset in data["assets"]:
            asset["coverage_preferences"] = [
                c for c in asset["coverage_preferences"] if c not in modest_tags
            ]
        manifest = DemoManifest.model_validate(data)
        with pytest.raises(ManifestCoverageError):
            validate_manifest_coverage(manifest)


class TestProductionReadiness:
    def test_synthetic_pack_never_satisfies_production_readiness(self):
        manifest = DemoManifest.model_validate(a_valid_manifest_dict())
        with pytest.raises(ManifestCoverageError):
            assert_production_content_ready(manifest)

    def test_verified_pack_satisfies_production_readiness(self):
        data = a_valid_manifest_dict()
        for asset in data["assets"]:
            asset["provenance_status"] = "verified_project_owned"
        manifest = DemoManifest.model_validate(data)
        assert_production_content_ready(manifest)  # does not raise


class TestFingerprint:
    def test_manifest_hash_is_stable_for_identical_content(self):
        manifest_a = DemoManifest.model_validate(a_valid_manifest_dict())
        manifest_b = DemoManifest.model_validate(a_valid_manifest_dict())
        assert manifest_sha256(manifest_a) == manifest_sha256(manifest_b)

    def test_manifest_hash_changes_when_content_changes(self):
        data = a_valid_manifest_dict()
        manifest_a = DemoManifest.model_validate(data)
        data["assets"][0]["alt_text"] = data["assets"][0]["alt_text"] + " Updated."
        manifest_b = DemoManifest.model_validate(data)
        assert manifest_sha256(manifest_a) != manifest_sha256(manifest_b)

    def test_canonical_json_is_deterministic(self):
        manifest = DemoManifest.model_validate(a_valid_manifest_dict())
        assert canonical_manifest_json(manifest) == canonical_manifest_json(manifest)
