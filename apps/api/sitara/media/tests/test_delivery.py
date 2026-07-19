"""Signed design-image delivery service tests (Phase 11 Part B, spec §15/§19).

Zero network: presigning is a purely local computation (boto3 builds the URL
without any request), the storage double is in-memory, and the conftest
socket guard fails loudly on any accidental connection.
"""

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.utils import timezone

from sitara.designs.models import DesignVersion
from sitara.generation.tests.factory import make_complete_design
from sitara.media.delivery import S3DesignImageSigner, issue_design_image_urls
from sitara.media.exceptions import (
    DesignImageDeliveryUnavailable,
    DesignImageNotReady,
)

pytestmark = pytest.mark.django_db


class RecordingSigner:
    """Captures exactly what the service asks a signer to do."""

    def __init__(self):
        self.calls = []

    def sign_get(self, key, *, ttl_seconds, filename):
        self.calls.append({"key": key, "ttl_seconds": ttl_seconds, "filename": filename})
        return f"https://signed.example/{filename}?X-Amz-Expires={ttl_seconds}"


def _ingested_version(*, with_objects=True) -> DesignVersion:
    design = make_complete_design()
    version = DesignVersion.objects.create(
        design=design,
        version_number=1,
        design_spec={"schema_version": 1},
        design_spec_schema_version=1,
        design_spec_template_version="v1",
        design_spec_provider="fixture",
        design_spec_model="fixture-model",
        design_spec_generated_at=timezone.now(),
        image_prompt="A delivery-test prompt.",
        prompt_builder_version="3.0.0",
        image_storage_key=f"design-images/{design.id}/v1/original.webp",
        image_sha256="a" * 64,
        image_size_bytes=1000,
        image_width=1536,
        image_height=2048,
        thumbnail_storage_key=f"design-images/{design.id}/v1/thumbnail.webp",
        thumbnail_sha256="b" * 64,
        thumbnail_size_bytes=100,
        thumbnail_width=384,
        thumbnail_height=512,
        image_processor_version="1.0.0",
        image_ingested_at=timezone.now(),
    )
    if with_objects:
        store = storages["design_images"]
        store.save(version.image_storage_key, ContentFile(b"original-bytes"))
        store.save(version.thumbnail_storage_key, ContentFile(b"thumbnail-bytes"))
    return version


class TestPreconditions:
    def test_incomplete_provenance_raises_not_ready(self):
        design = make_complete_design()
        bare = DesignVersion.objects.create(design=design, version_number=1)
        with pytest.raises(DesignImageNotReady):
            issue_design_image_urls(bare, signer=RecordingSigner())

    def test_filesystem_backend_fails_closed(self, settings):
        version = _ingested_version()
        settings.DESIGN_IMAGE_STORAGE_BACKEND = "filesystem"
        with pytest.raises(DesignImageDeliveryUnavailable):
            issue_design_image_urls(version, signer=RecordingSigner())

    def test_storage_outage_is_controlled_unavailability(self):
        version = _ingested_version()

        class OutageStorage:
            def exists(self, key):
                raise ConnectionError("storage down")

        with pytest.raises(DesignImageDeliveryUnavailable):
            issue_design_image_urls(version, signer=RecordingSigner(), storage=OutageStorage())

    def test_missing_object_is_controlled_unavailability(self):
        version = _ingested_version(with_objects=False)
        with pytest.raises(DesignImageDeliveryUnavailable):
            issue_design_image_urls(version, signer=RecordingSigner())

    def test_each_object_is_checked_independently(self):
        # BOTH keys must be confirmed: a missing thumbnail alone (the
        # realistic partial-crash shape) must refuse, and so must a missing
        # original alone — proving the per-key loop never short-circuits.
        version = _ingested_version()
        store = storages["design_images"]

        store.delete(version.thumbnail_storage_key)  # only the thumbnail gone
        with pytest.raises(DesignImageDeliveryUnavailable):
            issue_design_image_urls(version, signer=RecordingSigner())

        store.save(version.thumbnail_storage_key, ContentFile(b"thumbnail-bytes"))
        store.delete(version.image_storage_key)  # only the original gone
        with pytest.raises(DesignImageDeliveryUnavailable):
            issue_design_image_urls(version, signer=RecordingSigner())

    def test_signer_construction_failure_is_controlled_unavailability(self, monkeypatch):
        # REL-001/TEST-005: the DEFAULT (non-injected) signer's construction
        # has the broadest botocore exception surface (credential/region/data
        # resolution) — a failure there must also classify into the
        # controlled unavailability, proving construction sits INSIDE the
        # classified block.
        version = _ingested_version()

        def exploding_constructor(*args, **kwargs):
            raise RuntimeError("botocore data loading failed")

        monkeypatch.setattr("sitara.media.delivery.S3DesignImageSigner", exploding_constructor)
        with pytest.raises(DesignImageDeliveryUnavailable) as exc:
            issue_design_image_urls(version)
        assert "botocore" not in str(exc.value)

    def test_signing_failure_is_controlled_unavailability(self):
        # REL-001: ANY failure inside signer construction/presigning must
        # classify into the controlled unavailability — never escape as an
        # unhandled botocore exception (the endpoint's taxonomy is 404/409/503).
        version = _ingested_version()

        class ExplodingSigner:
            def sign_get(self, key, *, ttl_seconds, filename):
                raise RuntimeError("botocore internal validation error")

        with pytest.raises(DesignImageDeliveryUnavailable) as exc:
            issue_design_image_urls(version, signer=ExplodingSigner())
        assert "botocore" not in str(exc.value)


class TestIssuance:
    def test_signer_receives_the_exact_ttl_keys_and_safe_filenames(self, settings):
        settings.DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS = 120
        version = _ingested_version()
        signer = RecordingSigner()
        issued = issue_design_image_urls(version, signer=signer)
        assert [call["key"] for call in signer.calls] == [
            version.image_storage_key,
            version.thumbnail_storage_key,
        ]
        assert all(call["ttl_seconds"] == 120 for call in signer.calls)
        assert [call["filename"] for call in signer.calls] == [
            "design-original.webp",
            "design-thumbnail.webp",
        ]
        assert issued.original_url != issued.thumbnail_url

    def test_explicit_ttl_override_reaches_the_signer(self):
        version = _ingested_version()
        signer = RecordingSigner()
        issue_design_image_urls(version, ttl_seconds=45, signer=signer)
        assert all(call["ttl_seconds"] == 45 for call in signer.calls)

    def test_mocked_signing_time_proves_the_declared_expiry(self, settings):
        settings.DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS = 300
        version = _ingested_version()
        fixed_now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
        issued = issue_design_image_urls(version, signer=RecordingSigner(), now=fixed_now)
        assert issued.expires_at == fixed_now + timedelta(seconds=300)

    def test_both_urls_share_one_declared_expiry(self):
        version = _ingested_version()
        issued = issue_design_image_urls(version, signer=RecordingSigner())
        # One dataclass field: a single instant for BOTH URLs by construction.
        assert issued.expires_at is not None

    def test_urls_are_not_persisted_on_the_version(self):
        version = _ingested_version()
        issued = issue_design_image_urls(version, signer=RecordingSigner())
        version.refresh_from_db()
        for field in [f.name for f in DesignVersion._meta.fields]:
            value = getattr(version, field)
            if isinstance(value, str):
                assert issued.original_url not in value
                assert issued.thumbnail_url not in value

    def test_urls_and_keys_never_reach_the_logs(self, caplog):
        version = _ingested_version()
        with caplog.at_level(logging.DEBUG):
            issued = issue_design_image_urls(version, signer=RecordingSigner())
        assert issued.original_url not in caplog.text
        assert issued.thumbnail_url not in caplog.text
        assert version.image_storage_key not in caplog.text


class TestRealSigner:
    def test_presigned_url_targets_the_signing_endpoint_with_bounded_expiry(self, settings):
        settings.S3_SIGNED_URL_ENDPOINT_URL = "http://localhost:9000"
        version = _ingested_version()
        issued = issue_design_image_urls(version, ttl_seconds=90, signer=S3DesignImageSigner())
        parts = urlsplit(issued.original_url)
        assert parts.scheme == "http"
        assert parts.netloc == "localhost:9000"
        # Path-style addressing: bucket then the exact private key.
        assert parts.path == f"/{settings.S3_BUCKET_NAME}/{version.image_storage_key}"
        query = parse_qs(parts.query)
        assert query["X-Amz-Expires"] == ["90"]  # bounded expiry embedded
        assert "X-Amz-Signature" in query  # SigV4
        assert query["response-content-type"] == ["image/webp"]
        assert query["response-content-disposition"] == ['inline; filename="design-original.webp"']
        # No credential material beyond the standard SigV4 query auth fields.
        assert settings.S3_SECRET_ACCESS_KEY not in issued.original_url
        # The THUMBNAIL URL carries the same contract independently.
        thumb_parts = urlsplit(issued.thumbnail_url)
        assert thumb_parts.netloc == "localhost:9000"
        assert thumb_parts.path == f"/{settings.S3_BUCKET_NAME}/{version.thumbnail_storage_key}"
        thumb_query = parse_qs(thumb_parts.query)
        assert thumb_query["X-Amz-Expires"] == ["90"]
        assert thumb_query["response-content-type"] == ["image/webp"]
        assert thumb_query["response-content-disposition"] == [
            'inline; filename="design-thumbnail.webp"'
        ]
        assert settings.S3_SECRET_ACCESS_KEY not in issued.thumbnail_url
