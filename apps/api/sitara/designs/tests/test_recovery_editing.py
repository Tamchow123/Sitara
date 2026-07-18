"""Design lifecycle editability (Phase 10): only a draft — or a
generation_failed design with no DesignVersion, which returns to draft — may be
edited. Every other state is a controlled ``design_not_editable``.
"""

import pytest

from sitara.designs.models import Design, DesignSession, DesignVersion
from sitara.designs.services import DraftUpdateError, update_design_draft

from .utils import bootstrap_csrf, csrf_client, send_json

pytestmark = pytest.mark.django_db


def _design(status=Design.Status.DRAFT) -> Design:
    return Design.objects.create(design_session=DesignSession.objects.create(), status=status)


class TestServiceEditability:
    def test_draft_is_editable(self):
        design = _design()
        update_design_draft(design, title="hello")
        design.refresh_from_db()
        assert design.title == "hello"
        assert design.status == Design.Status.DRAFT

    def test_failed_without_version_is_recoverable_and_returns_to_draft(self):
        design = _design(Design.Status.GENERATION_FAILED)
        update_design_draft(design, title="retry")
        design.refresh_from_db()
        assert design.title == "retry"
        assert design.status == Design.Status.DRAFT

    def test_failed_with_version_is_not_editable(self):
        design = _design(Design.Status.GENERATION_FAILED)
        DesignVersion.objects.create(design=design, version_number=1)
        with pytest.raises(DraftUpdateError) as exc:
            update_design_draft(design, title="nope")
        assert exc.value.code == "design_not_editable"

    def test_draft_with_existing_version_is_not_editable(self):
        # Legacy Phase 8/9 shape: the management command created a version
        # while the status stayed draft. The version freezes the inputs.
        design = _design(Design.Status.DRAFT)
        DesignVersion.objects.create(design=design, version_number=1)
        with pytest.raises(DraftUpdateError) as exc:
            update_design_draft(design, title="nope")
        assert exc.value.code == "design_not_editable"

    def test_generating_is_not_editable(self):
        design = _design(Design.Status.GENERATING)
        with pytest.raises(DraftUpdateError) as exc:
            update_design_draft(design, title="nope")
        assert exc.value.code == "design_not_editable"

    def test_generated_is_not_editable(self):
        design = _design(Design.Status.GENERATED)
        with pytest.raises(DraftUpdateError) as exc:
            update_design_draft(design, title="nope")
        assert exc.value.code == "design_not_editable"


class TestPatchApiConflict:
    def test_patch_on_draft_with_version_returns_409(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        created = send_json(client, "post", "/api/v1/designs/", {"title": "legacy"}, token=token)
        design_id = created.json()["id"]
        DesignVersion.objects.create(design_id=design_id, version_number=1)
        response = send_json(
            client, "patch", f"/api/v1/designs/{design_id}/", {"title": "edit"}, token=token
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "design_not_editable"

    def test_patch_on_generated_design_returns_409(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        created = send_json(client, "post", "/api/v1/designs/", {"title": "x"}, token=token)
        design_id = created.json()["id"]
        # Force the design into a terminal generated state.
        Design.objects.filter(pk=design_id).update(status=Design.Status.GENERATED)
        response = send_json(
            client, "patch", f"/api/v1/designs/{design_id}/", {"title": "edit"}, token=token
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "design_not_editable"
