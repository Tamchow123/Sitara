"""Versioned inspiration-context snapshot: schema, canonicalisation, hash,
provider/acknowledgement projections and pre-spend safety (Phase 13)."""

import pytest
from pydantic import ValidationError

from sitara.catalogue.models import InspirationAsset
from sitara.catalogue.services import retire_inspiration_asset
from sitara.catalogue.tests.utils import make_eligible_asset
from sitara.designs.models import DesignInspiration
from sitara.generation.inspiration_context import (
    INSPIRATION_CONTEXT_SCHEMA_VERSION,
    InspirationAcknowledgement,
    InspirationAssetIneligible,
    InspirationContextItem,
    InspirationContextSnapshot,
    InspirationMetadataUnavailable,
    InspirationProviderCues,
    build_inspiration_context_snapshot,
    inspiration_acknowledgements,
    inspiration_context_sha256,
    provider_inspiration_cues,
)

from .factory import make_complete_design

pytestmark = pytest.mark.django_db


def _select(design, *assets):
    for index, asset in enumerate(assets, start=1):
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=index)


def _item(position=1, asset_id="11111111-1111-1111-1111-111111111111", **overrides):
    fields = {
        "asset_id": asset_id,
        "position": position,
        "provider_cues": InspirationProviderCues(
            garment_type="lehenga",
            visual_description="Front view of an emerald bridal outfit.",
            cultural_context="Broad Pakistani bridal styling reference.",
        ),
        "acknowledgement": InspirationAcknowledgement(title="Emerald look", attribution="Studio A"),
    }
    fields.update(overrides)
    return InspirationContextItem(**fields)


class TestSchemaValidation:
    def test_empty_item_list_is_valid(self):
        snapshot = InspirationContextSnapshot(schema_version=1, items=[])
        assert snapshot.items == []

    def test_maximum_three_items(self):
        items = [
            _item(position=i, asset_id=f"1111111{i}-1111-1111-1111-111111111111") for i in (1, 2, 3)
        ]
        snapshot = InspirationContextSnapshot(schema_version=1, items=items)
        assert len(snapshot.items) == 3

    def test_more_than_three_items_rejected(self):
        with pytest.raises(ValidationError):
            [
                _item(position=i, asset_id=f"1111111{i}-1111-1111-1111-111111111111")
                for i in (1, 2, 3, 4)
            ]

    def test_non_contiguous_positions_rejected(self):
        items = [
            _item(position=1),
            _item(position=3, asset_id="22222222-2222-2222-2222-222222222222"),
        ]
        with pytest.raises(ValidationError):
            InspirationContextSnapshot(schema_version=1, items=items)

    def test_duplicate_positions_rejected(self):
        items = [
            _item(position=1, asset_id="11111111-1111-1111-1111-111111111111"),
            _item(position=1, asset_id="22222222-2222-2222-2222-222222222222"),
        ]
        with pytest.raises(ValidationError):
            InspirationContextSnapshot(schema_version=1, items=items)

    def test_duplicate_asset_ids_rejected(self):
        items = [_item(position=1), _item(position=2)]  # same default asset_id
        with pytest.raises(ValidationError):
            InspirationContextSnapshot(schema_version=1, items=items)

    def test_out_of_order_positions_rejected(self):
        items = [
            _item(position=2, asset_id="11111111-1111-1111-1111-111111111111"),
            _item(position=1, asset_id="22222222-2222-2222-2222-222222222222"),
        ]
        with pytest.raises(ValidationError):
            InspirationContextSnapshot(schema_version=1, items=items)

    def test_null_garment_type_and_cultural_context_are_valid(self):
        item = _item(
            provider_cues=InspirationProviderCues(
                garment_type=None, visual_description="A plain description.", cultural_context=None
            )
        )
        assert item.provider_cues.garment_type is None
        assert item.provider_cues.cultural_context is None

    def test_invalid_garment_type_shape_rejected(self):
        with pytest.raises(ValidationError):
            InspirationProviderCues(
                garment_type="Not A Machine Value!",
                visual_description="A plain description.",
                cultural_context=None,
            )

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            InspirationContextSnapshot(schema_version=1, items=[], extra_field="nope")

    def test_unknown_field_on_item_rejected(self):
        with pytest.raises(ValidationError):
            InspirationContextItem(
                asset_id="11111111-1111-1111-1111-111111111111",
                position=1,
                provider_cues=InspirationProviderCues(
                    garment_type=None, visual_description="x", cultural_context=None
                ),
                acknowledgement=InspirationAcknowledgement(title="t", attribution=""),
                image_url="https://example.com/leak.jpg",
            )

    def test_non_uuid_asset_id_rejected(self):
        with pytest.raises(ValidationError):
            _item(asset_id="not-a-uuid")


class TestBuildSnapshot:
    def test_no_selection_yields_empty_snapshot(self):
        design = make_complete_design()
        snapshot = build_inspiration_context_snapshot(design)
        assert snapshot.schema_version == INSPIRATION_CONTEXT_SCHEMA_VERSION
        assert snapshot.items == []

    def test_one_selection(self, inmemory_storage):
        design = make_complete_design()
        asset = make_eligible_asset()
        _select(design, asset)
        snapshot = build_inspiration_context_snapshot(design)
        assert len(snapshot.items) == 1
        item = snapshot.items[0]
        assert item.position == 1
        assert item.asset_id == str(asset.pk)
        assert item.provider_cues.garment_type == "lehenga"
        assert item.acknowledgement.title == asset.title

    def test_two_selections_preserve_order(self, inmemory_storage):
        design = make_complete_design()
        first = make_eligible_asset(title="First look")
        second = make_eligible_asset(title="Second look")
        _select(design, first, second)
        snapshot = build_inspiration_context_snapshot(design)
        titles = [item.acknowledgement.title for item in snapshot.items]
        assert titles == ["First look", "Second look"]
        assert [item.position for item in snapshot.items] == [1, 2]

    def test_three_selections(self, inmemory_storage):
        design = make_complete_design()
        assets = [make_eligible_asset(title=f"Look {i}") for i in range(3)]
        _select(design, *assets)
        snapshot = build_inspiration_context_snapshot(design)
        assert len(snapshot.items) == 3

    def test_retired_asset_is_ineligible(self, inmemory_storage):
        design = make_complete_design()
        asset = make_eligible_asset()
        _select(design, asset)
        retire_inspiration_asset(asset)
        with pytest.raises(InspirationAssetIneligible):
            build_inspiration_context_snapshot(design)

    def test_unsafe_visual_description_is_rejected(self, inmemory_storage):
        # Approval already rejects unsafe metadata (Phase 13 defence in
        # depth); simulate a LEGACY already-approved asset whose metadata
        # would fail today's scan, via a direct update() that bypasses the
        # model's save()-time immutability freeze — exactly the scenario the
        # phase spec requires selection-time revalidation to still catch.
        design = make_complete_design()
        asset = make_eligible_asset()
        InspirationAsset.objects.filter(pk=asset.pk).update(
            alt_text="Styled after Sabyasachi's signature look."
        )
        asset.refresh_from_db()
        _select(design, asset)
        with pytest.raises(InspirationMetadataUnavailable):
            build_inspiration_context_snapshot(design)

    def test_unsafe_cultural_context_is_rejected(self, inmemory_storage):
        design = make_complete_design()
        asset = make_eligible_asset()
        InspirationAsset.objects.filter(pk=asset.pk).update(
            cultural_context="Visit https://example.com for more."
        )
        asset.refresh_from_db()
        _select(design, asset)
        with pytest.raises(InspirationMetadataUnavailable):
            build_inspiration_context_snapshot(design)

    def test_non_conforming_garment_type_is_a_documented_error(self, inmemory_storage):
        # garment_type's machine-id pattern is enforced by the admin
        # ModelForm, not by save() or a DB constraint — a value written
        # through another path (fixture, future non-form service) could
        # still violate it. The snapshot builder must translate that into
        # its documented exception, never a raw pydantic.ValidationError.
        design = make_complete_design()
        asset = make_eligible_asset()
        InspirationAsset.objects.filter(pk=asset.pk).update(garment_type="Not Valid!")
        asset.refresh_from_db()
        _select(design, asset)
        with pytest.raises(InspirationMetadataUnavailable):
            build_inspiration_context_snapshot(design)

    def test_safe_ordinary_cultural_prose_is_accepted(self, inmemory_storage):
        design = make_complete_design()
        asset = make_eligible_asset(
            cultural_context="Broad Pakistani bridal styling reference with layered dupatta drape."
        )
        _select(design, asset)
        snapshot = build_inspiration_context_snapshot(design)
        assert snapshot.items[0].provider_cues.cultural_context == asset.cultural_context

    def test_blank_garment_type_becomes_null(self, inmemory_storage):
        design = make_complete_design()
        asset = make_eligible_asset(garment_type="")
        _select(design, asset)
        snapshot = build_inspiration_context_snapshot(design)
        assert snapshot.items[0].provider_cues.garment_type is None

    def test_no_image_bytes_are_read(self, inmemory_storage, monkeypatch):
        from django.core.files.storage import default_storage

        design = make_complete_design()
        asset = make_eligible_asset()
        _select(design, asset)

        def _forbidden_open(*args, **kwargs):
            raise AssertionError("inspiration context building must never read image bytes")

        monkeypatch.setattr(default_storage, "open", _forbidden_open)
        build_inspiration_context_snapshot(design)  # must not raise


class TestHashDeterminism:
    def _snapshot(self, **first_overrides):
        base = InspirationProviderCues(
            garment_type="lehenga", visual_description="A description.", cultural_context=None
        )
        first = InspirationContextItem(
            asset_id="11111111-1111-1111-1111-111111111111",
            position=1,
            provider_cues=first_overrides.pop("provider_cues", base),
            acknowledgement=first_overrides.pop(
                "acknowledgement", InspirationAcknowledgement(title="Look one", attribution="")
            ),
        )
        return InspirationContextSnapshot(schema_version=1, items=[first])

    def test_repeated_hash_is_deterministic(self):
        snapshot = self._snapshot()
        assert inspiration_context_sha256(snapshot) == inspiration_context_sha256(snapshot)

    def test_two_semantically_identical_snapshots_hash_the_same(self):
        first_hash = inspiration_context_sha256(self._snapshot())
        second_hash = inspiration_context_sha256(self._snapshot())
        assert first_hash == second_hash

    def test_changing_order_changes_hash(self):
        cues = InspirationProviderCues(
            garment_type="lehenga", visual_description="A description.", cultural_context=None
        )
        first = InspirationContextItem(
            asset_id="11111111-1111-1111-1111-111111111111",
            position=1,
            provider_cues=cues,
            acknowledgement=InspirationAcknowledgement(title="A", attribution=""),
        )
        second = InspirationContextItem(
            asset_id="22222222-2222-2222-2222-222222222222",
            position=2,
            provider_cues=cues,
            acknowledgement=InspirationAcknowledgement(title="B", attribution=""),
        )
        forward = InspirationContextSnapshot(
            schema_version=1,
            items=[
                first,
                InspirationContextItem(**{**second.model_dump(), "position": 2}),
            ],
        )
        swapped = InspirationContextSnapshot(
            schema_version=1,
            items=[
                InspirationContextItem(**{**second.model_dump(), "position": 1}),
                InspirationContextItem(**{**first.model_dump(), "position": 2}),
            ],
        )
        assert inspiration_context_sha256(forward) != inspiration_context_sha256(swapped)

    def test_changing_a_provider_cue_changes_hash(self):
        baseline = self._snapshot()
        changed = self._snapshot(
            provider_cues=InspirationProviderCues(
                garment_type="saree", visual_description="A description.", cultural_context=None
            )
        )
        assert inspiration_context_sha256(baseline) != inspiration_context_sha256(changed)

    def test_changing_title_or_attribution_changes_the_audit_hash(self):
        baseline = self._snapshot()
        changed_title = self._snapshot(
            acknowledgement=InspirationAcknowledgement(title="Different look", attribution="")
        )
        changed_attribution = self._snapshot(
            acknowledgement=InspirationAcknowledgement(title="Look one", attribution="Studio B")
        )
        baseline_hash = inspiration_context_sha256(baseline)
        assert baseline_hash != inspiration_context_sha256(changed_title)
        assert baseline_hash != inspiration_context_sha256(changed_attribution)

    def test_title_and_attribution_changes_never_affect_provider_cues(self):
        baseline = self._snapshot()
        changed = self._snapshot(
            acknowledgement=InspirationAcknowledgement(
                title="Different look", attribution="Studio B"
            )
        )
        assert provider_inspiration_cues(baseline) == provider_inspiration_cues(changed)


class TestProjections:
    def test_provider_cues_omit_uuid_title_attribution(self):
        item = _item()
        snapshot = InspirationContextSnapshot(schema_version=1, items=[item])
        cues = provider_inspiration_cues(snapshot)
        assert cues == [
            {
                "position": 1,
                "garment_type": "lehenga",
                "visual_description": "Front view of an emerald bridal outfit.",
                "cultural_context": "Broad Pakistani bridal styling reference.",
            }
        ]
        blob = str(cues)
        assert item.asset_id not in blob
        assert "Emerald look" not in blob
        assert "Studio A" not in blob

    def test_acknowledgements_omit_provider_cues_and_uuid(self):
        item = _item()
        snapshot = InspirationContextSnapshot(schema_version=1, items=[item])
        acknowledgements = inspiration_acknowledgements(snapshot)
        assert acknowledgements == [
            {"position": 1, "title": "Emerald look", "attribution": "Studio A"}
        ]
        blob = str(acknowledgements)
        assert item.asset_id not in blob
        assert "lehenga" not in blob

    def test_empty_snapshot_projections_are_empty(self):
        snapshot = InspirationContextSnapshot(schema_version=1, items=[])
        assert provider_inspiration_cues(snapshot) == []
        assert inspiration_acknowledgements(snapshot) == []
