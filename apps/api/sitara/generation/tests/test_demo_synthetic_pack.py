"""The development-only synthetic demo pack."""

import hashlib

import pytest

from sitara.generation.demo.manifest import (
    GARMENT_TYPES,
    ManifestCoverageError,
    assert_production_content_ready,
    validate_manifest_coverage,
)
from sitara.generation.demo.synthetic_pack import (
    SYNTHETIC_PACK_ID,
    SyntheticPackNotAllowed,
    build_synthetic_demo_pack,
)


class TestSyntheticPack:
    def test_builds_a_valid_manifest(self):
        manifest, images = build_synthetic_demo_pack()
        assert manifest.pack_id == SYNTHETIC_PACK_ID
        assert set(images) == {a.asset_id for a in manifest.assets}

    def test_covers_all_six_garment_categories(self):
        manifest, _images = build_synthetic_demo_pack()
        covered = {g for a in manifest.assets for g in a.garment_types}
        assert covered == set(GARMENT_TYPES)

    def test_satisfies_pack_wide_coverage_validation(self):
        manifest, _images = build_synthetic_demo_pack()
        validate_manifest_coverage(manifest)  # does not raise

    def test_every_asset_is_labelled_as_a_placeholder(self):
        manifest, _images = build_synthetic_demo_pack()
        for asset in manifest.assets:
            assert asset.provenance_status == "synthetic_development_placeholder"
            assert "placeholder" in asset.alt_text.lower()

    def test_never_satisfies_production_content_readiness(self):
        manifest, _images = build_synthetic_demo_pack()
        with pytest.raises(ManifestCoverageError):
            assert_production_content_ready(manifest)

    def test_image_bytes_match_manifest_hashes(self):
        manifest, images = build_synthetic_demo_pack()
        for asset in manifest.assets:
            assert hashlib.sha256(images[asset.asset_id]).hexdigest() == asset.sha256
            assert len(images[asset.asset_id]) == asset.size_bytes

    def test_deterministic_across_calls(self):
        manifest_a, images_a = build_synthetic_demo_pack()
        manifest_b, images_b = build_synthetic_demo_pack()
        assert manifest_a.model_dump(mode="json") == manifest_b.model_dump(mode="json")
        assert images_a == images_b

    def test_rejected_in_production(self, settings):
        settings.APP_ENV = "production"
        with pytest.raises(SyntheticPackNotAllowed):
            build_synthetic_demo_pack()

    def test_no_external_network_use(self, settings):
        # The repository-wide autouse `no_network` fixture (conftest.py)
        # already fails any socket.connect call; simply exercising the
        # builder here proves it makes none.
        build_synthetic_demo_pack()
