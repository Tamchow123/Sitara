"""Phase 7 draft API: questionnaire linkage, answers, validate.

Everything runs with enforce_csrf_checks=True — the same enforcement a real
browser faces."""

import uuid

import pytest

from sitara.catalogue.services import retire_inspiration_asset
from sitara.catalogue.tests.utils import make_eligible_asset
from sitara.designs.models import Design, DesignInspiration
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.services import activate_questionnaire_version

from .utils import (
    COMPLETE_ANSWERS,
    CONTRACT_SCHEMA,
    DESIGNS_URL,
    bootstrap_csrf,
    create_design,
    csrf_client,
    design_url,
    make_active_questionnaire,
    register,
    send_json,
    validate_url,
)

pytestmark = pytest.mark.django_db


def _create(client, payload, token=None):
    token = token or bootstrap_csrf(client)
    return send_json(client, "post", DESIGNS_URL, payload, token=token)


class TestBackwardCompatibility:
    def test_title_only_create_leaves_questionnaire_null(self):
        client = csrf_client()
        body = create_design(client, title="legacy concept").json()
        assert body["questionnaire"] is None
        assert body["answers"] == {}
        assert body["selected_inspirations"] == []


class TestQuestionnaireAssignment:
    def test_assigning_an_active_questionnaire(self):
        version = make_active_questionnaire()
        client = csrf_client()
        response = _create(client, {"questionnaire_version_id": str(version.id)})
        assert response.status_code == 201, response.content
        questionnaire = response.json()["questionnaire"]
        assert questionnaire["id"] == str(version.id)
        assert questionnaire["version"] == 1
        assert questionnaire["schema"] == CONTRACT_SCHEMA

    def test_assigning_a_retired_questionnaire_is_allowed(self):
        active = make_active_questionnaire(version=1)
        draft = QuestionnaireVersion.objects.create(
            version=2, status="draft", schema=CONTRACT_SCHEMA
        )
        activate_questionnaire_version(draft)  # retires version 1
        active.refresh_from_db()
        assert active.status == QuestionnaireVersion.Status.RETIRED
        client = csrf_client()
        response = _create(client, {"questionnaire_version_id": str(active.id)})
        assert response.status_code == 201, response.content
        assert response.json()["questionnaire"]["id"] == str(active.id)

    def test_assigning_a_draft_questionnaire_is_rejected(self):
        draft = QuestionnaireVersion.objects.create(
            version=1, status="draft", schema=CONTRACT_SCHEMA
        )
        client = csrf_client()
        response = _create(client, {"questionnaire_version_id": str(draft.id)})
        assert response.status_code == 400
        assert "questionnaire_version_id" in response.json()["error"]["fields"]
        assert Design.objects.count() == 0

    def test_unknown_questionnaire_id_is_rejected(self):
        client = csrf_client()
        response = _create(client, {"questionnaire_version_id": str(uuid.uuid4())})
        assert response.status_code == 400
        assert "questionnaire_version_id" in response.json()["error"]["fields"]

    def test_questionnaire_assignment_is_immutable(self):
        version = make_active_questionnaire(version=1)
        other = QuestionnaireVersion.objects.create(
            version=2, status="retired", schema=CONTRACT_SCHEMA
        )
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        # Re-sending the SAME id is a harmless no-op.
        same = send_json(
            client,
            "patch",
            design_url(design_id),
            {"questionnaire_version_id": str(version.id)},
            token=token,
        )
        assert same.status_code == 200
        # A DIFFERENT id is rejected.
        changed = send_json(
            client,
            "patch",
            design_url(design_id),
            {"questionnaire_version_id": str(other.id)},
            token=token,
        )
        assert changed.status_code == 400
        assert "questionnaire_version_id" in changed.json()["error"]["fields"]
        assert Design.objects.get(pk=design_id).questionnaire_version_id == version.id


class TestAnswerAutosave:
    def test_partial_autosave_persists_normalised_answers(self):
        version = make_active_questionnaire()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"answers": {"garment_type": "lehenga", "final_notes": "  hi\r\nthere  "}},
            token=token,
        )
        assert response.status_code == 200, response.content
        answers = response.json()["answers"]
        assert answers["garment_type"] == "lehenga"
        assert answers["final_notes"] == "hi\nthere"

    def test_answers_without_a_questionnaire_are_rejected(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = create_design(client, token=token).json()["id"]
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"answers": {"garment_type": "lehenga"}},
            token=token,
        )
        assert response.status_code == 400
        assert "answers" in response.json()["error"]["fields"]

    def test_invalid_answer_option_is_rejected_keyed_by_question(self):
        version = make_active_questionnaire()
        client = csrf_client()
        response = _create(
            client,
            {"questionnaire_version_id": str(version.id), "answers": {"garment_type": "kurta"}},
        )
        assert response.status_code == 400
        assert "garment_type" in response.json()["error"]["fields"]
        assert Design.objects.count() == 0

    def test_create_with_questionnaire_and_answers_together(self):
        version = make_active_questionnaire()
        client = csrf_client()
        response = _create(
            client,
            {
                "questionnaire_version_id": str(version.id),
                "answers": {"garment_type": "lehenga"},
            },
        )
        assert response.status_code == 201, response.content
        assert response.json()["answers"] == {"garment_type": "lehenga"}

    def test_resume_against_a_retired_questionnaire_still_saves(self):
        active = make_active_questionnaire(version=1)
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(active.id)}, token=token
        ).json()["id"]
        # Retire the linked version by activating a newer one.
        draft = QuestionnaireVersion.objects.create(
            version=2, status="draft", schema=CONTRACT_SCHEMA
        )
        activate_questionnaire_version(draft)
        active.refresh_from_db()
        assert active.status == QuestionnaireVersion.Status.RETIRED
        # The design linked to the retired version remains editable/resumable.
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"answers": {"garment_type": "saree", "silhouette": "classic_saree_drape"}},
            token=token,
        )
        assert response.status_code == 200, response.content
        assert response.json()["answers"]["garment_type"] == "saree"


class TestValidateEndpoint:
    def _complete_design(self, client, token):
        version = make_active_questionnaire()
        design_id = _create(
            client,
            {"questionnaire_version_id": str(version.id), "answers": COMPLETE_ANSWERS},
            token=token,
        ).json()["id"]
        return design_id

    def test_validate_succeeds_for_a_complete_draft(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = self._complete_design(client, token)
        response = send_json(client, "post", validate_url(design_id), {}, token=token)
        assert response.status_code == 200, response.content
        assert response.json() == {"valid": True}
        assert response["Cache-Control"] == "no-store"

    def test_validate_fails_for_incomplete_draft_with_question_errors(self):
        version = make_active_questionnaire()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client,
            {"questionnaire_version_id": str(version.id), "answers": {"garment_type": "lehenga"}},
            token=token,
        ).json()["id"]
        response = send_json(client, "post", validate_url(design_id), {}, token=token)
        assert response.status_code == 400
        fields = response.json()["error"]["fields"]
        assert "silhouette" in fields

    def test_validate_without_questionnaire_is_rejected(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = create_design(client, token=token).json()["id"]
        response = send_json(client, "post", validate_url(design_id), {}, token=token)
        assert response.status_code == 400

    def test_validate_requires_csrf(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = self._complete_design(client, token)
        # No CSRF token → JSON 403.
        response = client.post(validate_url(design_id), data="{}", content_type="application/json")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_failed"

    def test_validate_on_foreign_design_is_404(self):
        owner = csrf_client()
        otoken = bootstrap_csrf(owner)
        design_id = self._complete_design(owner, otoken)
        stranger = csrf_client()
        stoken = bootstrap_csrf(stranger)
        response = send_json(stranger, "post", validate_url(design_id), {}, token=stoken)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestCatalogueRetirement:
    """Phase 22 / ADR 0025: the curated catalogue left the product.

    These replace the Phase 7 selection tests. What is asserted now is that the
    write path is GONE rather than quietly ignored, that a design made before
    the retirement keeps its rows and still renders, and that no migration in
    this phase touched anything persisted."""

    def test_creating_with_inspiration_asset_ids_is_a_controlled_unknown_field(self):
        version = make_active_questionnaire()
        client = csrf_client()
        response = _create(
            client,
            {
                "questionnaire_version_id": str(version.id),
                "inspiration_asset_ids": [str(uuid.uuid4())],
            },
        )
        assert response.status_code == 400
        body = response.json()["error"]
        assert body["code"] == "validation_failed"
        # Named explicitly: a silent ignore would teach a client its selection
        # was saved when nothing was.
        assert "inspiration_asset_ids" in body["fields"]

    def test_patching_inspiration_asset_ids_is_a_controlled_unknown_field(self):
        version = make_active_questionnaire()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"inspiration_asset_ids": []},
            token=token,
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_failed"
        assert "inspiration_asset_ids" in response.json()["error"]["fields"]
        assert DesignInspiration.objects.filter(design_id=design_id).count() == 0

    def test_a_historical_selection_still_renders_and_carries_no_asset(self, inmemory_storage):
        # Written directly, because the write path this phase removed is the
        # only thing that ever created one. That is exactly the design an old
        # database holds.
        version = make_active_questionnaire()
        asset = make_eligible_asset()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        DesignInspiration.objects.create(design_id=design_id, inspiration_asset=asset, position=1)

        detail = client.get(design_url(design_id))
        assert detail.status_code == 200
        selection = detail.json()["selected_inspirations"][0]
        assert selection == {"id": str(asset.id), "position": 1, "available": False}
        # The asset is still publicly eligible in the dormant admin, and it is
        # STILL reported unavailable: availability means "usable in a design",
        # and after the retirement nothing is.
        assert "asset" not in selection
        body = detail.content.decode()
        assert asset.image_storage_key not in body
        assert asset.image_sha256 not in body
        assert "inspiration-assets" not in body

    def test_an_ineligible_historical_selection_still_blocks_completion(self, inmemory_storage):
        """Deliberately unchanged. The check mirrors the provider-facing rights
        gate in ``generation.context``; relaxing it here would only move the
        refusal to generation time, where it is less clear."""
        version = make_active_questionnaire()
        asset = make_eligible_asset()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client,
            {"questionnaire_version_id": str(version.id), "answers": COMPLETE_ANSWERS},
            token=token,
        ).json()["id"]
        assert (
            send_json(client, "post", validate_url(design_id), {}, token=token).status_code == 200
        )
        DesignInspiration.objects.create(design_id=design_id, inspiration_asset=asset, position=1)
        retire_inspiration_asset(asset)

        response = send_json(client, "post", validate_url(design_id), {}, token=token)
        assert response.status_code == 400
        assert "inspiration_asset_ids" in response.json()["error"]["fields"]

    def test_the_row_survives_untouched(self, inmemory_storage):
        """The FK is PROTECT and nothing in this phase deletes or rewrites it."""
        version = make_active_questionnaire()
        asset = make_eligible_asset()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        row = DesignInspiration.objects.create(
            design_id=design_id, inspiration_asset=asset, position=1
        )
        client.get(design_url(design_id))
        row.refresh_from_db()
        assert row.inspiration_asset_id == asset.pk
        assert row.position == 1


class TestRetiredCatalogueRoutes:
    """The three public endpoints are gone at the ROUTE layer.

    404 from URL resolution, never a 500 from a view that survived its routes,
    and never a redirect that would suggest a moved surface."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/inspiration-assets/",
            "/api/v1/inspiration-assets",
            f"/api/v1/inspiration-assets/{uuid.uuid4()}/image/",
            f"/api/v1/inspiration-assets/{uuid.uuid4()}/thumbnail/",
        ],
    )
    def test_the_public_catalogue_endpoints_no_longer_resolve(self, path):
        response = csrf_client().get(path)
        assert response.status_code == 404

    def test_no_catalogue_route_name_is_registered(self):
        from django.urls import NoReverseMatch, reverse

        for name in (
            "inspiration-asset-list",
            "inspiration-asset-image",
            "inspiration-asset-thumbnail",
        ):
            with pytest.raises(NoReverseMatch):
                reverse(name)


class TestOwnershipAndPromotion:
    def test_foreign_design_is_404_everywhere(self):
        version = make_active_questionnaire()
        owner = csrf_client()
        design_id = _create(owner, {"questionnaire_version_id": str(version.id)}).json()["id"]
        stranger = csrf_client()
        assert stranger.get(design_url(design_id)).status_code == 404

    def test_anonymous_draft_survives_registration(self):
        version = make_active_questionnaire()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client,
            {"questionnaire_version_id": str(version.id), "answers": {"garment_type": "lehenga"}},
            token=token,
        ).json()["id"]
        from .utils import unique_email

        register(client, unique_email())
        # The next design interaction claims the anonymous workspace.
        detail = client.get(design_url(design_id))
        assert detail.status_code == 200
        assert detail.json()["answers"] == {"garment_type": "lehenga"}


class TestContentTypeAndCaching:
    def test_answers_response_carries_no_store(self):
        version = make_active_questionnaire()
        client = csrf_client()
        token = bootstrap_csrf(client)
        response = _create(client, {"questionnaire_version_id": str(version.id)}, token=token)
        assert response["Cache-Control"] == "no-store"
