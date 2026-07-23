"""Enqueue-service tests (Part A): idempotency, gating, concurrency and the
post-commit broker-failure path. No Celery task actually runs — a recorder or
raiser is injected as ``enqueue_task``.
"""

import threading
import uuid
from unittest import mock

import pytest

from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.generation import errors
from sitara.generation.pipeline import (
    DesignAlreadyGenerated,
    DesignIncomplete,
    GenerationInProgress,
    GenerationUnavailable,
    QueueUnavailable,
    enqueue_design_generation,
)

from .factory import make_complete_design

pytestmark = pytest.mark.django_db

_Status = GenerationAttempt.Status
_AVAILABLE = "sitara.generation.pipeline.generation_is_available"


class _Recorder:
    def __init__(self, exc=None):
        self.calls = []
        self._exc = exc

    def __call__(self, attempt):
        self.calls.append(attempt.id)
        if self._exc is not None:
            raise self._exc


def _incomplete_design():
    # A design linked to the questionnaire but with no answers is incomplete.
    from sitara.designs.models import DesignSession

    from .factory import make_active_v1

    return Design.objects.create(
        design_session=DesignSession.objects.create(),
        questionnaire_version=make_active_v1(),
        answers={},
    )


class TestAvailabilityAndCompleteness:
    def test_gates_closed_rejects_before_any_work(self):
        design = make_complete_design()
        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=False):
            with pytest.raises(GenerationUnavailable):
                enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
                )
        assert GenerationAttempt.objects.count() == 0
        assert recorder.calls == []

    def test_incomplete_design_creates_no_attempt(self, django_capture_on_commit_callbacks):
        design = _incomplete_design()
        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(DesignIncomplete):
                enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
                )
        assert GenerationAttempt.objects.count() == 0
        assert recorder.calls == []


class TestIdempotency:
    def test_first_request_creates_a_queued_attempt(self, django_capture_on_commit_callbacks):
        design = make_complete_design()
        recorder = _Recorder()
        key = uuid.uuid4()
        with mock.patch(_AVAILABLE, return_value=True):
            with django_capture_on_commit_callbacks(execute=True):
                attempt, created = enqueue_design_generation(
                    design, idempotency_key=key, enqueue_task=recorder
                )
        assert created is True
        assert attempt.status == _Status.QUEUED
        assert attempt.celery_task_id == str(attempt.id)  # deterministic task id
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATING
        assert recorder.calls == [attempt.id]  # enqueued exactly once

    def test_same_key_returns_same_attempt_and_queues_once(
        self, django_capture_on_commit_callbacks
    ):
        design = make_complete_design()
        recorder = _Recorder()
        key = uuid.uuid4()
        with mock.patch(_AVAILABLE, return_value=True):
            with django_capture_on_commit_callbacks(execute=True):
                first, created1 = enqueue_design_generation(
                    design, idempotency_key=key, enqueue_task=recorder
                )
            with django_capture_on_commit_callbacks(execute=True):
                second, created2 = enqueue_design_generation(
                    design, idempotency_key=key, enqueue_task=recorder
                )
        assert created1 is True and created2 is False
        assert first.id == second.id
        assert GenerationAttempt.objects.filter(design=design).count() == 1
        assert recorder.calls == [first.id]  # only the first submission

    def test_replay_ignores_current_gates(self, django_capture_on_commit_callbacks):
        design = make_complete_design()
        recorder = _Recorder()
        key = uuid.uuid4()
        with mock.patch(_AVAILABLE, return_value=True):
            with django_capture_on_commit_callbacks(execute=True):
                first, _ = enqueue_design_generation(
                    design, idempotency_key=key, enqueue_task=recorder
                )
        # Gates now closed, but a replay of the SAME key still returns the attempt.
        with mock.patch(_AVAILABLE, return_value=False):
            second, created = enqueue_design_generation(
                design, idempotency_key=key, enqueue_task=recorder
            )
        assert created is False
        assert second.id == first.id


class TestConcurrencyAndState:
    def test_in_progress_rejects_a_different_key(self, django_capture_on_commit_callbacks):
        design = make_complete_design()
        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=True):
            with django_capture_on_commit_callbacks(execute=True):
                enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
                )
            with pytest.raises(GenerationInProgress):
                enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
                )

    def test_already_generated_rejects(self, django_capture_on_commit_callbacks):
        design = make_complete_design()
        design.status = Design.Status.GENERATED
        design.save(update_fields=["status"])
        version = DesignVersion.objects.create(design=design, version_number=1)
        GenerationAttempt.objects.create(
            design=design,
            design_version=version,
            status=_Status.SUCCEEDED,
            staged_image_storage_key="generation-staging/x/raw.webp",
            staged_image_sha256="a" * 64,
            staged_image_size_bytes=10,
            staged_image_width=1,
            staged_image_height=1,
            completed_at="2026-07-18T00:00:00Z",
        )
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=_Recorder()
                )

    def test_staged_output_on_a_failed_attempt_rejects_as_generated(self):
        # CDX-009: already-paid raw output exists (the attempt later failed
        # terminally) — a new key must NOT create a second prediction.
        from django.utils import timezone as tz

        design = make_complete_design()
        design.status = Design.Status.GENERATION_FAILED
        design.save(update_fields=["status"])
        version = DesignVersion.objects.create(design=design, version_number=1)
        GenerationAttempt.objects.create(
            design=design,
            design_version=version,
            status=_Status.FAILED,
            error_code="internal_generation_error",
            completed_at=tz.now(),
            staged_image_storage_key="generation-staging/x/raw.webp",
            staged_image_sha256="a" * 64,
            staged_image_size_bytes=10,
            staged_image_width=1,
            staged_image_height=1,
        )
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=_Recorder()
                )

    def test_final_image_key_on_the_version_rejects_as_generated(self):
        # Phase 11: a version carrying a permanent ingested image can only
        # exist with COMPLETE provenance (all-or-none constraint), so the
        # guard scenario is built with the full valid shape.
        from django.utils import timezone

        design = make_complete_design()
        design.status = Design.Status.GENERATION_FAILED
        design.save(update_fields=["status"])
        DesignVersion.objects.create(
            design=design,
            version_number=1,
            design_spec={"schema_version": 1},
            design_spec_schema_version=1,
            design_spec_template_version="v1",
            design_spec_provider="fixture",
            design_spec_model="fixture-model",
            design_spec_generated_at=timezone.now(),
            image_prompt="A prompt.",
            prompt_builder_version="3.0.0",
            image_storage_key="design-images/d/v/original.webp",
            image_sha256="a" * 64,
            image_size_bytes=1000,
            image_width=1536,
            image_height=2048,
            thumbnail_storage_key="design-images/d/v/thumbnail.webp",
            thumbnail_sha256="b" * 64,
            thumbnail_size_bytes=100,
            thumbnail_width=384,
            thumbnail_height=512,
            image_processor_version="1.0.0",
            image_ingested_at=timezone.now(),
        )
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=_Recorder()
                )

    def test_multiple_versions_reject_as_not_generatable(self):
        from sitara.generation.pipeline import DesignNotGeneratable

        design = make_complete_design()
        v1 = DesignVersion.objects.create(design=design, version_number=1)
        DesignVersion.objects.create(
            design=design,
            version_number=2,
            parent_version=v1,
            refinement_request={"schema_version": 1, "change_type": "colour_story", "note": ""},
            refinement_request_schema_version=1,
            refinement_request_sha256="e" * 64,
        )
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(DesignNotGeneratable):
                enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=_Recorder()
                )
        assert GenerationAttempt.objects.count() == 0

    def test_resume_links_existing_incomplete_version(self, django_capture_on_commit_callbacks):
        design = make_complete_design()
        design.status = Design.Status.GENERATION_FAILED
        design.save(update_fields=["status"])
        version = DesignVersion.objects.create(design=design, version_number=1)
        with mock.patch(_AVAILABLE, return_value=True):
            with django_capture_on_commit_callbacks(execute=True):
                attempt, created = enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=_Recorder()
                )
        assert created is True
        assert attempt.design_version_id == version.id


class TestBrokerFailure:
    def test_broker_failure_marks_attempt_failed_and_raises(
        self, django_capture_on_commit_callbacks
    ):
        design = make_complete_design()
        raiser = _Recorder(exc=RuntimeError("broker down"))
        captured = {}
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(QueueUnavailable):
                with django_capture_on_commit_callbacks(execute=True):
                    captured["attempt"], _ = enqueue_design_generation(
                        design, idempotency_key=uuid.uuid4(), enqueue_task=raiser
                    )
        attempt = GenerationAttempt.objects.get(pk=captured["attempt"].id)
        assert attempt.status == _Status.FAILED
        assert attempt.error_code == errors.QUEUE_UNAVAILABLE
        assert attempt.completed_at is not None
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATION_FAILED


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_different_keys_admit_exactly_one():
    """Two genuinely concurrent enqueue requests with DIFFERENT idempotency
    keys must admit exactly one attempt; the loser serialises on the Design row
    lock and is rejected as already-in-progress."""
    design = make_complete_design()
    barrier = threading.Barrier(2)
    submitted: list = []
    results: dict = {}

    def worker(idx: int):
        from django.db import connection as thread_connection

        try:
            barrier.wait(timeout=10)
            _attempt, created = enqueue_design_generation(
                design,
                idempotency_key=uuid.uuid4(),
                enqueue_task=lambda a: submitted.append(a.id),
            )
            results[idx] = ("ok", created)
        except GenerationInProgress:
            results[idx] = ("in_progress", False)
        except Exception as exc:  # noqa: BLE001 - record any DB-race outcome
            results[idx] = ("error", type(exc).__name__)
        finally:
            thread_connection.close()

    with mock.patch(_AVAILABLE, return_value=True):
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

    assert GenerationAttempt.objects.filter(design=design).count() == 1
    outcomes = sorted(state for state, _ in results.values())
    # Exactly one 'ok'; the other is rejected in-progress (or, if both passed
    # the check before either committed, blocked by the partial-unique DB
    # constraint) — never two successful admissions.
    assert outcomes[0] in ("error", "in_progress")
    assert outcomes[1] == "ok"
    assert len(submitted) == 1


_DEMO_AVAILABLE = "sitara.generation.pipeline.demo_generation_is_available"


class TestDemoMode:
    """Phase 15 Part C spec §18/§27: demo takes absolute precedence over
    every paid flag/key when ``DEMO_MODE=True``, and the resolved mode is
    frozen onto the attempt at enqueue time — never re-derived later."""

    def test_demo_available_creates_a_demo_attempt(
        self, settings, django_capture_on_commit_callbacks
    ):
        settings.DEMO_MODE = True
        design = make_complete_design()
        recorder = _Recorder()
        with mock.patch(_DEMO_AVAILABLE, return_value=True):
            with django_capture_on_commit_callbacks(execute=True):
                attempt, created = enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
                )
        assert created is True
        assert attempt.is_demo is True

    def test_demo_unavailable_with_no_pack_raises_generation_unavailable(self, settings):
        settings.DEMO_MODE = True
        design = make_complete_design()
        recorder = _Recorder()
        with mock.patch(_DEMO_AVAILABLE, return_value=False):
            with pytest.raises(GenerationUnavailable):
                enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
                )
        assert GenerationAttempt.objects.count() == 0
        assert recorder.calls == []

    def test_demo_takes_precedence_over_paid_flags_and_keys_without_evaluating_live(self, settings):
        settings.DEMO_MODE = True
        settings.ALLOW_PAID_AI_CALLS = True
        settings.LIVE_GENERATION_ENABLED = True
        settings.REPLICATE_API_TOKEN = "r8_test_not_a_real_token"
        settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
        design = make_complete_design()
        recorder = _Recorder()

        def _boom(*args, **kwargs):
            raise AssertionError("live readiness must never be evaluated when DEMO_MODE=True")

        with (
            mock.patch(_DEMO_AVAILABLE, return_value=True),
            mock.patch(_AVAILABLE, side_effect=_boom),
        ):
            attempt, created = enqueue_design_generation(
                design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
            )
        assert created is True
        assert attempt.is_demo is True

    def test_resuming_an_incomplete_version_inherits_its_frozen_demo_mode(self, settings):
        # A first attempt links a DesignVersion under demo mode, then fails
        # at the image stage (a linked, incomplete version is left behind).
        # A brand-new enqueue call resuming that version previews it BEFORE
        # checking availability (spec §27 step 1), so it correctly checks
        # DEMO readiness for this resume even though DEMO_MODE has since
        # flipped to False — the linked version's mode wins over whatever
        # current settings would otherwise pick, so a settings change can
        # never turn an existing demo version into a live (paid)
        # continuation.
        from django.utils import timezone

        settings.DEMO_MODE = True
        design = make_complete_design()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        first = GenerationAttempt.objects.create(design=design, status=_Status.QUEUED, is_demo=True)
        version = DesignVersion.objects.create(
            design=design,
            version_number=1,
            design_spec={"title": "A placeholder demo concept."},
            design_spec_schema_version=1,
            design_spec_template_version="demo-1.0.0",
            design_spec_provider="demo",
            design_spec_model="demo-spec-2.0.0",
            design_spec_generated_at=timezone.now(),
            image_prompt="A deterministic placeholder prompt.",
            prompt_builder_version="3.0.0",
            is_demo=True,
        )
        GenerationAttempt.objects.filter(pk=first.pk).update(
            status=_Status.FAILED,
            design_version=version,
            error_code=errors.IMAGE_PREDICTION_FAILED,
            completed_at=first.created_at,
        )
        design.status = Design.Status.GENERATION_FAILED
        design.save(update_fields=["status"])

        settings.DEMO_MODE = False
        recorder = _Recorder()
        with mock.patch(_DEMO_AVAILABLE, return_value=True):
            attempt, created = enqueue_design_generation(
                design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
            )
        assert created is True
        assert attempt.is_demo is True
        assert attempt.design_version_id == version.id


class TestResolveGenerationModeAgreesWithEnqueue:
    """The public ``resolve_generation_mode()`` (ai_gateway.policy, used by
    the /config endpoint) and ``enqueue_design_generation``'s own admit/
    reject decision are two independently-maintained implementations of the
    same demo-precedence rule (spec §18/§27). This regression test asserts
    they stay behaviourally consistent across the settings matrix, so any
    future drift between them fails CI immediately instead of silently."""

    @pytest.mark.parametrize(
        "demo_mode,demo_ready,live_ready",
        [
            (True, True, True),
            (True, True, False),
            (True, False, True),
            (True, False, False),
            (False, True, True),
            (False, True, False),
            (False, False, True),
            (False, False, False),
        ],
    )
    def test_resolved_mode_matches_enqueue_outcome(
        self, settings, demo_mode, demo_ready, live_ready
    ):
        from sitara.ai_gateway.policy import resolve_generation_mode

        settings.DEMO_MODE = demo_mode
        with (
            mock.patch(_DEMO_AVAILABLE, return_value=demo_ready),
            mock.patch(
                "sitara.generation.demo.config.demo_generation_is_available",
                return_value=demo_ready,
            ),
            mock.patch(_AVAILABLE, return_value=live_ready),
            mock.patch("sitara.ai_gateway.policy.generation_is_available", return_value=live_ready),
        ):
            mode = resolve_generation_mode()
            design = make_complete_design()
            recorder = _Recorder()
            if mode == "unavailable":
                with pytest.raises(GenerationUnavailable):
                    enqueue_design_generation(
                        design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
                    )
            else:
                attempt, created = enqueue_design_generation(
                    design, idempotency_key=uuid.uuid4(), enqueue_task=recorder
                )
                assert created is True
                assert attempt.is_demo is (mode == "demo")
