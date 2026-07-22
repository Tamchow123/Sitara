"""The Celery generation task (Phase 10).

One durable task drives one :class:`GenerationAttempt` through the resumable
:func:`run_generation_attempt` state machine. Task settings:

- ``acks_late`` + ``reject_on_worker_lost``: a lost worker redelivers the task,
  and the state machine resumes safely from persisted markers (a linked
  DesignVersion, an image prompt, a prediction id, or a staged object).
- bounded soft/hard time limits — never an unbounded run.
- NO ``autoretry_for`` over the whole task and NO infinite retry: only an
  explicitly classified transient failure (:class:`GenerationRetry`) triggers a
  bounded retry, which reuses the same prediction and never repeats the text
  stage. When retries are exhausted the attempt is marked terminally failed.

The task holds no Django transaction or row lock while a provider call is in
flight; that discipline lives in :mod:`sitara.generation.pipeline`.
"""

from celery import shared_task
from celery.utils.log import get_task_logger

from .pipeline import (
    GenerationRetry,
    build_pipeline_config,
    fail_attempt,
    pipeline_budget_seconds,
    run_generation_attempt,
)

logger = get_task_logger(__name__)

# Bounded transient retry policy (safe, classified transients only).
MAX_TRANSIENT_RETRIES = 5
RETRY_COUNTDOWN_SECONDS = 5

# Bounded execution time — a generation must never run unbounded, but the limit
# must exceed the pipeline's configured stage budget so a slow-but-succeeding
# render is not killed. The soft margin absorbs scheduling/DB overhead; the
# hard limit adds a further grace period before a SIGKILL.
_SOFT_MARGIN_SECONDS = 60
_HARD_MARGIN_SECONDS = 60
SOFT_TIME_LIMIT_SECONDS = pipeline_budget_seconds() + _SOFT_MARGIN_SECONDS
HARD_TIME_LIMIT_SECONDS = SOFT_TIME_LIMIT_SECONDS + _HARD_MARGIN_SECONDS


@shared_task(
    bind=True,
    name="sitara.generation.tasks.generate_design_attempt",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=SOFT_TIME_LIMIT_SECONDS,
    time_limit=HARD_TIME_LIMIT_SECONDS,
)
def generate_design_attempt(self, attempt_id):
    """Run one generation attempt; bounded-retry only on classified transients.

    Live factories (the gated Replicate provider and the hardened downloader)
    are resolved inside the pipeline when no double is injected; the config is
    built from Django settings (model, poll bounds, size caps)."""
    try:
        run_generation_attempt(attempt_id, config=build_pipeline_config())
    except GenerationRetry as exc:
        if self.request.retries >= MAX_TRANSIENT_RETRIES:
            logger.warning(
                "generation transient retries exhausted attempt=%s code=%s",
                attempt_id,
                exc.code,
            )
            fail_attempt(attempt_id, exc.code)
            return
        raise self.retry(
            countdown=RETRY_COUNTDOWN_SECONDS,
            max_retries=MAX_TRANSIENT_RETRIES,
            exc=exc,
        ) from exc


# ---------------------------------------------------------------------------
# Periodic maintenance tasks (Phase 16, Part C) — scheduled by Celery Beat.
# Both are bounded and idempotent, so a duplicate Beat delivery is safe: the
# purge re-selects a fresh batch and re-deletes idempotently, and the stuck
# reconciler re-checks each attempt under the advisory lock.
# ---------------------------------------------------------------------------
@shared_task(name="sitara.generation.tasks.purge_expired_designs")
def purge_expired_designs():
    from .maintenance import purge_expired_designs as _purge

    return _purge()


@shared_task(name="sitara.generation.tasks.reconcile_stuck_generations")
def reconcile_stuck_generations():
    from .maintenance import reconcile_stuck_generations as _reconcile

    return _reconcile()
