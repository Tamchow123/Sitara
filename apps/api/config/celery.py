"""Celery application — Redis broker and result backend.

Local manual test (documented in the README):

    docker compose exec api python -c \
        "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

Celery Beat (Phase 16, Part C) schedules two bounded, idempotent maintenance
tasks — the stuck-generation reconciler and the expired-design retention purge —
using a STATIC settings-based schedule (no django-celery-beat / database-managed
schedules; static settings are sufficient). No endpoint may enqueue arbitrary
tasks.
"""

import os

from celery import Celery
from celery.schedules import schedule
from celery.signals import task_postrun, task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("sitara")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

_GENERATION_TASK = "sitara.generation.tasks.generate_design_attempt"


@task_prerun.connect
def _bind_correlation(task_id=None, task=None, **_kwargs):
    """Bind the task's correlation context at entry (Phase 16, Part E): the task
    id is the request id, and — for the generation task, whose task id IS the
    attempt UUID — also the attempt id. Cleared in task_postrun's finally-like
    handler so it never leaks between tasks on a reused worker."""
    from config.correlation import set_attempt_id, set_request_id

    set_request_id(str(task_id) if task_id else None)
    name = getattr(task, "name", None)
    set_attempt_id(str(task_id) if (task_id and name == _GENERATION_TASK) else None)


@task_postrun.connect
def _clear_correlation(**_kwargs):
    from config.correlation import clear

    clear()


@app.on_after_configure.connect
def _register_beat_schedule(sender, **_kwargs):
    """Register the periodic maintenance schedule from Django settings once the
    app is configured, so the intervals are operator-tunable and Beat and the
    worker agree on task names/routing."""
    from django.conf import settings

    sender.add_periodic_task(
        schedule(run_every=settings.GENERATION_STUCK_INTERVAL_SECONDS),
        sender.signature("sitara.generation.tasks.reconcile_stuck_generations", queue="generation"),
        name="reconcile-stuck-generations",
    )
    sender.add_periodic_task(
        schedule(run_every=settings.DESIGN_PURGE_INTERVAL_SECONDS),
        sender.signature("sitara.generation.tasks.purge_expired_designs", queue="generation"),
        name="purge-expired-designs",
    )
