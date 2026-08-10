"""Generation API tests (Part A): POST /designs/<id>/generate/ and GET
/jobs/<id>/. Real CSRF enforcement; ownership-first 404s; no provider/storage
provenance ever leaves the API. No Celery task runs (the post-commit submission
is rolled back with the test transaction).

Since Phase 21 (ADR 0023) every generate test signs in first, via
``signed_in_client``. That is not test scaffolding for its own sake — producing
a concept requires an account, so an anonymous generate is a 401 and a test that
kept using an anonymous client would be asserting the refusal by accident. The
refusal itself is exercised deliberately in ``TestGenerateRequiresAnAccount``.
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
    signed_in_client,
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
        client, token = signed_in_client()
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
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        key = str(uuid.uuid4())
        first = _post_generate(client, design_id, token=token, key=key)
        second = _post_generate(client, design_id, token=token, key=key)
        assert first.status_code == second.status_code == 202
        assert first.json()["job"]["id"] == second.json()["job"]["id"]
        assert GenerationAttempt.objects.filter(design_id=design_id).count() == 1

    def test_job_payload_leaks_no_provider_or_storage_values(self):
        client, token = signed_in_client()
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
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=None)
        assert response.status_code == 403

    def test_missing_idempotency_key_is_400(self):
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=token, key=None)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_idempotency_key"

    def test_malformed_idempotency_key_is_400(self):
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=token, key="not-a-uuid")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_idempotency_key"

    def test_non_empty_body_is_rejected(self):
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=token, body={"foo": 1})
        assert response.status_code == 400

    def _raw_post(self, client, design_id, token, raw_body, content_type="application/json"):
        return client.post(
            _generate_url(design_id),
            data=raw_body,
            content_type=content_type,
            REMOTE_ADDR=unique_ip(),
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

    @pytest.mark.parametrize("raw_body", ["null", "[]", '"x"', "0", "[{}]"])
    def test_non_object_json_bodies_are_rejected(self, raw_body):
        # Only no body or exactly {} may enqueue paid work — JSON null (which
        # parses to None), arrays and scalars are all out of contract.
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        response = self._raw_post(client, design_id, token, raw_body)
        assert response.status_code == 400, response.content
        assert GenerationAttempt.objects.filter(design_id=design_id).count() == 0

    def test_form_encoded_body_is_rejected_as_unsupported_media(self):
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        response = self._raw_post(
            client, design_id, token, "a=1", content_type="application/x-www-form-urlencoded"
        )
        assert response.status_code == 415
        assert GenerationAttempt.objects.filter(design_id=design_id).count() == 0

    def test_truly_empty_body_is_accepted(self):
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        with mock.patch(_AVAILABLE, return_value=True):
            # A genuinely empty body (zero bytes) with the JSON content type —
            # the test client's default multipart encoding would not be empty.
            response = client.post(
                _generate_url(design_id),
                data="",
                content_type="application/json",
                REMOTE_ADDR=unique_ip(),
                HTTP_X_CSRFTOKEN=token,
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )
        assert response.status_code == 202, response.content

    def test_a_multipart_body_is_refused_and_does_not_crash(self):
        """A controlled 415, where this used to be an unhandled 500.

        Django's CSRF check reads ``request.POST`` looking for
        ``csrfmiddlewaretoken`` before the view runs. For a multipart body the
        upload parser consumes the request stream WITHOUT caching it, so the
        view's later ``request.body`` raised ``RawPostDataException`` — a
        ``RuntimeError`` DRF does not catch, so it escaped as a 500. The
        urlencoded case never had the fault (Django caches ``_body`` there and
        DRF's parser negotiation answers it), which is exactly why the sibling
        test above passed while this hole stayed open.

        Two things are asserted, not one: the controlled status, AND that nothing
        was enqueued — a form submission must not be quietly read as the empty
        body this endpoint accepts."""
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        with mock.patch(_AVAILABLE, return_value=True):
            response = client.post(
                _generate_url(design_id),
                data={"a": "1"},  # the test client's default is multipart
                REMOTE_ADDR=unique_ip(),
                HTTP_X_CSRFTOKEN=token,
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )
        assert response.status_code == 415, response.content
        assert response.json()["error"]["code"] == "unsupported_media_type"
        assert GenerationAttempt.objects.filter(design_id=design_id).count() == 0

    @pytest.mark.parametrize(
        "content_type",
        ["application/json", "Application/JSON", "application/json; charset=utf-8"],
    )
    def test_the_media_type_check_accepts_json_however_it_is_spelled(self, content_type):
        """Media types are case-insensitive (RFC 7231) and may carry parameters, and
        `request.content_type` keeps those parameters rather than stripping them.
        A case-sensitive prefix test would refuse a perfectly correct header from a
        non-browser client — failing closed, but wrongly.

        This test is why the implementation splits the parameters off itself: the
        first version relied on Django to do it, and this is what caught that."""
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        with mock.patch(_AVAILABLE, return_value=True):
            response = self._raw_post(client, design_id, token, "{}", content_type=content_type)
        assert response.status_code == 202, response.content

    def test_a_json_like_media_type_is_still_refused(self):
        """``application/json-patch+json`` merely STARTS WITH application/json. It
        is a different format with different semantics, and a prefix test would
        have admitted it — leaving the exact-{} body check as the only thing
        stopping it, which is not where that decision belongs."""
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        response = self._raw_post(
            client, design_id, token, "{}", content_type="application/json-patch+json"
        )
        assert response.status_code == 415, response.content
        assert GenerationAttempt.objects.filter(design_id=design_id).count() == 0

    def test_inaccessible_design_is_404(self):
        client, token = signed_in_client()
        response = _post_generate(client, uuid.uuid4(), token=token)
        assert response.status_code == 404

    def test_unavailable_generation_is_503(self):
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        response = _post_generate(client, design_id, token=token, available=False)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "generation_unavailable"

    def test_second_in_progress_request_is_409(self):
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        first = _post_generate(client, design_id, token=token)
        assert first.status_code == 202
        second = _post_generate(client, design_id, token=token)
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "generation_in_progress"

    def test_already_generated_design_returns_409_at_the_api(self):
        from django.utils import timezone

        from sitara.designs.models import DesignVersion

        client, token = signed_in_client()
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

        client, token = signed_in_client()
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


class TestGenerateRequiresAnAccount:
    """ADR 0023: the last action before a concept requires an account.

    Everything before it does not, which is half the point and is asserted here
    rather than assumed — a gate that quietly crept backwards into the
    questionnaire would be a worse regression than no gate at all."""

    def test_an_anonymous_owner_of_a_complete_design_is_refused(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)

        response = _post_generate(client, design_id, token=token)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"
        assert GenerationAttempt.objects.count() == 0
        assert Design.objects.get(pk=design_id).status == Design.Status.DRAFT

    def test_the_refusal_names_no_design_and_promises_the_answers_are_kept(self):
        """The message is the whole reason this is a 401 and not a bare 403: the
        visitor is about to be sent away to register, and the one thing they need
        to know is that they will not have to answer it all again."""
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)

        body = _post_generate(client, design_id, token=token).json()

        message = body["error"]["message"]
        # Both halves, so a copy edit cannot quietly drop either: the visitor is
        # told what to do, and told their work is not gone. A message containing
        # only one of those would pass a single substring check.
        assert "answers are saved" in message.lower()
        assert "sign in" in message.lower() or "create an account" in message.lower()
        assert design_id not in json.dumps(body)

    def test_the_refusal_is_json_and_never_a_redirect(self):
        """An API that redirects a JSON request to a login page hands the client
        an HTML body it cannot read, and a 302 the fetch layer follows silently."""
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)

        response = _post_generate(client, design_id, token=token)

        assert response.status_code == 401
        assert response["Content-Type"].startswith("application/json")
        assert "Location" not in response

    def test_the_answers_survive_the_refusal_and_generate_after_signing_in(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        assert _post_generate(client, design_id, token=token).status_code == 401

        register(client, unique_email())
        signed_in_token = bootstrap_csrf(client)
        response = _post_generate(client, design_id, token=signed_in_token)

        assert response.status_code == 202, response.content
        design = Design.objects.get(pk=design_id)
        assert design.answers == COMPLETE_ANSWERS

    def test_the_account_check_runs_before_ownership(self):
        """An anonymous caller naming a design that is not theirs — or one that
        never existed — gets the account refusal rather than a 404.

        Deliberate, and the opposite of this module's usual order. The refusal is
        about the caller's own capability and says nothing whatever about the
        resource, so answering it first reveals strictly less than a 404 would."""
        stranger = csrf_client()
        token = bootstrap_csrf(stranger)

        response = _post_generate(stranger, uuid.uuid4(), token=token)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"

    def test_csrf_is_still_checked_first(self):
        """The account gate must not become a way to skip CSRF, or to learn
        whether a design exists without holding a token."""
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)

        response = _post_generate(client, design_id, token=None)

        assert response.status_code == 403

    def test_the_refusal_consumes_no_idempotency_key(self):
        """A refused request must not burn the key the client will retry with
        after signing in — otherwise the first real attempt would look like a
        replay of a job that never existed."""
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        key = str(uuid.uuid4())

        assert _post_generate(client, design_id, token=token, key=key).status_code == 401
        # Asserted HERE, between the two requests, not only at the end: a final
        # count of one cannot tell "none from the refusal, one from the retry"
        # apart from "one from the refusal, then a replay of it".
        assert GenerationAttempt.objects.filter(design_id=design_id).count() == 0

        register(client, unique_email())
        signed_in_token = bootstrap_csrf(client)
        response = _post_generate(client, design_id, token=signed_in_token, key=key)

        assert response.status_code == 202, response.content
        assert GenerationAttempt.objects.filter(design_id=design_id).count() == 1
        # And the job really is this request's, not a replay of a refused one.
        assert response.json()["job"]["status"] == "queued"

    def test_answering_the_questionnaire_still_needs_no_account(self):
        """The other half of ADR 0023, and the half that is easy to break: a
        visitor must be able to reach the end of the questionnaire — read the
        schema, create a design, save answers, validate them — signed out.

        Asserted at the API rather than reasoned about, because the gate lives one
        function call away from all of these."""
        client = csrf_client()
        token = bootstrap_csrf(client)
        version = make_active_questionnaire()

        assert client.get("/api/v1/questionnaire/active/").status_code == 200

        created = send_json(
            client,
            "post",
            DESIGNS_URL,
            {"questionnaire_version_id": str(version.id), "answers": COMPLETE_ANSWERS},
            token=token,
        )
        assert created.status_code == 201, created.content
        design_id = created.json()["id"]

        # And re-reading and re-saving the draft, which is what a wizard does on
        # every screen.
        assert client.get(f"{DESIGNS_URL}{design_id}/").status_code == 200
        patched = send_json(
            client,
            "patch",
            f"{DESIGNS_URL}{design_id}/",
            {"answers": COMPLETE_ANSWERS},
            token=token,
        )
        assert patched.status_code == 200, patched.content

        # And validate, which is the LAST call the wizard makes before the button
        # that now needs an account. Easy to leave out of this list and the one
        # most likely to be swept up by a future gate widening, since it sits
        # right beside generate in the same module.
        validated = send_json(
            client,
            "post",
            f"{DESIGNS_URL}{design_id}/validate/",
            {"answers": COMPLETE_ANSWERS},
            token=token,
        )
        assert validated.status_code == 200, validated.content

    def test_a_claim_that_cannot_happen_is_a_not_found_never_a_generation_failure(self):
        """The failure this phase's own risk list called out.

        A visitor answers everything anonymously; then a DIFFERENT account signs
        in on that same browser session. ADR 0004 never transfers a workspace, so
        the pointer is dropped and the design stays with the first party — which
        means the second party cannot see it. The endpoint must say exactly that:
        a plain not_found. Never a generation error, and never a 500.

        Note what is NOT lost: the first visitor's answers are untouched, which is
        asserted below. This is the reachable sequence for a fresh anonymous
        session — a claim cannot otherwise fail, because a fresh session's
        workspace UUID is unclaimed and known to nobody else."""
        first = csrf_client()
        first_token = bootstrap_csrf(first)
        design_id = _complete_design(first, first_token)
        workspace_id = Design.objects.get(pk=design_id).design_session_id

        # The first party signs in AND makes one design request. Both steps are
        # needed and the second is easy to forget: the claim is lazy, performed by
        # accessible_designs rather than by login, so registering alone leaves the
        # workspace unclaimed and still up for grabs. That window is exactly what
        # the next few lines exploit, and it is real — see ADR 0023.
        register(first, unique_email())
        assert first.get(DESIGNS_URL).status_code == 200
        assert Design.objects.get(pk=design_id).design_session.user_id is not None

        # A second browser session, pointed at the same workspace (a shared
        # device), signs in as somebody else.
        second = csrf_client()
        bootstrap_csrf(second)
        store = second.session
        store["sitara_design_session_id"] = str(workspace_id)
        store.save()
        register(second, unique_email())
        second_token = bootstrap_csrf(second)

        response = _post_generate(second, design_id, token=second_token)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        # The first party's work is intact and still theirs.
        design = Design.objects.get(pk=design_id)
        assert design.answers == COMPLETE_ANSWERS
        assert design.status == Design.Status.DRAFT
        assert design.design_session_id == workspace_id


class TestJobRetrieval:
    def test_owner_can_retrieve_the_job(self):
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        job_id = _post_generate(client, design_id, token=token).json()["job"]["id"]
        response = client.get(_job_url(job_id))
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert response.json()["job"]["id"] == job_id

    def test_unvetted_persisted_error_code_is_masked(self):
        # Defence in depth at the public boundary: a persisted code outside
        # the stable allowlist (legacy data or manual intervention) is
        # reported as the generic internal code — never echoed.
        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        job_id = _post_generate(client, design_id, token=token).json()["job"]["id"]
        from django.utils import timezone

        GenerationAttempt.objects.filter(pk=job_id).update(
            status="failed",
            error_code="Raw legacy provider text!",
            completed_at=timezone.now(),
        )
        response = client.get(_job_url(job_id))
        assert response.status_code == 200
        assert response.json()["job"]["error_code"] == "internal_generation_error"

    def test_valid_persisted_error_code_is_returned_unmasked(self):
        # The pass-through side of the masking boundary: a genuine stable
        # code must reach the client EXACTLY (an inverted validity check
        # would collapse every real failure into the generic code).
        from django.utils import timezone

        client, token = signed_in_client()
        design_id = _complete_design(client, token)
        job_id = _post_generate(client, design_id, token=token).json()["job"]["id"]
        GenerationAttempt.objects.filter(pk=job_id).update(
            status="failed",
            error_code="image_prediction_failed",
            completed_at=timezone.now(),
        )
        response = client.get(_job_url(job_id))
        assert response.status_code == 200
        assert response.json()["job"]["error_code"] == "image_prediction_failed"

    def test_foreign_job_is_404(self):
        owner, token = signed_in_client()
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

    def test_login_promotion_carries_an_anonymous_draft_into_a_job(self):
        """Re-scoped by Phase 21, not deleted.

        This used to generate anonymously and then register, proving a job stayed
        visible across the promotion. An anonymous session can no longer hold a job
        at all (ADR 0023), so that exact sequence is unreachable. What the
        promotion still has to do — and what actually matters now — is carry the
        DRAFT across: a visitor answers everything anonymously, signs in at the
        last step, and generates their own answers rather than losing them.

        The design id is captured while anonymous and used unchanged afterwards, so
        a promotion that quietly created a second workspace would fail here."""
        client = csrf_client()
        anonymous_token = bootstrap_csrf(client)
        design_id = _complete_design(client, anonymous_token)
        assert Design.objects.get(pk=design_id).design_session.user_id is None

        # The last step before a concept: refused while anonymous.
        refused = _post_generate(client, design_id, token=anonymous_token)
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == "authentication_required"

        # Sign in on the SAME browser session, then repeat the request.
        register(client, unique_email())
        token = bootstrap_csrf(client)
        response = _post_generate(client, design_id, token=token)

        assert response.status_code == 202, response.content
        job_id = response.json()["job"]["id"]
        assert client.get(_job_url(job_id)).status_code == 200
        # The same design, now owned by the account — claimed, never copied.
        design = Design.objects.get(pk=design_id)
        assert design.design_session.user_id is not None
        assert Design.objects.count() == 1
