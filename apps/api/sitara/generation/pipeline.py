"""Durable asynchronous generation pipeline (Phase 10, Parts A & B).

Two public entry points:

* :func:`enqueue_design_generation` — the short, Design-row-locked transaction
  that creates (or idempotently replays) one queued :class:`GenerationAttempt`,
  moves the Design to ``generating`` and submits the Celery task on commit.
* :func:`run_generation_attempt` — the resumable state machine the Celery task
  (and the offline command and tests) run. It inspects persisted markers and
  resumes safely: a linked DesignVersion skips the Anthropic stage, an existing
  image prompt skips prompt persistence, an existing prediction id is never
  resubmitted, and an already-staged object is verified rather than
  regenerated.

The image provider, downloader and storage are INJECTED (fakes in tests, the
offline command's fixtures). When not injected, the LIVE factories at the bottom
of this module resolve the gated Replicate provider and the hardened downloader
(Part B) — always fail-closed, constructing no network client unless every gate
passes. A non-blocking PostgreSQL advisory lock (in the two-integer lock space,
distinct from the Design-level spec lock's bigint space) guarantees duplicate
broker delivery never executes one attempt twice. Logs carry only operation
names, row UUIDs and exception types — never a prompt, answer, output URL,
storage key or provider error body.
"""

import contextlib
import hashlib
import io
import logging
import math
import secrets
import time
import uuid
from dataclasses import dataclass

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from sitara.ai_gateway.image_generation import (
    PREDICTION_ABORTED,
    PREDICTION_CANCELED,
    PREDICTION_FAILED,
    ImageGenerationRequest,
    ImageProviderError,
)
from sitara.ai_gateway.policy import generation_is_available
from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.designs.services import design_completion_errors

from . import errors
from .context import DesignNotReady
from .image_download import MAX_REDIRECTS
from .prompt_builder import ImagePromptBuildError
from .prompt_service import ImagePromptImmutable, build_and_store_image_prompt
from .services import (
    DesignChangedDuringGeneration,
    GenerationFailed,
    GenerationRefused,
    ProviderIdentityChanged,
    generate_design_spec_for_design,
)

logger = logging.getLogger(__name__)

_Status = GenerationAttempt.Status

# Advisory-lock namespace for attempt execution locks. Uses the TWO-integer
# lock space, which PostgreSQL keeps entirely separate from the single-bigint
# space the Design spec lock uses — so the two locks can never collide.
_ATTEMPT_LOCK_NAMESPACE = 0x51A  # arbitrary fixed namespace

# Monotonic clock for the poll wall-clock deadline, indirected so tests can
# inject a deterministic clock.
_monotonic = time.monotonic

# The reviewed Phase 2 rendering profile and safe pipeline bounds. Part B wires
# the environment-driven values (model, timeouts, size caps) into this config
# from Django settings; the defaults here keep Part A self-contained and make
# accidental unbounded work impossible.
DEFAULT_ASPECT_RATIO = "3:4"
DEFAULT_OUTPUT_FORMAT = "webp"
DEFAULT_OUTPUT_QUALITY = 80
DEFAULT_SAFETY_TOLERANCE = 2
DEFAULT_PROMPT_UPSAMPLING = False


@dataclass(frozen=True)
class PipelineConfig:
    """Tunable, injectable pipeline parameters. The image PROFILE defaults are
    the reviewed Phase 2 rendering settings; ``build_pipeline_config`` fills the
    model/timeout/size values from Django settings for live rendering, while
    tests keep the fast defaults."""

    model: str = ""
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    output_format: str = DEFAULT_OUTPUT_FORMAT
    output_quality: int = DEFAULT_OUTPUT_QUALITY
    safety_tolerance: int = DEFAULT_SAFETY_TOLERANCE
    prompt_upsampling: bool = DEFAULT_PROMPT_UPSAMPLING
    poll_interval_seconds: float = 0.0
    poll_max_attempts: int = 90
    # Wall-clock bound (seconds) on the whole poll loop; 0 disables it (tests
    # bound by attempt count alone). When set, polling stops once this many
    # seconds elapse regardless of per-call latency, so REPLICATE_POLL_TIMEOUT
    # is a TRUE bound even if individual status requests are slow.
    poll_timeout_seconds: float = 0.0
    raw_max_bytes: int = 20_000_000
    raw_max_pixels: int = 40_000_000


def build_pipeline_config() -> "PipelineConfig":
    """Build the live pipeline configuration from Django settings.

    The environment-driven values (model, poll interval/timeout, size caps) come
    from settings; the reviewed Phase 2 image profile (aspect ratio 3:4, WebP,
    quality/safety/upsampling) stays on the PipelineConfig defaults. Polling is
    bounded by BOTH an attempt count (``ceil(timeout/interval)``, so total sleep
    is at most ``(attempts-1)*interval < timeout``) AND a wall-clock deadline of
    ``timeout`` seconds, so slow individual status calls cannot push total
    polling past REPLICATE_POLL_TIMEOUT."""
    interval = settings.REPLICATE_POLL_INTERVAL_SECONDS
    timeout = settings.REPLICATE_POLL_TIMEOUT_SECONDS
    max_attempts = max(1, math.ceil(timeout / interval)) if interval > 0 else 1
    return PipelineConfig(
        model=settings.DEFAULT_IMAGE_MODEL,
        poll_interval_seconds=float(interval),
        poll_max_attempts=max_attempts,
        poll_timeout_seconds=float(timeout),
        raw_max_bytes=settings.GENERATION_RAW_MAX_BYTES,
        raw_max_pixels=settings.GENERATION_RAW_MAX_PIXELS,
    )


def pipeline_budget_seconds() -> int:
    """The worst-case wall-clock budget of one attempt's stages, from the SAME
    settings the stages use — colocated with :func:`build_pipeline_config` so all
    settings-derived pipeline timing lives in one place. The Celery task derives
    its soft/hard time limits from this so a legitimately slow render is never
    interrupted mid-flight.

    Stages: the text stage may make up to two Anthropic requests; the image
    stage submits (one REPLICATE_TIMEOUT), polls under a REPLICATE_POLL_TIMEOUT
    wall-clock deadline plus one in-flight status call that may run up to
    REPLICATE_TIMEOUT past it, then downloads over a bounded multi-hop budget."""
    text = 2 * settings.ANTHROPIC_TIMEOUT_SECONDS
    download = settings.REPLICATE_TIMEOUT_SECONDS * (MAX_REDIRECTS + 1)
    image = (
        # submit + one trailing in-flight poll call + the poll wall-clock bound
        2 * settings.REPLICATE_TIMEOUT_SECONDS + settings.REPLICATE_POLL_TIMEOUT_SECONDS + download
    )
    return text + image


# ---------------------------------------------------------------------------
# Enqueue-boundary exceptions (mapped to HTTP by the view).
# ---------------------------------------------------------------------------


class GenerationUnavailable(Exception):
    """Live generation is not currently available (gates closed). -> 503."""


class GenerationInProgress(Exception):
    """Another attempt for this Design is queued or running. -> 409."""


class DesignAlreadyGenerated(Exception):
    """This Design already produced a completed generation. -> 409."""


class DesignNotGeneratable(Exception):
    """The Design is in a state from which it cannot be generated. -> 409."""


class DesignIncomplete(Exception):
    """The Design failed authoritative completeness validation. -> 400.

    Carries the safe per-field completion errors."""

    def __init__(self, field_errors: dict):
        self.field_errors = field_errors
        super().__init__("the design is not complete")


class QueueUnavailable(Exception):
    """The broker rejected the task after the attempt committed. -> 503."""


# ---------------------------------------------------------------------------
# Pipeline-internal control-flow exceptions (never surface to the caller).
# ---------------------------------------------------------------------------


class _TerminalGenerationError(Exception):
    """A terminal pipeline failure carrying one stable error code."""

    def __init__(self, code: str):
        assert errors.is_valid_error_code(code), code
        self.code = code
        super().__init__(code)


class GenerationRetry(Exception):
    """A safe, classified transient failure. The attempt is LEFT in progress
    (its persisted markers intact) so a bounded task retry resumes it without
    repeating the text stage or resubmitting a prediction. Carries a stable
    code for logging only."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _PollTimedOut(Exception):
    """Internal: polling exceeded the configured bound."""


class _LiveImageProviderUnavailable(Exception):
    """Internal: no live image provider/downloader exists yet (Part A). Part B
    replaces the live factories with the gated Replicate implementations."""


# ---------------------------------------------------------------------------
# Enqueue service
# ---------------------------------------------------------------------------


def _submit_to_celery(attempt: GenerationAttempt) -> None:
    """Default task submission: deterministic task id, explicit generation
    queue. Imported lazily to avoid an import cycle with the task module."""
    from .tasks import generate_design_attempt

    generate_design_attempt.apply_async(
        args=[str(attempt.id)], task_id=str(attempt.id), queue="generation"
    )


def _mark_queue_unavailable(attempt: GenerationAttempt) -> None:
    """A broker submission failure AFTER commit: the attempt is queued in the
    database but was never actually submitted. Mark it failed and move the
    Design to generation_failed so no orphan queued job survives. Runs in its
    own transaction (the enqueue transaction has already committed)."""
    with transaction.atomic():
        locked = GenerationAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status in (_Status.SUCCEEDED, _Status.FAILED):
            return
        locked.status = _Status.FAILED
        locked.error_code = errors.QUEUE_UNAVAILABLE
        locked.completed_at = timezone.now()
        locked.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
        Design.objects.filter(pk=locked.design_id).update(
            status=Design.Status.GENERATION_FAILED, updated_at=timezone.now()
        )


def enqueue_design_generation(
    design, *, idempotency_key, enqueue_task=None, require_availability=True
):
    """Create or idempotently replay one queued attempt for ``design``.

    Returns ``(attempt, created)``. ``created`` is False for an idempotent
    replay of an existing key. The whole decision runs in one short transaction
    under the Design row lock; the Celery task is submitted with
    ``transaction.on_commit`` so a worker never observes an uncommitted attempt.

    ``require_availability`` gates on the public ``generation_is_available()``
    flag and MUST stay True for every request originating from the public API.
    The offline fixture management command sets it False because it injects
    zero-network fixture providers and never makes a paid call — the completeness
    and concurrency checks still run.

    Raises GenerationUnavailable / DesignIncomplete / GenerationInProgress /
    DesignAlreadyGenerated / DesignNotGeneratable during the transaction (no
    attempt is created), or QueueUnavailable if the broker rejects the task
    after commit (the attempt is then marked failed)."""
    submit = enqueue_task or _submit_to_celery
    outcome: dict = {"attempt": None}

    def _on_commit():
        attempt = outcome["attempt"]
        try:
            submit(attempt)
        except Exception as exc:
            logger.warning(
                "generation enqueue broker failure attempt=%s design=%s exception_type=%s",
                attempt.id,
                attempt.design_id,
                type(exc).__name__,
            )
            _mark_queue_unavailable(attempt)
            raise QueueUnavailable("the generation queue is temporarily unavailable") from exc

    with transaction.atomic():
        locked = Design.objects.select_for_update().get(pk=design.pk)

        # 1. Idempotent replay: return an existing attempt for this exact key
        #    unchanged, regardless of the current provider gates.
        existing = GenerationAttempt.objects.filter(
            design=locked, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing, False

        # 2. New key: enforce availability BEFORE any work (public API only).
        if require_availability and not generation_is_available():
            raise GenerationUnavailable("live generation is not currently available")

        # 3. Re-run authoritative completeness (questionnaire + inspiration).
        completion_errors = design_completion_errors(locked)
        if completion_errors:
            raise DesignIncomplete(completion_errors)

        # 4. Reject if another attempt is already in progress.
        if GenerationAttempt.objects.filter(
            design=locked, status__in=GenerationAttempt.IN_PROGRESS_STATUSES
        ).exists():
            raise GenerationInProgress("a generation job is already in progress for this design")

        # 5. Determine the starting point from persisted state.
        if (
            locked.status == Design.Status.GENERATED
            or GenerationAttempt.objects.filter(design=locked, status=_Status.SUCCEEDED).exists()
        ):
            raise DesignAlreadyGenerated("this design has already been generated")
        versions = list(DesignVersion.objects.filter(design=locked).order_by("version_number"))
        if len(versions) > 1:
            # Multiple versions are a refinement scenario (a later phase); the
            # initial async pipeline never resumes into that.
            raise DesignNotGeneratable("this design cannot be generated")
        resume_version = versions[0] if versions else None

        # 6. Create the queued attempt (resuming an incomplete version if any).
        attempt = GenerationAttempt.objects.create(
            design=locked,
            design_version=resume_version,
            idempotency_key=idempotency_key,
            status=_Status.QUEUED,
        )
        attempt.celery_task_id = str(attempt.id)
        attempt.save(update_fields=["celery_task_id", "updated_at"])

        # 7. Move the Design into the generating state.
        locked.status = Design.Status.GENERATING
        locked.save(update_fields=["status", "updated_at"])

        outcome["attempt"] = attempt
        transaction.on_commit(_on_commit)

    return outcome["attempt"], True


# ---------------------------------------------------------------------------
# Attempt execution lock
# ---------------------------------------------------------------------------


def _attempt_lock_key(attempt_id) -> int:
    # A signed 32-bit key from the attempt UUID for the two-int advisory lock.
    return int.from_bytes(attempt_id.bytes[:4], "big", signed=True)


@contextlib.contextmanager
def _attempt_advisory_lock(attempt_id):
    """Non-blocking advisory lock for the whole attempt execution. Yields
    whether it was acquired; a duplicate delivery that fails to acquire yields
    False and the caller exits without doing work. Always released.

    Durability model (defence in depth beyond this lock): every externally
    visible write is independently idempotent — the terminal-status guard in
    ``_finalise_success``/``_finalise_failure`` (under a row lock), the
    submit-once ``image_submission_in_flight`` marker, the reuse of a persisted
    prediction id and the no-overwrite deterministic staging key together mean
    that even two workers briefly acting on one attempt (e.g. if a recycled
    backend connection silently dropped this session lock) cannot double-submit
    a prediction, double-stage an object, or corrupt state — at worst they waste
    a poll. The deterministic Celery ``task_id`` (the attempt UUID) is the first
    line of de-duplication. Operational reconciliation of an attempt left
    in-progress by a hard worker loss (a periodic stuck-job reaper) is owned by
    Phase 16 per docs/phases/PHASES.md; this deployment holds direct psycopg
    connections with ``conn_max_age=60`` and no connection pooler, so a dead
    worker's session — and its lock — is released promptly rather than lingering
    behind a pooler."""
    key = _attempt_lock_key(attempt_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [_ATTEMPT_LOCK_NAMESPACE, key])
        acquired = bool(cursor.fetchone()[0])
        try:
            yield acquired
        finally:
            if acquired:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [_ATTEMPT_LOCK_NAMESPACE, key])


# ---------------------------------------------------------------------------
# State-machine entry point
# ---------------------------------------------------------------------------


def run_generation_attempt(
    attempt_id,
    *,
    structured_provider=None,
    image_provider=None,
    image_downloader=None,
    storage=None,
    seed_factory=None,
    config=None,
):
    """Resume-safe execution of one attempt. See the module docstring.

    Returns the (refreshed) attempt on a terminal state, or None when the
    attempt does not exist or a duplicate delivery could not acquire the lock.
    Re-raises :class:`GenerationRetry` for a classified transient failure so the
    Celery task can bound-retry without repeating earlier stages."""
    config = config or PipelineConfig()
    # The Celery task passes the attempt id as a string; the advisory-lock key
    # derivation needs a UUID. Coerce once here (a malformed id is a safe no-op).
    if not isinstance(attempt_id, uuid.UUID):
        try:
            attempt_id = uuid.UUID(str(attempt_id))
        except (ValueError, AttributeError, TypeError) as exc:
            # Unreachable in production (the task id is always the attempt UUID),
            # but never silently no-op: log the type so a future mis-call is
            # diagnosable. The raw value is never echoed.
            logger.warning(
                "generation attempt id is not a valid UUID exception_type=%s",
                type(exc).__name__,
            )
            return None
    with _attempt_advisory_lock(attempt_id) as acquired:
        if not acquired:
            logger.info(
                "generation attempt already executing; duplicate delivery ignored attempt=%s",
                attempt_id,
            )
            return None
        attempt = GenerationAttempt.objects.filter(pk=attempt_id).first()
        if attempt is None:
            return None
        if attempt.status in (_Status.SUCCEEDED, _Status.FAILED):
            return attempt  # terminal states are idempotent

        try:
            return _execute(
                attempt,
                structured_provider,
                image_provider,
                image_downloader,
                storage,
                seed_factory,
                config,
            )
        except GenerationRetry:
            raise
        except SoftTimeLimitExceeded as exc:
            # A worker soft-time-limit interruption is NOT a domain failure.
            # Leave the attempt in-progress and signal a bounded retry so a
            # redelivery resumes from persisted markers (linked version, prompt,
            # prediction id) instead of resubmitting from scratch.
            logger.warning("generation soft time limit reached attempt=%s", attempt.id)
            raise GenerationRetry(errors.INTERNAL_GENERATION_ERROR) from exc
        except _TerminalGenerationError as exc:
            _finalise_failure(attempt, exc.code)
            return GenerationAttempt.objects.get(pk=attempt.pk)
        except Exception as exc:  # noqa: BLE001 - deliberate task boundary
            logger.warning(
                "unexpected generation failure attempt=%s exception_type=%s",
                attempt.id,
                type(exc).__name__,
            )
            _finalise_failure(attempt, errors.INTERNAL_GENERATION_ERROR)
            return GenerationAttempt.objects.get(pk=attempt.pk)


def _execute(
    attempt, structured_provider, image_provider, image_downloader, storage, seed_factory, config
):
    # Stage A — claim and pre-check.
    _set_started(attempt)
    design = Design.objects.get(pk=attempt.design_id)
    if design_completion_errors(design):
        raise _TerminalGenerationError(errors.DESIGN_CHANGED)

    attempt.refresh_from_db()

    # Stage B — DesignSpec (only when no version is linked yet).
    if attempt.design_version_id is None:
        _set_status(attempt, _Status.RUNNING_TEXT)
        version = _run_text_stage(design, attempt, structured_provider)
    else:
        version = DesignVersion.objects.get(pk=attempt.design_version_id)
        if version.design_id != attempt.design_id:
            raise _TerminalGenerationError(errors.DESIGN_CHANGED)

    # Stage C — deterministic image prompt (idempotent).
    version = _run_prompt_stage(version)

    # Stage D — image submission, polling, download and staging.
    _run_image_stage(
        attempt, version, image_provider, image_downloader, storage, seed_factory, config
    )

    # Stage E — success.
    _finalise_success(attempt)
    return GenerationAttempt.objects.get(pk=attempt.pk)


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _set_started(attempt: GenerationAttempt) -> None:
    if attempt.started_at is None:
        now = timezone.now()
        GenerationAttempt.objects.filter(pk=attempt.pk, started_at__isnull=True).update(
            started_at=now, updated_at=now
        )
        attempt.started_at = now


def _set_status(attempt: GenerationAttempt, status: str) -> None:
    if attempt.status != status:
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=status, updated_at=timezone.now()
        )
        attempt.status = status


def _run_text_stage(design, attempt, structured_provider) -> DesignVersion:
    provider = structured_provider
    if provider is None:
        # Live gated Anthropic provider (Phase 8). Only reached when a caller
        # did not inject one (never in Part A tests).
        from sitara.ai_gateway.policy import get_structured_design_generation_provider

        provider = get_structured_design_generation_provider()
    try:
        version = generate_design_spec_for_design(design, provider=provider, attempt=attempt)
    except GenerationRefused as exc:
        raise _TerminalGenerationError(errors.STRUCTURED_PROVIDER_REFUSED) from exc
    except DesignChangedDuringGeneration as exc:
        raise _TerminalGenerationError(errors.DESIGN_CHANGED) from exc
    except DesignNotReady as exc:
        # A concurrent path having already generated a version is a "changed"
        # condition, not incompleteness; keep the codes distinguishable.
        code = (
            errors.DESIGN_CHANGED
            if getattr(exc, "code", None) == "already_generated"
            else errors.DESIGN_INCOMPLETE
        )
        raise _TerminalGenerationError(code) from exc
    except (GenerationFailed, ProviderIdentityChanged) as exc:
        raise _TerminalGenerationError(errors.STRUCTURED_GENERATION_FAILED) from exc
    attempt.refresh_from_db()
    return version


def _run_prompt_stage(version: DesignVersion) -> DesignVersion:
    try:
        return build_and_store_image_prompt(version)
    except ImagePromptImmutable:
        # An identical prompt already exists (resume) — keep the existing one.
        version.refresh_from_db()
        return version
    except ImagePromptBuildError as exc:
        raise _TerminalGenerationError(errors.PROMPT_BUILD_FAILED) from exc


def _run_image_stage(
    attempt, version, image_provider, image_downloader, storage, seed_factory, config
) -> None:
    provider = image_provider if image_provider is not None else _live_image_provider(config)
    downloader = (
        image_downloader if image_downloader is not None else _live_image_downloader(config)
    )
    store = storage if storage is not None else default_storage

    _set_status(attempt, _Status.RUNNING_IMAGE)
    attempt.refresh_from_db()

    # Resume: an already-staged object means the image is done; finalisation
    # follows without another provider call.
    if attempt.staged_image_storage_key:
        return

    prediction_id = _ensure_prediction(attempt, version, provider, seed_factory, config)
    prediction = _poll(provider, prediction_id, config)

    if prediction.status == PREDICTION_FAILED:
        raise _TerminalGenerationError(errors.IMAGE_PREDICTION_FAILED)
    if prediction.status == PREDICTION_CANCELED:
        raise _TerminalGenerationError(errors.IMAGE_PREDICTION_CANCELED)
    if prediction.status == PREDICTION_ABORTED:
        raise _TerminalGenerationError(errors.IMAGE_PREDICTION_ABORTED)

    output_url = prediction.output_url
    if not output_url:
        raise _TerminalGenerationError(errors.IMAGE_OUTPUT_INVALID)

    data = _download(downloader, output_url, config)
    extension, width, height = _verify_image(data, config)
    sha256 = hashlib.sha256(data).hexdigest()
    key = _stage_raw_image(store, attempt.id, extension, data, sha256)
    _persist_staged(attempt, key, sha256, len(data), width, height)


def _ensure_prediction(attempt, version, provider, seed_factory, config) -> str:
    """Return the persisted prediction id, submitting AT MOST once.

    An existing prediction id is NEVER resubmitted (all retries poll the same
    prediction). Because Replicate gives no exactly-once create guarantee, a
    persisted ``image_submission_in_flight`` marker is written in the SAME
    transaction as the seed/parameters, BEFORE the provider call:

    - a definitely-pre-acceptance transient failure clears the marker so a
      bounded retry may safely resubmit (nothing was accepted);
    - an ambiguous transport failure leaves the marker set and ends terminally;
    - a crash between a SUCCESSFUL create and persisting the id leaves the
      marker set with no id, so a resume treats it as ambiguous rather than
      blindly resubmitting (conservative spend).

    The seed is generated ONCE and reused on every retry of the attempt."""
    if attempt.image_prediction_id:
        return attempt.image_prediction_id

    # Crash-window guard: a prior run began a submission (marker set) but never
    # persisted a prediction id — the provider MAY have accepted it. Never
    # resubmit; fail conservatively so a new attempt/key is required.
    if attempt.image_submission_in_flight:
        raise _TerminalGenerationError(errors.IMAGE_SUBMISSION_AMBIGUOUS)

    # Reuse a seed persisted by an earlier (pre-acceptance) attempt; only
    # generate one the first time so a restart never produces a second seed.
    seed = attempt.image_seed
    if seed is None:
        seed = int(seed_factory()) if seed_factory is not None else _generate_seed()
    if seed < 0:
        raise _TerminalGenerationError(errors.INTERNAL_GENERATION_ERROR)
    model = config.model or provider.name
    parameters = {
        "aspect_ratio": config.aspect_ratio,
        "output_format": config.output_format,
        "output_quality": config.output_quality,
        "safety_tolerance": config.safety_tolerance,
        "prompt_upsampling": config.prompt_upsampling,
    }
    # Persist seed + parameters + provider/model AND set the in-flight marker
    # BEFORE the provider call, all in one transaction.
    with transaction.atomic():
        locked = GenerationAttempt.objects.select_for_update().get(pk=attempt.pk)
        locked.image_provider = provider.name
        locked.image_model = model
        locked.image_seed = seed
        locked.image_parameters = parameters
        locked.image_submission_in_flight = True
        locked.save(
            update_fields=[
                "image_provider",
                "image_model",
                "image_seed",
                "image_parameters",
                "image_submission_in_flight",
                "updated_at",
            ]
        )
    attempt.refresh_from_db()

    request = ImageGenerationRequest(
        prompt=version.image_prompt,
        model=model,
        seed=seed,
        aspect_ratio=config.aspect_ratio,
        output_format=config.output_format,
        output_quality=config.output_quality,
        safety_tolerance=config.safety_tolerance,
        prompt_upsampling=config.prompt_upsampling,
    )
    try:
        prediction = provider.create_prediction(request)
    except ImageProviderError as exc:
        if exc.ambiguous_acceptance:
            # The provider may already have accepted the request; never
            # resubmit. Leave the marker set and end terminally.
            raise _TerminalGenerationError(errors.IMAGE_SUBMISSION_AMBIGUOUS) from exc
        # Definitely pre-acceptance transient failure: nothing was accepted, so
        # clear the marker and allow a bounded retry to resubmit (same seed).
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            image_submission_in_flight=False, updated_at=timezone.now()
        )
        attempt.image_submission_in_flight = False
        raise GenerationRetry(errors.IMAGE_PROVIDER_UNAVAILABLE) from exc

    # Persist the accepted prediction id and clear the marker together; never
    # clear or replace the id afterwards.
    GenerationAttempt.objects.filter(pk=attempt.pk).update(
        image_prediction_id=prediction.prediction_id,
        image_submission_in_flight=False,
        updated_at=timezone.now(),
    )
    attempt.image_prediction_id = prediction.prediction_id
    attempt.image_submission_in_flight = False
    return prediction.prediction_id


def _poll(provider, prediction_id, config):
    """Poll the same prediction id until terminal or the configured bound.

    A transient transport failure propagates as :class:`GenerationRetry`
    (bounded task retry, same prediction). A timeout attempts cancellation and
    ends the attempt as ``image_poll_timeout``. No lock or transaction is held
    while polling/sleeping."""
    attempts = max(config.poll_max_attempts, 1)
    # A wall-clock deadline (when configured) makes REPLICATE_POLL_TIMEOUT a true
    # bound regardless of per-call latency: polling stops once it elapses even if
    # individual status calls are slow. Tests leave it 0 and bound by count.
    deadline = _monotonic() + config.poll_timeout_seconds if config.poll_timeout_seconds else None
    for index in range(attempts):
        try:
            prediction = provider.get_prediction(prediction_id)
        except ImageProviderError as exc:
            raise GenerationRetry(errors.IMAGE_PROVIDER_UNAVAILABLE) from exc
        if prediction.is_terminal:
            return prediction
        if deadline is not None and _monotonic() >= deadline:
            break
        # Sleep only between polls, never after the final one — so total sleep
        # is bounded by (attempts - 1) * interval, strictly below the timeout.
        if config.poll_interval_seconds and index < attempts - 1:
            time.sleep(config.poll_interval_seconds)
    # Timed out: best-effort cancellation, then a terminal timeout.
    with contextlib.suppress(Exception):
        provider.cancel_prediction(prediction_id)
    raise _TerminalGenerationError(errors.IMAGE_POLL_TIMEOUT)


def _download(downloader, output_url, config) -> bytes:
    try:
        data = downloader(output_url)
    except _TerminalGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 - downloader raises varied transport errors
        raise _TerminalGenerationError(errors.IMAGE_DOWNLOAD_FAILED) from exc
    if not isinstance(data, bytes | bytearray) or len(data) == 0:
        raise _TerminalGenerationError(errors.IMAGE_OUTPUT_INVALID)
    if len(data) > config.raw_max_bytes:
        raise _TerminalGenerationError(errors.IMAGE_OUTPUT_INVALID)
    return bytes(data)


def _verify_image(data: bytes, config) -> tuple[str, int, int]:
    """Verify the raw bytes are a real, bounded PNG/JPEG/WebP. Never trusts an
    extension or Content-Type. Returns (extension, width, height)."""
    allowed = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
            if image_format not in allowed:
                raise _TerminalGenerationError(errors.IMAGE_OUTPUT_INVALID)
            if width <= 0 or height <= 0 or width * height > config.raw_max_pixels:
                # Zero-dimension or decompression-bomb / oversized image.
                raise _TerminalGenerationError(errors.IMAGE_OUTPUT_INVALID)
            image.load()  # force a full decode to catch truncation
    except _TerminalGenerationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise _TerminalGenerationError(errors.IMAGE_OUTPUT_INVALID) from exc
    return allowed[image_format], width, height


def _stage_raw_image(store, attempt_id, extension: str, data: bytes, sha256: str) -> str:
    """Copy verified raw output into private storage at a deterministic key.

    On task restart the deterministic object may already exist: a byte-identical
    object (same SHA-256) resumes finalisation without another provider call; a
    DIFFERENT object at the same key fails safely rather than being overwritten.
    ``file_overwrite=False`` storage never silently replaces a distinct object."""
    key = f"generation-staging/{attempt_id}/raw.{extension}"
    if store.exists(key):
        try:
            with store.open(key, "rb") as existing:
                existing_bytes = existing.read()
        except Exception as exc:  # noqa: BLE001
            raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED) from exc
        if hashlib.sha256(existing_bytes).hexdigest() == sha256:
            return key  # identical object already staged — resume
        raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED)
    try:
        saved_key = store.save(key, ContentFile(data))
    except Exception as exc:  # noqa: BLE001
        raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED) from exc
    if saved_key != key:
        # A non-overwriting backend renamed around an existing object — treat as
        # a staging conflict rather than accept a mismatched key.
        with contextlib.suppress(Exception):
            store.delete(saved_key)
        raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED)
    return key


def _persist_staged(attempt, key, sha256, size_bytes, width, height) -> None:
    GenerationAttempt.objects.filter(pk=attempt.pk).update(
        staged_image_storage_key=key,
        staged_image_sha256=sha256,
        staged_image_size_bytes=size_bytes,
        staged_image_width=width,
        staged_image_height=height,
        updated_at=timezone.now(),
    )


def _finalise_success(attempt) -> None:
    with transaction.atomic():
        locked = GenerationAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status == _Status.SUCCEEDED:
            return
        locked.status = _Status.SUCCEEDED
        locked.error_code = ""
        locked.completed_at = timezone.now()
        locked.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
        Design.objects.filter(pk=locked.design_id).update(
            status=Design.Status.GENERATED, updated_at=timezone.now()
        )


def _finalise_failure(attempt, code: str) -> None:
    with transaction.atomic():
        locked = GenerationAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status in (_Status.SUCCEEDED, _Status.FAILED):
            return
        locked.status = _Status.FAILED
        locked.error_code = code
        locked.completed_at = timezone.now()
        locked.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
        # Preserve any linked DesignVersion, prompt, prediction id and staged
        # data — never delete or rewrite newer design work.
        Design.objects.filter(pk=locked.design_id).update(
            status=Design.Status.GENERATION_FAILED, updated_at=timezone.now()
        )


def fail_attempt(attempt_id, code: str) -> None:
    """Mark an attempt terminally failed with a stable code (used by the task
    when a bounded transient retry is exhausted). No-op if already terminal."""
    if not errors.is_valid_error_code(code):
        code = errors.INTERNAL_GENERATION_ERROR
    attempt = GenerationAttempt.objects.filter(pk=attempt_id).first()
    if attempt is not None:
        _finalise_failure(attempt, code)


def _generate_seed() -> int:
    """A cryptographically-generated non-negative 32-bit seed (zero allowed)."""
    return secrets.randbelow(2**32)


# ---------------------------------------------------------------------------
# Live provider factories (Phase 10 Part B): the gated Replicate provider and
# the hardened output downloader. Both fail closed — if the paid-image gate is
# not open they map to a terminal image_provider_unavailable rather than
# constructing any network client.
# ---------------------------------------------------------------------------


def _live_image_provider(config):
    from sitara.ai_gateway.policy import (
        PaidGenerationDisabled,
        get_image_generation_provider_async,
    )

    try:
        return get_image_generation_provider_async()
    except PaidGenerationDisabled as exc:
        raise _TerminalGenerationError(errors.IMAGE_PROVIDER_UNAVAILABLE) from exc


def _live_image_downloader(config):
    from .image_download import download_replicate_output

    def _download(url):
        return download_replicate_output(
            url,
            max_bytes=config.raw_max_bytes,
            timeout_seconds=settings.REPLICATE_TIMEOUT_SECONDS,
        )

    return _download
