"""The centralised demo-asset storage key builder."""

import pytest

from sitara.generation.demo.storage import build_demo_asset_key, demo_asset_storage

_VALID_HASH = "a" * 64


class TestKeyBuilder:
    def test_builds_expected_key_shape(self):
        key = build_demo_asset_key(
            pack_id="sitara-demo-v1", manifest_hash=_VALID_HASH, asset_id="lehenga-baraat-001"
        )
        assert key == f"demo-assets/sitara-demo-v1/{_VALID_HASH}/lehenga-baraat-001.webp"

    def test_deterministic_for_same_inputs(self):
        key_a = build_demo_asset_key(pack_id="pack", manifest_hash=_VALID_HASH, asset_id="asset-1")
        key_b = build_demo_asset_key(pack_id="pack", manifest_hash=_VALID_HASH, asset_id="asset-1")
        assert key_a == key_b

    @pytest.mark.parametrize("bad_pack_id", ["../escape", "Pack", "pack/id", "", "pack id"])
    def test_rejects_unsafe_pack_id(self, bad_pack_id):
        with pytest.raises(ValueError):
            build_demo_asset_key(pack_id=bad_pack_id, manifest_hash=_VALID_HASH, asset_id="asset-1")

    @pytest.mark.parametrize("bad_asset_id", ["../escape", "Asset", "asset/id", ""])
    def test_rejects_unsafe_asset_id(self, bad_asset_id):
        with pytest.raises(ValueError):
            build_demo_asset_key(pack_id="pack", manifest_hash=_VALID_HASH, asset_id=bad_asset_id)

    @pytest.mark.parametrize("bad_hash", ["not-a-hash", "a" * 63, "A" * 64, ""])
    def test_rejects_malformed_manifest_hash(self, bad_hash):
        with pytest.raises(ValueError):
            build_demo_asset_key(pack_id="pack", manifest_hash=bad_hash, asset_id="asset-1")


class TestStorageResolution:
    def test_resolves_to_default_storage_at_call_time(self):
        from django.core.files.storage import default_storage

        assert demo_asset_storage() is default_storage
