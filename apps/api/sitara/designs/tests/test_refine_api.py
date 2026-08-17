"""Refinement API tests (Phase 14 Part C): POST /designs/<id>/refine/.

Real CSRF enforcement; ownership-first 404s; no provider/storage provenance
ever leaves the API. No Celery task runs (the post-commit submission is
rolled back with the test transaction).

Since Phase 21 (ADR 0023) every refine test signs in first, via
``signed_in_client``. In practice a refiner always has an account -- they cannot
have reached a version-1 concept without one -- so the gate here is defence in
depth against a session that expired between generating and refining. The
refusal itself is exercised deliberately in ``TestRefineRequiresAnAccount``.
"""

import json
import uuid
from pathlib import Path
from unittest import mock

import pytest

from sitara.designs.models import Design, DesignVersion
from sitara.questionnaire.models import QuestionnaireVersion

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_owned_design_id,
    create_ready_design_version,
    csrf_client,
    make_active_questionnaire,
    register,
    signed_in_client,
    unique_email,
    unique_ip,
)

pytestmark = pytest.mark.django_db

_AVAILABLE = "sitara.generation.pipeline.generation_is_available"

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "generation" / "tests" / "fixtures" / "nikah_lehenga.json"
)


def _load_valid_spec() -> dict:
    with _FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _refine_url(design_id) -> str:
    return f"{DESIGNS_URL}{design_id}/refine/"


def _job_url(job_id) -> str:
    return f"/api/v1/jobs/{job_id}/"


def _generated_design(client, token) -> tuple[str, DesignVersion]:
    design_id = create_owned_design_id(client, title="Refinable design")
    version = create_ready_design_version(
        design_id, design_spec=_load_valid_spec(), with_storage_objects=False
    )
    design = Design.objects.get(pk=design_id)
    design.status = Design.Status.GENERATED
    # A PINNED questionnaire version, because a refinement now revalidates any
    # changed canonical selection against it (ADR 0028) and refuses a design
    # that has none. This is not fixture decoration: a design cannot reach a
    # generated version without one -- `validate` refuses to run until a
    # questionnaire is selected -- so pinning it here makes the fixture match
    # the only shape production can produce.
    # Reused rather than created outright: at most one version may be active
    # (a PostgreSQL partial unique constraint), and a throttle test creates many
    # designs in one transaction.
    design.questionnaire_version = (
        QuestionnaireVersion.objects.filter(status="active").first() or make_active_questionnaire()
    )
    design.save(update_fields=["status", "questionnaire_version"])
    return design_id, version


def _post_refine(
    client,
    design_id,
    *,
    token=None,
    key="__uuid__",
    body=None,
    source_version_id=None,
    available=True,
):
    if key == "__uuid__":
        key = str(uuid.uuid4())
    extra = {"REMOTE_ADDR": unique_ip()}
    if token is not None:
        extra["HTTP_X_CSRFTOKEN"] = token
    if key is not None:
        extra["HTTP_IDEMPOTENCY_KEY"] = key
    if body is None:
        body = {
            "source_version_id": str(source_version_id) if source_version_id else str(uuid.uuid4()),
            "change_type": "colour_story",
            "note": "",
        }
    payload = json.dumps(body)
    with mock.patch(_AVAILABLE, return_value=available):
        return client.post(
            _refine_url(design_id), data=payload, content_type="application/json", **extra
        )


def _complete_one_round(client, token, design_id, source: DesignVersion) -> DesignVersion:
    """Post one refinement and finish it the way the async pipeline would.

    Without the terminal SUCCEEDED attempt and the linked child version the
    round still reads as in progress, and the next request would be refused for
    that reason instead of the one under test."""
    from django.utils import timezone

    from sitara.designs.models import GenerationAttempt

    posted = _post_refine(client, design_id, token=token, source_version_id=source.pk)
    assert posted.status_code == 202, posted.content
    job_id = posted.json()["job"]["id"]

    design = Design.objects.get(pk=design_id)
    number = source.version_number + 1
    child = DesignVersion.objects.create(
        design=design,
        version_number=number,
        parent_version=source,
        refinement_request={"schema_version": 1, "change_type": "colour_story", "note": ""},
        refinement_request_schema_version=1,
        refinement_request_sha256="e" * 64,
    )
    _make_version_ready(child)
    GenerationAttempt.objects.filter(pk=job_id).update(
        status=GenerationAttempt.Status.SUCCEEDED,
        design_version=child,
        completed_at=timezone.now(),
        staged_image_storage_key=f"generation-staging/test/raw-v{number}.webp",
        staged_image_sha256=f"{number:064d}",
        staged_image_size_bytes=1000,
        staged_image_width=800,
        staged_image_height=1000,
    )
    design.status = Design.Status.GENERATED
    design.save(update_fields=["status"])
    return child


def _make_version_ready(version: DesignVersion) -> None:
    """The spec, prompt and permanent-image provenance a refinable source needs.

    Copied onto the child so the NEXT round can use it as its source — the real
    pipeline's prompt and image stages leave exactly this behind."""
    from django.utils import timezone

    from sitara.generation.design_spec import DESIGN_SPEC_SCHEMA_VERSION, SPEC_TEMPLATE_VERSION
    from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION

    number = version.version_number
    version.design_spec = _load_valid_spec()
    version.design_spec_schema_version = DESIGN_SPEC_SCHEMA_VERSION
    version.design_spec_template_version = SPEC_TEMPLATE_VERSION
    version.design_spec_provider = "fixture"
    version.design_spec_model = "fixture-model"
    version.design_spec_generated_at = timezone.now()
    version.image_prompt = f"A deterministic placeholder prompt for v{number}."
    version.prompt_builder_version = PROMPT_BUILDER_VERSION
    version.image_storage_key = f"design-images/{version.design_id}/v{number}/original.webp"
    version.image_sha256 = f"{number:064d}"
    version.image_size_bytes = 100_000
    version.image_width = 900
    version.image_height = 1200
    version.thumbnail_storage_key = f"design-images/{version.design_id}/v{number}/thumbnail.webp"
    version.thumbnail_sha256 = f"{number + 500:064d}"
    version.thumbnail_size_bytes = 5_000
    version.thumbnail_width = 200
    version.thumbnail_height = 260
    version.image_processor_version = "1.0.0"
    version.image_ingested_at = timezone.now()
    version.save()


class TestRefineSuccess:
    def test_first_request_returns_202_with_refinement_job_and_location(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        response = _post_refine(client, design_id, token=token, source_version_id=version.pk)
        assert response.status_code == 202, response.content
        job = response.json()["job"]
        assert job["design_id"] == design_id
        assert job["status"] == "queued"
        assert job["generation_kind"] == "refinement"
        assert job["error_code"] is None
        assert response["Cache-Control"] == "no-store"
        assert response["Location"] == _job_url(job["id"])
        design = Design.objects.get(pk=design_id)
        assert design.status == Design.Status.GENERATING

    def test_same_key_returns_the_same_job(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        key = str(uuid.uuid4())
        first = _post_refine(client, design_id, token=token, key=key, source_version_id=version.pk)
        second = _post_refine(client, design_id, token=token, key=key, source_version_id=version.pk)
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["job"]["id"] == second.json()["job"]["id"]

    def test_optional_note_is_accepted_and_never_echoed(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        body = {
            "source_version_id": str(version.pk),
            "change_type": "colour_story",
            "note": "A distinctive note fragment 8f2c1a.",
        }
        response = _post_refine(client, design_id, token=token, body=body)
        assert response.status_code == 202, response.content
        assert "8f2c1a" not in response.content.decode()


class TestRefineValidation:
    def test_missing_source_version_id_is_rejected(self):
        client, token = signed_in_client()
        design_id, _version = _generated_design(client, token)
        body = {"change_type": "colour_story", "note": ""}
        response = _post_refine(client, design_id, token=token, body=body)
        assert response.status_code == 400, response.content

    def test_unknown_change_type_is_rejected_with_refinement_invalid(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        body = {
            "source_version_id": str(version.pk),
            "change_type": "garment_type",
            "note": "",
        }
        response = _post_refine(client, design_id, token=token, body=body)
        assert response.status_code == 400, response.content
        assert response.json()["error"]["code"] == "refinement_invalid"

    def test_the_retired_styling_details_category_is_rejected(self):
        # ADR 0028 retired it. A NEW request naming it is a controlled 400 with
        # the same generic code as any other out-of-contract request — never a
        # 500, and never an accepted job that could not change the concept.
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        body = {
            "source_version_id": str(version.pk),
            "change_type": "styling_details",
            "note": "",
        }
        response = _post_refine(client, design_id, token=token, body=body)
        assert response.status_code == 400, response.content
        assert response.json()["error"]["code"] == "refinement_invalid"

    def test_unsafe_note_is_rejected_with_refinement_invalid(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        body = {
            "source_version_id": str(version.pk),
            "change_type": "colour_story",
            "note": "Please style it like Sabyasachi.",
        }
        response = _post_refine(client, design_id, token=token, body=body)
        assert response.status_code == 400, response.content
        assert response.json()["error"]["code"] == "refinement_invalid"

    def test_unknown_field_is_rejected(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        body = {
            "source_version_id": str(version.pk),
            "change_type": "colour_story",
            "note": "",
            "seed": 1,
        }
        response = _post_refine(client, design_id, token=token, body=body)
        assert response.status_code == 400, response.content

    def test_missing_idempotency_key_is_rejected(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        response = _post_refine(
            client, design_id, token=token, key=None, source_version_id=version.pk
        )
        assert response.status_code == 400, response.content

    def test_missing_csrf_token_is_rejected(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        response = _post_refine(client, design_id, token=None, source_version_id=version.pk)
        assert response.status_code == 403, response.content

    def test_a_multipart_body_is_refused_and_does_not_crash(self):
        """Pinning a claim that turned out to be wrong, so nobody re-derives it.

        Reviewing the sibling generate endpoint's 500 fix, three separate readers
        concluded this view shared the fault, because it too calls ``_parse_body``
        after Django's CSRF check has consumed a multipart stream. It does not, and
        the reason is not obvious: DRF's ``Request._parse`` catches the
        ``RawPostDataException`` itself and, for a view that lists no form parser,
        returns empty data rather than raising. So the serializer simply sees no
        fields and answers 400. Only a direct ``request.body`` read — which the
        generate view used to do and no longer does — escapes that rescue.

        Measured, not reasoned: this test exists so the next reader gets the answer
        from a run rather than from a plausible chain of inference."""
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        with mock.patch(_AVAILABLE, return_value=True):
            response = client.post(
                _refine_url(design_id),
                # The test client's default encoding is multipart.
                data={"source_version_id": str(version.pk), "change_type": "colour_story"},
                REMOTE_ADDR=unique_ip(),
                HTTP_X_CSRFTOKEN=token,
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )
        assert response.status_code == 400, response.content
        assert response.json()["error"]["code"] == "validation_failed"
        # And nothing was refined: the form fields were never read.
        assert DesignVersion.objects.filter(design_id=design_id).count() == 1
        assert Design.objects.get(pk=design_id).status == Design.Status.GENERATED


class TestRefineConflicts:
    def test_draft_design_is_not_refinable(self):
        client, token = signed_in_client()
        design_id = create_owned_design_id(client, title="Still a draft")
        response = _post_refine(client, design_id, token=token)
        assert response.status_code == 409, response.content
        assert response.json()["error"]["code"] == "design_not_refinable"

    def test_a_neckline_refinement_of_a_v1_concept_is_category_unavailable(self):
        # ADR 0028's version dispatch, at the API boundary: the fixture concept
        # is DesignSpec v1, which carries no neckline_style at all, so there is
        # nothing for a neckline refinement to change. A controlled 409 — the
        # request is well formed, it is this design that cannot accept it — and
        # no job is queued.
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        body = {
            "source_version_id": str(version.pk),
            "change_type": "neckline",
            "note": "",
        }
        response = _post_refine(client, design_id, token=token, body=body)
        assert response.status_code == 409, response.content
        assert response.json()["error"]["code"] == "refinement_category_unavailable"
        assert not DesignVersion.objects.filter(design_id=design_id, version_number=2).exists()

    def test_foreign_source_version_is_source_unavailable(self):
        client, token = signed_in_client()
        design_id, _version = _generated_design(client, token)
        other_client, other_token = signed_in_client()
        _other_design_id, other_version = _generated_design(other_client, other_token)
        response = _post_refine(client, design_id, token=token, source_version_id=other_version.pk)
        assert response.status_code == 409, response.content
        assert response.json()["error"]["code"] == "refinement_source_unavailable"

    def test_refining_the_same_version_again_is_source_unavailable(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        _complete_one_round(client, token, design_id, version)

        # The reported reason is that the source is out of date, not that the
        # budget is spent — the design has two of its three rounds left, and
        # saying "already refined" here would be a false statement about it.
        again = _post_refine(client, design_id, token=token, source_version_id=version.pk)
        assert again.status_code == 409, again.content
        assert again.json()["error"]["code"] == "refinement_source_unavailable"

    def test_a_second_refinement_from_the_new_version_is_accepted(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        version_2 = _complete_one_round(client, token, design_id, version)

        second = _post_refine(client, design_id, token=token, source_version_id=version_2.pk)
        assert second.status_code == 202, second.content

    def test_the_fourth_refinement_is_limit_reached(self, settings):
        assert settings.MAX_REFINEMENTS == 3
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        for _ in range(settings.MAX_REFINEMENTS):
            version = _complete_one_round(client, token, design_id, version)

        fourth = _post_refine(client, design_id, token=token, source_version_id=version.pk)
        assert fourth.status_code == 409, fourth.content
        assert fourth.json()["error"]["code"] == "refinement_limit_reached"
        # The customer-facing wording has to match the fact. "Already refined"
        # was true when the budget was one and is a lie about a design that has
        # been refined three times.
        assert fourth.json()["error"]["message"] == "This design has used all of its refinements."

    def test_availability_gate_closed_returns_503(self):
        client, token = signed_in_client()
        design_id, version = _generated_design(client, token)
        response = _post_refine(
            client, design_id, token=token, source_version_id=version.pk, available=False
        )
        assert response.status_code == 503, response.content
        assert response.json()["error"]["code"] == "generation_unavailable"


class TestOwnershipAndNotFound:
    def test_foreign_design_returns_indistinguishable_404(self):
        owner_client, owner_token = signed_in_client()
        design_id, version = _generated_design(owner_client, owner_token)

        other_client, other_token = signed_in_client()
        response = _post_refine(
            other_client, design_id, token=other_token, source_version_id=version.pk
        )
        assert response.status_code == 404, response.content
        assert response.json()["error"]["code"] == "not_found"

    def test_nonexistent_design_returns_404(self):
        client, token = signed_in_client()
        response = _post_refine(client, str(uuid.uuid4()), token=token)
        assert response.status_code == 404, response.content


class TestRefineRequiresAnAccount:
    """ADR 0023 applies to refinement as well, for the same reason: it is the
    other action that spends a provider call and produces something to keep.

    The reachable route here is a session that expired between generating and
    refining — the refiner had an account a moment ago. Without this gate that
    request would spend a generation for a caller with nowhere to keep the
    result."""

    def test_an_anonymous_refine_is_refused_and_queues_nothing(self):
        # A generated design in an ANONYMOUS workspace is only constructible
        # directly now, which is the point: the HTTP path to it is closed.
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id, version = _generated_design(client, token)

        response = _post_refine(client, design_id, token=token, source_version_id=version.pk)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"
        assert DesignVersion.objects.filter(design_id=design_id).count() == 1
        assert Design.objects.get(pk=design_id).status == Design.Status.GENERATED

    def test_the_refusal_is_json_and_never_a_redirect(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id, version = _generated_design(client, token)

        response = _post_refine(client, design_id, token=token, source_version_id=version.pk)

        assert response["Content-Type"].startswith("application/json")
        assert "Location" not in response

    def test_csrf_is_still_checked_first(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id, version = _generated_design(client, token)

        response = _post_refine(client, design_id, token=None, source_version_id=version.pk)

        assert response.status_code == 403

    def test_the_refusal_costs_nothing_the_signed_in_retry_needs(self):
        """Neither the idempotency key nor the design's refinement allowance
        may be spent by a request that was refused before it reached either."""
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id, version = _generated_design(client, token)
        key = str(uuid.uuid4())

        refused = _post_refine(
            client, design_id, token=token, key=key, source_version_id=version.pk
        )
        assert refused.status_code == 401
        # Asserted between the two requests: a final state of one version cannot
        # tell a refusal that spent nothing apart from one that spent the
        # allowance and was then replayed.
        assert DesignVersion.objects.filter(design_id=design_id).count() == 1
        assert Design.objects.get(pk=design_id).status == Design.Status.GENERATED

        register(client, unique_email())
        signed_in_token = bootstrap_csrf(client)
        response = _post_refine(
            client, design_id, token=signed_in_token, key=key, source_version_id=version.pk
        )

        assert response.status_code == 202, response.content
