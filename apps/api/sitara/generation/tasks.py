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

from .pipeline import GenerationRetry, fail_attempt, run_generation_attempt

logger = get_task_logger(__name__)

# Bounded transient retry policy (safe, classified transients only).
MAX_TRANSIENT_RETRIES = 5
RETRY_COUNTDOWN_SECONDS = 5
# Bounded execution time — a generation must never run unbounded.
SOFT_TIME_LIMIT_SECONDS = 240
HARD_TIME_LIMIT_SECONDS = 300


@shared_task(
    bind=True,
    name="sitara.generation.tasks.generate_design_attempt",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=SOFT_TIME_LIMIT_SECONDS,
    time_limit=HARD_TIME_LIMIT_SECONDS,
)
def generate_design_attempt(self, attempt_id):
    """Run one generation attempt; bounded-retry only on classified transients."""
    try:
        run_generation_attempt(attempt_id)
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
