"""Celery task registration, routing and settings (Phase 10)."""

from unittest import mock

from django.conf import settings

from config.celery import app
from sitara.generation import tasks

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
        run.assert_called_once_with("00000000-0000-0000-0000-000000000000")
