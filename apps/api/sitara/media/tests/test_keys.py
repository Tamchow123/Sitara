"""Deterministic permanent key layout (Phase 11 spec §3)."""

import uuid

import pytest

from sitara.media.ingest import build_design_image_keys

_DESIGN = uuid.UUID("11111111-2222-3333-4444-555555555555")
_VERSION = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


class TestExactLayout:
    def test_exact_key_output(self):
        keys = build_design_image_keys(_DESIGN, _VERSION)
        assert keys.original == (
            "design-images/11111111-2222-3333-4444-555555555555/"
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/original.webp"
        )
        assert keys.thumbnail == (
            "design-images/11111111-2222-3333-4444-555555555555/"
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/thumbnail.webp"
        )

    def test_string_input_is_normalised_to_canonical_lowercase(self):
        keys = build_design_image_keys(
            "11111111-2222-3333-4444-555555555555".upper(),
            "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
        )
        assert keys.original.startswith("design-images/11111111-2222-3333-4444-555555555555/")
        assert "/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/" in keys.original

    def test_keys_differ_and_use_fixed_lowercase_webp_extension(self):
        keys = build_design_image_keys(_DESIGN, _VERSION)
        assert keys.original != keys.thumbnail
        assert keys.original.endswith(".webp")
        assert keys.thumbnail.endswith(".webp")

    @pytest.mark.parametrize(
        "bad",
        ["", "not-a-uuid", "../../../etc/passwd", "11111111-2222-3333-4444-55555555555Z"],
    )
    def test_non_uuid_input_is_rejected(self, bad):
        with pytest.raises(ValueError):
            build_design_image_keys(bad, _VERSION)
        with pytest.raises(ValueError):
            build_design_image_keys(_DESIGN, bad)
