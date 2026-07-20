"""End-to-end generation with fakes (Phase 10 Part B).

Real PostgreSQL, Celery eager mode and private in-memory test storage; the live
Replicate provider/downloader and the gated Anthropic provider are replaced with
zero-network fakes. Proves the full queued -> running_text -> running_image ->
succeeded journey, a worker-restart resume that never re-submits, and the
offline fixture command — all without a single provider network call.
"""

import io
import socket
import uuid

import pytest
from django.core.management import call_command
from django.test import Client

from sitara.ai_gateway.image_generation import PREDICTION_SUCCEEDED, ImageProviderError
from sitara.designs.jobs import public_job_payload
from sitara.designs.models import Design, DesignSession, DesignVersion, GenerationAttempt
from sitara.designs.services import DESIGN_SESSION_KEY
from sitara.generation.fixture_provider import FixtureStructuredDesignProvider
from sitara.generation.image_fixtures import (
    FakeImageProvider,
    InMemoryStorage,
    synthetic_webp_downloader,
)
from sitara.generation.pipeline import (
    PipelineConfig,
    enqueue_design_generation,
    run_generation_attempt,
)

from .factory import make_complete_design

_Status = GenerationAttempt.Status


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("network access attempted during e2e tests")

    monkeypatch.setattr(socket.socket, "connect", guard)


def _open_all_gates(settings):
    settings.DEMO_MODE = False
    settings.ALLOW_PAID_AI_CALLS = True
    settings.LIVE_GENERATION_ENABLED = True
    settings.REPLICATE_API_TOKEN = "r8_test_not_a_real_token"
    settings.DEFAULT_IMAGE_MODEL = "black-forest-labs/flux-1.1-pro"
    settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
    settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"


def _inject_fakes(monkeypatch, storage, image=None):
    monkeypatch.setattr(
        "sitara.generation.pipeline._live_image_provider",
        lambda config: image or FakeImageProvider(),
    )
    monkeypatch.setattr(
        "sitara.generation.pipeline._live_image_downloader",
        lambda config: synthetic_webp_downloader,
    )
    monkeypatch.setattr("sitara.generation.pipeline.default_storage", storage)
    monkeypatch.setattr(
        "sitara.ai_gateway.policy.get_structured_design_generation_provider",
        lambda: FixtureStructuredDesignProvider(),
    )


@pytest.mark.django_db(transaction=True)
def test_end_to_end_generation_succeeds_via_eager_task(settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    _open_all_gates(settings)
    storage = InMemoryStorage()
    _inject_fakes(monkeypatch, storage)

    design = make_complete_design()
    attempt, created = enqueue_design_generation(design, idempotency_key=uuid.uuid4())
    assert created is True
    # The on_commit-submitted eager task ran the whole pipeline.
    attempt.refresh_from_db()
    assert attempt.status == _Status.SUCCEEDED
    design.refresh_from_db()
    assert design.status == Design.Status.GENERATED
    assert DesignVersion.objects.filter(design=design).count() == 1
    version = DesignVersion.objects.get(design=design)
    # Phase 11: success now includes the canonical permanent ingest.
    assert version.has_permanent_image
    assert version.image_storage_key == f"design-images/{design.id}/{version.id}/original.webp"
    assert version.thumbnail_storage_key == f"design-images/{design.id}/{version.id}/thumbnail.webp"
    from django.core.files.storage import storages as storage_aliases

    final_store = storage_aliases["design_images"]
    assert final_store.exists(version.image_storage_key)
    assert final_store.exists(version.thumbnail_storage_key)
    # Raw staging is retained after ingest (crash recovery; purge is Phase 16).
    assert attempt.staged_image_storage_key.startswith(f"generation-staging/{attempt.id}/raw.")
    assert storage.exists(attempt.staged_image_storage_key)

    # The public job payload leaks no private provenance.
    job = public_job_payload(attempt)["job"]
    assert set(job) == {
        "id",
        "design_id",
        "design_version_id",
        "status",
        "error_code",
        "generation_kind",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    }
    assert job["generation_kind"] == "initial"


@pytest.mark.django_db(transaction=True)
def test_end_to_end_generation_with_inspiration_never_sends_a_reference_image(
    settings, monkeypatch, inmemory_storage
):
    """Phase 13: an inspiration selected through the full async pipeline still
    reaches Replicate with an empty ``reference_image_urls`` tuple, the
    persisted DesignVersion carries the exact snapshot, and zero catalogue
    storage reads occur outside asset approval/ingest."""
    from sitara.catalogue.tests.utils import make_eligible_asset
    from sitara.designs.models import DesignInspiration
    from sitara.generation.inspiration_context import build_inspiration_context_snapshot

    settings.CELERY_TASK_ALWAYS_EAGER = True
    _open_all_gates(settings)
    storage = InMemoryStorage()
    fake_image = FakeImageProvider()
    _inject_fakes(monkeypatch, storage, image=fake_image)

    design = make_complete_design()
    asset = make_eligible_asset()
    DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
    expected_snapshot = build_inspiration_context_snapshot(design)

    attempt, created = enqueue_design_generation(design, idempotency_key=uuid.uuid4())
    assert created is True
    attempt.refresh_from_db()
    assert attempt.status == _Status.SUCCEEDED

    assert fake_image.last_request is not None
    assert fake_image.last_request.reference_image_urls == ()

    version = DesignVersion.objects.get(design=design)
    assert version.inspiration_context == expected_snapshot.model_dump(mode="json")


@pytest.mark.django_db(transaction=True)
def test_worker_restart_reuses_prediction_without_new_calls(settings, monkeypatch):
    _open_all_gates(settings)
    storage = InMemoryStorage()
    structured = FixtureStructuredDesignProvider()
    # Poll raises a transient once (worker "restart"), then succeeds.
    image = FakeImageProvider(poll_actions=[ImageProviderError("timeout"), PREDICTION_SUCCEEDED])

    design = make_complete_design()
    design.status = Design.Status.GENERATING
    design.save(update_fields=["status"])
    attempt = GenerationAttempt.objects.create(design=design, status=_Status.QUEUED)

    fast = PipelineConfig(poll_interval_seconds=0.0, poll_max_attempts=5)
    from sitara.generation.pipeline import GenerationRetry

    # First delivery: text+prompt+create succeed, poll fails transiently.
    with pytest.raises(GenerationRetry):
        run_generation_attempt(
            attempt.id,
            structured_provider=structured,
            image_provider=image,
            image_downloader=synthetic_webp_downloader,
            storage=storage,
            seed_factory=lambda: 3,
            config=fast,
        )
    attempt.refresh_from_db()
    prediction_id = attempt.image_prediction_id
    assert prediction_id
    assert structured._calls == 1
    assert image.create_calls == 1

    # Redelivery: no new text call, no new prediction; the SAME prediction is polled.
    result = run_generation_attempt(
        attempt.id,
        structured_provider=structured,
        image_provider=image,
        image_downloader=synthetic_webp_downloader,
        storage=storage,
        seed_factory=lambda: 3,
        config=fast,
    )
    assert result.status == _Status.SUCCEEDED
    assert structured._calls == 1  # Anthropic not called again
    assert image.create_calls == 1  # prediction not re-created
    assert result.image_prediction_id == prediction_id


@pytest.mark.django_db(transaction=True)
def test_end_to_end_image_failure_marks_generation_failed(settings, monkeypatch):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    _open_all_gates(settings)
    storage = InMemoryStorage()
    _inject_fakes(monkeypatch, storage, image=FakeImageProvider(poll_actions=["failed"]))

    design = make_complete_design()
    attempt, _ = enqueue_design_generation(design, idempotency_key=uuid.uuid4())
    attempt.refresh_from_db()
    assert attempt.status == _Status.FAILED
    assert attempt.error_code == "image_prediction_failed"
    design.refresh_from_db()
    assert design.status == Design.Status.GENERATION_FAILED


@pytest.mark.django_db(transaction=True)
def test_offline_fixture_command_runs_and_is_idempotent(monkeypatch):
    storage = InMemoryStorage()
    monkeypatch.setattr(
        "sitara.generation.management.commands.run_generation_fixture.default_storage", storage
    )
    design = make_complete_design()
    key = str(uuid.uuid4())
    out = io.StringIO()
    call_command(
        "run_generation_fixture", "--design", str(design.id), "--idempotency-key", key, stdout=out
    )

    attempt = GenerationAttempt.objects.get(design=design)
    assert attempt.status == _Status.SUCCEEDED
    assert DesignVersion.objects.filter(design=design).count() == 1
    assert attempt.staged_image_size_bytes > 0
    assert len(attempt.staged_image_sha256) == 64

    # The printed output is a SAFE-FIELDS-ONLY contract (spec section 21):
    # UUIDs, status, processor version and dimensions — never a prompt,
    # answer, storage key, hash, signed URL or provider metadata.
    version = DesignVersion.objects.get(design=design)
    output = out.getvalue()
    assert str(attempt.id) in output
    assert str(version.id) in output
    assert "processor_version=1.0.0" in output
    assert f"original={version.image_width}x{version.image_height}" in output
    assert f"thumbnail={version.thumbnail_width}x{version.thumbnail_height}" in output
    assert "design-images/" not in output
    assert "generation-staging/" not in output
    assert attempt.staged_image_sha256 not in output
    assert version.image_sha256 not in output
    assert version.thumbnail_sha256 not in output
    assert version.image_prompt not in output
    assert "http" not in output  # no signed or provider URL of any kind

    # Repeating the same key creates no duplicate work.
    call_command("run_generation_fixture", "--design", str(design.id), "--idempotency-key", key)
    assert GenerationAttempt.objects.filter(design=design).count() == 1
    assert DesignVersion.objects.filter(design=design).count() == 1


@pytest.mark.django_db(transaction=True)
def test_pipeline_ingested_version_delivers_through_the_images_endpoint(settings, monkeypatch):
    # The FULL journey in one continuous test: a real pipeline run (staging ->
    # canonical permanent ingest) followed by the real ownership-checked
    # delivery endpoint signing THAT version — no hand-built provenance
    # fixtures on either side, so the ingest/delivery seam is genuinely
    # proven to compose.
    settings.CELERY_TASK_ALWAYS_EAGER = True
    _open_all_gates(settings)
    settings.S3_SIGNED_URL_ENDPOINT_URL = "http://localhost:9000"
    storage = InMemoryStorage()
    _inject_fakes(monkeypatch, storage)

    design = make_complete_design()
    attempt, _created = enqueue_design_generation(design, idempotency_key=uuid.uuid4())
    attempt.refresh_from_db()
    assert attempt.status == _Status.SUCCEEDED
    version = DesignVersion.objects.get(design=design)
    assert version.has_permanent_image

    # Bind the design's workspace to a browser session and fetch signed URLs
    # through the real HTTP endpoint (presigning is a local computation — the
    # module-level socket guard proves no network is touched).
    client = Client()
    client.get("/api/v1/auth/csrf/")
    session = client.session
    session[DESIGN_SESSION_KEY] = str(design.design_session_id)
    session.save()
    response = client.get(f"/api/v1/designs/{design.id}/versions/{version.id}/images/")
    assert response.status_code == 200, response.content
    images = response.json()["images"]
    assert images["original"]["width"] == version.image_width
    assert images["original"]["height"] == version.image_height
    assert images["thumbnail"]["width"] == version.thumbnail_width
    assert images["thumbnail"]["height"] == version.thumbnail_height
    # The REAL signer signed the REAL ingested keys.
    assert version.image_storage_key in images["original"]["url"]
    assert version.thumbnail_storage_key in images["thumbnail"]["url"]
    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "no-referrer"

    # A different browser session gains nothing from the same journey.
    stranger = Client()
    stranger.get("/api/v1/auth/csrf/")
    assert (
        stranger.get(f"/api/v1/designs/{design.id}/versions/{version.id}/images/").status_code
        == 404
    )
    assert Design.objects.filter(pk=design.pk).exists()
    assert DesignSession.objects.filter(pk=design.design_session_id).exists()
