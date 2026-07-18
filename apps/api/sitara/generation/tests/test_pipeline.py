"""Resumable pipeline state-machine tests (Part A) — zero network, zero live
providers. Fakes are injected for the structured provider, image provider,
downloader and storage.
"""

import hashlib
import threading
import uuid

import pytest

from sitara.ai_gateway.image_generation import (
    PREDICTION_ABORTED,
    PREDICTION_CANCELED,
    PREDICTION_FAILED,
    PREDICTION_PROCESSING,
    PREDICTION_SUCCEEDED,
    ImageProviderError,
)
from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.generation import errors
from sitara.generation.fixture_provider import FixtureStructuredDesignProvider
from sitara.generation.image_fixtures import (
    FakeImageProvider,
    InMemoryStorage,
    invalid_bytes_downloader,
    make_synthetic_png,
    synthetic_webp_downloader,
)
from sitara.generation.pipeline import (
    PipelineConfig,
    _attempt_lock_keys,
    run_generation_attempt,
)

from .factory import make_complete_design

pytestmark = pytest.mark.django_db

_Status = GenerationAttempt.Status
_FAST = PipelineConfig(poll_interval_seconds=0.0, poll_max_attempts=10)


def _queued_attempt(design) -> GenerationAttempt:
    design.status = Design.Status.GENERATING
    design.save(update_fields=["status"])
    return GenerationAttempt.objects.create(design=design, status=_Status.QUEUED)


def _run(
    attempt,
    *,
    structured=None,
    image=None,
    downloader=synthetic_webp_downloader,
    storage=None,
    seed_factory=None,
    config=_FAST,
):
    return run_generation_attempt(
        attempt.id,
        structured_provider=structured or FixtureStructuredDesignProvider(),
        image_provider=image or FakeImageProvider(),
        image_downloader=downloader,
        storage=storage or InMemoryStorage(),
        seed_factory=seed_factory or (lambda: 0),
        config=config,
    )


class TestHappyPath:
    def test_full_pipeline_reaches_succeeded(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt)
        assert result.status == _Status.SUCCEEDED
        assert result.error_code == ""
        assert result.completed_at is not None
        # DesignVersion linked; Design generated.
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATED
        assert result.design_version_id is not None
        version = DesignVersion.objects.get(pk=result.design_version_id)
        assert version.design_spec is not None
        assert version.prompt_builder_version == "3.0.0"
        # The FINAL design image key stays blank in Phase 10.
        assert version.image_storage_key == ""
        # Staged raw image metadata is populated all-or-none.
        assert result.staged_image_storage_key.startswith(f"generation-staging/{attempt.id}/raw.")
        assert len(result.staged_image_sha256) == 64
        assert result.staged_image_size_bytes > 0
        assert result.staged_image_width == 768
        assert result.staged_image_height == 1024

    def test_one_design_version_created(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt)
        assert DesignVersion.objects.filter(design=design).count() == 1

    def test_seed_and_parameters_recorded_privately(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, seed_factory=lambda: 12345)
        assert result.image_seed == 12345
        assert result.image_parameters["aspect_ratio"] == "3:4"
        assert result.image_parameters["output_format"] == "webp"
        assert "prompt" not in result.image_parameters


class TestResume:
    def test_redelivery_after_linked_version_skips_text_provider(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        structured = FixtureStructuredDesignProvider()
        # First run fails at the image stage (terminal), leaving a linked
        # DesignVersion behind.
        _run(
            attempt,
            structured=structured,
            image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]),
        )
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED
        assert attempt.design_version_id is not None
        first_calls = structured._calls
        # A brand-new attempt reuses the existing DesignVersion (image-only).
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        retry = GenerationAttempt.objects.create(
            design=design, design_version_id=attempt.design_version_id, status=_Status.QUEUED
        )
        result = _run(retry, structured=structured)
        assert result.status == _Status.SUCCEEDED
        # The text provider was NOT called again on the resuming attempt.
        assert structured._calls == first_calls
        # Still exactly one DesignVersion.
        assert DesignVersion.objects.filter(design=design).count() == 1

    def test_transient_image_failure_retries_without_another_text_call(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        structured = FixtureStructuredDesignProvider()
        image = FakeImageProvider(
            poll_actions=[ImageProviderError("timeout"), PREDICTION_SUCCEEDED]
        )
        # First invocation raises GenerationRetry (transient poll failure).
        from sitara.generation.pipeline import GenerationRetry

        with pytest.raises(GenerationRetry):
            _run(attempt, structured=structured, image=image)
        attempt.refresh_from_db()
        assert attempt.status == _Status.RUNNING_IMAGE
        assert attempt.image_prediction_id  # persisted before polling
        assert structured._calls == 1
        prediction_id = attempt.image_prediction_id
        # Redelivery resumes: no new text call, same prediction, succeeds.
        result = _run(attempt, structured=structured, image=image)
        assert result.status == _Status.SUCCEEDED
        assert structured._calls == 1
        assert image.create_calls == 1
        assert result.image_prediction_id == prediction_id

    def test_existing_prompt_is_reused(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        version = DesignVersion.objects.get(pk=attempt.design_version_id)
        original_prompt = version.image_prompt
        # Resume image-only against the same version; prompt is not rebuilt.
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        retry = GenerationAttempt.objects.create(
            design=design, design_version=version, status=_Status.QUEUED
        )
        _run(retry)
        version.refresh_from_db()
        assert version.image_prompt == original_prompt

    def test_terminal_invocation_is_idempotent(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt)
        attempt.refresh_from_db()
        completed = attempt.completed_at
        # Re-running a succeeded attempt is a no-op (does not re-generate).
        again = _run(attempt)
        assert again.status == _Status.SUCCEEDED
        assert again.completed_at == completed
        assert DesignVersion.objects.filter(design=design).count() == 1


class TestImageFailures:
    @pytest.mark.parametrize(
        "state,code",
        [
            (PREDICTION_FAILED, errors.IMAGE_PREDICTION_FAILED),
            (PREDICTION_CANCELED, errors.IMAGE_PREDICTION_CANCELED),
            (PREDICTION_ABORTED, errors.IMAGE_PREDICTION_ABORTED),
        ],
    )
    def test_terminal_prediction_states_map_to_codes(self, state, code):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, image=FakeImageProvider(poll_actions=[state]))
        assert result.status == _Status.FAILED
        assert result.error_code == code
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATION_FAILED

    def test_ambiguous_submission_is_terminal_without_retry(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        image = FakeImageProvider(
            create_error=ImageProviderError("timeout", ambiguous_acceptance=True)
        )
        result = _run(attempt, image=image)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_SUBMISSION_AMBIGUOUS
        # No prediction id was persisted (ambiguous never resubmits/polls).
        assert result.image_prediction_id == ""

    def test_invalid_output_bytes_are_rejected(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, downloader=invalid_bytes_downloader)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_OUTPUT_INVALID

    def test_poll_timeout_cancels_and_fails(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        image = FakeImageProvider(poll_actions=[PREDICTION_PROCESSING])
        result = _run(attempt, image=image, config=PipelineConfig(poll_max_attempts=3))
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_POLL_TIMEOUT
        assert image.cancel_calls == 1

    def test_wall_clock_deadline_bounds_polling_before_attempt_count(self, monkeypatch):
        # Even with a high attempt count, a wall-clock deadline stops polling
        # once elapsed — so slow status calls cannot exceed REPLICATE_POLL_TIMEOUT.
        import sitara.generation.pipeline as pipeline_module

        # First _monotonic() sets the deadline base (0); the next check reports a
        # time already past the 100s deadline, so the loop breaks after one poll.
        times = iter([0.0] + [500.0] * 20)
        monkeypatch.setattr(pipeline_module, "_monotonic", lambda: next(times))
        design = make_complete_design()
        attempt = _queued_attempt(design)
        image = FakeImageProvider(poll_actions=[PREDICTION_PROCESSING])
        result = _run(
            attempt,
            image=image,
            config=PipelineConfig(poll_max_attempts=90, poll_timeout_seconds=100.0),
        )
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_POLL_TIMEOUT
        # The deadline broke the loop long before the 90-attempt count.
        assert image.get_calls == 1
        assert image.cancel_calls == 1

    def test_error_codes_are_all_in_the_allowlist(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        assert errors.is_valid_error_code(result.error_code)


class TestStagingResume:
    def test_matching_staged_object_resumes_without_reverify_failure(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        storage = InMemoryStorage()
        _run(attempt, storage=storage)
        attempt.refresh_from_db()
        key = attempt.staged_image_storage_key
        assert storage.exists(key)

    def test_existing_matching_object_at_the_deterministic_key_resumes(self):
        # Task-restart scenario: the raw object was already staged at the
        # deterministic key before this run. The RECOVERY PROBE
        # (_recover_staged_object) finds and verifies it before any provider
        # operation, so the run finishes without re-downloading or re-saving.
        from sitara.generation.image_fixtures import make_synthetic_webp

        design = make_complete_design()
        attempt = _queued_attempt(design)
        storage = InMemoryStorage()
        key = f"generation-staging/{attempt.id}/raw.webp"
        storage._objects[key] = make_synthetic_webp()  # identical bytes
        result = _run(attempt, storage=storage)
        assert result.status == _Status.SUCCEEDED
        assert result.staged_image_storage_key == key
        assert storage.exists(key)

    def test_stage_raw_image_resumes_on_matching_object_directly(self):
        # The IN-BAND branch of _stage_raw_image (reached when an object
        # appears between the recovery probe and the save — a concurrent
        # redelivery race) must resume on byte-identical content without a
        # second save, independent of the recovery probe.
        from sitara.generation.image_fixtures import make_synthetic_webp
        from sitara.generation.pipeline import _stage_raw_image

        data = make_synthetic_webp()
        sha = hashlib.sha256(data).hexdigest()
        storage = InMemoryStorage()
        key = "generation-staging/some-attempt/raw.webp"
        storage._objects[key] = data
        # InMemoryStorage.save raises on an existing key, so a successful
        # return proves the resume branch ran instead of a second save.
        assert _stage_raw_image(storage, "some-attempt", "webp", data, sha, _FAST) == key

    def test_stage_raw_image_fails_on_conflicting_object_directly(self):
        from sitara.generation.image_fixtures import make_synthetic_webp
        from sitara.generation.pipeline import _stage_raw_image, _TerminalGenerationError

        data = make_synthetic_webp()
        sha = hashlib.sha256(data).hexdigest()
        storage = InMemoryStorage()
        key = "generation-staging/some-attempt/raw.webp"
        storage._objects[key] = b"different bytes that must never be overwritten"
        with pytest.raises(_TerminalGenerationError) as exc:
            _stage_raw_image(storage, "some-attempt", "webp", data, sha, _FAST)
        assert exc.value.code == errors.IMAGE_STAGING_FAILED
        assert storage._objects[key] == b"different bytes that must never be overwritten"

    def test_stage_raw_image_transient_exists_error_is_retryable_directly(self):
        # The staged bytes are ALREADY-PAID output: a transport failure during
        # the pre-save existence probe must be a bounded retry with the
        # UNVERIFIED code, never a terminal (or unclassified) failure.
        from sitara.generation.image_fixtures import make_synthetic_webp
        from sitara.generation.pipeline import GenerationRetry, _stage_raw_image

        class _FailingExists(InMemoryStorage):
            def exists(self, key):
                raise ConnectionError("storage blip")

        data = make_synthetic_webp()
        sha = hashlib.sha256(data).hexdigest()
        with pytest.raises(GenerationRetry) as exc:
            _stage_raw_image(_FailingExists(), "some-attempt", "webp", data, sha, _FAST)
        assert exc.value.code == errors.IMAGE_STAGING_UNVERIFIED

    def test_stage_raw_image_transient_readback_error_is_retryable_directly(self):
        # exists()==True but the byte-identity read-back hits a transport
        # error: the object was just confirmed durable, so retry — terminal is
        # reserved for a CONFIRMED hash divergence.
        from sitara.generation.image_fixtures import make_synthetic_webp
        from sitara.generation.pipeline import GenerationRetry, _stage_raw_image

        class _FailingOpen(InMemoryStorage):
            def open(self, key, mode="rb"):
                raise ConnectionError("storage blip")

        data = make_synthetic_webp()
        sha = hashlib.sha256(data).hexdigest()
        storage = _FailingOpen()
        key = "generation-staging/some-attempt/raw.webp"
        storage._objects[key] = data
        with pytest.raises(GenerationRetry) as exc:
            _stage_raw_image(storage, "some-attempt", "webp", data, sha, _FAST)
        assert exc.value.code == errors.IMAGE_STAGING_UNVERIFIED
        assert storage._objects[key] == data  # untouched

    def test_transient_save_error_during_staging_is_retryable(self):
        # End-to-end: a storage blip while SAVING the freshly downloaded,
        # already-billed image is a bounded retry; the redelivery reuses the
        # persisted prediction id (create_calls stays 1 — never a second paid
        # submission) and succeeds once storage recovers.
        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)

        class _BlippingSave(InMemoryStorage):
            def __init__(self):
                super().__init__()
                self.save_calls = 0

            def save(self, key, content):
                self.save_calls += 1
                if self.save_calls == 1:
                    raise ConnectionError("storage blip")
                return super().save(key, content)

        storage = _BlippingSave()
        provider = FakeImageProvider()
        with pytest.raises(GenerationRetry) as exc:
            _run(attempt, image=provider, storage=storage)
        assert exc.value.code == errors.IMAGE_STAGING_UNVERIFIED
        attempt.refresh_from_db()
        assert attempt.status == _Status.RUNNING_IMAGE
        assert attempt.image_prediction_id  # persisted before the blip
        assert provider.create_calls == 1
        # Redelivery with healthy storage: same prediction, no new submission.
        result = _run(attempt, image=provider, storage=storage)
        assert result.status == _Status.SUCCEEDED
        assert provider.create_calls == 1
        assert result.staged_image_storage_key.startswith(f"generation-staging/{attempt.id}/")

    def test_backend_renaming_around_the_key_fails_safely(self):
        # A non-overwriting backend that saves under a DIFFERENT key than
        # requested is a staging conflict: the renamed object is cleaned up
        # best-effort and the attempt fails with image_staging_failed.
        design = make_complete_design()
        attempt = _queued_attempt(design)

        class _RenamingStorage(InMemoryStorage):
            def save(self, key, content):
                renamed = key + ".alt"
                self._objects[renamed] = content.read()
                return renamed

        storage = _RenamingStorage()
        result = _run(attempt, storage=storage)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_STAGING_FAILED
        # Best-effort cleanup removed the mis-keyed object.
        assert storage._objects == {}

    def test_soft_time_limit_during_rename_cleanup_is_retryable(self):
        from celery.exceptions import SoftTimeLimitExceeded

        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)

        class _RenamingInterruptedDelete(InMemoryStorage):
            def save(self, key, content):
                renamed = key + ".alt"
                self._objects[renamed] = content.read()
                return renamed

            def delete(self, key):
                raise SoftTimeLimitExceeded()

        with pytest.raises(GenerationRetry):
            _run(attempt, storage=_RenamingInterruptedDelete())
        attempt.refresh_from_db()
        assert attempt.status == _Status.RUNNING_IMAGE  # retryable, not terminal

    def test_conflicting_object_at_the_deterministic_key_fails_safely(self):
        # A DIFFERENT (non-image) object at the deterministic key must never
        # be overwritten or finalised on — the RECOVERY PROBE rejects it and
        # the attempt fails with image_staging_failed.
        design = make_complete_design()
        attempt = _queued_attempt(design)
        storage = InMemoryStorage()
        key = f"generation-staging/{attempt.id}/raw.webp"
        storage._objects[key] = b"a different object that must not be replaced"
        result = _run(attempt, storage=storage)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_STAGING_FAILED
        # The pre-existing object is untouched.
        assert storage._objects[key] == b"a different object that must not be replaced"


class TestStagingRecovery:
    """CDX-004: paid output staged just before a crash (metadata lost) is
    recovered without any provider operation; persisted metadata is verified
    against the real object before an attempt may finalise.

    Also owns the enqueue guard's spend-resolution boundary tests: codes in
    ``_SPEND_RESOLVED_CODES`` keep the recovery path open, every other code
    on an evidence-bearing failed attempt blocks regeneration (fail closed),
    and evidence-free failures always readmit."""

    def test_saved_object_without_metadata_is_recovered_without_provider_calls(self):
        from sitara.generation.image_fixtures import make_synthetic_webp

        design = make_complete_design()
        attempt = _queued_attempt(design)
        # First get a linked version + prompt (fail the image stage cleanly),
        # then simulate "object staged, metadata write lost, URL expired".
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        retry = GenerationAttempt.objects.create(
            design=design, design_version_id=attempt.design_version_id, status=_Status.QUEUED
        )
        storage = InMemoryStorage()
        storage._objects[f"generation-staging/{retry.id}/raw.webp"] = make_synthetic_webp()

        def expired_url_downloader(_url):  # the temporary provider URL is gone
            raise AssertionError("download must not be attempted during recovery")

        provider = FakeImageProvider()
        result = _run(retry, image=provider, downloader=expired_url_downloader, storage=storage)
        assert result.status == _Status.SUCCEEDED
        assert provider.create_calls == 0  # no provider operation at all
        assert provider.get_calls == 0
        assert result.staged_image_storage_key == f"generation-staging/{retry.id}/raw.webp"
        assert len(result.staged_image_sha256) == 64

    def test_metadata_with_matching_object_resumes_to_succeeded(self):
        # SUCCESS branch of _verify_persisted_staging: valid persisted metadata
        # whose object exists and matches finalises WITHOUT any provider or
        # downloader operation.
        from sitara.generation.image_fixtures import make_synthetic_webp

        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        data = make_synthetic_webp()
        key = "generation-staging/verified/raw.webp"
        retry = GenerationAttempt.objects.create(
            design=design,
            design_version_id=attempt.design_version_id,
            status=_Status.RUNNING_IMAGE,
            staged_image_storage_key=key,
            staged_image_sha256=hashlib.sha256(data).hexdigest(),
            staged_image_size_bytes=len(data),
            staged_image_width=768,
            staged_image_height=1024,
        )
        storage = InMemoryStorage()
        storage._objects[key] = data
        provider = FakeImageProvider()

        def never_download(_url):
            raise AssertionError("downloader must not run on a verified resume")

        result = _run(retry, image=provider, downloader=never_download, storage=storage)
        assert result.status == _Status.SUCCEEDED
        assert provider.create_calls == 0
        assert provider.get_calls == 0

    def test_verified_resume_finalises_with_gates_closed(self, settings):
        # Round-3 CDX-002: provider resolution is DEFERRED — a resume that
        # only needs storage verification must finalise already-paid output
        # even when the live gates are closed (no provider or downloader is
        # ever constructed on this path).
        settings.DEMO_MODE = True  # paid-image gate closed
        from sitara.generation.image_fixtures import make_synthetic_webp

        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        data = make_synthetic_webp()
        key = "generation-staging/gated-resume/raw.webp"
        retry = GenerationAttempt.objects.create(
            design=design,
            design_version_id=attempt.design_version_id,
            status=_Status.RUNNING_IMAGE,
            staged_image_storage_key=key,
            staged_image_sha256=hashlib.sha256(data).hexdigest(),
            staged_image_size_bytes=len(data),
            staged_image_width=768,
            staged_image_height=1024,
        )
        storage = InMemoryStorage()
        storage._objects[key] = data
        result = run_generation_attempt(
            retry.id,
            structured_provider=FixtureStructuredDesignProvider(),
            image_provider=None,  # would fail closed if it were resolved
            image_downloader=None,
            storage=storage,
            seed_factory=lambda: 0,
            config=_FAST,
        )
        assert result.status == _Status.SUCCEEDED

    def test_transient_storage_error_during_verification_is_retryable(self):
        # A storage blip during resume-verification must be a bounded retry —
        # never a terminal failure that (with the enqueue guard) would strand
        # the design permanently. The staged object is durable; retrying is safe.
        from sitara.generation.image_fixtures import make_synthetic_webp
        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        data = make_synthetic_webp()
        key = "generation-staging/blip/raw.webp"
        retry = GenerationAttempt.objects.create(
            design=design,
            design_version_id=attempt.design_version_id,
            status=_Status.RUNNING_IMAGE,
            staged_image_storage_key=key,
            staged_image_sha256=hashlib.sha256(data).hexdigest(),
            staged_image_size_bytes=len(data),
            staged_image_width=768,
            staged_image_height=1024,
        )

        class _BlippingStorage(InMemoryStorage):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def open(self, k, mode="rb"):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("storage blip")
                return super().open(k, mode)

        storage = _BlippingStorage()
        storage._objects[key] = data
        # First delivery: the blip surfaces as a bounded retry, NOT terminal.
        # The retry carries the UNVERIFIED code (content state unknown), never
        # the confirmed image_staging_failed code the enqueue guard readmits.
        with pytest.raises(GenerationRetry) as exc:
            _run(retry, storage=storage)
        assert exc.value.code == errors.IMAGE_STAGING_UNVERIFIED
        retry.refresh_from_db()
        assert retry.status == _Status.RUNNING_IMAGE
        # Redelivery with healthy storage verifies and succeeds.
        result = _run(retry, storage=storage)
        assert result.status == _Status.SUCCEEDED

    def test_transient_storage_error_during_recovery_probe_is_retryable(self):
        # The pre-provider RECOVERY PROBE shares the transient discipline: a
        # storage blip during its existence check is a bounded retry (never
        # terminal), and the redelivery recovers the already-staged object
        # without any provider operation — a blip must never strand paid
        # output staged just before a metadata-losing crash.
        from sitara.generation.image_fixtures import make_synthetic_webp
        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        retry = GenerationAttempt.objects.create(
            design=design, design_version_id=attempt.design_version_id, status=_Status.QUEUED
        )

        class _BlippingExists(InMemoryStorage):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def exists(self, key):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("storage blip")
                return super().exists(key)

        storage = _BlippingExists()
        storage._objects[f"generation-staging/{retry.id}/raw.webp"] = make_synthetic_webp()

        def never_download(_url):
            raise AssertionError("download must not be attempted during recovery")

        provider = FakeImageProvider()
        with pytest.raises(GenerationRetry) as exc:
            _run(retry, image=provider, downloader=never_download, storage=storage)
        assert exc.value.code == errors.IMAGE_STAGING_UNVERIFIED
        retry.refresh_from_db()
        assert retry.status == _Status.RUNNING_IMAGE
        assert provider.create_calls == 0
        # Redelivery with healthy storage recovers the paid output — still no
        # provider operation of any kind.
        result = _run(retry, image=provider, downloader=never_download, storage=storage)
        assert result.status == _Status.SUCCEEDED
        assert provider.create_calls == 0
        assert provider.get_calls == 0

    def test_design_can_be_regenerated_after_confirmed_staging_failure(self):
        # An attempt whose staged data FAILED verification (error_code
        # image_staging_failed) has confirmed-unusable output — it must not
        # permanently block the design; a fresh idempotency key may retry.
        import uuid as uuid_module
        from unittest import mock

        from django.utils import timezone as tz

        from sitara.generation.pipeline import enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        # Simulate: metadata was persisted, then verification terminally
        # failed (missing/corrupt object) — staged metadata remains, code is
        # image_staging_failed.
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            error_code=errors.IMAGE_STAGING_FAILED,
            completed_at=tz.now(),
            staged_image_storage_key="generation-staging/unusable/raw.webp",
            staged_image_sha256="b" * 64,
            staged_image_size_bytes=10,
            staged_image_width=1,
            staged_image_height=1,
        )
        design.refresh_from_db()
        # A new key is admitted (the confirmed-bad staging does not block) and
        # resumes the existing version.
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            new_attempt, created = enqueue_design_generation(
                design,
                idempotency_key=uuid_module.uuid4(),
                enqueue_task=lambda a: None,
            )
        assert created is True
        assert new_attempt.design_version_id == attempt.design_version_id

    def test_unverified_staging_exhaustion_keeps_blocking_regeneration(self):
        # Retry-EXHAUSTED verification persists image_staging_unverified: the
        # staged content state is UNKNOWN (the object may be a perfectly valid
        # paid image behind a storage outage), so it must NOT qualify for the
        # confirmed-bad readmission — a second paid generation stays blocked.
        import uuid as uuid_module
        from unittest import mock

        from django.utils import timezone as tz

        from sitara.generation.pipeline import DesignAlreadyGenerated, enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            error_code=errors.IMAGE_STAGING_UNVERIFIED,
            completed_at=tz.now(),
            staged_image_storage_key="generation-staging/unreachable/raw.webp",
            staged_image_sha256="c" * 64,
            staged_image_size_bytes=10,
            staged_image_width=1,
            staged_image_height=1,
        )
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design,
                    idempotency_key=uuid_module.uuid4(),
                    enqueue_task=lambda a: None,
                )

    def test_unverified_exhaustion_with_empty_staged_key_still_blocks(self):
        # REAL first-staging exhaustion path: the provider was billed (id
        # persisted), the download succeeded, but store.save() blipped and the
        # bounded retries ran out — the FAILED row has an EMPTY staged key.
        # The guard must still block a second paid submission via the
        # persisted prediction id.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import (
            DesignAlreadyGenerated,
            GenerationRetry,
            enqueue_design_generation,
            fail_attempt,
        )

        design = make_complete_design()
        attempt = _queued_attempt(design)

        class _AlwaysFailingSave(InMemoryStorage):
            def save(self, key, content):
                raise ConnectionError("storage outage")

        provider = FakeImageProvider()
        with pytest.raises(GenerationRetry) as exc:
            _run(attempt, image=provider, storage=_AlwaysFailingSave())
        assert exc.value.code == errors.IMAGE_STAGING_UNVERIFIED
        # Exhaustion wiring (proven at the task boundary in test_tasks.py)
        # persists the classified code on the SAME attempt.
        fail_attempt(attempt.id, errors.IMAGE_STAGING_UNVERIFIED)
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED
        assert attempt.error_code == errors.IMAGE_STAGING_UNVERIFIED
        assert attempt.staged_image_storage_key == ""  # never persisted
        assert attempt.image_prediction_id  # the billed submission
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design,
                    idempotency_key=uuid_module.uuid4(),
                    enqueue_task=lambda a: None,
                )
        assert provider.create_calls == 1  # never a second paid submission

    def test_unverified_exhaustion_with_inflight_marker_blocks(self):
        # Crash window: the in-flight marker was set (submission may have been
        # accepted) but no id was ever persisted; unverified exhaustion with
        # an empty staged key must still block — spend may have occurred.
        import uuid as uuid_module
        from unittest import mock

        from django.utils import timezone as tz

        from sitara.generation.pipeline import DesignAlreadyGenerated, enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            error_code=errors.IMAGE_STAGING_UNVERIFIED,
            completed_at=tz.now(),
            image_prediction_id="",
            image_submission_in_flight=True,
        )
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design,
                    idempotency_key=uuid_module.uuid4(),
                    enqueue_task=lambda a: None,
                )

    def test_ambiguous_submission_blocks_regeneration(self):
        # REAL path: the provider raises an ambiguous-acceptance error (the
        # create MAY have been accepted and billed). The FAILED row carries
        # the in-flight marker and must block a fresh idempotency key — the
        # same unresolved-spend discipline as image_staging_unverified.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import DesignAlreadyGenerated, enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        provider = FakeImageProvider(
            create_error=ImageProviderError("boom", ambiguous_acceptance=True)
        )
        result = _run(attempt, image=provider)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_SUBMISSION_AMBIGUOUS
        assert result.image_submission_in_flight is True
        assert result.staged_image_storage_key == ""
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design,
                    idempotency_key=uuid_module.uuid4(),
                    enqueue_task=lambda a: None,
                )
        assert provider.create_calls == 1  # never a second paid submission

    def test_confirmed_prediction_failure_still_readmits(self):
        # BOUNDARY: image_prediction_failed is a provider-CONFIRMED outcome
        # (the persisted id was polled to a terminal failed state). The spec's
        # recovery path — a new idempotency key retrying only the image
        # stage — must keep working for confirmed terminal failures.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_PREDICTION_FAILED
        assert result.image_prediction_id  # id persisted — evidence alone
        design.refresh_from_db()  # must NOT block a confirmed failure
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            new_attempt, created = enqueue_design_generation(
                design,
                idempotency_key=uuid_module.uuid4(),
                enqueue_task=lambda a: None,
            )
        assert created is True
        assert new_attempt.design_version_id == result.design_version_id

    def test_poll_outage_with_live_prediction_blocks_regeneration(self):
        # A poll-transport outage terminates an attempt whose prediction is
        # LIVE (id persisted, billed, outcome never confirmed). Exhaustion
        # persists image_provider_unavailable — not in the resolved set, so
        # the evidence-bearing row must block a second paid submission.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import (
            DesignAlreadyGenerated,
            GenerationRetry,
            enqueue_design_generation,
            fail_attempt,
        )

        design = make_complete_design()
        attempt = _queued_attempt(design)
        provider = FakeImageProvider(poll_actions=[ImageProviderError("status outage")])
        with pytest.raises(GenerationRetry) as exc:
            _run(attempt, image=provider)
        assert exc.value.code == errors.IMAGE_PROVIDER_UNAVAILABLE
        fail_attempt(attempt.id, errors.IMAGE_PROVIDER_UNAVAILABLE)  # exhaustion
        attempt.refresh_from_db()
        assert attempt.image_prediction_id  # the live, billed prediction
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design, idempotency_key=uuid_module.uuid4(), enqueue_task=lambda a: None
                )
        assert provider.create_calls == 1

    def test_pre_acceptance_unavailable_without_evidence_readmits(self):
        # The SAME code raised pre-acceptance (marker cleared, no id) carries
        # no submission evidence — the provider was provably never invoked,
        # so exhaustion there must not strand the design.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import (
            GenerationRetry,
            enqueue_design_generation,
            fail_attempt,
        )

        design = make_complete_design()
        attempt = _queued_attempt(design)
        provider = FakeImageProvider(
            create_error=ImageProviderError("connect", ambiguous_acceptance=False)
        )
        with pytest.raises(GenerationRetry):
            _run(attempt, image=provider)
        fail_attempt(attempt.id, errors.IMAGE_PROVIDER_UNAVAILABLE)  # exhaustion
        attempt.refresh_from_db()
        assert attempt.image_submission_in_flight is False  # cleared pre-acceptance
        assert attempt.image_prediction_id == ""
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            new_attempt, created = enqueue_design_generation(
                design, idempotency_key=uuid_module.uuid4(), enqueue_task=lambda a: None
            )
        assert created is True

    def test_post_submission_crash_blocks_regeneration(self):
        # An unclassified crash after the provider accepted the create (the
        # marker is still set; internal_generation_error) is unresolved spend
        # — the fail-closed default must block a fresh key.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import DesignAlreadyGenerated, enqueue_design_generation

        class _CrashAfterCreate(FakeImageProvider):
            def create_prediction(self, request):
                super().create_prediction(request)
                raise RuntimeError("worker killed after provider accepted the request")

        design = make_complete_design()
        attempt = _queued_attempt(design)
        provider = _CrashAfterCreate()
        result = _run(attempt, image=provider)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.INTERNAL_GENERATION_ERROR
        assert result.image_submission_in_flight is True
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design, idempotency_key=uuid_module.uuid4(), enqueue_task=lambda a: None
                )
        assert provider.create_calls == 1

    def test_download_failure_after_successful_prediction_blocks(self):
        # The prediction CONFIRMED succeeded (billed) but its output was never
        # obtained — possibly still valid and recoverable. Readmitting would
        # re-bill with certainty, so the evidence-bearing row blocks.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import DesignAlreadyGenerated, enqueue_design_generation

        def failing_downloader(_url):
            raise ConnectionError("download outage")

        design = make_complete_design()
        attempt = _queued_attempt(design)
        provider = FakeImageProvider()
        result = _run(attempt, image=provider, downloader=failing_downloader)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_DOWNLOAD_FAILED
        assert result.image_prediction_id
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design, idempotency_key=uuid_module.uuid4(), enqueue_task=lambda a: None
                )
        assert provider.create_calls == 1

    def test_poll_timeout_with_live_prediction_blocks_regeneration(self):
        # Our own poll DEADLINE is not a provider-confirmed outcome: the
        # cancellation is best-effort and never confirmed, and the prediction
        # may still complete (and bill) after the deadline — so an
        # evidence-bearing poll-timeout row must block a fresh key.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import DesignAlreadyGenerated, enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        provider = FakeImageProvider(poll_actions=[PREDICTION_PROCESSING])
        result = _run(attempt, image=provider)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_POLL_TIMEOUT
        assert result.image_prediction_id  # the live, billed prediction
        assert provider.cancel_calls == 1  # best-effort cancel WAS attempted
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design, idempotency_key=uuid_module.uuid4(), enqueue_task=lambda a: None
                )
        assert provider.create_calls == 1

    @pytest.mark.parametrize(
        ("poll_action", "expected_code"),
        [
            (PREDICTION_CANCELED, errors.IMAGE_PREDICTION_CANCELED),
            (PREDICTION_ABORTED, errors.IMAGE_PREDICTION_ABORTED),
        ],
    )
    def test_provider_reported_terminal_states_still_readmit(self, poll_action, expected_code):
        # BOUNDARY: canceled/aborted are provider-REPORTED terminal outcomes
        # polled from the provider itself — spend resolved, recovery path open
        # despite the persisted (evidence-bearing) prediction id.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, image=FakeImageProvider(poll_actions=[poll_action]))
        assert result.status == _Status.FAILED
        assert result.error_code == expected_code
        assert result.image_prediction_id
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            new_attempt, created = enqueue_design_generation(
                design, idempotency_key=uuid_module.uuid4(), enqueue_task=lambda a: None
            )
        assert created is True
        assert new_attempt.design_version_id == result.design_version_id

    def test_invalid_output_still_readmits(self):
        # BOUNDARY: output was OBTAINED and CONFIRMED unusable — like
        # image_staging_failed, regeneration is the only possible remedy, so
        # the resolved-outcome allowlist keeps the recovery path open.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, downloader=invalid_bytes_downloader)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_OUTPUT_INVALID
        assert result.image_prediction_id  # evidence alone must not block
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            new_attempt, created = enqueue_design_generation(
                design, idempotency_key=uuid_module.uuid4(), enqueue_task=lambda a: None
            )
        assert created is True
        assert new_attempt.design_version_id == result.design_version_id

    def test_unverified_exhaustion_without_submission_evidence_readmits(self):
        # BOUNDARY: no staged key, no prediction id, no in-flight marker —
        # the marker is written BEFORE the create call, so the provider was
        # provably never invoked and nothing paid exists. A fresh key may
        # safely retry rather than stranding the design.
        import uuid as uuid_module
        from unittest import mock

        from django.utils import timezone as tz

        from sitara.generation.pipeline import enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            error_code=errors.IMAGE_STAGING_UNVERIFIED,
            completed_at=tz.now(),
            image_prediction_id="",
            image_submission_in_flight=False,
        )
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            new_attempt, created = enqueue_design_generation(
                design,
                idempotency_key=uuid_module.uuid4(),
                enqueue_task=lambda a: None,
            )
        assert created is True
        assert new_attempt.design_version_id == attempt.design_version_id

    def test_metadata_with_missing_object_fails_safely(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        retry = GenerationAttempt.objects.create(
            design=design,
            design_version_id=attempt.design_version_id,
            status=_Status.RUNNING_IMAGE,
            staged_image_storage_key="generation-staging/ghost/raw.webp",
            staged_image_sha256="a" * 64,
            staged_image_size_bytes=100,
            staged_image_width=768,
            staged_image_height=1024,
        )
        # Storage does NOT contain the object the metadata claims.
        result = _run(retry, storage=InMemoryStorage())
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_STAGING_FAILED

    def test_metadata_with_corrupt_object_fails_safely(self):
        from sitara.generation.image_fixtures import make_synthetic_webp

        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        key = "generation-staging/tampered/raw.webp"
        retry = GenerationAttempt.objects.create(
            design=design,
            design_version_id=attempt.design_version_id,
            status=_Status.RUNNING_IMAGE,
            staged_image_storage_key=key,
            staged_image_sha256="a" * 64,  # will not match the object below
            staged_image_size_bytes=100,
            staged_image_width=768,
            staged_image_height=1024,
        )
        storage = InMemoryStorage()
        storage._objects[key] = make_synthetic_webp()
        result = _run(retry, storage=storage)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_STAGING_FAILED

    def test_recovered_object_with_format_key_mismatch_fails_safely(self):
        # A REAL image whose sniffed format contradicts its deterministic
        # key's extension is conflicting content — CONFIRMED bad staging, so
        # the attempt fails with the confirmed code and never finalises on it.
        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        retry = GenerationAttempt.objects.create(
            design=design, design_version_id=attempt.design_version_id, status=_Status.QUEUED
        )
        storage = InMemoryStorage()
        # Valid PNG bytes sitting under the .webp key.
        storage._objects[f"generation-staging/{retry.id}/raw.webp"] = make_synthetic_png()
        result = _run(retry, storage=storage)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_STAGING_FAILED

    def test_recovery_probes_past_a_missing_webp_key(self):
        # The probe iterates the FULL extension order: output staged as PNG
        # (no .webp object) is still found and recovered.
        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        retry = GenerationAttempt.objects.create(
            design=design, design_version_id=attempt.design_version_id, status=_Status.QUEUED
        )
        storage = InMemoryStorage()
        key = f"generation-staging/{retry.id}/raw.png"
        storage._objects[key] = make_synthetic_png()
        provider = FakeImageProvider()
        result = _run(retry, image=provider, storage=storage)
        assert result.status == _Status.SUCCEEDED
        assert result.staged_image_storage_key == key
        assert provider.create_calls == 0


class TestPromptReuseAsIs:
    def test_resumed_version_uses_the_stored_prompt_verbatim(self):
        # CDX-005: the persisted prompt is immutable audit data and is reused
        # AS-IS on resume — never rebuilt, never an immutability conflict.
        design = make_complete_design()
        attempt = _queued_attempt(design)
        _run(attempt, image=FakeImageProvider(poll_actions=[PREDICTION_FAILED]))
        attempt.refresh_from_db()
        # Simulate a version whose prompt was stored by an OLDER builder.
        DesignVersion.objects.filter(pk=attempt.design_version_id).update(
            image_prompt="A historical prompt from an older builder.",
            prompt_builder_version="2.0.0",
        )
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        retry = GenerationAttempt.objects.create(
            design=design, design_version_id=attempt.design_version_id, status=_Status.QUEUED
        )
        healthy = FakeImageProvider()
        result = _run(retry, image=healthy)
        assert result.status == _Status.SUCCEEDED
        # The provider received the EXACT stored prompt (spec §20), and the
        # audit fields were not rewritten.
        assert healthy.last_request.prompt == "A historical prompt from an older builder."
        version = DesignVersion.objects.get(pk=attempt.design_version_id)
        assert version.prompt_builder_version == "2.0.0"


class TestUnknownProviderStatus:
    def test_unknown_terminal_status_fails_closed_and_blocks_regeneration(self):
        # CDX-006: only an explicit `succeeded` may proceed to download; an
        # unknown/novel provider state is untrusted and fails closed. It also
        # proves NOTHING about the accepted (billed) prediction's fate, so it
        # lands on internal_generation_error — an UNRESOLVED code — and the
        # evidence-bearing row (persisted prediction id) blocks a fresh key.
        import uuid as uuid_module
        from unittest import mock

        from sitara.generation.pipeline import DesignAlreadyGenerated, enqueue_design_generation

        design = make_complete_design()
        attempt = _queued_attempt(design)
        downloads = []

        def counting_downloader(url):
            downloads.append(url)
            return b"never used"

        provider = FakeImageProvider(poll_actions=["blocked"])
        result = _run(attempt, image=provider, downloader=counting_downloader)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.INTERNAL_GENERATION_ERROR
        assert downloads == []  # nothing downloaded, nothing staged
        assert result.staged_image_storage_key == ""
        assert result.image_prediction_id  # the billed submission
        design.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.generation_is_available", return_value=True):
            with pytest.raises(DesignAlreadyGenerated):
                enqueue_design_generation(
                    design, idempotency_key=uuid_module.uuid4(), enqueue_task=lambda a: None
                )
        assert provider.create_calls == 1


class TestPixelCap:
    def test_oversized_pixel_count_is_rejected_and_never_staged(self):
        # Decompression-bomb guard: a valid image whose pixel count exceeds
        # the configured cap is rejected as image_output_invalid, and nothing
        # reaches storage. The synthetic image is 768x1024 (~786k pixels).
        design = make_complete_design()
        attempt = _queued_attempt(design)
        storage = InMemoryStorage()
        result = _run(
            attempt,
            storage=storage,
            config=PipelineConfig(poll_max_attempts=10, raw_max_pixels=1000),
        )
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_OUTPUT_INVALID
        assert result.staged_image_storage_key == ""
        assert storage._objects == {}


class TestStageAErrorMappings:
    def test_classified_provider_error_maps_to_structured_generation_failed(self):
        # A classified Anthropic transport/API failure is a KNOWN structured
        # generation failure — it must persist structured_generation_failed,
        # never the unclassified internal_generation_error.
        from sitara.ai_gateway.structured_design import StructuredDesignProviderError

        class _FailingStructured:
            def generate(self, request):
                raise StructuredDesignProviderError("api_error")

        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, structured=_FailingStructured())
        assert result.status == _Status.FAILED
        assert result.error_code == errors.STRUCTURED_GENERATION_FAILED

    def test_design_changed_between_enqueue_and_execution(self):
        # Stage A re-checks domain readiness: a design whose answers became
        # incomplete after enqueue fails terminally with design_changed.
        design = make_complete_design()
        attempt = _queued_attempt(design)
        # Simulate post-enqueue corruption (QuerySet.update bypasses the
        # service guards deliberately, as permitted for tests).
        Design.objects.filter(pk=design.pk).update(answers={})
        result = _run(attempt)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.DESIGN_CHANGED

    def test_structured_provider_refusal_maps_to_stable_code(self):
        from . import fakes

        design = make_complete_design()
        attempt = _queued_attempt(design)
        refusing = fakes.SequenceProvider([fakes.refusal_result()])
        result = _run(attempt, structured=refusing)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.STRUCTURED_PROVIDER_REFUSED
        assert refusing.calls == 1


class TestDuplicateDelivery:
    def test_missing_attempt_is_a_safe_noop(self):
        assert run_generation_attempt(uuid.uuid4()) is None

    def test_malformed_attempt_id_is_a_logged_noop(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="sitara.generation.pipeline"):
            assert run_generation_attempt("not-a-uuid") is None
        assert any("not a valid UUID" in record.message for record in caplog.records)
        # The raw value is never echoed.
        assert "not-a-uuid" not in caplog.text


class _CrashAfterCreateProvider(FakeImageProvider):
    """create_prediction succeeds at the provider, but the process 'crashes'
    (raises) immediately after — before the caller persists the prediction id.
    Simulates the worst-case create-then-crash window."""

    def create_prediction(self, request):
        super().create_prediction(request)
        raise RuntimeError("worker killed after provider accepted the request")


class TestSubmissionCrashWindow:
    def test_crash_after_create_before_persist_never_resubmits(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        crashing = _CrashAfterCreateProvider()
        # First run: the provider accepted a prediction, then the run crashed
        # before persisting the id -> terminal internal error, marker left set.
        result = _run(attempt, image=crashing)
        assert result.status == _Status.FAILED
        attempt.refresh_from_db()
        assert attempt.image_submission_in_flight is True
        assert attempt.image_prediction_id == ""
        assert crashing.create_calls == 1
        # A resuming attempt against the same version must NOT resubmit: it sees
        # the in-flight marker with no id and fails as ambiguous.
        design.refresh_from_db()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        # Re-run the SAME attempt id (redelivery) with a healthy provider.
        healthy = FakeImageProvider()
        # Reset the terminal state to running_image to simulate redelivery
        # resuming an interrupted (non-terminal) attempt.
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=_Status.RUNNING_IMAGE, error_code="", completed_at=None
        )
        again = _run(attempt, image=healthy)
        assert again.status == _Status.FAILED
        assert again.error_code == errors.IMAGE_SUBMISSION_AMBIGUOUS
        assert healthy.create_calls == 0  # never resubmitted

    def test_empty_prediction_id_from_provider_is_ambiguous(self):
        # PIPELINE-layer defence in depth (distinct from the Replicate
        # adapter's own guard): an accepted create returning an empty id can
        # never be polled or reconciled — resolve conservatively as ambiguous
        # with the in-flight marker left set (spend-safe; never resubmitted).
        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, image=FakeImageProvider(prediction_id=""))
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_SUBMISSION_AMBIGUOUS
        attempt.refresh_from_db()
        assert attempt.image_submission_in_flight is True
        assert attempt.image_prediction_id == ""

    def test_overlong_prediction_id_from_provider_is_ambiguous(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = _run(attempt, image=FakeImageProvider(prediction_id="x" * 129))
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_SUBMISSION_AMBIGUOUS
        attempt.refresh_from_db()
        assert attempt.image_submission_in_flight is True

    def test_pre_acceptance_transient_clears_marker_and_retries(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        # create fails as a definitely-pre-acceptance transient (not ambiguous).
        image = FakeImageProvider(
            create_error=ImageProviderError("connection", ambiguous_acceptance=False)
        )
        from sitara.generation.pipeline import GenerationRetry

        with pytest.raises(GenerationRetry):
            _run(attempt, image=image)
        attempt.refresh_from_db()
        # Nothing was accepted: the marker is cleared so a retry may resubmit.
        assert attempt.image_submission_in_flight is False
        assert attempt.image_prediction_id == ""
        assert attempt.image_seed == 0  # seed persisted once, reused on retry
        # A healthy retry resubmits with the SAME persisted seed.
        healthy = FakeImageProvider()
        result = _run(attempt, image=healthy)
        assert result.status == _Status.SUCCEEDED
        assert healthy.create_calls == 1
        assert healthy.last_request.seed == 0


class TestSoftTimeLimit:
    def test_soft_time_limit_is_retryable_not_terminal(self):
        from celery.exceptions import SoftTimeLimitExceeded

        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)

        class _TimeoutOnPoll(FakeImageProvider):
            def get_prediction(self, prediction_id):
                raise SoftTimeLimitExceeded()

        image = _TimeoutOnPoll()
        # A soft-time-limit mid-poll must NOT mark the attempt terminally failed;
        # it becomes a bounded retry so a redelivery resumes the same prediction.
        with pytest.raises(GenerationRetry):
            _run(attempt, image=image)
        attempt.refresh_from_db()
        assert attempt.status == _Status.RUNNING_IMAGE
        assert attempt.image_prediction_id  # prediction already submitted/persisted

    def test_soft_time_limit_mid_create_is_retryable_and_keeps_the_marker(self):
        # An interruption while predictions.create is in flight must NOT be
        # classified as a safe pre-acceptance failure: the attempt stays
        # in-progress with image_submission_in_flight SET, so the redelivery
        # resolves conservatively (ambiguous — never a resubmission).
        from celery.exceptions import SoftTimeLimitExceeded

        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)

        class _TimeoutOnCreate(FakeImageProvider):
            def create_prediction(self, request):
                raise SoftTimeLimitExceeded()

        with pytest.raises(GenerationRetry):
            _run(attempt, image=_TimeoutOnCreate())
        attempt.refresh_from_db()
        assert attempt.status == _Status.RUNNING_IMAGE  # not terminally failed
        assert attempt.image_submission_in_flight is True  # marker preserved
        assert attempt.image_prediction_id == ""
        # Redelivery with a healthy provider must NOT resubmit: marker + no id
        # resolves as ambiguous (conservative spend).
        healthy = FakeImageProvider()
        result = _run(attempt, image=healthy)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_SUBMISSION_AMBIGUOUS
        assert healthy.create_calls == 0

    def test_soft_time_limit_mid_download_is_retryable_not_terminal(self):
        from celery.exceptions import SoftTimeLimitExceeded

        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)

        def interrupted_downloader(_url):
            raise SoftTimeLimitExceeded()

        image = FakeImageProvider()
        with pytest.raises(GenerationRetry):
            _run(attempt, image=image, downloader=interrupted_downloader)
        attempt.refresh_from_db()
        # Not image_download_failed: the attempt stays resumable with its
        # persisted prediction id.
        assert attempt.status == _Status.RUNNING_IMAGE
        assert attempt.image_prediction_id
        # Redelivery with a working downloader completes WITHOUT another
        # prediction submission — the same output is re-downloaded.
        result = _run(attempt, image=image)
        assert result.status == _Status.SUCCEEDED
        assert image.create_calls == 1

    def test_soft_time_limit_mid_staging_is_retryable_not_terminal(self):
        from celery.exceptions import SoftTimeLimitExceeded

        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)

        class _InterruptedStorage(InMemoryStorage):
            def save(self, key, content):
                raise SoftTimeLimitExceeded()

        with pytest.raises(GenerationRetry):
            _run(attempt, storage=_InterruptedStorage())
        attempt.refresh_from_db()
        assert attempt.status == _Status.RUNNING_IMAGE  # not image_staging_failed
        # Redelivery with working storage completes from the persisted markers.
        result = _run(attempt)
        assert result.status == _Status.SUCCEEDED

    def test_soft_time_limit_during_staging_resume_read_is_retryable(self):
        # The existing-object verify-on-resume branch (store.open) must also
        # treat a worker interruption as retryable, never image_staging_failed.
        from celery.exceptions import SoftTimeLimitExceeded

        from sitara.generation.image_fixtures import make_synthetic_webp
        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)

        class _InterruptedOpenStorage(InMemoryStorage):
            def open(self, key, mode="rb"):
                raise SoftTimeLimitExceeded()

        storage = _InterruptedOpenStorage()
        key = f"generation-staging/{attempt.id}/raw.webp"
        storage._objects[key] = make_synthetic_webp()
        with pytest.raises(GenerationRetry):
            _run(attempt, storage=storage)
        attempt.refresh_from_db()
        assert attempt.status == _Status.RUNNING_IMAGE

    def test_soft_time_limit_during_timeout_cancellation_is_retryable(self):
        # An interruption during the best-effort cancel call (poll-timeout
        # branch) propagates as a bounded retry — redelivery polls the same
        # prediction again — instead of being silently absorbed.
        from celery.exceptions import SoftTimeLimitExceeded

        from sitara.generation.pipeline import GenerationRetry

        design = make_complete_design()
        attempt = _queued_attempt(design)

        class _InterruptedCancel(FakeImageProvider):
            def cancel_prediction(self, prediction_id):
                raise SoftTimeLimitExceeded()

        image = _InterruptedCancel(poll_actions=[PREDICTION_PROCESSING])
        with pytest.raises(GenerationRetry):
            _run(attempt, image=image, config=PipelineConfig(poll_max_attempts=2))
        attempt.refresh_from_db()
        assert attempt.status == _Status.RUNNING_IMAGE  # not image_poll_timeout
        assert attempt.image_prediction_id


class TestStatusTransitions:
    def test_pipeline_passes_through_running_text_then_running_image(self):
        design = make_complete_design()
        attempt = _queued_attempt(design)
        seen = []

        class _StatusSpyStructured(FixtureStructuredDesignProvider):
            def generate(self, request):
                seen.append(("text", _current_status(attempt.id)))
                return super().generate(request)

        class _StatusSpyImage(FakeImageProvider):
            def create_prediction(self, request):
                seen.append(("image", _current_status(attempt.id)))
                return super().create_prediction(request)

        result = _run(attempt, structured=_StatusSpyStructured(), image=_StatusSpyImage())
        assert result.status == _Status.SUCCEEDED
        assert ("text", _Status.RUNNING_TEXT) in seen
        assert ("image", _Status.RUNNING_IMAGE) in seen


class TestCrossDesignGuard:
    def test_attempt_from_a_different_design_is_never_linked(self):
        from sitara.generation.services import (
            DesignChangedDuringGeneration,
            generate_design_spec_for_design,
        )

        from .factory import make_active_v1

        questionnaire = make_active_v1()
        design = make_complete_design(questionnaire=questionnaire)
        other = make_complete_design(questionnaire=questionnaire)
        foreign_attempt = GenerationAttempt.objects.create(
            design=other, status=_Status.RUNNING_TEXT
        )
        with pytest.raises(DesignChangedDuringGeneration):
            generate_design_spec_for_design(
                design, provider=FixtureStructuredDesignProvider(), attempt=foreign_attempt
            )
        # Nothing persisted for either design.
        assert DesignVersion.objects.filter(design=design).count() == 0
        foreign_attempt.refresh_from_db()
        assert foreign_attempt.design_version_id is None


def _current_status(attempt_id):
    return GenerationAttempt.objects.values_list("status", flat=True).get(pk=attempt_id)


class TestLiveFactories:
    """The live provider/downloader factories are fail-closed and wire config
    correctly. These are the only path to the real gated provider in production,
    so their own bodies must be exercised (tests otherwise inject fakes)."""

    def test_live_image_provider_with_gate_closed_is_terminal(self, settings):
        settings.DEMO_MODE = True  # paid-image gate closed
        from sitara.generation.pipeline import _live_image_provider, _TerminalGenerationError

        with pytest.raises(_TerminalGenerationError) as exc:
            _live_image_provider(PipelineConfig())
        assert exc.value.code == errors.IMAGE_PROVIDER_UNAVAILABLE

    def test_run_attempt_without_injected_provider_fails_closed(self, settings):
        # Reaching the image stage with no injected provider and a closed gate
        # ends the attempt with a controlled image_provider_unavailable.
        settings.DEMO_MODE = True
        design = make_complete_design()
        attempt = _queued_attempt(design)
        result = run_generation_attempt(
            attempt.id,
            structured_provider=FixtureStructuredDesignProvider(),
            image_provider=None,  # resolve the live factory (fails closed)
            image_downloader=synthetic_webp_downloader,
            storage=InMemoryStorage(),
            seed_factory=lambda: 0,
            config=_FAST,
        )
        assert result.status == _Status.FAILED
        assert result.error_code == errors.IMAGE_PROVIDER_UNAVAILABLE

    def test_live_image_downloader_wires_config_and_settings(self, settings, monkeypatch):
        settings.REPLICATE_TIMEOUT_SECONDS = 30
        from sitara.generation import pipeline as pipeline_module

        recorded = {}

        def _fake_download(url, *, max_bytes, timeout_seconds):
            recorded["args"] = (url, max_bytes, timeout_seconds)
            return b"bytes"

        monkeypatch.setattr(
            "sitara.generation.image_download.download_replicate_output", _fake_download
        )
        downloader = pipeline_module._live_image_downloader(PipelineConfig(raw_max_bytes=999))
        assert downloader("https://replicate.delivery/x/raw.webp") == b"bytes"
        assert recorded["args"] == ("https://replicate.delivery/x/raw.webp", 999, 30)


def test_lock_keys_consume_both_uuid_slices_independently():
    # Two attempt ids sharing their FIRST four bytes must still derive
    # different lock keys (the second slice differs) — the exact collision
    # this change eliminates. A regression slicing bytes[:4] twice would
    # make these tuples equal.
    import uuid as uuid_module

    shared_prefix = bytes.fromhex("00112233")
    a = uuid_module.UUID(bytes=shared_prefix + bytes.fromhex("44556677") + bytes(8))
    b = uuid_module.UUID(bytes=shared_prefix + bytes.fromhex("8899aabb") + bytes(8))
    keys_a = _attempt_lock_keys(a)
    keys_b = _attempt_lock_keys(b)
    assert keys_a[0] == keys_b[0]  # shared first slice
    assert keys_a[1] != keys_b[1]  # second slice genuinely consulted
    assert keys_a != keys_b


@pytest.mark.django_db(transaction=True)
def test_duplicate_delivery_is_serialised_by_the_advisory_lock():
    """A second (duplicate) delivery whose advisory lock is already held by
    another session performs NO work and returns None."""
    design = make_complete_design()
    attempt = _queued_attempt(design)
    key_high, key_low = _attempt_lock_keys(attempt.id)

    holding = threading.Event()
    release = threading.Event()

    def holder():
        from django.db import connection as thread_connection

        try:
            with thread_connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s, %s)", [key_high, key_low])
                holding.set()
                release.wait(timeout=10)
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [key_high, key_low])
        finally:
            thread_connection.close()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert holding.wait(timeout=10)
        structured = FixtureStructuredDesignProvider()
        image = FakeImageProvider()
        result = run_generation_attempt(
            attempt.id,
            structured_provider=structured,
            image_provider=image,
            image_downloader=synthetic_webp_downloader,
            storage=InMemoryStorage(),
            seed_factory=lambda: 0,
            config=_FAST,
        )
        assert result is None  # duplicate delivery exits without work
        assert structured._calls == 0
        assert image.create_calls == 0
        assert DesignVersion.objects.filter(design=design).count() == 0
        attempt.refresh_from_db()
        assert attempt.status == _Status.QUEUED
    finally:
        release.set()
        thread.join(timeout=10)
