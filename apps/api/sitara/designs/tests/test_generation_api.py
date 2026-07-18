"""Generation API tests (Part A): POST /designs/<id>/generate/ and GET
/jobs/<id>/. Real CSRF enforcement; ownership-first 404s; no provider/storage
provenance ever leaves the API. No Celery task runs (the post-commit submission
is rolled back with the test transaction).
"""

import json
import uuid
from unittest import mock

import pytest

from sitara.designs.models import Design, DesignSession, GenerationAttempt

from .utils import (
    COMPLETE_ANSWERS,
    DESIGNS_URL,
    bootstrap_csrf,
    csrf_client,
    make_active_questionnaire,
    register,
    send_json,
    unique_email,
    unique_ip,
)

pytestmark = pytest.mark.django_db

_AVAILABLE = "sitara.generation.pipeline.generation_is_available"


def _generate_url(design_id) -> str:
    return f"{DESIGNS_URL}{design_id}/generate/"


def _job_url(job_id) -> str:
    return f"/api/v1/jobs/{job_id}/"


def _complete_design(client, token) -> str:
    version = make_active_questionnaire()
    response = send_json(
        client,
        "post",
        DESIGNS_URL,
        {"questionnaire_version_id": str(version.id), "answers": COMPLETE_ANSWERS},
        token=token,
    )
    assert response.status_code == 201, response.content
    return response.json()["id"]


def _post_generate(client, design_id, *, token=None, key="__uuid__", body=None, available=True):
    if key == "__uuid__":
        key = str(uuid.uuid4())
    extra = {"REMOTE_ADDR": unique_ip()}
    if token is not None:
        extra["HTTP_X_CSRFTOKEN"] = token
    if key is not None:
        extra["HTTP_IDEMPOTENCY_KEY"] = key
    payload = json.dumps(body if body is not None else {})
    with mock.patch(_AVAILABLE, return_value=available):
        return client.post(
            _generate_url(design_id), data=payload, content_type="application/json", **extra
        )


class TestGenerateSuccess:
    def test_first_request_returns_202_with_job_and_location(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=token)
        assert response.status_code == 202, response.content
        job = response.json()["job"]
        assert job["design_id"] == design_id
        assert job["status"] == "queued"
        assert job["error_code"] is None
        assert response["Cache-Control"] == "no-store"
        assert response["Location"] == _job_url(job["id"])
        design = Design.objects.get(pk=design_id)
        assert design.status == Design.Status.GENERATING

    def test_same_key_returns_the_same_job(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        key = str(uuid.uuid4())
        first = _post_generate(client, design_id, token=token, key=key)
        second = _post_generate(client, design_id, token=token, key=key)
        assert first.status_code == second.status_code == 202
        assert first.json()["job"]["id"] == second.json()["job"]["id"]
        assert GenerationAttempt.objects.filter(design_id=design_id).count() == 1

    def test_job_payload_leaks_no_provider_or_storage_values(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        job = _post_generate(client, design_id, token=token).json()["job"]
        forbidden = {
            "image_provider",
            "image_model",
            "image_prediction_id",
            "image_seed",
            "image_parameters",
            "staged_image_storage_key",
            "staged_image_sha256",
            "celery_task_id",
            "prompt",
            "image_prompt",
            "design_spec",
        }
        assert not (set(job) & forbidden)


class TestGenerateRejections:
    def test_missing_csrf_is_403(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=None)
        assert response.status_code == 403

    def test_missing_idempotency_key_is_400(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=token, key=None)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_idempotency_key"

    def test_malformed_idempotency_key_is_400(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=token, key="not-a-uuid")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_idempotency_key"

    def test_non_empty_body_is_rejected(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=token, body={"foo": 1})
        assert response.status_code == 400

    def test_inaccessible_design_is_404(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        response = _post_generate(client, uuid.uuid4(), token=token)
        assert response.status_code == 404

    def test_unavailable_generation_is_503(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=token, available=False)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "generation_unavailable"

    def test_second_in_progress_request_is_409(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        first = _post_generate(client, design_id, token=token)
        assert first.status_code == 202
        second = _post_generate(client, design_id, token=token)
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "generation_in_progress"

    def test_already_generated_design_returns_409_at_the_api(self):
        from django.utils import timezone

        from sitara.designs.models import DesignVersion

        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        first = _post_generate(client, design_id, token=token)
        assert first.status_code == 202
        # Complete the job out-of-band (as the worker would).
        attempt_id = first.json()["job"]["id"]
        version = DesignVersion.objects.create(design_id=design_id, version_number=1)
        GenerationAttempt.objects.filter(pk=attempt_id).update(
            design_version=version,
            status="succeeded",
            staged_image_storage_key="generation-staging/x/raw.webp",
            staged_image_sha256="a" * 64,
            staged_image_size_bytes=10,
            staged_image_width=1,
            staged_image_height=1,
            completed_at=timezone.now(),
        )
        Design.objects.filter(pk=design_id).update(status=Design.Status.GENERATED)
        # A NEW idempotency key on the completed design is a controlled 409.
        response = _post_generate(client, design_id, token=token)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "design_already_generated"

    def test_broker_failure_returns_503_queue_unavailable(self):
        from sitara.generation.pipeline import QueueUnavailable

        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        # The view maps a post-commit broker failure to a controlled 503.
        with mock.patch(
            "sitara.designs.views.enqueue_design_generation",
            side_effect=QueueUnavailable("broker down"),
        ):
            extra = {
                "REMOTE_ADDR": unique_ip(),
                "HTTP_X_CSRFTOKEN": token,
                "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
            }
            response = client.post(
                _generate_url(design_id),
                data=json.dumps({}),
                content_type="application/json",
                **extra,
            )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "queue_unavailable"
        assert response["Cache-Control"] == "no-store"


class TestJobRetrieval:
    def test_owner_can_retrieve_the_job(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        job_id = _post_generate(client, design_id, token=token).json()["job"]["id"]
        response = client.get(_job_url(job_id))
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert response.json()["job"]["id"] == job_id

    def test_foreign_job_is_404(self):
        owner = csrf_client()
        token = bootstrap_csrf(owner)
        design_id = _complete_design(owner, token)
        job_id = _post_generate(owner, design_id, token=token).json()["job"]["id"]
        # A different browser session must not see the job.
        stranger = csrf_client()
        bootstrap_csrf(stranger)
        assert stranger.get(_job_url(job_id)).status_code == 404

    def test_nonexistent_job_is_404(self):
        client = csrf_client()
        bootstrap_csrf(client)
        assert client.get(_job_url(uuid.uuid4())).status_code == 404

    def test_get_job_does_not_create_a_workspace(self):
        # A fresh anonymous caller (no prior session) retrieving an unknown job
        # must not materialise a DesignSession.
        client = csrf_client()
        assert client.get(_job_url(uuid.uuid4())).status_code == 404
        assert DesignSession.objects.count() == 0

    def test_login_promotion_retains_job_access(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        job_id = _post_generate(client, design_id, token=token).json()["job"]["id"]
        # Register in the SAME browser session: the anonymous workspace is
        # promoted, and the job remains accessible.
        register(client, unique_email())
        assert client.get(_job_url(job_id)).status_code == 200
