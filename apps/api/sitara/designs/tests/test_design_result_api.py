"""Private design result API tests (Phase 12 Part A, spec §7).

GET /api/v1/designs/<design>/versions/<version>/result/ with real ownership
flows: anonymous workspaces, authenticated accounts, lazy post-login
promotion, indistinguishable 404s, the controlled 409/503 states, and the
curated-payload/no-store/no-provenance response contract. Revalidation and
the safety scan are local computations — no network is touched.
"""

import copy
import json
import logging
from pathlib import Path

import pytest
from django.utils import timezone

from sitara.designs.models import Design, DesignSession, DesignVersion

from .utils import (
    bootstrap_csrf,
    create_design,
    create_owned_design_id,
    create_ready_design_version,
    csrf_client,
    login,
    logout,
    register,
    unique_email,
)

pytestmark = pytest.mark.django_db

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "generation" / "tests" / "fixtures" / "nikah_lehenga.json"
)


def _load_valid_spec() -> dict:
    with _FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _result_url(design_id, version_id) -> str:
    return f"/api/v1/designs/{design_id}/versions/{version_id}/result/"


def _make_owned_design(client) -> str:
    return create_owned_design_id(client, title="Result test")


def _attach_ready_version(
    design_id,
    *,
    design_spec: dict | None = None,
    schema_version: int = 1,
    inspiration_context: dict | None = None,
    inspiration_context_schema_version: int | None = None,
    inspiration_context_sha256: str = "",
) -> DesignVersion:
    """A DesignVersion with every result prerequisite satisfied. The result
    endpoint never checks object-store existence, so no storage objects are
    created."""
    spec = design_spec if design_spec is not None else _load_valid_spec()
    return create_ready_design_version(
        design_id,
        design_spec=spec,
        schema_version=schema_version,
        image_prompt="A result-API-test prompt.",
        with_storage_objects=False,
        inspiration_context=inspiration_context,
        inspiration_context_schema_version=inspiration_context_schema_version,
        inspiration_context_sha256=inspiration_context_sha256,
    )


_RESULT_SECTION_KEYS = {
    "design_id",
    "design_version_id",
    "version_number",
    "title",
    "concept_summary",
    "garment_breakdown",
    "colour_story",
    "fabrics_and_texture",
    "embellishment_plan",
    "coverage_and_drape",
    "cultural_context",
    "styling_notes",
    "construction_caveats",
    "image_alt_text",
    "created_at",
    "inspiration_acknowledgements",
    "lineage",
}


class TestAuthorisedAccess:
    def test_anonymous_owner_receives_the_complete_curated_result(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id)
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 200, response.content
        body = response.json()
        assert set(body) == {"result"}
        result = body["result"]
        assert set(result) == _RESULT_SECTION_KEYS
        assert result["design_id"] == str(design_id)
        assert result["design_version_id"] == str(version.pk)
        assert result["version_number"] == 1
        assert result["title"] == "Ivory and gold flared lehenga for a nikah"
        assert set(result["garment_breakdown"]) == {
            "overall_form",
            "garment_components",
            "silhouette",
            "drape_or_layering",
            "key_proportions",
        }
        assert set(result["colour_story"]) == {"palette_summary", "placement", "rationale"}
        assert result["fabrics_and_texture"][0] == {
            "fabric": "Silk",
            "placement": "Choli and lehenga skirt base",
            "finish_and_movement": (
                "A smooth, gently structured drape that holds the skirt's flare."
            ),
        }
        assert set(result["embellishment_plan"]) == {
            "techniques",
            "density",
            "placement",
            "motifs",
            "restraint_notes",
        }
        assert set(result["coverage_and_drape"]) == {
            "sleeves",
            "neckline",
            "back_and_midriff",
            "head_covering",
            "dupatta_or_saree_drape",
        }
        assert set(result["cultural_context"]) == {
            "regional_direction",
            "interpretation_notes",
            "safeguards",
        }
        assert result["cultural_context"]["regional_direction"]
        assert result["styling_notes"]
        assert result["construction_caveats"]
        assert result["image_alt_text"]
        assert result["created_at"]
        assert result["lineage"] == {
            "kind": "initial",
            "parent_version_id": None,
            "refinement": None,
        }

    def test_authenticated_owner_receives_it(self):
        client = csrf_client()
        register(client, unique_email())
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id)
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 200, response.content

    def test_anonymous_to_authenticated_promotion_retains_access(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id)
        email = unique_email()
        register(client, email)
        claimed = client.get(_result_url(design_id, version.pk))
        assert claimed.status_code == 200, claimed.content
        logout(client)
        login(client, email)
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 200, response.content

    def test_response_has_no_store(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id)
        response = client.get(_result_url(design_id, version.pk))
        assert response["Cache-Control"] == "no-store"

    def test_response_omits_source_selections_and_private_provenance(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id)
        response = client.get(_result_url(design_id, version.pk))
        raw = response.content.decode()
        assert "source_selections" not in raw
        assert version.image_prompt not in raw
        assert "prompt_builder_version" not in raw
        assert version.design_spec_provider not in raw
        assert version.design_spec_model not in raw
        assert version.image_storage_key not in raw
        assert version.image_sha256 not in raw
        assert version.thumbnail_storage_key not in raw
        assert "3.0.0" not in raw  # prompt-builder version stays private
        assert "1.0.0" not in raw  # image-processor version stays private
        assert "seed" not in raw
        assert "prediction" not in raw
        assert "staged" not in raw
        assert "input_tokens" not in raw
        assert "output_tokens" not in raw

    def test_urls_are_never_issued(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id)
        response = client.get(_result_url(design_id, version.pk))
        raw = response.content.decode()
        assert "http://" not in raw
        assert "https://" not in raw
        assert "signed" not in raw.lower()


class TestIndistinguishable404:
    def _authorised_pair(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id)
        return client, design_id, version

    def test_other_session_other_account_and_nonexistent_are_identical_404s(self):
        _owner, design_id, version = self._authorised_pair()

        stranger = csrf_client()
        bootstrap_csrf(stranger)
        foreign = stranger.get(_result_url(design_id, version.pk))

        account_client = csrf_client()
        register(account_client, unique_email())
        other_account = account_client.get(_result_url(design_id, version.pk))

        ghost = csrf_client().get(
            _result_url(
                "00000000-0000-4000-8000-000000000000",
                "00000000-0000-4000-8000-000000000001",
            )
        )

        assert foreign.status_code == other_account.status_code == ghost.status_code == 404
        assert foreign.json() == other_account.json() == ghost.json()

    def test_version_of_another_owned_design_cannot_be_mixed_into_the_path(self):
        client = csrf_client()
        first_design = _make_owned_design(client)
        second_response = create_design(client, title="Second owned design")
        second_design = second_response.json()["id"]
        version = _attach_ready_version(first_design)
        response = client.get(_result_url(second_design, version.pk))
        assert response.status_code == 404

    def test_unknown_version_uuid_on_an_owned_design_is_404(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        _attach_ready_version(design_id)
        response = client.get(_result_url(design_id, "00000000-0000-4000-8000-00000000dead"))
        assert response.status_code == 404
        assert response["Cache-Control"] == "no-store"

    def test_failed_get_creates_no_workspace(self):
        before = DesignSession.objects.count()
        fresh = csrf_client()
        response = fresh.get(
            _result_url(
                "11111111-0000-4000-8000-000000000000",
                "11111111-0000-4000-8000-000000000001",
            )
        )
        assert response.status_code == 404
        assert DesignSession.objects.count() == before


class TestReadinessConflict:
    def test_never_generated_version_returns_409(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = DesignVersion.objects.create(design_id=design_id, version_number=1)
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "design_result_not_ready"
        assert response["Cache-Control"] == "no-store"

    def test_spec_without_image_prompt_returns_409(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = DesignVersion.objects.create(
            design_id=design_id,
            version_number=1,
            design_spec=_load_valid_spec(),
            design_spec_schema_version=1,
            design_spec_template_version="v1",
            design_spec_provider="fixture",
            design_spec_model="fixture-model",
            design_spec_generated_at=timezone.now(),
        )
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "design_result_not_ready"

    def test_missing_permanent_image_returns_409(self):
        # Design.status can be GENERATED while the version still lacks
        # complete permanent image provenance — readiness must check the
        # version fields directly, never Design.status alone.
        client = csrf_client()
        design_id = _make_owned_design(client)
        Design.objects.filter(pk=design_id).update(status=Design.Status.GENERATED)
        version = DesignVersion.objects.create(
            design_id=design_id,
            version_number=1,
            design_spec=_load_valid_spec(),
            design_spec_schema_version=1,
            design_spec_template_version="v1",
            design_spec_provider="fixture",
            design_spec_model="fixture-model",
            design_spec_generated_at=timezone.now(),
            image_prompt="A staged-only prompt.",
            prompt_builder_version="3.0.0",
        )
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "design_result_not_ready"


class TestControlledUnavailability:
    def test_unsupported_schema_version_returns_controlled_503(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id, schema_version=2)
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "design_result_unavailable"
        assert response["Cache-Control"] == "no-store"

    def test_corrupt_design_spec_returns_controlled_503(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id, design_spec={"schema_version": 1})
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "design_result_unavailable"

    def test_unsafe_stored_narrative_returns_controlled_503(self, caplog):
        client = csrf_client()
        design_id = _make_owned_design(client)
        unsafe_spec = copy.deepcopy(_load_valid_spec())
        unsafe_spec["styling_notes"] = ["Style it exactly like a Manish Malhotra red carpet look."]
        version = _attach_ready_version(design_id, design_spec=unsafe_spec)
        with caplog.at_level(logging.DEBUG):
            response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "design_result_unavailable"
        raw = response.content.decode()
        assert "Manish Malhotra" not in raw
        assert "manish" not in raw.lower()
        # The rejected narrative must never reach the logs either, only the
        # safe boundary signal (operation, row UUID, exception TYPE).
        assert "Manish Malhotra" not in caplog.text
        assert "manish" not in caplog.text.lower()

    def test_errors_and_logs_never_contain_result_narrative(self, caplog):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id, design_spec={"schema_version": 1})
        with caplog.at_level(logging.DEBUG):
            response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 503
        assert "Ivory and gold" not in caplog.text
        for record in caplog.records:
            assert "design_spec" not in record.getMessage()
        # The safe boundary log carries only operation, row UUID and
        # exception TYPE.
        assert any(
            "design result unavailable" in record.message and str(version.pk) in record.getMessage()
            for record in caplog.records
        )


def _snapshot(items: list[dict]):
    from sitara.generation.inspiration_context import InspirationContextSnapshot

    return InspirationContextSnapshot(schema_version=1, items=items)


def _item(position: int, *, title: str, attribution: str, asset_id: str | None = None) -> dict:
    return {
        "asset_id": asset_id or f"{'1' * 7}{position}-1111-1111-1111-111111111111",
        "position": position,
        "provider_cues": {
            "garment_type": "lehenga",
            "visual_description": "A visual description.",
            "cultural_context": None,
        },
        "acknowledgement": {"title": title, "attribution": attribution},
    }


class TestInspirationAcknowledgements:
    def test_legacy_version_returns_an_empty_list(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(design_id)
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 200
        assert response.json()["result"]["inspiration_acknowledgements"] == []

    def test_acknowledgement_order_and_attribution_are_preserved(self):
        from sitara.generation.inspiration_context import inspiration_context_sha256

        client = csrf_client()
        design_id = _make_owned_design(client)
        snapshot = _snapshot(
            [
                _item(1, title="First look", attribution="Studio A"),
                _item(2, title="Second look", attribution=""),
                _item(3, title="Third look", attribution="Studio C"),
            ]
        )
        version = _attach_ready_version(
            design_id,
            inspiration_context=snapshot.model_dump(mode="json"),
            inspiration_context_schema_version=1,
            inspiration_context_sha256=inspiration_context_sha256(snapshot),
        )
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 200
        acknowledgements = response.json()["result"]["inspiration_acknowledgements"]
        assert acknowledgements == [
            {"position": 1, "title": "First look", "attribution": "Studio A"},
            {"position": 2, "title": "Second look", "attribution": ""},
            {"position": 3, "title": "Third look", "attribution": "Studio C"},
        ]

    def test_retired_source_still_renders_the_stored_acknowledgement(self, inmemory_storage):
        # The acknowledgement is read ONLY from the persisted snapshot, never
        # by re-querying the (now-retired) live catalogue asset.
        from sitara.catalogue.services import retire_inspiration_asset
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.generation.inspiration_context import inspiration_context_sha256

        asset = make_eligible_asset(title="A retired look")
        retire_inspiration_asset(asset)
        client = csrf_client()
        design_id = _make_owned_design(client)
        snapshot = _snapshot(
            [_item(1, title="A retired look", attribution="", asset_id=str(asset.id))]
        )
        version = _attach_ready_version(
            design_id,
            inspiration_context=snapshot.model_dump(mode="json"),
            inspiration_context_schema_version=1,
            inspiration_context_sha256=inspiration_context_sha256(snapshot),
        )
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 200
        assert response.json()["result"]["inspiration_acknowledgements"] == [
            {"position": 1, "title": "A retired look", "attribution": ""}
        ]

    def test_no_internal_cue_or_asset_uuid_leaks(self):
        from sitara.generation.inspiration_context import inspiration_context_sha256

        client = csrf_client()
        design_id = _make_owned_design(client)
        snapshot = _snapshot([_item(1, title="A look", attribution="Studio A")])
        version = _attach_ready_version(
            design_id,
            inspiration_context=snapshot.model_dump(mode="json"),
            inspiration_context_schema_version=1,
            inspiration_context_sha256=inspiration_context_sha256(snapshot),
        )
        response = client.get(_result_url(design_id, version.pk))
        raw = response.content.decode()
        assert "asset_id" not in raw
        assert "provider_cues" not in raw
        assert "garment_type" not in raw
        assert snapshot.items[0].asset_id not in raw

    def test_foreign_owner_remains_404(self):
        from sitara.generation.inspiration_context import inspiration_context_sha256

        owner = csrf_client()
        design_id = _make_owned_design(owner)
        snapshot = _snapshot([_item(1, title="A look", attribution="Studio A")])
        version = _attach_ready_version(
            design_id,
            inspiration_context=snapshot.model_dump(mode="json"),
            inspiration_context_schema_version=1,
            inspiration_context_sha256=inspiration_context_sha256(snapshot),
        )
        stranger = csrf_client()
        response = stranger.get(_result_url(design_id, version.pk))
        assert response.status_code == 404

    # An "unsupported inspiration_context_schema_version" 503 path exists in
    # load_inspiration_acknowledgements as defence in depth, but is
    # unreachable through any legitimate write today: the
    # designs_designversion_inspiration_context_schema_version_valid DB
    # constraint already pins the value to 1 (proved directly in
    # designs/tests/test_provenance.py::test_invalid_schema_version_is_blocked).

    def test_corrupt_context_returns_controlled_503_without_exposing_content(self, caplog):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ready_version(
            design_id,
            inspiration_context={"schema_version": 1, "items": "not-a-list"},
            inspiration_context_schema_version=1,
            inspiration_context_sha256="a" * 64,
        )
        with caplog.at_level(logging.DEBUG):
            response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "design_result_unavailable"
        assert "not-a-list" not in response.content.decode()
        assert "not-a-list" not in caplog.text

    def test_hash_mismatch_returns_controlled_503(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        snapshot = _snapshot([_item(1, title="A look", attribution="Studio A")])
        version = _attach_ready_version(
            design_id,
            inspiration_context=snapshot.model_dump(mode="json"),
            inspiration_context_schema_version=1,
            # Well-formed shape but a hash that cannot match the content.
            inspiration_context_sha256="0" * 64,
        )
        response = client.get(_result_url(design_id, version.pk))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "design_result_unavailable"
