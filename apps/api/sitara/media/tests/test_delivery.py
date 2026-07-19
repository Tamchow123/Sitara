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

    def sign_get(self, key, *, ttl_seconds, filename, disposition="inline"):
        self.calls.append(
            {
                "key": key,
                "ttl_seconds": ttl_seconds,
                "filename": filename,
                "disposition": disposition,
            }
        )
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


@pytest.fixture(autouse=True)
def shared_pool_left_fully_idle():
    """Fail the LEAKING test, not a later victim.

    The existence pool is module-level and shared across the whole test
    session, so a test that submits a blocking storage double MUST release
    it (the try/finally convention used below) or it would silently consume
    shared worker capacity for the rest of the session. After every test in
    this module a barrier sized to the whole pool proves all workers are
    free again — it can only trip if every worker picks up a probe
    simultaneously — so a leaked still-blocked double fails here, at its
    source, instead of surfacing as mystery timeouts in unrelated tests.
    """
    import threading

    from sitara.media.delivery import _EXISTENCE_POOL_WORKERS, _existence_pool

    yield
    barrier = threading.Barrier(_EXISTENCE_POOL_WORKERS + 1)
    probes = [_existence_pool.submit(barrier.wait, 10) for _ in range(_EXISTENCE_POOL_WORKERS)]
    try:
        barrier.wait(10)
    except threading.BrokenBarrierError:
        pytest.fail(
            "a design-image-exists pool worker is still blocked — this test "
            "leaked a hung storage double without releasing it"
        )
    finally:
        for probe in probes:
            probe.cancel()


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
        # Construction-failure guard: the DEFAULT (non-injected) signer's construction
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
        # Signing-failure guard: ANY failure inside signer construction/presigning must
        # classify into the controlled unavailability — never escape as an
        # unhandled botocore exception (the endpoint's taxonomy is 404/409/503).
        version = _ingested_version()

        class ExplodingSigner:
            def sign_get(self, key, *, ttl_seconds, filename, disposition="inline"):
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
            version.image_storage_key,
            version.thumbnail_storage_key,
        ]
        assert all(call["ttl_seconds"] == 120 for call in signer.calls)
        assert [call["filename"] for call in signer.calls] == [
            "design-original.webp",
            "sitara-concept.webp",
            "design-thumbnail.webp",
        ]
        assert [call["disposition"] for call in signer.calls] == [
            "inline",
            "attachment",
            "inline",
        ]
        assert issued.original_url != issued.thumbnail_url
        assert issued.original_download_url != issued.original_url
        assert issued.original_download_url != issued.thumbnail_url

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
                assert issued.original_download_url not in value
                assert issued.thumbnail_url not in value

    def test_urls_and_keys_never_reach_the_logs(self, caplog):
        version = _ingested_version()
        with caplog.at_level(logging.DEBUG):
            issued = issue_design_image_urls(version, signer=RecordingSigner())
        assert issued.original_url not in caplog.text
        assert issued.original_download_url not in caplog.text
        assert issued.thumbnail_url not in caplog.text
        assert version.image_storage_key not in caplog.text


class TestBoundedConcurrentChecks:
    """The delivery-side latency contract: both existence checks run
    CONCURRENTLY and the whole phase is bounded by the in-process deadline —
    the two properties that keep the controlled 503 inside the browser's
    fixed 5s transport abort (apps/web/src/lib/transport.ts)."""

    def test_deadline_fits_the_frontend_transport_budget(self):
        from sitara.media.delivery import EXISTENCE_DEADLINE_SECONDS

        # 5s transport budget minus network/app overhead: the storage phase
        # must leave real slack for the rest of the request cycle.
        assert EXISTENCE_DEADLINE_SECONDS <= 4

    def test_existence_checks_run_concurrently(self):
        # Deterministic overlap proof: each exists() blocks on a two-party
        # barrier, so the call can only complete if BOTH checks are in
        # flight simultaneously. A sequential implementation deadlocks the
        # first call until its barrier timeout and fails this test.
        import threading

        barrier = threading.Barrier(2)

        class BarrierStorage:
            def exists(self, key):
                barrier.wait(timeout=5)
                return True

        version = _ingested_version(with_objects=False)
        issued = issue_design_image_urls(
            version, signer=RecordingSigner(), storage=BarrierStorage()
        )
        assert issued.original_url and issued.thumbnail_url

    def test_hung_storage_hits_the_deadline_not_the_client_timeout(self):
        # A check that never returns must surface as the controlled 503
        # within the in-process deadline — independent of the storage
        # client's own (ingest-sized) timeouts. The worker is released via
        # the event afterwards so no thread outlives the test.
        import threading
        import time as time_module

        release = threading.Event()

        class HungStorage:
            def exists(self, key):
                release.wait(timeout=30)
                return True

        from sitara.media.delivery import EXISTENCE_DEADLINE_SECONDS

        version = _ingested_version(with_objects=False)
        started = time_module.monotonic()
        try:
            with pytest.raises(DesignImageDeliveryUnavailable):
                issue_design_image_urls(version, signer=RecordingSigner(), storage=HungStorage())
            elapsed = time_module.monotonic() - started
            assert elapsed < 5  # well inside the browser budget
            # And AT the declared deadline — a near-zero-deadline regression
            # (broken arithmetic, stray early return) must fail here too.
            assert elapsed >= EXISTENCE_DEADLINE_SECONDS - 0.5
        finally:
            release.set()

    def test_abandoned_checks_never_grow_threads_beyond_the_shared_pool(self):
        # The pool is module-level and bounded: repeated deadline-hitting
        # requests saturate the fixed worker cap instead of growing thread
        # count with request volume. The final request runs against an
        # ALREADY-FULL pool, proving the circuit-breaker path: its checks
        # stay queued, the deadline still expires on time, and the
        # per-request cancel() drops them without them EVER executing.
        import threading
        import time as time_module

        from sitara.media.delivery import (
            _EXISTENCE_POOL_WORKERS,
            EXISTENCE_DEADLINE_SECONDS,
        )

        releases = []
        exists_counters = []

        def make_hung_storage():
            # Per-request factory (not a class like the other doubles): each
            # request needs its own release event and exists() call counter.
            release = threading.Event()
            releases.append(release)
            counter = {"exists_calls": 0}
            exists_counters.append(counter)

            class OneShotHung:
                def exists(self, key):
                    counter["exists_calls"] += 1
                    release.wait(timeout=30)
                    return True

            return OneShotHung()

        def pool_thread_count():
            return len(
                [
                    thread
                    for thread in threading.enumerate()
                    if thread.name.startswith("design-image-exists")
                ]
            )

        version = _ingested_version(with_objects=False)
        # Exactly enough requests to occupy every worker slot (two checks
        # each), parametrised so the test stays conclusive if the cap
        # changes. The past-saturation request below would push a
        # per-request-executor regression OVER the cap and fail the ceiling
        # assertion — and its checks would run, failing the never-executed
        # assertion too.
        saturating_requests = _EXISTENCE_POOL_WORKERS // 2
        try:
            for _ in range(saturating_requests):
                with pytest.raises(DesignImageDeliveryUnavailable):
                    issue_design_image_urls(
                        version, signer=RecordingSigner(), storage=make_hung_storage()
                    )
                assert pool_thread_count() <= _EXISTENCE_POOL_WORKERS
            # Past saturation: every worker is still hung, so this request's
            # checks never leave the queue.
            started = time_module.monotonic()
            with pytest.raises(DesignImageDeliveryUnavailable):
                issue_design_image_urls(
                    version, signer=RecordingSigner(), storage=make_hung_storage()
                )
            elapsed = time_module.monotonic() - started
            # The controlled 503 lands ON the deadline — queueing behind a
            # full pool must not stretch the wait beyond it.
            assert elapsed < EXISTENCE_DEADLINE_SECONDS + 1.0
            assert pool_thread_count() <= _EXISTENCE_POOL_WORKERS
            # The queued checks were cancelled while still pending: they
            # never executed at all...
            assert exists_counters[-1]["exists_calls"] == 0
            # ...while every saturating request's two checks genuinely ran.
            assert all(counter["exists_calls"] == 2 for counter in exists_counters[:-1])
        finally:
            for release in releases:
                release.set()


class TestRealSigner:
    def test_presigned_url_targets_the_signing_endpoint_with_bounded_expiry(self, settings):
        settings.S3_SIGNED_URL_ENDPOINT_URL = "http://localhost:9000"
        # DISTINCT sentinel credentials: SigV4 query auth legitimately embeds
        # the ACCESS key id in X-Amz-Credential, so the secret-not-in-URL
        # assertions below are only meaningful when the two values differ
        # (an environment using one placeholder for both would otherwise
        # false-positive this test).
        settings.S3_ACCESS_KEY_ID = "test-access-key-id"
        settings.S3_SECRET_ACCESS_KEY = "test-secret-material"
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
        # The PUBLIC access key id is embedded in the credential scope...
        assert query["X-Amz-Credential"][0].startswith("test-access-key-id/")
        assert query["response-content-type"] == ["image/webp"]
        assert query["response-content-disposition"] == ['inline; filename="design-original.webp"']
        # ...while the SECRET never appears anywhere in the URL.
        assert "test-secret-material" not in issued.original_url
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
        assert "test-secret-material" not in issued.thumbnail_url
        # The DOWNLOAD URL targets the SAME object as the original, under an
        # attachment disposition and a fixed, generic filename.
        download_parts = urlsplit(issued.original_download_url)
        assert download_parts.netloc == "localhost:9000"
        assert download_parts.path == f"/{settings.S3_BUCKET_NAME}/{version.image_storage_key}"
        download_query = parse_qs(download_parts.query)
        assert download_query["X-Amz-Expires"] == ["90"]
        assert download_query["response-content-type"] == ["image/webp"]
        assert download_query["response-content-disposition"] == [
            'attachment; filename="sitara-concept.webp"'
        ]
        assert "test-secret-material" not in issued.original_download_url

    def test_sign_get_rejects_an_unrecognised_disposition(self, settings):
        settings.S3_SIGNED_URL_ENDPOINT_URL = "http://localhost:9000"
        signer = S3DesignImageSigner()
        with pytest.raises(ValueError):
            signer.sign_get(
                "some/key.webp",
                ttl_seconds=60,
                filename="x.webp",
                disposition="x-attachment; evil=1",
            )
