"""The narrow references read (Phase 22).

The phone-handoff panel asks one question every couple of seconds for as long
as a code is live: has a photograph arrived yet? It used to ask it by fetching
the whole design — questionnaire schema, saved answers, job snapshot and all —
over the same shop wifi the customer's phone was using to push the photograph.

So the thing worth testing here is mostly what the payload does NOT carry, and
that narrowing it did not quietly hand anyone a new way in.
"""

import io
import uuid

import pytest
from PIL import Image

from sitara.designs.models import Design

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_owned_design_id,
    csrf_client,
    send_json,
    unique_ip,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("inmemory_storage")]


def references_url(design_id) -> str:
    return f"{DESIGNS_URL}{design_id}/references/"


def _png_bytes(colour=(200, 30, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 60), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(client, design_id, *, colour=(200, 30, 60)):
    token = bootstrap_csrf(client)
    return client.post(
        f"{DESIGNS_URL}{design_id}/inspiration-uploads/",
        data={
            "image": io.BytesIO(_png_bytes(colour)),
            "rights_acknowledged": "true",
        },
        HTTP_X_CSRFTOKEN=token,
        REMOTE_ADDR=unique_ip(),
    )


class TestThePayload:
    def test_it_returns_the_design_s_uploads_in_position_order(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        assert upload(client, design_id, colour=(10, 20, 30)).status_code == 201
        assert upload(client, design_id, colour=(40, 50, 60)).status_code == 201

        response = client.get(references_url(design_id), REMOTE_ADDR=unique_ip())

        assert response.status_code == 200, response.content
        uploads = response.json()["inspiration_uploads"]
        assert [item["position"] for item in uploads] == [1, 2]

    def test_it_carries_nothing_but_the_uploads(self):
        """The whole point of the endpoint.

        Asserted as an exact key set, not as "the schema is absent": a payload
        that grows back one field at a time is exactly how a narrow read stops
        being narrow, and the next person to add one should have to delete this
        line to do it."""
        client = csrf_client()
        design_id = create_owned_design_id(client)
        upload(client, design_id)

        body = client.get(references_url(design_id), REMOTE_ADDR=unique_ip()).json()

        assert set(body) == {"inspiration_uploads"}
        for absent in (
            "questionnaire",
            "answers",
            "latest_job",
            "selected_inspirations",
            "title",
            "status",
        ):
            assert absent not in body

    def test_an_upload_exposes_no_storage_key_hash_or_byte_size(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        upload(client, design_id)

        item = client.get(references_url(design_id), REMOTE_ADDR=unique_ip()).json()[
            "inspiration_uploads"
        ][0]

        assert set(item) == {
            "id",
            "position",
            "width",
            "height",
            "rights_acknowledged_at",
            "created_at",
        }

    def test_a_design_with_no_uploads_is_an_empty_list_not_a_404(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = client.get(references_url(design_id), REMOTE_ADDR=unique_ip())

        assert response.status_code == 200
        assert response.json() == {"inspiration_uploads": []}

    def test_it_is_not_cached(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = client.get(references_url(design_id), REMOTE_ADDR=unique_ip())

        assert response["Cache-Control"] == "no-store"


class TestOwnership:
    def test_a_foreign_design_is_the_same_404_as_a_nonexistent_one(self):
        owner = csrf_client()
        design_id = create_owned_design_id(owner)
        stranger = csrf_client()
        bootstrap_csrf(stranger)

        foreign = stranger.get(references_url(design_id), REMOTE_ADDR=unique_ip())
        missing = stranger.get(references_url(uuid.uuid4()), REMOTE_ADDR=unique_ip())

        assert foreign.status_code == missing.status_code == 404
        assert foreign.content == missing.content

    def test_a_stranger_learns_nothing_about_what_the_design_holds(self):
        owner = csrf_client()
        design_id = create_owned_design_id(owner)
        upload(owner, design_id)
        stranger = csrf_client()
        bootstrap_csrf(stranger)

        body = stranger.get(references_url(design_id), REMOTE_ADDR=unique_ip()).content.decode()

        assert str(design_id) not in body
        assert "inspiration_uploads" not in body

    def test_it_only_answers_get(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        token = bootstrap_csrf(client)

        for method in ("post", "put", "patch", "delete"):
            response = send_json(client, method, references_url(design_id), {}, token=token)
            assert response.status_code == 405, method

    def test_a_read_does_not_create_a_workspace(self):
        """A GET for a design that does not exist must not leave a session
        workspace behind — the same rule the list endpoint follows."""
        from sitara.designs.models import DesignSession

        stranger = csrf_client()
        before = DesignSession.objects.count()

        stranger.get(references_url(uuid.uuid4()), REMOTE_ADDR=unique_ip())

        assert DesignSession.objects.count() == before


class TestItAgreesWithTheDesignDetail:
    def test_the_two_payloads_carry_the_same_uploads(self):
        """The narrow read is a projection, not a second source of truth.

        If they ever disagree, the panel is watching something the rest of the
        app does not believe."""
        client = csrf_client()
        design_id = create_owned_design_id(client)
        upload(client, design_id, colour=(10, 20, 30))
        upload(client, design_id, colour=(40, 50, 60))

        narrow = client.get(references_url(design_id), REMOTE_ADDR=unique_ip()).json()
        detail = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip()).json()

        assert narrow["inspiration_uploads"] == detail["inspiration_uploads"]


def test_a_reference_read_still_pushes_the_idle_deadline_back(settings):
    """The panel polls this and nothing else while a code is live.

    If it did not count as activity, a stylist watching the handoff panel for
    longer than the timeout would have the workspace released out from under
    her — by the very screen she was staring at."""
    from django.utils import timezone

    from sitara.designs.models import DesignSession

    settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 3600
    client = csrf_client()
    design_id = create_owned_design_id(client)
    workspace = Design.objects.get(pk=design_id).design_session
    DesignSession.objects.filter(pk=workspace.pk).update(
        last_seen_at=timezone.now() - timezone.timedelta(minutes=30)
    )
    stale = DesignSession.objects.get(pk=workspace.pk).last_seen_at

    client.get(references_url(design_id), REMOTE_ADDR=unique_ip())

    assert DesignSession.objects.get(pk=workspace.pk).last_seen_at > stale
