"""Refinement enqueue-service tests (Phase 14 Part C): idempotency, gating,
preconditions and concurrency. No Celery task actually runs — a recorder is
injected as ``enqueue_task``."""

import copy
import threading
import uuid
from unittest import mock

import pytest
from django.db import connection

from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.generation.pipeline import (
    DesignNotRefinable,
    GenerationInProgress,
    GenerationUnavailable,
    QueueUnavailable,
    enqueue_design_refinement,
)
from sitara.generation.refinement import normalise_refinement_request
from sitara.generation.refinement_service import RefinementLimitReached, RefinementSourceUnavailable

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


def _request(change_type="colour_story", note=""):
    return normalise_refinement_request(
        {"schema_version": 1, "change_type": change_type, "note": note}
    )


def _generated_design_with_v1(
    *, questionnaire=None, is_demo: bool = False
) -> tuple[Design, DesignVersion]:
    """A Design in GENERATED status with a complete, valid version 1 —
    created directly via ORM (no provider call), matching the shape a real
    initial-generation pipeline would leave behind. Pass a shared
    ``questionnaire`` when a test needs a SECOND design in the same test
    (each QuestionnaireVersion.version must be globally unique, so
    make_complete_design's default of always minting version=1 collides on a
    second call within one test transaction)."""
    from django.utils import timezone

    from sitara.designs.services import create_next_design_version_locked
    from sitara.generation.context import build_generation_context
    from sitara.generation.design_spec import DESIGN_SPEC_SCHEMA_VERSION, SPEC_TEMPLATE_VERSION
    from sitara.generation.fixture_provider import build_fixture_spec
    from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION

    design = make_complete_design(questionnaire=questionnaire)
    source_selections = build_generation_context(design).source_selections
    spec_payload = build_fixture_spec(source_selections)
    version = create_next_design_version_locked(design)
    version.design_spec = spec_payload
    version.design_spec_schema_version = DESIGN_SPEC_SCHEMA_VERSION
    version.design_spec_template_version = SPEC_TEMPLATE_VERSION
    version.design_spec_provider = "fixture"
    version.design_spec_model = "fixture-model"
    version.design_spec_generated_at = timezone.now()
    version.image_prompt = "A deterministic placeholder prompt."
    version.prompt_builder_version = PROMPT_BUILDER_VERSION
    version.image_storage_key = f"design-images/{design.id}/v1/original.webp"
    version.image_sha256 = "a" * 64
    version.image_size_bytes = 100_000
    version.image_width = 900
    version.image_height = 1200
    version.thumbnail_storage_key = f"design-images/{design.id}/v1/thumbnail.webp"
    version.thumbnail_sha256 = "b" * 64
    version.thumbnail_size_bytes = 5_000
    version.thumbnail_width = 200
    version.thumbnail_height = 260
    version.image_processor_version = "1.0.0"
    version.image_ingested_at = timezone.now()
    version.is_demo = is_demo
    version.save()
    design.status = Design.Status.GENERATED
    design.save(update_fields=["status"])
    return design, version


def _complete_refinement(design: Design, source: DesignVersion) -> DesignVersion:
    """One finished refinement round: the child version plus the SUCCEEDED
    attempt that produced it, in the shape the real pipeline leaves behind.

    The succeeded attempt is the point. A database constraint requires it to
    carry a non-empty ``staged_image_storage_key``, so any guard that asks
    "does this design have a staged image anywhere?" is true from the first
    completed round onward — which is how the design-wide form of that filter
    silently refused rounds two and three."""
    from django.utils import timezone

    from sitara.designs.services import create_next_design_version
    from sitara.generation.design_spec import DESIGN_SPEC_SCHEMA_VERSION, SPEC_TEMPLATE_VERSION
    from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION

    number = source.version_number + 1
    child = create_next_design_version(
        design,
        parent_version=source,
        refinement_request={"schema_version": 1, "change_type": "colour_story", "note": ""},
        refinement_request_schema_version=1,
        refinement_request_sha256="e" * 64,
    )
    child.design_spec = copy.deepcopy(source.design_spec)
    child.design_spec_schema_version = DESIGN_SPEC_SCHEMA_VERSION
    child.design_spec_template_version = SPEC_TEMPLATE_VERSION
    child.design_spec_provider = "fixture"
    child.design_spec_model = "fixture-model"
    child.design_spec_generated_at = timezone.now()
    child.image_prompt = f"A deterministic placeholder prompt for v{number}."
    child.prompt_builder_version = PROMPT_BUILDER_VERSION
    child.image_storage_key = f"design-images/{design.id}/v{number}/original.webp"
    child.image_sha256 = f"{number:064d}"
    child.image_size_bytes = 100_000
    child.image_width = 900
    child.image_height = 1200
    child.thumbnail_storage_key = f"design-images/{design.id}/v{number}/thumbnail.webp"
    child.thumbnail_sha256 = f"{number + 500:064d}"
    child.thumbnail_size_bytes = 5_000
    child.thumbnail_width = 200
    child.thumbnail_height = 260
    child.image_processor_version = "1.0.0"
    child.image_ingested_at = timezone.now()
    child.is_demo = source.is_demo
    child.save()

    GenerationAttempt.objects.create(
        design=design,
        design_version=child,
        idempotency_key=uuid.uuid4(),
        status=_Status.SUCCEEDED,
        completed_at=timezone.now(),
        generation_kind=GenerationAttempt.GenerationKind.REFINEMENT,
        source_design_version=source,
        is_demo=source.is_demo,
        refinement_request={"schema_version": 1, "change_type": "colour_story", "note": ""},
        refinement_request_schema_version=1,
        refinement_request_sha256="e" * 64,
        staged_image_storage_key=f"staging/{design.id}/v{number}.webp",
        staged_image_sha256=f"{number + 900:064d}",
        staged_image_size_bytes=100_000,
        staged_image_width=900,
        staged_image_height=1200,
    )
    design.status = Design.Status.GENERATED
    design.save(update_fields=["status"])
    return child


class TestAvailabilityAndStatus:
    def test_gates_closed_rejects_before_any_work(self):
        design, _v1 = _generated_design_with_v1()
        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=False):
            with pytest.raises(GenerationUnavailable):
                enqueue_design_refinement(
                    design,
                    source_version_id=_v1.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=recorder,
                )
        assert GenerationAttempt.objects.count() == 0
        assert recorder.calls == []

    def test_draft_design_is_not_refinable(self):
        design = make_complete_design()
        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(DesignNotRefinable):
                enqueue_design_refinement(
                    design,
                    source_version_id=uuid.uuid4(),
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=recorder,
                )
        assert GenerationAttempt.objects.count() == 0

    def test_generating_design_is_not_refinable(self):
        design, _v1 = _generated_design_with_v1()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(DesignNotRefinable):
                enqueue_design_refinement(
                    design,
                    source_version_id=_v1.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=_Recorder(),
                )

    def test_generation_failed_design_from_a_resolved_refinement_failure_is_refinable(self):
        """A prior refinement attempt that failed cleanly (no staged image, no
        unresolved provider spend) leaves the Design in generation_failed —
        _finalise_failure never restores it to generated, since no new
        version was produced. That must not permanently block every future
        retry: version 1 is still complete and readable, so a fresh
        refinement request is accepted (regression test for a cross-commit
        defect between the Part C enqueue gate and the Part D frontend's
        retry-after-failure UI)."""
        from django.utils import timezone

        design, v1 = _generated_design_with_v1()
        GenerationAttempt.objects.create(
            design=design,
            idempotency_key=uuid.uuid4(),
            status=_Status.FAILED,
            error_code="refinement_no_change",
            completed_at=timezone.now(),
            generation_kind=GenerationAttempt.GenerationKind.REFINEMENT,
            source_design_version=v1,
            refinement_request=_request().model_dump(mode="json"),
            refinement_request_schema_version=1,
            refinement_request_sha256="c" * 64,
        )
        design.status = Design.Status.GENERATION_FAILED
        design.save(update_fields=["status"])
        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=True):
            attempt, created = enqueue_design_refinement(
                design,
                source_version_id=v1.pk,
                refinement_request=_request(),
                idempotency_key=uuid.uuid4(),
                enqueue_task=recorder,
            )
        assert created is True
        assert attempt.status == _Status.QUEUED
        assert (
            GenerationAttempt.objects.filter(
                design=design, generation_kind=GenerationAttempt.GenerationKind.REFINEMENT
            ).count()
            == 2
        )

    def test_generation_failed_design_with_no_version_is_source_unavailable_not_refinable(self):
        """A generation_failed Design from a failed INITIAL generation (no
        version 1 ever created) must not be treated as refinable just because
        the status check was relaxed — the source-version lookup fails
        closed instead, distinguishing this from the resolved-refinement-
        failure case above."""
        design = make_complete_design()
        design.status = Design.Status.GENERATION_FAILED
        design.save(update_fields=["status"])
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(RefinementSourceUnavailable):
                enqueue_design_refinement(
                    design,
                    source_version_id=uuid.uuid4(),
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=_Recorder(),
                )
        assert GenerationAttempt.objects.count() == 0


class TestSourceVersionValidation:
    def test_foreign_source_version_is_rejected(self):
        design, _v1 = _generated_design_with_v1()
        other_design, other_v1 = _generated_design_with_v1(
            questionnaire=design.questionnaire_version
        )
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(RefinementSourceUnavailable):
                enqueue_design_refinement(
                    design,
                    source_version_id=other_v1.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=_Recorder(),
                )
        assert GenerationAttempt.objects.count() == 0

    def test_nonexistent_source_version_is_rejected(self):
        design, _v1 = _generated_design_with_v1()
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(RefinementSourceUnavailable):
                enqueue_design_refinement(
                    design,
                    source_version_id=uuid.uuid4(),
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=_Recorder(),
                )

    def test_incomplete_source_version_is_rejected(self):
        from sitara.designs.services import create_next_design_version_locked

        design = make_complete_design()
        version = create_next_design_version_locked(design)  # bare, no spec
        design.status = Design.Status.GENERATED
        design.save(update_fields=["status"])
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(RefinementSourceUnavailable):
                enqueue_design_refinement(
                    design,
                    source_version_id=version.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=_Recorder(),
                )
        assert GenerationAttempt.objects.count() == 0


class TestSuccessfulEnqueue:
    def test_creates_a_queued_refinement_attempt(self, django_capture_on_commit_callbacks):
        design, v1 = _generated_design_with_v1()
        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=True):
            with django_capture_on_commit_callbacks(execute=True):
                attempt, created = enqueue_design_refinement(
                    design,
                    source_version_id=v1.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=recorder,
                )
        assert created is True
        assert attempt.status == _Status.QUEUED
        assert attempt.generation_kind == GenerationAttempt.GenerationKind.REFINEMENT
        assert attempt.source_design_version_id == v1.pk
        assert attempt.refinement_request["change_type"] == "colour_story"
        assert recorder.calls == [attempt.id]
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATING
        # Version 1 remains readable while the refinement runs.
        v1.refresh_from_db()
        assert v1.design_spec is not None
        assert v1.image_storage_key != ""

    def test_version_1_remains_readable_while_refinement_is_queued(self):
        design, v1 = _generated_design_with_v1()
        with mock.patch(_AVAILABLE, return_value=True):
            enqueue_design_refinement(
                design,
                source_version_id=v1.pk,
                refinement_request=_request(),
                idempotency_key=uuid.uuid4(),
                enqueue_task=_Recorder(),
            )
        assert DesignVersion.objects.filter(pk=v1.pk).exists()


class TestIdempotency:
    def test_same_key_returns_the_same_attempt_with_no_extra_work(self):
        design, v1 = _generated_design_with_v1()
        key = uuid.uuid4()
        with mock.patch(_AVAILABLE, return_value=True):
            first, first_created = enqueue_design_refinement(
                design,
                source_version_id=v1.pk,
                refinement_request=_request(),
                idempotency_key=key,
                enqueue_task=_Recorder(),
            )
            second, second_created = enqueue_design_refinement(
                design,
                source_version_id=v1.pk,
                refinement_request=_request(),
                idempotency_key=key,
                enqueue_task=_Recorder(),
            )
        assert first.pk == second.pk
        assert first_created is True
        assert second_created is False
        assert GenerationAttempt.objects.filter(design=design).count() == 1

    def test_replay_ignores_current_availability_gate(self):
        design, v1 = _generated_design_with_v1()
        key = uuid.uuid4()
        with mock.patch(_AVAILABLE, return_value=True):
            attempt, _created = enqueue_design_refinement(
                design,
                source_version_id=v1.pk,
                refinement_request=_request(),
                idempotency_key=key,
                enqueue_task=_Recorder(),
            )
        with mock.patch(_AVAILABLE, return_value=False):
            replayed, created = enqueue_design_refinement(
                design,
                source_version_id=v1.pk,
                refinement_request=_request(),
                idempotency_key=key,
                enqueue_task=_Recorder(),
            )
        assert replayed.pk == attempt.pk
        assert created is False

    def test_same_key_on_a_different_design_is_independent(self):
        design_a, v1_a = _generated_design_with_v1()
        design_b, v1_b = _generated_design_with_v1(questionnaire=design_a.questionnaire_version)
        key = uuid.uuid4()
        with mock.patch(_AVAILABLE, return_value=True):
            attempt_a, created_a = enqueue_design_refinement(
                design_a,
                source_version_id=v1_a.pk,
                refinement_request=_request(),
                idempotency_key=key,
                enqueue_task=_Recorder(),
            )
            attempt_b, created_b = enqueue_design_refinement(
                design_b,
                source_version_id=v1_b.pk,
                refinement_request=_request(),
                idempotency_key=key,
                enqueue_task=_Recorder(),
            )
        assert created_a is True
        assert created_b is True
        assert attempt_a.pk != attempt_b.pk


class TestInProgressAndLimit:
    def test_in_progress_attempt_blocks_a_new_refinement(self):
        design, v1 = _generated_design_with_v1()
        with mock.patch(_AVAILABLE, return_value=True):
            enqueue_design_refinement(
                design,
                source_version_id=v1.pk,
                refinement_request=_request(),
                idempotency_key=uuid.uuid4(),
                enqueue_task=_Recorder(),
            )
            with pytest.raises(GenerationInProgress):
                enqueue_design_refinement(
                    design,
                    source_version_id=v1.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=_Recorder(),
                )
        assert GenerationAttempt.objects.filter(design=design).count() == 1

    def test_refining_the_same_version_twice_is_rejected(self):
        # Reported as an out-of-date SOURCE rather than as a limit: a version
        # with a child is never the design's latest, and step 5's
        # validate_source_version runs before step 6's `refined_versions`
        # check. Both refuse; the first one to see it names the reason.
        design, v1 = _generated_design_with_v1()
        v2 = _complete_refinement(design, v1)
        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(RefinementSourceUnavailable):
                enqueue_design_refinement(
                    design,
                    source_version_id=v1.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=recorder,
                )
        assert recorder.calls == []
        assert DesignVersion.objects.filter(design=design).count() == 2
        assert v2.pk  # sanity: the existing child is untouched

    def test_a_second_refinement_from_the_new_version_is_allowed(
        self, django_capture_on_commit_callbacks
    ):
        # The regression this guard nearly shipped: a SUCCEEDED attempt is
        # required by database constraint to carry a non-empty staged image key,
        # so a design-wide "anything staged?" filter becomes true the moment the
        # FIRST refinement succeeds — harmless while a design got one round, and
        # a silent refusal of rounds two and three. The filter is scoped to the
        # source version instead, which is what "never regenerate paid output
        # that already exists" actually meant.
        design, v1 = _generated_design_with_v1()
        v2 = _complete_refinement(design, v1)
        assert (
            GenerationAttempt.objects.filter(design=design)
            .exclude(staged_image_storage_key="")
            .exists()
        )

        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=True):
            with django_capture_on_commit_callbacks(execute=True):
                attempt, created = enqueue_design_refinement(
                    design,
                    source_version_id=v2.pk,
                    refinement_request=_request("fabric_and_texture"),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=recorder,
                )

        assert created is True
        assert attempt.source_design_version_id == v2.pk
        assert recorder.calls == [attempt.id]

    def test_the_fourth_refinement_is_refused_by_the_budget(self, settings):
        assert settings.MAX_REFINEMENTS == 3
        design, version = _generated_design_with_v1()
        for _ in range(settings.MAX_REFINEMENTS):
            version = _complete_refinement(design, version)

        recorder = _Recorder()
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(RefinementLimitReached):
                enqueue_design_refinement(
                    design,
                    source_version_id=version.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=recorder,
                )
        # Nothing queued and nothing charged: the budget is checked before the
        # attempt row and before the daily count reservation.
        assert recorder.calls == []
        assert not GenerationAttempt.objects.filter(design=design, status=_Status.QUEUED).exists()

    def test_a_middle_version_is_refused_once_a_newer_one_exists(self):
        # v2 is neither the latest version nor childless, so both guards apply.
        # `validate_source_version` runs first (step 5, before the child check in
        # step 6), so the reported reason is the source being out of date —
        # asserted so a future reordering of those steps is visible here.
        design, v1 = _generated_design_with_v1()
        v2 = _complete_refinement(design, v1)
        _complete_refinement(design, v2)
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(RefinementSourceUnavailable):
                enqueue_design_refinement(
                    design,
                    source_version_id=v2.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=_Recorder(),
                )

    def test_broker_failure_marks_attempt_failed_and_design_failed(
        self, django_capture_on_commit_callbacks
    ):
        design, v1 = _generated_design_with_v1()
        recorder = _Recorder(exc=RuntimeError("broker down"))
        captured = {}
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(QueueUnavailable):
                with django_capture_on_commit_callbacks(execute=True):
                    captured["attempt"], _created = enqueue_design_refinement(
                        design,
                        source_version_id=v1.pk,
                        refinement_request=_request(),
                        idempotency_key=uuid.uuid4(),
                        enqueue_task=recorder,
                    )
        attempt = GenerationAttempt.objects.get(pk=captured["attempt"].id)
        assert attempt.status == _Status.FAILED
        assert attempt.error_code == "queue_unavailable"
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATION_FAILED
        # Version 1 is untouched by the broker failure.
        v1.refresh_from_db()
        assert v1.design_spec is not None


@pytest.mark.django_db(transaction=True)
class TestConcurrency:
    def test_concurrent_refinement_requests_admit_exactly_one(self):
        design, v1 = _generated_design_with_v1()
        created: list = []
        refused: list = []
        failures: list = []
        barrier = threading.Barrier(3, timeout=10)

        def worker():
            try:
                barrier.wait()
                attempt, was_created = enqueue_design_refinement(
                    design,
                    source_version_id=v1.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=_Recorder(),
                )
                if was_created:
                    created.append(attempt.id)
                else:
                    refused.append(attempt.id)
            except GenerationInProgress:
                refused.append("in_progress")
            except BaseException as exc:  # noqa: BLE001 - surfaced in the assert
                failures.append(exc)
            finally:
                connection.close()

        # Patched ONCE outside the thread loop: mock.patch's enter/exit is not
        # thread-safe, and patching per-worker can make another thread observe
        # a spuriously-restored (unpatched) gate mid-test.
        with mock.patch(_AVAILABLE, return_value=True):
            threads = [threading.Thread(target=worker) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        assert failures == []
        assert len(created) == 1
        assert len(refused) == 2
        assert GenerationAttempt.objects.filter(design=design).count() == 1


_DEMO_AVAILABLE = "sitara.generation.pipeline.demo_generation_is_available"


class TestDemoRefinementInheritance:
    """Phase 15 Part C spec §20: a refinement's mode is INHERITED from its
    source version, never independently resolved from current settings — a
    demo source can never be refined through the live path and a live source
    can never be refined through the demo path."""

    def test_refinement_of_a_demo_source_is_demo_and_checks_demo_readiness(self, settings):
        design, v1 = _generated_design_with_v1(is_demo=True)
        settings.DEMO_MODE = False  # current setting must be irrelevant

        def _boom(*args, **kwargs):
            raise AssertionError("a demo-sourced refinement must never check live readiness")

        with (
            mock.patch(_DEMO_AVAILABLE, return_value=True),
            mock.patch(_AVAILABLE, side_effect=_boom),
        ):
            attempt, created = enqueue_design_refinement(
                design,
                source_version_id=v1.pk,
                refinement_request=_request(),
                idempotency_key=uuid.uuid4(),
                enqueue_task=_Recorder(),
            )
        assert created is True
        assert attempt.is_demo is True

    def test_refinement_of_a_live_source_is_live_and_checks_live_readiness(self, settings):
        design, v1 = _generated_design_with_v1(is_demo=False)
        settings.DEMO_MODE = True  # current setting must be irrelevant

        def _boom(*args, **kwargs):
            raise AssertionError("a live-sourced refinement must never check demo readiness")

        with (
            mock.patch(_AVAILABLE, return_value=True),
            mock.patch(_DEMO_AVAILABLE, side_effect=_boom),
        ):
            attempt, created = enqueue_design_refinement(
                design,
                source_version_id=v1.pk,
                refinement_request=_request(),
                idempotency_key=uuid.uuid4(),
                enqueue_task=_Recorder(),
            )
        assert created is True
        assert attempt.is_demo is False

    def test_demo_refinement_unavailable_when_the_pack_is_missing(self, settings):
        design, v1 = _generated_design_with_v1(is_demo=True)
        settings.DEMO_MODE = True
        recorder = _Recorder()
        with mock.patch(_DEMO_AVAILABLE, return_value=False):
            with pytest.raises(GenerationUnavailable):
                enqueue_design_refinement(
                    design,
                    source_version_id=v1.pk,
                    refinement_request=_request(),
                    idempotency_key=uuid.uuid4(),
                    enqueue_task=recorder,
                )
        assert (
            GenerationAttempt.objects.filter(
                design=design, generation_kind=GenerationAttempt.GenerationKind.REFINEMENT
            ).count()
            == 0
        )
        assert recorder.calls == []
