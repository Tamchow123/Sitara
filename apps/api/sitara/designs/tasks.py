"""The Celery task that mails an owner a copy of their own render (Phase 19).

Rendering and sending happen here rather than in the request because neither
fits the browser's budget: ``apps/web/src/lib/transport.ts`` aborts at 5s, while
a storage read, a PNG composition measured at 10-18s at its worst, and an SMTP
round trip together do not reliably fit. The endpoint returns 202 and this runs
afterwards.

``acks_late`` and ``reject_on_worker_lost`` are on project-wide, so a worker
killed after the message reaches the backend but before its ack reaches the
broker gets this task redelivered. SMTP is not a resumable stage — it is a
non-transactional external side effect — so idempotency comes from the durable
claim in :mod:`sitara.designs.render_delivery`, not from re-deriving state.

The time limits are computed in ``config.settings`` beside
``ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS``, which must outlast them; that
inequality is enforced at startup there rather than asserted here.
"""

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings

from .render_delivery import deliver_render

logger = get_task_logger(__name__)

SOFT_TIME_LIMIT_SECONDS = settings.ACCOUNT_EMAIL_SEND_SOFT_TIME_LIMIT_SECONDS
HARD_TIME_LIMIT_SECONDS = settings.ACCOUNT_EMAIL_SEND_HARD_TIME_LIMIT_SECONDS


@shared_task(
    name="sitara.designs.tasks.send_design_render",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=SOFT_TIME_LIMIT_SECONDS,
    time_limit=HARD_TIME_LIMIT_SECONDS,
)
def send_design_render(design_version_id, kind):
    """Mail one render of one version to the version's own owner.

    Deliberately takes two identifiers and nothing else. No recipient address,
    no rendered bytes and no signed URL crosses the queue, where they would rest
    in Redis in the clear and survive any broker inspection; everything is
    re-derived from database state inside the task.

    No ``autoretry_for``: a failure leaves the durable claim standing and the
    bounded stale-claim path retries at most once. An automatic retry here would
    multiply real emails."""
    return deliver_render(design_version_id, kind)
