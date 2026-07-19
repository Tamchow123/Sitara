"""Design detail `latest_job` field tests (Phase 12 Part A, spec §7).

The design detail response gains one sanitised public snapshot of the
design's most recent generation attempt — never on the list payload, never
any private attempt provenance."""

from datetime import timedelta

import pytest
from django.utils import timezone

from sitara.designs.models import GenerationAttempt

from .utils import DESIGNS_URL, create_owned_design_id, csrf_client, design_url

pytestmark = pytest.mark.django_db


def _make_owned_design(client) -> str:
    return create_owned_design_id(client, title="Latest job test")


class TestLatestJob:
    def test_no_attempts_produces_latest_job_null(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        response = client.get(design_url(design_id))
        assert response.status_code == 200
        assert response.json()["latest_job"] is None

    def test_latest_attempt_is_selected_by_newest_created_at(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        now = timezone.now()
        older = GenerationAttempt.objects.create(
            design_id=design_id,
            status=GenerationAttempt.Status.FAILED,
            error_code="internal_generation_error",
            completed_at=now,
        )
        GenerationAttempt.objects.filter(pk=older.pk).update(created_at=now - timedelta(minutes=5))
        newer = GenerationAttempt.objects.create(
            design_id=design_id,
            status=GenerationAttempt.Status.FAILED,
            error_code="internal_generation_error",
            completed_at=now,
        )
        response = client.get(design_url(design_id))
        assert response.json()["latest_job"]["id"] == str(newer.pk)

    def test_latest_attempt_tie_breaks_deterministically_on_uuid(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        same_instant = timezone.now()
        first = GenerationAttempt.objects.create(
            design_id=design_id,
            status=GenerationAttempt.Status.FAILED,
            error_code="internal_generation_error",
            completed_at=same_instant,
        )
        second = GenerationAttempt.objects.create(
            design_id=design_id,
            status=GenerationAttempt.Status.FAILED,
            error_code="internal_generation_error",
            completed_at=same_instant,
        )
        GenerationAttempt.objects.filter(pk__in=[first.pk, second.pk]).update(
            created_at=same_instant
        )
        expected = max(first.pk, second.pk)
        response = client.get(design_url(design_id))
        job_id = response.json()["latest_job"]["id"]
        # Deterministic regardless of insertion order: repeating the request
        # must always return the same winner.
        assert job_id == str(expected)
        again = client.get(design_url(design_id))
        assert again.json()["latest_job"]["id"] == job_id

    def test_latest_job_exposes_only_the_public_shape(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        GenerationAttempt.objects.create(
            design_id=design_id,
            status=GenerationAttempt.Status.FAILED,
            error_code="internal_generation_error",
            completed_at=timezone.now(),
            image_provider="replicate",
            image_model="a-very-secret-model-name",
            image_prediction_id="pred_abc123",
            image_seed=42,
            staged_image_storage_key="design-images/staged/secret-key.webp",
            staged_image_sha256="c" * 64,
            staged_image_size_bytes=1000,
            staged_image_width=1536,
            staged_image_height=2048,
            celery_task_id="11111111-2222-4333-8444-555555555555",
        )
        response = client.get(design_url(design_id))
        job = response.json()["latest_job"]
        assert set(job) == {
            "id",
            "design_id",
            "design_version_id",
            "status",
            "error_code",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
        }
        raw = response.content.decode()
        assert "replicate" not in raw
        assert "a-very-secret-model-name" not in raw
        assert "pred_abc123" not in raw
        assert "design-images/staged/secret-key.webp" not in raw
        assert "c" * 64 not in raw

    def test_design_list_payload_has_no_job_data(self):
        client = csrf_client()
        design_id = _make_owned_design(client)
        GenerationAttempt.objects.create(
            design_id=design_id,
            status=GenerationAttempt.Status.FAILED,
            error_code="internal_generation_error",
            completed_at=timezone.now(),
        )
        response = client.get(DESIGNS_URL)
        assert response.status_code == 200
        row = response.json()["designs"][0]
        assert "latest_job" not in row
        assert set(row) == {"id", "title", "status", "created_at", "updated_at"}
