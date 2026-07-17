"""Ingestion service tests: storage atomicity, cleanup and log safety."""

import hashlib

import pytest
from django.core.files.storage import default_storage

from sitara.catalogue.models import InspirationAsset
from sitara.catalogue.services import AssetIngestError, ingest_inspiration_image

from .conftest import ORIGINAL_DEFAULT_STORAGE
from .utils import (
    list_all_storage_keys,
    make_asset,
    make_eligible_asset,
    make_image_bytes,
    make_upload,
)

pytestmark = pytest.mark.django_db

_SECRET_NAME = "leak-marker-original-name.jpg"


class TestIngest:
    def test_successful_ingest_stores_exactly_the_two_derivatives(self):
        asset = make_asset()
        original = make_image_bytes()
        ingest_inspiration_image(asset, make_upload(original, name=_SECRET_NAME))
        asset.refresh_from_db()

        keys = sorted(list_all_storage_keys())
        assert keys == sorted([asset.image_storage_key, asset.thumbnail_storage_key])
        # The raw original was never stored: nothing carries its name and
        # neither stored object holds its bytes.
        for key in keys:
            assert "leak-marker" not in key
            with default_storage.open(key, "rb") as handle:
                assert handle.read() != original

    def test_stored_sha256_matches_the_stored_main_object(self):
        asset = make_asset()
        ingest_inspiration_image(asset, make_upload(make_image_bytes()))
        asset.refresh_from_db()
        with default_storage.open(asset.image_storage_key, "rb") as handle:
            stored = handle.read()
        assert asset.image_sha256 == hashlib.sha256(stored).hexdigest()
        assert asset.image_size_bytes == len(stored)
        assert asset.image_width and asset.image_height

    def test_rejected_upload_leaves_row_and_storage_untouched(self):
        asset = make_asset()
        with pytest.raises(Exception, match="decodable"):
            ingest_inspiration_image(asset, make_upload(b"not an image"))
        asset.refresh_from_db()
        assert asset.image_storage_key == ""
        assert list_all_storage_keys() == []

    def test_non_draft_asset_is_refused(self):
        asset = make_eligible_asset()
        with pytest.raises(AssetIngestError, match="draft"):
            ingest_inspiration_image(asset, make_upload(make_image_bytes()))

    def test_existing_image_is_never_overwritten(self):
        asset = make_asset()
        ingest_inspiration_image(asset, make_upload(make_image_bytes()))
        asset.refresh_from_db()
        first_key = asset.image_storage_key
        with pytest.raises(AssetIngestError, match="already has a processed image"):
            ingest_inspiration_image(asset, make_upload(make_image_bytes()))
        asset.refresh_from_db()
        assert asset.image_storage_key == first_key

    def test_partial_storage_write_is_cleaned_up(self, monkeypatch, caplog):
        asset = make_asset()
        real_save = default_storage.save
        written = []
        calls = {"count": 0}

        def flaky_save(name, content, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated-storage-outage-detail")
            saved = real_save(name, content, **kwargs)
            written.append(saved)
            return saved

        monkeypatch.setattr(default_storage, "save", flaky_save)
        with caplog.at_level("ERROR"):
            with pytest.raises(AssetIngestError, match="could not be stored"):
                ingest_inspiration_image(asset, make_upload(make_image_bytes(), name=_SECRET_NAME))

        # The first (successful) object was removed again...
        assert written and not default_storage.exists(written[0])
        # ...the row never gained keys...
        asset.refresh_from_db()
        assert asset.image_storage_key == ""
        # ...and the log carries only operation, asset id and exception type.
        assert f"inspiration_asset_id={asset.pk}" in caplog.text
        assert "exception_type=OSError" in caplog.text
        assert "simulated-storage-outage-detail" not in caplog.text
        assert "leak-marker" not in caplog.text

    def test_database_failure_cleans_up_both_objects(self, monkeypatch, caplog):
        asset = make_asset()
        real_save = default_storage.save
        written = []

        def recording_save(name, content, **kwargs):
            saved = real_save(name, content, **kwargs)
            written.append(saved)
            return saved

        def exploding_model_save(self, *args, **kwargs):
            raise RuntimeError("simulated-database-outage-detail")

        monkeypatch.setattr(default_storage, "save", recording_save)
        monkeypatch.setattr(InspirationAsset, "save", exploding_model_save)
        with caplog.at_level("ERROR"):
            with pytest.raises(AssetIngestError, match="could not be attached"):
                ingest_inspiration_image(asset, make_upload(make_image_bytes(), name=_SECRET_NAME))

        assert len(written) == 2
        for key in written:
            assert not default_storage.exists(key)
        asset.refresh_from_db()
        assert asset.image_storage_key == ""
        assert "exception_type=RuntimeError" in caplog.text
        assert "simulated-database-outage-detail" not in caplog.text
        assert "leak-marker" not in caplog.text

    def test_no_storage_secrets_appear_in_logs(self, monkeypatch, caplog):
        asset = make_asset()

        def always_fails(name, content, **kwargs):
            raise OSError("endpoint http://minio:9000 credentials sitara-minio")

        monkeypatch.setattr(default_storage, "save", always_fails)
        with caplog.at_level("ERROR"):
            with pytest.raises(AssetIngestError):
                ingest_inspiration_image(asset, make_upload(make_image_bytes()))
        assert "minio" not in caplog.text
        assert "9000" not in caplog.text


class TestStorageSafety:
    def test_shipped_default_storage_is_private(self):
        """The REAL (pre-test-override) storage configuration stays
        private: no public ACL, signed query auth, no overwrites."""
        assert ORIGINAL_DEFAULT_STORAGE["BACKEND"] == "storages.backends.s3.S3Storage"
        options = ORIGINAL_DEFAULT_STORAGE["OPTIONS"]
        assert options["default_acl"] is None
        assert options["querystring_auth"] is True
        assert options["file_overwrite"] is False

    def test_ingest_never_overrides_storage_acls(self, monkeypatch):
        """The service hands storage exactly (key, content) — no ACL, no
        per-object overrides that could weaken the private default."""
        asset = make_asset()
        real_save = default_storage.save
        seen_kwargs = []

        def recording_save(name, content, **kwargs):
            seen_kwargs.append(kwargs)
            return real_save(name, content, **kwargs)

        monkeypatch.setattr(default_storage, "save", recording_save)
        ingest_inspiration_image(asset, make_upload(make_image_bytes()))
        assert seen_kwargs == [{}, {}]
