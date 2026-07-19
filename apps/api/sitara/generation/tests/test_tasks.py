"""Celery task registration, routing, settings and retry exhaustion (Phase 10)."""

import math
from unittest import mock

import pytest
from django.conf import settings

from config.celery import app
from sitara.designs.models import Design, GenerationAttempt
from sitara.generation import errors, tasks
from sitara.generation.image_download import MAX_REDIRECTS
from sitara.generation.pipeline import (
    GenerationRetry,
    build_pipeline_config,
    pipeline_budget_seconds,
)

from .factory import make_complete_design

_TASK_NAME = "sitara.generation.tasks.generate_design_attempt"


class TestTaskRegistration:
    def test_task_is_registered(self):
        assert _TASK_NAME in app.tasks

    def test_task_is_routed_to_the_generation_queue(self):
        assert settings.CELERY_TASK_ROUTES[_TASK_NAME] == {"queue": "generation"}

    def test_task_uses_durable_delivery_settings(self):
        registered = app.tasks[_TASK_NAME]
        assert registered.acks_late is True
        assert registered.reject_on_worker_lost is True
        assert registered.soft_time_limit == tasks.SOFT_TIME_LIMIT_SECONDS
        assert registered.time_limit == tasks.HARD_TIME_LIMIT_SECONDS
        # No blanket autoretry over the whole pipeline.
        assert not getattr(registered, "autoretry_for", ())

    def test_task_delegates_to_the_state_machine(self):
        with mock.patch.object(tasks, "run_generation_attempt") as run:
            tasks.generate_design_attempt.apply(args=["00000000-0000-0000-0000-000000000000"])
        run.assert_called_once()
        # The attempt id is passed positionally; a settings-derived config kwarg
        # is also supplied.
        assert run.call_args.args[0] == "00000000-0000-0000-0000-000000000000"
        assert "config" in run.call_args.kwargs


@pytest.mark.django_db
class TestTransientRetryExhaustion:
    def test_exhausted_retries_persist_the_classified_code(self):
        # The task boundary converts exhausted GenerationRetry redeliveries
        # into a terminal row carrying the SAME classified code — the wiring
        # the fail-closed enqueue guard depends on (image_staging_unverified
        # must never degrade to a different code on exhaustion).
        design = make_complete_design()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        attempt = GenerationAttempt.objects.create(
            design=design, status=GenerationAttempt.Status.RUNNING_IMAGE
        )
        retryable = GenerationRetry(errors.IMAGE_STAGING_UNVERIFIED)
        with mock.patch.object(tasks, "run_generation_attempt", side_effect=retryable):
            tasks.generate_design_attempt.push_request(retries=tasks.MAX_TRANSIENT_RETRIES)
            try:
                tasks.generate_design_attempt.run(str(attempt.id))
            finally:
                tasks.generate_design_attempt.pop_request()
        attempt.refresh_from_db()
        assert attempt.status == GenerationAttempt.Status.FAILED
        assert attempt.error_code == errors.IMAGE_STAGING_UNVERIFIED
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATION_FAILED

    def test_unexhausted_transient_requests_a_bounded_retry(self):
        # Below the bound the task re-raises via self.retry (a Retry signal in
        # eager/apply mode) and must NOT touch the attempt row.
        from celery.exceptions import Retry

        design = make_complete_design()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        attempt = GenerationAttempt.objects.create(
            design=design, status=GenerationAttempt.Status.RUNNING_IMAGE
        )
        retryable = GenerationRetry(errors.IMAGE_STAGING_UNVERIFIED)
        with (
            mock.patch.object(tasks, "run_generation_attempt", side_effect=retryable),
            mock.patch.object(tasks, "fail_attempt") as fail_spy,
        ):
            # called_directly=False + is_eager=True make self.retry raise the
            # Retry signal without attempting a real broker publish.
            tasks.generate_design_attempt.push_request(
                retries=0,
                id=str(attempt.id),
                called_directly=False,
                is_eager=True,
                args=[str(attempt.id)],
                kwargs={},
            )
            try:
                with pytest.raises(Retry):
                    tasks.generate_design_attempt.run(str(attempt.id))
            finally:
                tasks.generate_design_attempt.pop_request()
        # The exhaustion path was never entered — isolated from Celery's
        # eager-retry replay semantics, not only inferred from row state.
        fail_spy.assert_not_called()
        attempt.refresh_from_db()
        assert attempt.status == GenerationAttempt.Status.RUNNING_IMAGE
        assert attempt.error_code == ""


class TestTaskTimeLimits:
    def test_soft_limit_exceeds_the_configured_pipeline_budget(self):
        # A legitimately slow render must never be interrupted mid-flight: the
        # task's soft limit must exceed the worst-case configured stage budget.
        assert tasks.SOFT_TIME_LIMIT_SECONDS > pipeline_budget_seconds()
        assert tasks.HARD_TIME_LIMIT_SECONDS > tasks.SOFT_TIME_LIMIT_SECONDS

    def test_budget_covers_text_image_download_and_ingest(self):
        from sitara.generation.pipeline import INGEST_STAGE_BUDGET_SECONDS

        expected_download = settings.REPLICATE_TIMEOUT_SECONDS * (MAX_REDIRECTS + 1)
        expected = (
            2 * settings.ANTHROPIC_TIMEOUT_SECONDS
            # image: submit + one trailing in-flight poll call + poll wall-clock
            + 2 * settings.REPLICATE_TIMEOUT_SECONDS
            + settings.REPLICATE_POLL_TIMEOUT_SECONDS
            + expected_download
            # Phase 11 stage E: canonical ingest processing + storage I/O.
            + INGEST_STAGE_BUDGET_SECONDS
        )
        assert pipeline_budget_seconds() == expected

    def test_soft_limit_covers_the_true_worst_case_poll_wall_clock(self):
        # With a wall-clock poll deadline, total polling is bounded by the poll
        # timeout plus one trailing in-flight status call — the budget covers it.
        true_poll_bound = (
            settings.REPLICATE_POLL_TIMEOUT_SECONDS + settings.REPLICATE_TIMEOUT_SECONDS
        )
        assert pipeline_budget_seconds() >= true_poll_bound


class TestBuildPipelineConfig:
    def test_translates_settings_into_config(self, settings):
        settings.DEFAULT_IMAGE_MODEL = "black-forest-labs/flux-1.1-pro"
        settings.REPLICATE_POLL_INTERVAL_SECONDS = 2
        settings.REPLICATE_POLL_TIMEOUT_SECONDS = 180
        settings.GENERATION_RAW_MAX_BYTES = 12345
        settings.GENERATION_RAW_MAX_PIXELS = 67890
        config = build_pipeline_config()
        assert config.model == "black-forest-labs/flux-1.1-pro"
        assert config.poll_interval_seconds == 2.0
        assert config.poll_max_attempts == math.ceil(180 / 2)
        assert config.poll_timeout_seconds == 180.0
        assert config.raw_max_bytes == 12345
        assert config.raw_max_pixels == 67890
        # The reviewed Phase 2 profile stays on the defaults.
        assert config.aspect_ratio == "3:4"
        assert config.output_format == "webp"

    def test_poll_bound_keeps_total_sleep_below_the_timeout(self, settings):
        # poll_max_attempts is derived so (attempts - 1) * interval < timeout,
        # even when the timeout is not an exact multiple of the interval.
        settings.REPLICATE_POLL_INTERVAL_SECONDS = 7
        settings.REPLICATE_POLL_TIMEOUT_SECONDS = 180
        config = build_pipeline_config()
        max_total_sleep = (config.poll_max_attempts - 1) * config.poll_interval_seconds
        assert max_total_sleep < settings.REPLICATE_POLL_TIMEOUT_SECONDS
