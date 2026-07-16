"""Celery application — Redis broker and result backend.

Local manual test (documented in the README):

    docker compose exec api python -c \
        "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

No Celery Beat in Phase 3A. No endpoint may enqueue arbitrary tasks.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("sitara")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
