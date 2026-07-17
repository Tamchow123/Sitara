"""Phase 7 draft API: questionnaire linkage, answers, inspirations, validate.

Everything runs with enforce_csrf_checks=True — the same enforcement a real
browser faces."""

import uuid

import pytest

from sitara.catalogue.models import UsageRights
from sitara.catalogue.services import retire_inspiration_asset
from sitara.catalogue.tests.utils import make_asset_with_image, make_eligible_asset, make_rights
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


class TestInspirationSelections:
    def test_zero_to_three_selections_accepted_and_ordered(self, inmemory_storage):
        version = make_active_questionnaire()
        assets = [make_eligible_asset() for _ in range(3)]
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        ordered_ids = [str(assets[2].id), str(assets[0].id), str(assets[1].id)]
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"inspiration_asset_ids": ordered_ids},
            token=token,
        )
        assert response.status_code == 200, response.content
        selections = response.json()["selected_inspirations"]
        assert [s["id"] for s in selections] == ordered_ids
        assert [s["position"] for s in selections] == [1, 2, 3]
        assert all(s["available"] for s in selections)

    def test_fourth_selection_is_rejected(self, inmemory_storage):
        version = make_active_questionnaire()
        assets = [make_eligible_asset() for _ in range(4)]
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"inspiration_asset_ids": [str(a.id) for a in assets]},
            token=token,
        )
        assert response.status_code == 400
        assert "inspiration_asset_ids" in response.json()["error"]["fields"]
        assert DesignInspiration.objects.filter(design_id=design_id).count() == 0

    def test_duplicate_selection_is_rejected(self, inmemory_storage):
        version = make_active_questionnaire()
        asset = make_eligible_asset()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"inspiration_asset_ids": [str(asset.id), str(asset.id)]},
            token=token,
        )
        assert response.status_code == 400
        assert "inspiration_asset_ids" in response.json()["error"]["fields"]

    def test_ineligible_assets_are_rejected(self, inmemory_storage):
        version = make_active_questionnaire()
        # A draft (unapproved) asset with an image but no approval.
        draft_asset = make_asset_with_image(usage_rights=make_rights(verified=True))
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"inspiration_asset_ids": [str(draft_asset.id)]},
            token=token,
        )
        assert response.status_code == 400
        assert "inspiration_asset_ids" in response.json()["error"]["fields"]

    def test_retired_selection_becomes_unavailable_without_private_data(self, inmemory_storage):
        version = make_active_questionnaire()
        asset = make_eligible_asset()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client,
            {
                "questionnaire_version_id": str(version.id),
                "inspiration_asset_ids": [str(asset.id)],
            },
            token=token,
        ).json()["id"]
        # Retire the asset AFTER it was selected.
        retire_inspiration_asset(asset)
        detail = client.get(design_url(design_id)).json()
        selection = detail["selected_inspirations"][0]
        assert selection["available"] is False
        assert selection["asset"] is None
        assert selection["id"] == str(asset.id)
        # No storage key, hash, rights evidence or internal note leaks.
        body = client.get(design_url(design_id)).content.decode()
        assert asset.image_storage_key not in body
        assert asset.image_sha256 not in body

    def test_complete_validation_fails_while_a_selection_is_unavailable(self, inmemory_storage):
        version = make_active_questionnaire()
        asset = make_eligible_asset()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client,
            {
                "questionnaire_version_id": str(version.id),
                "answers": COMPLETE_ANSWERS,
                "inspiration_asset_ids": [str(asset.id)],
            },
            token=token,
        ).json()["id"]
        # Valid while eligible.
        assert (
            send_json(client, "post", validate_url(design_id), {}, token=token).status_code == 200
        )
        retire_inspiration_asset(asset)
        response = send_json(client, "post", validate_url(design_id), {}, token=token)
        assert response.status_code == 400
        assert "inspiration_asset_ids" in response.json()["error"]["fields"]

    def test_selection_can_be_replaced_and_reordered(self, inmemory_storage):
        version = make_active_questionnaire()
        first = make_eligible_asset()
        second = make_eligible_asset()
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client,
            {
                "questionnaire_version_id": str(version.id),
                "inspiration_asset_ids": [str(first.id)],
            },
            token=token,
        ).json()["id"]
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"inspiration_asset_ids": [str(second.id)]},
            token=token,
        )
        assert response.status_code == 200
        selections = response.json()["selected_inspirations"]
        assert [s["id"] for s in selections] == [str(second.id)]
        assert DesignInspiration.objects.filter(design_id=design_id).count() == 1


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

    def test_expired_rights_selection_is_rejected(self, inmemory_storage):
        from datetime import timedelta

        from django.utils import timezone

        version = make_active_questionnaire()
        now = timezone.now()
        # Verified long ago with a future expiry (so approval succeeds), then
        # move expiry into the past — still after verified_at, satisfying the
        # DB constraint — so the asset stops being publicly eligible.
        rights = make_rights(
            verified=True,
            verified_at=now - timedelta(days=10),
            expires_at=now + timedelta(days=1),
        )
        asset = make_eligible_asset(rights=rights)
        UsageRights.objects.filter(pk=rights.pk).update(expires_at=now - timedelta(days=1))
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _create(
            client, {"questionnaire_version_id": str(version.id)}, token=token
        ).json()["id"]
        response = send_json(
            client,
            "patch",
            design_url(design_id),
            {"inspiration_asset_ids": [str(asset.id)]},
            token=token,
        )
        assert response.status_code == 400
        assert "inspiration_asset_ids" in response.json()["error"]["fields"]
