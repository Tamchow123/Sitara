"""Celery task registration, routing and settings (Phase 10)."""

import math
from unittest import mock

from django.conf import settings

from config.celery import app
from sitara.generation import tasks
from sitara.generation.image_download import MAX_REDIRECTS
from sitara.generation.pipeline import build_pipeline_config, pipeline_budget_seconds

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


class TestTaskTimeLimits:
    def test_soft_limit_exceeds_the_configured_pipeline_budget(self):
        # A legitimately slow render must never be interrupted mid-flight: the
        # task's soft limit must exceed the worst-case configured stage budget.
        assert tasks.SOFT_TIME_LIMIT_SECONDS > pipeline_budget_seconds()
        assert tasks.HARD_TIME_LIMIT_SECONDS > tasks.SOFT_TIME_LIMIT_SECONDS

    def test_budget_covers_text_image_and_download(self):
        expected_download = settings.REPLICATE_TIMEOUT_SECONDS * (MAX_REDIRECTS + 1)
        expected = (
            2 * settings.ANTHROPIC_TIMEOUT_SECONDS
            # image: submit + one trailing in-flight poll call + poll wall-clock
            + 2 * settings.REPLICATE_TIMEOUT_SECONDS
            + settings.REPLICATE_POLL_TIMEOUT_SECONDS
            + expected_download
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
