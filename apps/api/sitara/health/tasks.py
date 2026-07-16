"""Harmless Celery smoke-test task.

Manual local test:

    docker compose exec api python -c \
        "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

There is deliberately NO endpoint that enqueues tasks."""

from celery import shared_task


@shared_task
def ping() -> dict[str, str | bool]:
    return {"pong": True, "service": "sitara-api"}
