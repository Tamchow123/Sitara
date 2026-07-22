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

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("sitara")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


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
