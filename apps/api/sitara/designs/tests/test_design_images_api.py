"""Signed design-image URL API tests (Phase 11 Part B, spec §17/§19).

GET /api/v1/designs/<design>/versions/<version>/images/ with real ownership
flows: anonymous workspaces, authenticated accounts, lazy post-login
promotion, indistinguishable 404s, the controlled 409/503 states, and the
no-store/no-referrer/no-provenance response contract. Presigning is a local
computation — no network is touched.
"""

import copy

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.utils import timezone

from sitara.designs.models import Design, DesignSession, DesignVersion

from .utils import (
    bootstrap_csrf,
    create_design,
    csrf_client,
    login,
    logout,
    register,
    unique_email,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def inmemory_design_image_storage(settings):
    storages_config = copy.deepcopy(settings.STORAGES)
    storages_config["design_images"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = storages_config


def _images_url(design_id, version_id) -> str:
    return f"/api/v1/designs/{design_id}/versions/{version_id}/images/"


def _make_owned_design(client) -> str:
    response = create_design(client, title="Signed image test")
    assert response.status_code == 201, response.content
    return response.json()["id"]


def _attach_ingested_version(design_id, *, with_objects=True) -> DesignVersion:
    version = DesignVersion.objects.create(
        design_id=design_id,
        version_number=1,
        design_spec={"schema_version": 1},
        design_spec_schema_version=1,
        design_spec_template_version="v1",
        design_spec_provider="fixture",
        design_spec_model="fixture-model",
        design_spec_generated_at=timezone.now(),
        image_prompt="An API-test prompt.",
        prompt_builder_version="3.0.0",
        image_storage_key=f"design-images/{design_id}/v1/original.webp",
        image_sha256="a" * 64,
        image_size_bytes=1000,
        image_width=1536,
        image_height=2048,
        thumbnail_storage_key=f"design-images/{design_id}/v1/thumbnail.webp",
        thumbnail_sha256="b" * 64,
        thumbnail_size_bytes=100,
        thumbnail_width=384,
        thumbnail_height=512,
        image_processor_version="1.0.0",
        image_ingested_at=timezone.now(),
    )
    if with_objects:
        store = storages["design_images"]
        store.save(version.image_storage_key, ContentFile(b"original"))
        store.save(version.thumbnail_storage_key, ContentFile(b"thumbnail"))
    return version


class TestAuthorisedAccess:
    def test_anonymous_owner_receives_signed_urls(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ingested_version(design_id)
        response = client.get(_images_url(design_id, version.pk))
        assert response.status_code == 200, response.content
        images = response.json()["images"]
        assert images["original"]["url"]
        assert images["original"]["width"] == 1536
        assert images["original"]["height"] == 2048
        assert images["thumbnail"]["url"]
        assert images["thumbnail"]["width"] == 384
        assert images["thumbnail"]["height"] == 512
        assert images["expires_at"]

    def test_authenticated_owner_receives_signed_urls(self):
        client = csrf_client()
        register(client, unique_email())
        design_id = _make_owned_design(client)
        version = _attach_ingested_version(design_id)
        response = client.get(_images_url(design_id, version.pk))
        assert response.status_code == 200, response.content

    def test_anonymous_to_authenticated_promotion_retains_access(self):
        client = csrf_client()
        design_id = _make_owned_design(client)  # anonymous workspace
        version = _attach_ingested_version(design_id)
        email = unique_email()
        register(client, email)
        # The first design-API touch while authenticated performs the lazy
        # claim — the anonymous workspace becomes the user's.
        claimed = client.get(_images_url(design_id, version.pk))
        assert claimed.status_code == 200, claimed.content
        # Access survives a full logout/login cycle: ownership now flows from
        # the account, not the (flushed) browser session pointer.
        logout(client)
        login(client, email)
        response = client.get(_images_url(design_id, version.pk))
        assert response.status_code == 200, response.content

    def test_response_headers_are_no_store_and_no_referrer(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ingested_version(design_id)
        response = client.get(_images_url(design_id, version.pk))
        assert response["Cache-Control"] == "no-store"
        assert response["Referrer-Policy"] == "no-referrer"

    def test_response_contains_no_internal_provenance(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ingested_version(design_id)
        response = client.get(_images_url(design_id, version.pk))
        body = response.json()
        assert set(body) == {"images"}
        assert set(body["images"]) == {"original", "thumbnail", "expires_at"}
        assert set(body["images"]["original"]) == {"url", "width", "height"}
        assert set(body["images"]["thumbnail"]) == {"url", "width", "height"}
        raw = response.content.decode()
        assert version.image_sha256 not in raw
        assert version.thumbnail_sha256 not in raw
        assert version.image_prompt not in raw
        assert "prompt" not in raw
        assert "seed" not in raw
        assert "prediction" not in raw
        assert "staged" not in raw
        assert "1.0.0" not in raw  # processor version stays private

    def test_urls_are_never_logged(self, caplog):
        import logging

        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ingested_version(design_id)
        with caplog.at_level(logging.DEBUG):
            response = client.get(_images_url(design_id, version.pk))
        for url in (
            response.json()["images"]["original"]["url"],
            response.json()["images"]["thumbnail"]["url"],
        ):
            assert url not in caplog.text


class TestIndistinguishable404:
    def _authorised_pair(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ingested_version(design_id)
        return client, design_id, version

    def test_other_session_other_account_and_nonexistent_are_identical_404s(self):
        _owner, design_id, version = self._authorised_pair()

        stranger = csrf_client()
        bootstrap_csrf(stranger)  # a session, but not the owning one
        foreign = stranger.get(_images_url(design_id, version.pk))

        account_client = csrf_client()
        register(account_client, unique_email())
        other_account = account_client.get(_images_url(design_id, version.pk))

        ghost = csrf_client().get(
            _images_url(
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
        version = _attach_ingested_version(first_design)
        # Both resources are OWNED, but the version does not belong to the
        # design named in the path — indistinguishable 404, never a leak.
        response = client.get(_images_url(second_design, version.pk))
        assert response.status_code == 404

    def test_unknown_version_uuid_on_an_owned_design_is_404(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        _attach_ingested_version(design_id)
        response = client.get(_images_url(design_id, "00000000-0000-4000-8000-00000000dead"))
        assert response.status_code == 404
        assert response["Cache-Control"] == "no-store"
        assert response["Referrer-Policy"] == "no-referrer"

    def test_failed_get_creates_no_workspace_or_session_rows(self):
        before = DesignSession.objects.count()
        fresh = csrf_client()  # never bootstrapped: no Django session at all
        response = fresh.get(
            _images_url(
                "11111111-0000-4000-8000-000000000000",
                "11111111-0000-4000-8000-000000000001",
            )
        )
        assert response.status_code == 404
        assert DesignSession.objects.count() == before


class TestControlledFailureStates:
    def test_not_ingested_version_returns_409(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = DesignVersion.objects.create(design_id=design_id, version_number=1)
        response = client.get(_images_url(design_id, version.pk))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "design_image_not_ready"
        assert response["Cache-Control"] == "no-store"
        assert response["Referrer-Policy"] == "no-referrer"

    def test_generated_design_with_staging_only_returns_409(self):
        # A Phase 10 design: generated status, staged raw output, but no
        # permanent ingest — the controlled 409, never an unhandled error.
        client = csrf_client()
        design_id = _make_owned_design(client)
        Design.objects.filter(pk=design_id).update(status=Design.Status.GENERATED)
        version = DesignVersion.objects.create(
            design_id=design_id,
            version_number=1,
            design_spec={"schema_version": 1},
            design_spec_schema_version=1,
            design_spec_template_version="v1",
            design_spec_provider="fixture",
            design_spec_model="fixture-model",
            design_spec_generated_at=timezone.now(),
            image_prompt="A staged-only prompt.",
            prompt_builder_version="3.0.0",
        )
        response = client.get(_images_url(design_id, version.pk))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "design_image_not_ready"

    def test_filesystem_backend_returns_controlled_503(self, settings):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ingested_version(design_id)
        settings.DESIGN_IMAGE_STORAGE_BACKEND = "filesystem"
        response = client.get(_images_url(design_id, version.pk))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "design_image_delivery_unavailable"
        assert response["Cache-Control"] == "no-store"
        assert response["Referrer-Policy"] == "no-referrer"
        raw = response.content.decode()
        assert "file://" not in raw
        assert "/app/" not in raw  # no filesystem path ever leaves the API

    def test_storage_outage_returns_controlled_503(self, monkeypatch, caplog):
        client = csrf_client()
        design_id = _make_owned_design(client)
        version = _attach_ingested_version(design_id)

        class OutageStorage:
            def exists(self, key):
                raise ConnectionError("storage down")

        monkeypatch.setattr("sitara.media.delivery.design_image_storage", lambda: OutageStorage())
        response = client.get(_images_url(design_id, version.pk))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "design_image_delivery_unavailable"
        assert response["Cache-Control"] == "no-store"
        assert response["Referrer-Policy"] == "no-referrer"
        # REL-005: the boundary logs a safe operational signal — row UUID and
        # exception TYPE only, never the raw message or a storage key.
        assert any(
            "design image delivery unavailable" in record.message
            and str(version.pk) in record.getMessage()
            and "ConnectionError" in record.getMessage()
            for record in caplog.records
        )
        assert "storage down" not in caplog.text
        assert version.image_storage_key not in caplog.text
