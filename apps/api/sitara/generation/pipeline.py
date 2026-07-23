"""Durable asynchronous generation pipeline (Phase 10 Parts A & B; Phase 11
adds stage E — canonical permanent image ingest).

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
offline command's fixtures). When not injected, resolution branches on the
FROZEN ``GenerationAttempt.is_demo`` flag (Phase 15), never on live settings:
a demo attempt resolves the local, zero-network demo adapters
(:mod:`sitara.generation.demo`); a live attempt resolves the LIVE factories at
the bottom of this module, which construct the gated Replicate provider and
the hardened downloader (Part B) — always fail-closed, constructing no
network client unless every gate passes. A non-blocking PostgreSQL advisory
lock (in the two-integer lock space,
distinct from the Design-level spec lock's bigint space) guarantees duplicate
broker delivery never executes one attempt twice. Logs carry only operation
names, row UUIDs and exception types — never a prompt, answer, output URL,
storage key or provider error body.
"""

import contextlib
import dataclasses
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
from django.db.models import Q
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from sitara.ai_gateway.image_generation import (
    PREDICTION_ABORTED,
    PREDICTION_CANCELED,
    PREDICTION_FAILED,
    PREDICTION_SUCCEEDED,
    ImageGenerationRequest,
    ImageProviderError,
)
from sitara.ai_gateway.policy import generation_is_available
from sitara.ai_gateway.structured_design import StructuredDesignProviderError
from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.designs.services import design_completion_errors
from sitara.media.exceptions import (
    DesignImageImmutable,
    DesignImageIngestFailed,
    DesignImageIngestRetry,
)
from sitara.media.ingest import ingest_staged_design_image

from . import cost_accounting, cost_control, errors
from .context import DesignNotReady, build_generation_context
from .demo.config import (
    DemoAssetsUnavailable,
    demo_generation_is_available,
    load_active_demo_manifest,
)
from .demo.image_provider import DemoImageProvider, demo_image_downloader
from .demo.manifest import DemoManifest
from .demo.provider import (
    DemoRefinementStructuredDesignProvider,
    DemoStructuredDesignProvider,
)
from .demo.selector import DemoAssetSelection, DemoAssetUnavailable, select_demo_asset
from .design_spec import UnsupportedDesignSpecVersion, validate_design_spec
from .image_download import MAX_REDIRECTS
from .prompt_builder import ImagePromptBuildError
from .prompt_service import ImagePromptImmutable, build_and_store_image_prompt
from .refinement import (
    REFINEMENT_REQUEST_SCHEMA_VERSION,
    RefinementRequest,
    refinement_request_sha256,
)
from .refinement_service import (
    DesignChangedDuringRefinement,
    RefinementGenerationFailed,
    RefinementLimitReached,
    RefinementNoChangeProduced,
    RefinementSourceUnavailable,
    generate_refined_design_spec_for_design,
    validate_source_version,
)
from .services import (
    DesignChangedDuringGeneration,
    GenerationFailed,
    GenerationRefused,
    ProviderIdentityChanged,
    generate_design_spec_for_design,
)

logger = logging.getLogger(__name__)

_Status = GenerationAttempt.Status

# Terminal codes whose provider-spend question is RESOLVED, keeping the
# documented recovery path (a new idempotency key retrying only the image
# stage) open: a provider-REPORTED terminal failure/cancel/abort (polled from
# the provider itself), and output that was OBTAINED and CONFIRMED unusable
# (invalid bytes/bounds, or staged content that failed verification) — there
# regeneration is the only possible remedy. EVERY OTHER terminal code on a
# failed attempt carrying submission evidence (an accepted prediction id, or
# the in-flight marker persisted before the create call) blocks regeneration
# BY DEFAULT — fail closed: the spend question is unresolved (ambiguous
# acceptance, unverified staged output, a poll or download outage against a
# live prediction, our own poll DEADLINE whose best-effort cancellation is
# never confirmed and whose prediction may still complete and bill, or an
# unclassified crash after submission), so a fresh attempt could double-bill.
# This default also covers any FUTURE terminal code without per-code
# enumeration. Operator resolution/reconciliation arrives with Phase 16.
_SPEND_RESOLVED_CODES = (
    errors.IMAGE_PREDICTION_FAILED,
    errors.IMAGE_PREDICTION_CANCELED,
    errors.IMAGE_PREDICTION_ABORTED,
    errors.IMAGE_OUTPUT_INVALID,
    errors.IMAGE_STAGING_FAILED,
)

# Monotonic clock for the poll wall-clock deadline, indirected so tests can
# inject a deterministic clock.
_monotonic = time.monotonic

# Sleep primitive, indirected the same way so tests can inject a no-op —
# used only by the bounded demo progress delay (Phase 15) below.
_sleep = time.sleep

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
    # Bounded delay (Phase 15) applied only to demo attempts, only between
    # stages, never while a database lock or transaction is held — keeps the
    # genuine persisted progress states visible for a demonstration. Zero by
    # default (and always zero unless built from settings via
    # ``build_pipeline_config``, which every test-constructed PipelineConfig
    # bypasses).
    demo_stage_delay_seconds: float = 0.0


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
        demo_stage_delay_seconds=settings.DEMO_STAGE_DELAY_MS / 1000.0,
    )


def _demo_pipeline_config(config: "PipelineConfig") -> "PipelineConfig":
    """The config a demo attempt actually executes with: an empty ``model``
    (so ``_ensure_prediction`` falls back to the demo provider's own
    ``name``, never the configured live image model) and trivial poll
    bounds (the demo image provider always resolves on the first poll).
    Everything else — including ``demo_stage_delay_seconds`` — is preserved
    from the live/base config."""
    return dataclasses.replace(
        config,
        model="",
        poll_interval_seconds=0.0,
        poll_max_attempts=1,
        poll_timeout_seconds=0.0,
    )


def _demo_delay(config: "PipelineConfig") -> None:
    """A bounded, lock-free delay applied only to demo attempts, only
    between stages — never while a database lock or transaction is held."""
    if config.demo_stage_delay_seconds > 0:
        _sleep(config.demo_stage_delay_seconds)


# Worst-case wall-clock allowance (seconds) for stage E — canonical permanent
# ingest (Phase 11): one bounded staging read, full Pillow decode/orient/
# composite/resize/encode of an image up to GENERATION_RAW_MAX_PIXELS, two
# final-object writes and their read-back verifications. Deliberately
# conservative: ingest is idempotent but NOT incremental, so a soft-limit
# interruption re-runs the whole stage on retry — the budget must comfortably
# exceed a legitimately slow (large image / loaded storage) run, or bounded
# retries would exhaust at the same point every time.
INGEST_STAGE_BUDGET_SECONDS = 120


def pipeline_budget_seconds() -> int:
    """The worst-case wall-clock budget of one attempt's stages, from the SAME
    settings the stages use — colocated with :func:`build_pipeline_config` so all
    settings-derived pipeline timing lives in one place. The Celery task derives
    its soft/hard time limits from this so a legitimately slow render is never
    interrupted mid-flight.

    Stages: the text stage may make up to two Anthropic requests; the image
    stage submits (one REPLICATE_TIMEOUT), polls under a REPLICATE_POLL_TIMEOUT
    wall-clock deadline plus one in-flight status call that may run up to
    REPLICATE_TIMEOUT past it, then downloads over a bounded multi-hop budget;
    the ingest stage (E) gets the fixed INGEST_STAGE_BUDGET_SECONDS allowance
    for processing plus final-storage round-trips."""
    text = 2 * settings.ANTHROPIC_TIMEOUT_SECONDS
    download = settings.REPLICATE_TIMEOUT_SECONDS * (MAX_REDIRECTS + 1)
    image = (
        # submit + one trailing in-flight poll call + the poll wall-clock bound
        2 * settings.REPLICATE_TIMEOUT_SECONDS + settings.REPLICATE_POLL_TIMEOUT_SECONDS + download
    )
    return text + image + INGEST_STAGE_BUDGET_SECONDS


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


class DesignNotRefinable(Exception):
    """The Design's status does not support refinement (Phase 14). -> 409.

    A ``generated`` Design may be refined, as may a ``generation_failed``
    Design left by a resolved prior refinement failure (see
    :func:`enqueue_design_refinement` for the exact precondition) — a draft,
    currently generating, or generation_failed-with-no-usable-version-1
    design is rejected here or by the downstream source-version check."""


# ---------------------------------------------------------------------------
# Pipeline-internal control-flow exceptions (never surface to the caller).
# ---------------------------------------------------------------------------


class _TerminalGenerationError(Exception):
    """A terminal pipeline failure carrying one stable error code.

    ``clear_text_marker`` requests that ``text_submission_in_flight`` be
    cleared IN THE SAME atomic write that terminalises the attempt — never as
    a separate commit, so a crash can never land between a cleared marker and
    the terminal state (which would make an already-answered paid request
    silently resumable)."""

    def __init__(self, code: str, *, clear_text_marker: bool = False):
        assert errors.is_valid_error_code(code), code
        self.code = code
        self.clear_text_marker = clear_text_marker
        super().__init__(code)


class GenerationRetry(Exception):
    """A safe, classified transient failure. The attempt is LEFT in progress
    (its persisted markers intact) so a bounded task retry resumes it without
    repeating the text stage or resubmitting a prediction. Carries a stable
    code for logging only."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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


def _release_live_count(design_id, idempotency_key) -> None:
    """Best-effort return of a global daily count slot on a definite pre-provider
    failure. A ledger outage only leaves the slot counted (conservative); never
    crash. Idempotent — a missing/demo reservation is a harmless no-op."""
    try:
        cost_control.release_count(cost_control.count_reservation_id(design_id, idempotency_key))
    except cost_control.BudgetLedgerUnavailable:
        logger.warning("live count release unavailable design=%s", design_id)


def _mark_queue_unavailable(attempt: GenerationAttempt) -> None:
    """A broker submission failure AFTER commit: the attempt is queued in the
    database but was never actually submitted. Mark it failed and move the
    Design to generation_failed so no orphan queued job survives. The global
    daily count slot this attempt reserved is returned — queue submission
    definitely failed before any provider work could occur. Runs in its own
    transaction (the enqueue transaction has already committed)."""
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
    if not attempt.is_demo:
        _release_live_count(attempt.design_id, attempt.idempotency_key)


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

    # Idempotent replay: an unlocked read is safe here — the transaction
    # below re-checks it before ever writing, so a concurrent commit between
    # this read and the lock is still caught.
    existing = GenerationAttempt.objects.filter(
        design=design, idempotency_key=idempotency_key
    ).first()
    if existing is not None:
        return existing, False

    # Resolve and freeze the public generation mode BEFORE opening the
    # transaction/row lock (public API only) — availability I/O (a private
    # storage read for demo readiness, or the in-memory live gate check)
    # never runs while holding the Design row lock, keeping the locked
    # transaction below short. A fresh attempt resuming an existing
    # (incomplete) version must inherit THAT version's mode, never
    # re-resolve from current settings — the same demo/live lineage rule
    # refinement enforces — so the resumable version (if any) is previewed
    # here to pick the correct mode to check; the transaction below
    # re-queries it under the lock and is the sole authority for the value
    # actually persisted. Demo takes precedence over every paid flag — when
    # DEMO_MODE is true, ONLY demo readiness is evaluated; live readiness is
    # NEVER evaluated as a fallback from failed demo readiness. A demo
    # attempt's is_demo flag is the ONLY thing later pipeline stages
    # consult; they never re-read DEMO_MODE, so a later settings change can
    # never make an already-queued demo attempt spend money or a live
    # attempt silently become free. When require_availability is False (the
    # offline fixture command only), is_demo stays False here: that path
    # always injects its own zero-network
    # FixtureStructuredDesignProvider/FakeImageProvider directly, never the
    # real demo engine, so labelling it "demo" would be inaccurate and would
    # incorrectly trigger the demo pipeline-config overrides below.
    is_demo = False
    if require_availability:
        preview_version = (
            DesignVersion.objects.filter(design=design).order_by("version_number").first()
        )
        is_demo = preview_version.is_demo if preview_version is not None else settings.DEMO_MODE
        if is_demo:
            if not demo_generation_is_available():
                raise GenerationUnavailable("generation is not currently available")
        elif not generation_is_available():
            raise GenerationUnavailable("generation is not currently available")

    with transaction.atomic():
        locked = Design.objects.select_for_update().get(pk=design.pk)

        # 1. Idempotent replay, re-checked inside the lock: a concurrent
        #    request could have committed between the read above and here.
        existing = GenerationAttempt.objects.filter(
            design=locked, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing, False

        # 2. Generation mode and availability were already resolved above,
        #    before acquiring the row lock.

        # 3. Re-run authoritative completeness (questionnaire + inspiration).
        completion_errors = design_completion_errors(locked)
        if completion_errors:
            raise DesignIncomplete(completion_errors)

        # 4. Reject if another attempt is already in progress.
        if GenerationAttempt.objects.filter(
            design=locked, status__in=GenerationAttempt.IN_PROGRESS_STATUSES
        ).exists():
            raise GenerationInProgress("a generation job is already in progress for this design")

        # 5. Determine the starting point from persisted state. A completed
        #    version or an already-staged raw image rejects as generated (spec
        #    §5) — even when the attempt that produced it later terminally
        #    failed for an unrelated reason, the paid output exists and must
        #    never be regenerated. EXCEPTION: an attempt that terminally failed
        #    verification (``image_staging_failed``) has CONFIRMED-unusable
        #    staged data — blocking on it would permanently strand the design
        #    with no usable image, so a fresh attempt may proceed (its own
        #    deterministic key is distinct; the bad object stays orphaned for
        #    later cleanup). ``image_staging_unverified`` (bounded retries
        #    exhausted; content state UNKNOWN, possibly-valid paid output)
        #    deliberately does NOT qualify for the exception — it fails closed
        #    and keeps blocking regeneration until an operator intervenes.
        staged_elsewhere = (
            GenerationAttempt.objects.filter(design=locked)
            .exclude(staged_image_storage_key="")
            .exclude(status=_Status.FAILED, error_code=errors.IMAGE_STAGING_FAILED)
            .exists()
        )
        #    An attempt whose spend question is UNRESOLVED can end with an
        #    EMPTY staged key (retries ran out while staging paid output for
        #    the first time; the provider may have accepted a create that was
        #    never confirmed either way; a poll/download outage terminated an
        #    attempt whose prediction was live; a crash landed after
        #    submission). ALL such attempts must block whenever provider spend
        #    MAY have occurred: an accepted prediction id, a still-set image
        #    in-flight submission marker, or a still-set TEXT in-flight
        #    submission marker (the Anthropic request window). Each marker is
        #    persisted BEFORE its provider call, so with none present neither
        #    provider was ever invoked and a fresh attempt risks no paid
        #    output. Only the provider-CONFIRMED outcomes in
        #    ``_SPEND_RESOLVED_CODES`` keep the spec's recovery path open —
        #    every other code fails closed by default.
        unresolved_spend = (
            GenerationAttempt.objects.filter(design=locked, status=_Status.FAILED)
            .exclude(error_code__in=_SPEND_RESOLVED_CODES)
            .filter(
                Q(image_submission_in_flight=True)
                | ~Q(image_prediction_id="")
                | Q(text_submission_in_flight=True)
            )
            .exists()
        )
        if (
            locked.status == Design.Status.GENERATED
            or GenerationAttempt.objects.filter(design=locked, status=_Status.SUCCEEDED).exists()
            or staged_elsewhere
            or unresolved_spend
        ):
            raise DesignAlreadyGenerated("this design has already been generated")
        versions = list(DesignVersion.objects.filter(design=locked).order_by("version_number"))
        if len(versions) > 1:
            # Multiple versions are a refinement scenario (a later phase); the
            # initial async pipeline never resumes into that.
            raise DesignNotGeneratable("this design cannot be generated")
        resume_version = versions[0] if versions else None
        if resume_version is not None and resume_version.image_storage_key:
            # A final ingested image (Phase 11) means the design is complete.
            raise DesignAlreadyGenerated("this design has already been generated")
        if resume_version is not None:
            # A fresh attempt resuming an existing (incomplete) version must
            # inherit THAT version's mode, never re-resolve from current
            # settings — the same demo/live lineage rule refinement
            # enforces. This re-confirms the pre-lock preview above against
            # the freshly locked query; the only way it could legitimately
            # differ is a version appearing between the preview and the
            # lock, in which case THIS freshly queried value is
            # authoritative for the attempt actually created below. This
            # override does NOT re-check availability for the (possibly
            # different) resulting mode — that is safe only because step 4's
            # GenerationInProgress check above already rejects the sole
            # interleaving that could make this value differ from the
            # pre-lock preview (a concurrent in-progress attempt for the
            # same design); a future change must not let this override run
            # for a mode whose readiness was never confirmed.
            is_demo = resume_version.is_demo

        # 5b. Global daily count (Phase 16 Part B). Reserved atomically here —
        #     AFTER every rejection check passes and using the FINAL is_demo, so
        #     only a genuinely new LIVE attempt consumes a slot (demo never
        #     counts, no rejected request counts, and an idempotent replay
        #     returned long before this line). A rejected reservation raises
        #     CountLimitReached and rolls back this short transaction (nothing
        #     was created); a ledger outage fails closed. The Redis reservation
        #     is NOT covered by this transaction's rollback, so if a later DB
        #     statement raises (e.g. the (design, idempotency_key) UniqueConstraint
        #     backstop, or a transient DB error) the reservation is compensated in
        #     the except below — a rolled-back enqueue is a definite pre-provider
        #     failure. The bounded in-lock Redis call is an accepted trade-off
        #     (per-design scope) for reserving atomically with the creation
        #     decision. The slot is also returned by _mark_queue_unavailable if
        #     the post-commit broker submit fails.
        _count_reserved = require_availability and not is_demo
        if _count_reserved:
            cost_control.reserve_count(
                cost_control.count_reservation_id(locked.id, idempotency_key)
            )
        try:
            # 6. Create the queued attempt (resuming an incomplete version if any).
            attempt = GenerationAttempt.objects.create(
                design=locked,
                design_version=resume_version,
                idempotency_key=idempotency_key,
                status=_Status.QUEUED,
                is_demo=is_demo,
            )
            attempt.celery_task_id = str(attempt.id)
            attempt.save(update_fields=["celery_task_id", "updated_at"])

            # 7. Move the Design into the generating state.
            locked.status = Design.Status.GENERATING
            locked.save(update_fields=["status", "updated_at"])

            outcome["attempt"] = attempt
            transaction.on_commit(_on_commit)
        except Exception:
            # The DB side rolls back with the transaction; compensate the Redis
            # count reservation so a rolled-back enqueue never orphans a slot.
            if _count_reserved:
                _release_live_count(locked.id, idempotency_key)
            raise

    return outcome["attempt"], True


# ---------------------------------------------------------------------------
# Refinement enqueue service (Phase 14)
# ---------------------------------------------------------------------------


def enqueue_design_refinement(
    design,
    *,
    source_version_id,
    refinement_request: RefinementRequest,
    idempotency_key,
    enqueue_task=None,
    require_availability=True,
):
    """Create or idempotently replay one queued REFINEMENT attempt for
    ``design``.

    Mirrors :func:`enqueue_design_generation`'s shape and guarantees exactly
    (idempotent replay first, availability gate, one short transaction under
    the Design row lock, Celery submission on commit) but validates the
    refinement-specific preconditions: the Design must be ``generated`` OR
    ``generation_failed`` from a resolved prior refinement failure (never
    from an initial-generation failure — there is no version 1 to refine in
    that case, so the source-version lookup below fails closed), the source
    version must belong to this Design and pass
    :func:`~sitara.generation.refinement_service.validate_source_version`,
    no child version may already exist, and no other attempt for this Design
    may be in progress or carry unresolved provider-spend evidence.

    ``refinement_request`` must already be validated (Part A's
    ``normalise_refinement_request``) — this function performs no client-input
    validation itself.

    Returns ``(attempt, created)``. Raises GenerationUnavailable /
    DesignNotRefinable / RefinementSourceUnavailable / GenerationInProgress /
    RefinementLimitReached during the transaction (no attempt is created), or
    QueueUnavailable if the broker rejects the task after commit."""
    submit = enqueue_task or _submit_to_celery
    outcome: dict = {"attempt": None}

    def _on_commit():
        attempt = outcome["attempt"]
        try:
            submit(attempt)
        except Exception as exc:
            logger.warning(
                "refinement enqueue broker failure attempt=%s design=%s exception_type=%s",
                attempt.id,
                attempt.design_id,
                type(exc).__name__,
            )
            _mark_queue_unavailable(attempt)
            raise QueueUnavailable("the generation queue is temporarily unavailable") from exc

    # Idempotent replay: an unlocked read is safe here — the transaction
    # below re-checks it before ever writing.
    existing = GenerationAttempt.objects.filter(
        design=design, idempotency_key=idempotency_key
    ).first()
    if existing is not None:
        return existing, False

    # A refinement's mode is INHERITED from its source version, never
    # independently resolved from current settings, so the readiness check
    # must be for that specific mode, not whatever DEMO_MODE would currently
    # pick. Availability I/O never runs while holding the Design row lock —
    # resolved here from an unlocked preview of the source version, before
    # the transaction opens; the transaction below re-fetches and fully
    # validates the source version under the lock and is the sole authority
    # for the value actually persisted.
    is_demo = False
    if require_availability:
        preview_source = DesignVersion.objects.filter(design=design, pk=source_version_id).first()
        if preview_source is not None:
            is_demo = preview_source.is_demo
        if is_demo:
            if not demo_generation_is_available():
                raise GenerationUnavailable("generation is not currently available")
        elif not generation_is_available():
            raise GenerationUnavailable("generation is not currently available")

    with transaction.atomic():
        locked = Design.objects.select_for_update().get(pk=design.pk)

        # 1. Idempotent replay, re-checked inside the lock: a concurrent
        #    request could have committed between the read above and here.
        existing = GenerationAttempt.objects.filter(
            design=locked, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing, False

        # 2. Generation mode and availability were already resolved above,
        #    before acquiring the row lock, from a preview of the source
        #    version — step 5b below re-confirms the mode against the
        #    freshly locked query.

        # 3. Reject if another attempt is already in progress (Design-scoped,
        #    same constraint the initial pipeline relies on). Checked BEFORE
        #    the status gate below: a Design sitting in GENERATING because of
        #    that in-progress attempt must surface as "in progress", not the
        #    coarser "not refinable".
        if GenerationAttempt.objects.filter(
            design=locked, status__in=GenerationAttempt.IN_PROGRESS_STATUSES
        ).exists():
            raise GenerationInProgress("a generation job is already in progress for this design")

        # 4. A fully generated Design may be refined. A Design left in
        #    generation_failed by a RESOLVED refinement failure (no unresolved
        #    spend, no staged-but-unlinked image — checked below) is also
        #    eligible: the failure never restores status to generated (there
        #    is no new version to finalise), so gating on GENERATED alone
        #    would permanently block every retry after any refinement
        #    failure, however clean. A generation_failed Design with no
        #    version 1 at all (an initial-generation failure) is still
        #    correctly rejected below: source_version_id cannot resolve to
        #    any row, raising RefinementSourceUnavailable.
        if locked.status not in (Design.Status.GENERATED, Design.Status.GENERATION_FAILED):
            raise DesignNotRefinable("this design cannot be refined")

        # 5. The source version must belong to this Design and pass every
        #    structural/safety pre-spend check.
        source_version = DesignVersion.objects.filter(design=locked, pk=source_version_id).first()
        if source_version is None:
            raise RefinementSourceUnavailable("the source version is not available")
        validate_source_version(source_version)  # raises RefinementSourceUnavailable

        # 5b. A refinement's mode is INHERITED from its source version — a
        #     demo source can never be refined through the live path and a
        #     live source can never be refined through the demo path,
        #     regardless of the CURRENT DEMO_MODE setting. Re-confirms the
        #     pre-lock preview above against the freshly locked query; this
        #     does NOT re-check availability for a (possibly different)
        #     resulting mode — that is safe only because step 3's
        #     GenerationInProgress check above already rejects the sole
        #     interleaving that could make this value differ from the
        #     pre-lock preview (a concurrent in-progress attempt for the
        #     same design); a future change must not let this override run
        #     for a mode whose readiness was never confirmed.
        is_demo = source_version.is_demo

        # 6. A completed refinement, a staged-but-unresolved refinement
        #    attempt, or an attempt with unresolved provider-spend evidence
        #    all block a fresh refinement the same way DesignAlreadyGenerated
        #    blocks a fresh initial generation (spec §20: "no ambiguous text/
        #    image submission marker exists; no recoverable staged or
        #    permanent output exists").
        staged_elsewhere = (
            GenerationAttempt.objects.filter(
                design=locked, generation_kind=GenerationAttempt.GenerationKind.REFINEMENT
            )
            .exclude(staged_image_storage_key="")
            .exclude(status=_Status.FAILED, error_code=errors.IMAGE_STAGING_FAILED)
            .exists()
        )
        unresolved_spend = (
            GenerationAttempt.objects.filter(
                design=locked,
                generation_kind=GenerationAttempt.GenerationKind.REFINEMENT,
                status=_Status.FAILED,
            )
            .exclude(error_code__in=_SPEND_RESOLVED_CODES)
            .filter(
                Q(image_submission_in_flight=True)
                | ~Q(image_prediction_id="")
                | Q(text_submission_in_flight=True)
            )
            .exists()
        )
        if source_version.refined_versions.exists() or staged_elsewhere or unresolved_spend:
            raise RefinementLimitReached("this design has already been refined")

        # 6b. Global daily count (Phase 16 Part B) — a refinement is a new live
        #     billable attempt too. Reserved atomically after every rejection
        #     check, using the FINAL is_demo (demo never counts). The Redis
        #     reservation is compensated in the except below if any later DB
        #     statement rolls this transaction back. See the initial-generation
        #     enqueue for the full rationale.
        _count_reserved = require_availability and not is_demo
        if _count_reserved:
            cost_control.reserve_count(
                cost_control.count_reservation_id(locked.id, idempotency_key)
            )
        try:
            # 7. Create the queued refinement attempt, carrying its own durable
            #    copy of the canonical refinement request (the child DesignVersion
            #    does not exist yet to carry Part A's copy).
            refinement_request_hash = refinement_request_sha256(refinement_request)
            attempt = GenerationAttempt.objects.create(
                design=locked,
                idempotency_key=idempotency_key,
                status=_Status.QUEUED,
                generation_kind=GenerationAttempt.GenerationKind.REFINEMENT,
                source_design_version=source_version,
                refinement_request=refinement_request.model_dump(mode="json"),
                refinement_request_schema_version=REFINEMENT_REQUEST_SCHEMA_VERSION,
                refinement_request_sha256=refinement_request_hash,
                is_demo=is_demo,
            )
            attempt.celery_task_id = str(attempt.id)
            attempt.save(update_fields=["celery_task_id", "updated_at"])

            # 8. Move the Design into the generating state.
            locked.status = Design.Status.GENERATING
            locked.save(update_fields=["status", "updated_at"])

            outcome["attempt"] = attempt
            transaction.on_commit(_on_commit)
        except Exception:
            if _count_reserved:
                _release_live_count(locked.id, idempotency_key)
            raise

    return outcome["attempt"], True


# ---------------------------------------------------------------------------
# Attempt execution lock
# ---------------------------------------------------------------------------


def _attempt_lock_keys(attempt_id) -> tuple[int, int]:
    """Two signed 32-bit keys from the attempt UUID's FIRST EIGHT bytes for
    the two-int advisory-lock space (64 bits of entropy). A single 32-bit key
    under a fixed namespace would make attempts sharing their first four UUID
    bytes contend and be mistaken for duplicate deliveries. The Design spec
    lock uses the single-bigint form, which PostgreSQL tags with a different
    lock ``objsubid`` (1) than the two-int form used here (2) — the two key
    spaces do not overlap; no other two-int locks exist in this codebase."""
    return (
        int.from_bytes(attempt_id.bytes[:4], "big", signed=True),
        int.from_bytes(attempt_id.bytes[4:8], "big", signed=True),
    )


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
    key_high, key_low = _attempt_lock_keys(attempt_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [key_high, key_low])
        acquired = bool(cursor.fetchone()[0])
        try:
            yield acquired
        finally:
            if acquired:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [key_high, key_low])


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
    final_storage=None,
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
                final_storage,
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
            _finalise_failure(attempt, exc.code, clear_text_marker=exc.clear_text_marker)
            return GenerationAttempt.objects.get(pk=attempt.pk)
        except cost_control.BudgetExhausted:
            # A pre-spend reservation would have exceeded the hard daily ceiling
            # (text stage). No provider call ran; the submission marker (if any)
            # is preserved as evidence of an EARLIER billable call in this attempt.
            logger.info("generation budget exhausted at provider boundary attempt=%s", attempt.id)
            _finalise_failure(attempt, errors.LIVE_GENERATION_BUDGET_EXHAUSTED)
            return GenerationAttempt.objects.get(pk=attempt.pk)
        except cost_control.BudgetLedgerUnavailable:
            # The budget ledger could not be reached — fail closed; no provider
            # call ran. Never echo the ledger exception content.
            logger.warning("generation budget ledger unavailable attempt=%s", attempt.id)
            _finalise_failure(attempt, errors.INTERNAL_GENERATION_ERROR)
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
    attempt,
    structured_provider,
    image_provider,
    image_downloader,
    storage,
    final_storage,
    seed_factory,
    config,
):
    # A demo attempt executes with the demo pipeline config (empty model so
    # the persisted image_model is the demo provider's own honest identity,
    # trivial poll bounds) regardless of what the caller passed — branching
    # purely on the FROZEN attempt.is_demo flag, never on live settings, so
    # a later settings change can never affect an already-queued attempt.
    if attempt.is_demo:
        config = _demo_pipeline_config(config)

    # Stage A — claim and pre-check. The completeness re-check applies only to
    # INITIAL generation (it validates questionnaire/inspiration readiness,
    # which is irrelevant once a design is already generated and refinement
    # edits an existing DesignSpec instead); a refinement's own freshness
    # re-check happens inside generate_refined_design_spec_for_design.
    _set_started(attempt)
    design = Design.objects.get(pk=attempt.design_id)
    is_refinement = attempt.generation_kind == GenerationAttempt.GenerationKind.REFINEMENT
    if not is_refinement and design_completion_errors(design):
        raise _TerminalGenerationError(errors.DESIGN_CHANGED)

    attempt.refresh_from_db()

    # Stage B — DesignSpec (only when no version is linked yet).
    if attempt.design_version_id is None:
        _set_status(attempt, _Status.RUNNING_TEXT)
        if is_refinement:
            version = _run_refinement_text_stage(design, attempt, structured_provider, config)
        else:
            version = _run_text_stage(design, attempt, structured_provider, config)
    else:
        version = DesignVersion.objects.get(pk=attempt.design_version_id)
        if version.design_id != attempt.design_id:
            raise _TerminalGenerationError(errors.DESIGN_CHANGED)

    # Stage C — deterministic image prompt (idempotent).
    version = _run_prompt_stage(version)

    # Stage D — image submission, polling, download and raw staging. Skipped
    # entirely on a redelivery whose version already carries COMPLETE
    # permanent-image provenance: no Anthropic, no prompt rebuild, no
    # prediction create/poll, no download, no reprocessing — stage E verifies
    # the final objects and the attempt finalises.
    if version.has_permanent_image:
        _set_status(attempt, _Status.RUNNING_IMAGE)
        attempt.refresh_from_db()
    else:
        _run_image_stage(
            attempt, version, image_provider, image_downloader, storage, seed_factory, config
        )

    # Stage E — canonical permanent ingest (original + thumbnail verified in
    # private storage). The attempt is never marked succeeded, and the Design
    # never marked generated, before this completes.
    _run_ingest_stage(attempt, storage, final_storage)

    # Stage F — success.
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


def _resolve_structured_provider_impl(attempt, demo_factory):
    """Shared structured-design provider resolution: ``demo_factory()`` for a
    demo attempt, or the live gated Anthropic provider otherwise. Branches
    purely on the FROZEN ``attempt.is_demo``, never on live settings. Used by
    both the initial-generation and refinement resolution points below — the
    only difference between them is how the demo provider is built."""
    if attempt.is_demo:
        return demo_factory()
    from sitara.ai_gateway.policy import get_structured_design_generation_provider

    return get_structured_design_generation_provider()


def _resolve_structured_provider(design, attempt):
    """The initial-generation structured-design provider: the local
    deterministic demo adapter (built from a freshly re-validated
    GenerationContext — never a parsed prompt) for a demo attempt, or the
    live gated Anthropic provider otherwise."""
    return _resolve_structured_provider_impl(
        attempt, lambda: DemoStructuredDesignProvider(context=build_generation_context(design))
    )


def _run_text_stage(design, attempt, structured_provider, config) -> DesignVersion:
    # Crash-window guard (mirrors ``_ensure_prediction``): a prior delivery
    # began a text submission (marker set) but never linked a version — the
    # provider MAY have accepted and billed the request, and the private
    # prompt content must never be resent automatically. Fail conservatively;
    # the marker is submission evidence for the enqueue guard. The marker
    # itself is persisted inside ``services._generate_valid_spec`` immediately
    # BEFORE each paid request, so pre-call validation failures (unsafe user
    # text, lock contention, readiness) can never leave it set.
    if attempt.text_submission_in_flight:
        raise _TerminalGenerationError(errors.STRUCTURED_SUBMISSION_AMBIGUOUS)

    if attempt.is_demo:
        _demo_delay(config)

    try:
        provider = (
            structured_provider
            if structured_provider is not None
            else _resolve_structured_provider(design, attempt)
        )
        version = generate_design_spec_for_design(design, provider=provider, attempt=attempt)
    except GenerationRefused as exc:
        # The provider ANSWERED (a refusal): spend resolved; the marker is
        # cleared atomically with the terminal write.
        raise _TerminalGenerationError(
            errors.STRUCTURED_PROVIDER_REFUSED, clear_text_marker=True
        ) from exc
    except DesignChangedDuringGeneration as exc:
        # Domain outcome; any request concluded.
        raise _TerminalGenerationError(errors.DESIGN_CHANGED, clear_text_marker=True) from exc
    except DesignNotReady as exc:
        # Raised by domain validation strictly BEFORE the marker is set (the
        # marker lives immediately before provider.generate); clearing is a
        # harmless no-op kept for symmetry. A concurrent path having already
        # generated a version is a "changed" condition, not incompleteness.
        code = (
            errors.DESIGN_CHANGED
            if getattr(exc, "code", None) == "already_generated"
            else errors.DESIGN_INCOMPLETE
        )
        raise _TerminalGenerationError(code, clear_text_marker=True) from exc
    except (GenerationFailed, ProviderIdentityChanged) as exc:
        # Responses were RECEIVED; spend resolved.
        raise _TerminalGenerationError(
            errors.STRUCTURED_GENERATION_FAILED, clear_text_marker=True
        ) from exc
    except StructuredDesignProviderError as exc:
        # A classified Anthropic transport/API failure is a KNOWN structured
        # generation failure, never an unclassified internal error. Ambiguity
        # is decided by the GATEWAY (it knows the SDK's exception semantics;
        # the default is ambiguous — fail closed): an ambiguous acceptance
        # keeps the marker set and terminalises as ambiguous IMMEDIATELY (the
        # same taxonomy as the image side); a definitive answer resolves the
        # spend question, clearing the marker atomically with the terminal
        # write, and keeps the recovery path open.
        if exc.ambiguous_acceptance:
            raise _TerminalGenerationError(errors.STRUCTURED_SUBMISSION_AMBIGUOUS) from exc
        raise _TerminalGenerationError(
            errors.STRUCTURED_GENERATION_FAILED, clear_text_marker=True
        ) from exc
    # Success: the marker was cleared atomically with the version linkage in
    # services._finalise_atomic; nothing further to write here.
    attempt.refresh_from_db()
    return version


def _resolve_refinement_structured_provider(source_version, refinement_request, attempt):
    """The refinement structured-design provider: the local deterministic
    demo adapter (built from the persisted source spec — never a parsed
    prompt) for a demo attempt, or the live gated Anthropic provider
    otherwise."""
    return _resolve_structured_provider_impl(
        attempt,
        lambda: DemoRefinementStructuredDesignProvider(
            source_spec=source_version.design_spec, refinement_request=refinement_request
        ),
    )


def _run_refinement_text_stage(design, attempt, structured_provider, config) -> DesignVersion:
    """Stage B for a REFINEMENT attempt (Phase 14) — mirrors
    :func:`_run_text_stage`'s crash-window guard and error taxonomy exactly,
    but calls :func:`~sitara.generation.refinement_service.generate_refined_design_spec_for_design`
    against the persisted source version and the attempt's own durable copy
    of the canonical refinement request, never the raw questionnaire."""
    if attempt.text_submission_in_flight:
        raise _TerminalGenerationError(errors.STRUCTURED_SUBMISSION_AMBIGUOUS)

    source_version = attempt.source_design_version
    if source_version is None or attempt.refinement_request is None:
        # Defence in depth: the enqueue guard never creates a refinement
        # attempt without both. Unreachable in practice.
        raise _TerminalGenerationError(errors.REFINEMENT_SOURCE_UNAVAILABLE)
    try:
        refinement_request = RefinementRequest.model_validate(attempt.refinement_request)
    except Exception as exc:  # noqa: BLE001 - corrupt persisted JSON is a safety boundary
        raise _TerminalGenerationError(errors.REFINEMENT_SOURCE_UNAVAILABLE) from exc

    if attempt.is_demo:
        _demo_delay(config)

    try:
        provider = (
            structured_provider
            if structured_provider is not None
            else _resolve_refinement_structured_provider(
                source_version, refinement_request, attempt
            )
        )
        version = generate_refined_design_spec_for_design(
            design, source_version, refinement_request, provider=provider, attempt=attempt
        )
    except GenerationRefused as exc:
        raise _TerminalGenerationError(
            errors.STRUCTURED_PROVIDER_REFUSED, clear_text_marker=True
        ) from exc
    except DesignChangedDuringRefinement as exc:
        raise _TerminalGenerationError(errors.DESIGN_CHANGED, clear_text_marker=True) from exc
    except RefinementSourceUnavailable as exc:
        raise _TerminalGenerationError(
            errors.REFINEMENT_SOURCE_UNAVAILABLE, clear_text_marker=True
        ) from exc
    except RefinementLimitReached as exc:
        raise _TerminalGenerationError(
            errors.REFINEMENT_LIMIT_REACHED, clear_text_marker=True
        ) from exc
    except RefinementNoChangeProduced as exc:
        raise _TerminalGenerationError(errors.REFINEMENT_NO_CHANGE, clear_text_marker=True) from exc
    except (RefinementGenerationFailed, ProviderIdentityChanged) as exc:
        raise _TerminalGenerationError(
            errors.REFINEMENT_GENERATION_FAILED, clear_text_marker=True
        ) from exc
    except StructuredDesignProviderError as exc:
        if exc.ambiguous_acceptance:
            raise _TerminalGenerationError(errors.STRUCTURED_SUBMISSION_AMBIGUOUS) from exc
        raise _TerminalGenerationError(
            errors.REFINEMENT_GENERATION_FAILED, clear_text_marker=True
        ) from exc
    attempt.refresh_from_db()
    return version


def _run_prompt_stage(version: DesignVersion) -> DesignVersion:
    # An existing prompt is immutable audit data and is reused AS-IS (spec §12:
    # "existing image prompt means skip prompt persistence"; §20: the provider
    # receives the exact persisted DesignVersion.image_prompt). It is never
    # rebuilt — rebuilding under a newer builder version would either be a
    # no-op or an immutability conflict, and neither may touch the audit trail.
    if version.image_prompt:
        return version
    try:
        return build_and_store_image_prompt(version)
    except ImagePromptImmutable as exc:
        # Unreachable now (we only build when no prompt exists), kept as a
        # fail-closed defence: an immutability conflict is a terminal build
        # failure, never a licence to submit stale or divergent prompt data.
        raise _TerminalGenerationError(errors.PROMPT_BUILD_FAILED) from exc
    except ImagePromptBuildError as exc:
        raise _TerminalGenerationError(errors.PROMPT_BUILD_FAILED) from exc


# The verified image formats a staged object may carry, in probe order.
_STAGED_EXTENSIONS = ("webp", "png", "jpg")


def _staged_key(attempt_id, extension: str) -> str:
    return f"generation-staging/{attempt_id}/raw.{extension}"


def _read_staged_object(store, key: str, config) -> bytes:
    """Bounded read of a staged object.

    A TRANSIENT storage failure (connection blip, backend restart) is a
    bounded :class:`GenerationRetry` — the staged object is durable and no
    spend is at risk, so retrying is unambiguously safe and a blip must never
    permanently strand the attempt. The retry (and therefore the code the task
    persists on retry EXHAUSTION) uses ``image_staging_unverified``: the
    content state is unknown, not confirmed bad, so the enqueue guard keeps
    blocking regeneration for it. Terminal ``image_staging_failed`` is
    reserved for CONFIRMED bad content (empty or over the byte cap)."""
    try:
        with store.open(key, "rb") as handle:
            data = handle.read(config.raw_max_bytes + 1)
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - backend-specific transport errors
        raise GenerationRetry(errors.IMAGE_STAGING_UNVERIFIED) from exc
    if not data or len(data) > config.raw_max_bytes:
        raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED)
    return bytes(data)


def _staged_object_exists(store, key: str) -> bool:
    """Existence probe with the same transient-vs-terminal discipline as
    :func:`_read_staged_object` (a transport failure is a bounded retry with
    the unverified code, never a confirmed staging failure)."""
    try:
        return bool(store.exists(key))
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - backend-specific transport errors
        raise GenerationRetry(errors.IMAGE_STAGING_UNVERIFIED) from exc


def _verify_persisted_staging(store, attempt, config) -> None:
    """Metadata says the image is staged — verify the object really exists and
    matches the recorded SHA-256 before the attempt may finalise as succeeded.
    A CONFIRMED missing or divergent object fails safely (never a false
    success); a transient storage failure retries via ``_staged_object_exists``
    / ``_read_staged_object``."""
    key = attempt.staged_image_storage_key
    if not _staged_object_exists(store, key):
        raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED)
    data = _read_staged_object(store, key, config)
    if hashlib.sha256(data).hexdigest() != attempt.staged_image_sha256:
        raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED)


def _recover_staged_object(store, attempt, config) -> bool:
    """Recover a staged object whose metadata transaction never committed.

    A crash between ``store.save`` and ``_persist_staged`` leaves a fully
    staged private object with no metadata. The deterministic per-attempt key
    is written only by this pipeline (server-generated UUID path, private
    bucket), so a verified image found there IS this attempt's paid output —
    recover it instead of re-downloading (the provider's temporary URL may
    have expired) or resubmitting. Returns True when recovered."""
    for extension in _STAGED_EXTENSIONS:
        key = _staged_key(attempt.id, extension)
        if not _staged_object_exists(store, key):
            continue
        data = _read_staged_object(store, key, config)
        try:
            verified_extension, width, height = _verify_image(data, config)
        except _TerminalGenerationError as exc:
            # A non-image or bounds-violating object at OUR deterministic key
            # is a staging-integrity failure (conflicting content), not bad
            # provider output — fail safely, never finalise on it.
            raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED) from exc
        if verified_extension != extension:
            # The object's real format contradicts its key — conflicting
            # content; never finalise on it.
            raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED)
        sha256 = hashlib.sha256(data).hexdigest()
        _persist_staged(attempt, key, sha256, len(data), width, height)
        attempt.refresh_from_db()
        return True
    return False


def _select_demo_asset_for_attempt(attempt, version) -> tuple[DemoAssetSelection, DemoManifest]:
    """Resolve this attempt's demo asset selection, reusing a persisted
    selection on resume rather than reselecting (spec §31). Raises
    :class:`_TerminalGenerationError` with ``demo_assets_unavailable`` for
    every failure mode — a missing/invalid active manifest, a missing
    persisted spec, or no compatible asset — never exposing which internal
    object or path failed."""
    try:
        manifest = load_active_demo_manifest()
    except DemoAssetsUnavailable as exc:
        raise _TerminalGenerationError(errors.DEMO_ASSETS_UNAVAILABLE) from exc

    if attempt.demo_selection:
        stored = attempt.demo_selection
        return (
            DemoAssetSelection(
                asset_id=stored["asset_id"],
                manifest_hash=stored["manifest_hash"],
                manifest_schema_version=stored["manifest_schema_version"],
                selector_version=stored["selector_version"],
            ),
            manifest,
        )

    try:
        spec = validate_design_spec(version.design_spec)
    except (ValidationError, UnsupportedDesignSpecVersion) as exc:
        raise _TerminalGenerationError(errors.DEMO_ASSETS_UNAVAILABLE) from exc
    try:
        selection = select_demo_asset(spec, version.image_prompt, manifest)
    except DemoAssetUnavailable as exc:
        raise _TerminalGenerationError(errors.DEMO_ASSETS_UNAVAILABLE) from exc

    GenerationAttempt.objects.filter(pk=attempt.pk).update(
        demo_selection={
            "asset_id": selection.asset_id,
            "manifest_hash": selection.manifest_hash,
            "manifest_schema_version": selection.manifest_schema_version,
            "selector_version": selection.selector_version,
        },
        updated_at=timezone.now(),
    )
    attempt.demo_selection = {
        "asset_id": selection.asset_id,
        "manifest_hash": selection.manifest_hash,
        "manifest_schema_version": selection.manifest_schema_version,
        "selector_version": selection.selector_version,
    }
    return selection, manifest


def _demo_seed_factory(version, selection: DemoAssetSelection):
    """A deterministic non-negative seed from the persisted image prompt,
    manifest hash and selector version only — never a Design/attempt UUID,
    user identity, the current time or process randomness (spec §25)."""

    def factory() -> int:
        payload = f"{version.image_prompt}:{selection.manifest_hash}:{selection.selector_version}"
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    return factory


def _run_image_stage(
    attempt, version, image_provider, image_downloader, storage, seed_factory, config
) -> None:
    store = storage if storage is not None else default_storage

    _set_status(attempt, _Status.RUNNING_IMAGE)
    attempt.refresh_from_db()
    if attempt.is_demo:
        _demo_delay(config)

    # Resume with persisted metadata: VERIFY the staged object still exists and
    # matches before finalising — never a false success on missing/corrupt data.
    if attempt.staged_image_storage_key:
        _verify_persisted_staging(store, attempt, config)
        return

    # Recovery: the object may have been staged just before a crash that lost
    # the metadata write. Probe the deterministic keys BEFORE any provider
    # operation so already-paid output is never lost to an expired URL. The
    # probe deliberately runs even when a prediction id is already persisted:
    # the save-then-crash window occurs AFTER submission (id set, object
    # staged, metadata lost), so skipping the probe there would re-download —
    # and lose the output when the provider's temporary URL has expired. A few
    # existence checks per redelivery are cheap next to that loss.
    if _recover_staged_object(store, attempt, config):
        return

    # Provider/downloader resolution is DEFERRED until a provider operation is
    # actually required: the verify/recover paths above must finalise already-
    # paid output even when the live gates are closed (a closed gate would
    # otherwise fail the resume terminally before touching storage). Branches
    # purely on the FROZEN attempt.is_demo, never on live settings.
    demo_selection = None
    if image_provider is not None:
        provider = image_provider
    elif attempt.is_demo:
        demo_selection, demo_manifest = _select_demo_asset_for_attempt(attempt, version)
        provider = DemoImageProvider(selection=demo_selection, manifest=demo_manifest)
    else:
        provider = _live_image_provider(config)

    if image_downloader is not None:
        downloader = image_downloader
    elif attempt.is_demo:
        downloader = demo_image_downloader
    else:
        downloader = _live_image_downloader(config)

    effective_seed_factory = seed_factory
    if effective_seed_factory is None and attempt.is_demo and demo_selection is not None:
        effective_seed_factory = _demo_seed_factory(version, demo_selection)

    prediction_id = _ensure_prediction(attempt, version, provider, effective_seed_factory, config)
    prediction = _poll(provider, prediction_id, config)

    if prediction.status == PREDICTION_FAILED:
        raise _TerminalGenerationError(errors.IMAGE_PREDICTION_FAILED)
    if prediction.status == PREDICTION_CANCELED:
        raise _TerminalGenerationError(errors.IMAGE_PREDICTION_CANCELED)
    if prediction.status == PREDICTION_ABORTED:
        raise _TerminalGenerationError(errors.IMAGE_PREDICTION_ABORTED)
    if prediction.status != PREDICTION_SUCCEEDED:
        # Provider status is untrusted external data: an unknown, empty or
        # novel state proves NOTHING about the accepted prediction's fate, so
        # it must never land on a spend-RESOLVED code (which would readmit a
        # second billed submission). internal_generation_error keeps the
        # enqueue guard blocking — the persisted prediction id is submission
        # evidence — until the state is understood.
        raise _TerminalGenerationError(errors.INTERNAL_GENERATION_ERROR)

    output_url = prediction.output_url
    if not output_url:
        raise _TerminalGenerationError(errors.IMAGE_OUTPUT_INVALID)

    data = _download(downloader, output_url, config)
    extension, width, height = _verify_image(data, config)
    sha256 = hashlib.sha256(data).hexdigest()
    key = _stage_raw_image(store, attempt.id, extension, data, sha256, config)
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
    # choose one the first time so a restart never produces a second seed.
    seed = attempt.image_seed
    seed_reused = attempt.seed_reused
    if seed is None:
        # Phase 14: a refinement attempt tries to copy the succeeded initial
        # attempt's seed for the SAME source version first (a continuity aid
        # only — never a guarantee); falls back to a fresh seed exactly like
        # initial generation when none is available.
        if attempt.generation_kind == GenerationAttempt.GenerationKind.REFINEMENT:
            copied = _find_source_attempt_seed(attempt)
            if copied is not None:
                seed = copied
                seed_reused = True
        if seed is None:
            seed = int(seed_factory()) if seed_factory is not None else _generate_seed()
            seed_reused = False
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
    # Cost control (Phase 16, Part A): reserve the conservative maximum image-call
    # cost BEFORE the in-flight marker and the provider create call. A rejected or
    # unavailable reservation fails closed — the provider is never invoked. Demo
    # attempts never reach the ledger. The reservation identity is deterministic
    # (attempt + image_submission stage + pricing profile), so a bounded retry of
    # a pre-acceptance failure re-reserves cleanly and a redelivery never
    # double-reserves.
    cost_on = cost_accounting.cost_enabled(attempt)
    profile = cost_control.active_pricing_profile()
    if cost_on:
        try:
            cost_accounting.reserve(
                attempt,
                cost_control.STAGE_IMAGE_SUBMISSION,
                cost_control.replicate_call_max_micro_usd(profile),
                profile,
            )
        except cost_control.BudgetExhausted as exc:
            raise _TerminalGenerationError(errors.LIVE_GENERATION_BUDGET_EXHAUSTED) from exc
        except cost_control.BudgetLedgerUnavailable as exc:
            raise _TerminalGenerationError(errors.INTERNAL_GENERATION_ERROR) from exc
    # Persist seed + parameters + provider/model AND set the in-flight marker
    # BEFORE the provider call, all in one transaction.
    with transaction.atomic():
        locked = GenerationAttempt.objects.select_for_update().get(pk=attempt.pk)
        locked.image_provider = provider.name
        locked.image_model = model
        locked.image_seed = seed
        locked.seed_reused = seed_reused
        locked.image_parameters = parameters
        locked.image_submission_in_flight = True
        locked.save(
            update_fields=[
                "image_provider",
                "image_model",
                "image_seed",
                "seed_reused",
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
            # The provider may already have accepted (and billed) the request;
            # never resubmit and RETAIN the full reservation as unresolved spend.
            if cost_on:
                cost_accounting.retain(attempt, cost_control.STAGE_IMAGE_SUBMISSION, profile)
            raise _TerminalGenerationError(errors.IMAGE_SUBMISSION_AMBIGUOUS) from exc
        # Definitely pre-acceptance transient failure: nothing was accepted, so
        # RELEASE the reservation, clear the marker and allow a bounded retry to
        # resubmit (same seed, a fresh re-reservation).
        if cost_on:
            cost_accounting.release(attempt, cost_control.STAGE_IMAGE_SUBMISSION, profile)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            image_submission_in_flight=False, updated_at=timezone.now()
        )
        attempt.image_submission_in_flight = False
        raise GenerationRetry(errors.IMAGE_PROVIDER_UNAVAILABLE) from exc

    if not prediction.prediction_id or len(prediction.prediction_id) > 128:
        # Defence in depth (the provider adapter already rejects this): an
        # accepted create with no persistable id can never be reconciled —
        # resolve conservatively as ambiguous and retain; the marker stays set.
        if cost_on:
            cost_accounting.retain(attempt, cost_control.STAGE_IMAGE_SUBMISSION, profile)
        raise _TerminalGenerationError(errors.IMAGE_SUBMISSION_AMBIGUOUS)

    # Persist the accepted prediction id and clear the marker together FIRST —
    # this is the authoritative evidence of a real, already-billed provider
    # submission and the only handle to poll/cancel it, so it must be durably
    # recorded before the non-authoritative best-effort cost audit runs. Never
    # clear or replace the id afterwards.
    GenerationAttempt.objects.filter(pk=attempt.pk).update(
        image_prediction_id=prediction.prediction_id,
        image_submission_in_flight=False,
        updated_at=timezone.now(),
    )
    attempt.image_prediction_id = prediction.prediction_id
    attempt.image_submission_in_flight = False
    # An accepted prediction is a DEFINITE billable submission. Reconcile the
    # reservation to the configured conservative maximum as the estimated actual
    # (Replicate exposes no trustworthy per-call billing through the safe
    # boundary) — never released later merely because polling/download/staging/
    # ingest fails. This best-effort audit fold runs AFTER the prediction id is
    # durably saved and can never break the pipeline (its DB write is guarded).
    if cost_on:
        cost_accounting.reconcile_fixed(
            attempt,
            cost_control.STAGE_IMAGE_SUBMISSION,
            profile,
            cost_control.replicate_call_max_micro_usd(profile),
        )
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
    # Timed out: best-effort cancellation, then a terminal timeout. A worker
    # interruption during the cancel call stays retryable (redelivery polls the
    # same prediction again); every OTHER cancel failure is absorbed — the
    # timeout classification does not depend on the cancel outcome.
    try:
        provider.cancel_prediction(prediction_id)
    except SoftTimeLimitExceeded:
        raise
    except Exception:  # noqa: BLE001 - best-effort cancellation only
        pass
    raise _TerminalGenerationError(errors.IMAGE_POLL_TIMEOUT)


def _download(downloader, output_url, config) -> bytes:
    try:
        data = downloader(output_url)
    except _TerminalGenerationError:
        raise
    except SoftTimeLimitExceeded:
        # A worker interruption mid-download is NOT a terminal download
        # failure — propagate so the top-level handler retries; the persisted
        # prediction id lets the redelivery re-download the same output.
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


def _stage_raw_image(store, attempt_id, extension: str, data: bytes, sha256: str, config) -> str:
    """Copy verified raw output into private storage at a deterministic key.

    On task restart the deterministic object may already exist: a byte-identical
    object (same SHA-256) resumes finalisation without another provider call; a
    DIFFERENT object at the same key fails safely rather than being overwritten.
    ``file_overwrite=False`` storage never silently replaces a distinct object.

    Storage I/O here follows the shared transient-vs-confirmed discipline
    (:func:`_staged_object_exists` / :func:`_read_staged_object`): the bytes
    being staged belong to ALREADY-PAID provider output, so a transport
    failure is a bounded ``image_staging_unverified`` retry — the redelivery
    re-obtains the output via the recovery probe (completed save) or the
    persisted prediction id (no save) — while terminal
    ``image_staging_failed`` is reserved for CONFIRMED conflicts (a
    hash-divergent existing object; a key-renaming backend)."""
    key = _staged_key(attempt_id, extension)
    if _staged_object_exists(store, key):
        existing_bytes = _read_staged_object(store, key, config)
        if hashlib.sha256(existing_bytes).hexdigest() == sha256:
            return key  # identical object already staged — resume
        raise _TerminalGenerationError(errors.IMAGE_STAGING_FAILED)
    try:
        saved_key = store.save(key, ContentFile(data))
    except SoftTimeLimitExceeded:
        # A worker interruption mid-save is retryable: the redelivery either
        # finds the completed object (byte-identical resume) or re-saves it.
        raise
    except Exception as exc:  # noqa: BLE001 - transport error: whether the
        # object was persisted is unknown. Retrying is safe — the redelivery's
        # probe finds a completed save (byte-identical resume) or re-saves.
        raise GenerationRetry(errors.IMAGE_STAGING_UNVERIFIED) from exc
    if saved_key != key:
        # A non-overwriting backend renamed around an existing object — treat as
        # a staging conflict rather than accept a mismatched key. This is the
        # backend's AUTHORITATIVE answer (a completed save), not a transport
        # failure, so it stays terminal/confirmed. Cleanup is best-effort, but
        # a worker interruption still propagates (retryable).
        try:
            store.delete(saved_key)
        except SoftTimeLimitExceeded:
            raise
        except Exception:  # noqa: BLE001 - best-effort cleanup only
            pass
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


def _run_ingest_stage(attempt, storage, final_storage) -> None:
    """Stage E — canonical permanent ingest (Phase 11).

    Delegates to the crash-safe :func:`ingest_staged_design_image` service
    (idempotent, provider-free under every path) and maps its safe exceptions
    onto the pipeline taxonomy: a transient/unknown storage outcome becomes a
    bounded ``image_ingest_unverified`` retry that reruns ONLY verification/
    ingest; confirmed corrupt/conflicting permanent content (including an
    immutability conflict) terminalises as ``image_ingest_failed``. Neither
    ever causes an image resubmission — the staged metadata these attempts
    keep carrying blocks the enqueue guard."""
    attempt.refresh_from_db()
    # Resolve the staging storage exactly like stage D does (the module-level
    # default_storage reference), so an injected/patched staging double is
    # honoured by BOTH stages and the two can never read different sources.
    staging_store = storage if storage is not None else default_storage
    try:
        ingest_staged_design_image(
            attempt,
            staging_storage=staging_store,
            final_storage=final_storage,
        )
    except DesignImageIngestRetry as exc:
        raise GenerationRetry(errors.IMAGE_INGEST_UNVERIFIED) from exc
    except (DesignImageIngestFailed, DesignImageImmutable) as exc:
        raise _TerminalGenerationError(errors.IMAGE_INGEST_FAILED) from exc


def finalise_ingest_recovery(attempt_id) -> GenerationAttempt | None:
    """Complete a FAILED ingest-stage attempt after an operator-driven ingest.

    Used ONLY by the ``ingest_design_image`` management command once the
    ingest service has verified complete permanent provenance: an attempt that
    terminally failed at stage E (``image_ingest_failed`` /
    ``image_ingest_unverified``) is completed as succeeded and its Design
    marked generated — zero provider calls, nothing else touched. Any other
    status/code combination is left unchanged (in-progress attempts belong to
    the worker; other terminal codes are not ingest-stage outcomes). Returns
    the refreshed attempt, or None when it does not exist."""
    with transaction.atomic():
        locked = GenerationAttempt.objects.select_for_update().filter(pk=attempt_id).first()
        if locked is None:
            return None
        if locked.status == _Status.FAILED and locked.error_code in errors.INGEST_STAGE_ERROR_CODES:
            _mark_attempt_succeeded(locked)
    return GenerationAttempt.objects.get(pk=attempt_id)


def _mark_attempt_succeeded(locked: GenerationAttempt) -> None:
    """The SINGLE success transition: the locked attempt becomes succeeded
    (error cleared, completion stamped) and its Design becomes generated.
    Every success path — normal finalisation and operator ingest recovery —
    goes through here so the transition can never drift between callers.
    Callers hold the attempt row lock and have applied their own guard."""
    locked.status = _Status.SUCCEEDED
    locked.error_code = ""
    locked.completed_at = timezone.now()
    locked.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
    Design.objects.filter(pk=locked.design_id).update(
        status=Design.Status.GENERATED, updated_at=timezone.now()
    )
    # No reservation is left dangling once the attempt is terminal.
    cost_accounting.mark_complete(locked)


def _finalise_success(attempt) -> None:
    with transaction.atomic():
        locked = GenerationAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status == _Status.SUCCEEDED:
            return
        _mark_attempt_succeeded(locked)


def _retain_unresolved_submissions(
    attempt, *, image_unresolved: bool, text_unresolved: bool
) -> None:
    """Record any still-in-flight provider submission as unresolved (assume-spent)
    audit cost when an attempt terminalises. A submission marker still set at
    finalisation means the provider MAY have accepted and billed the request but
    its outcome was never resolved — the crash-window redelivery guards, the
    stuck-job reaper (``reconcile_if_stuck``) and an ambiguous-acceptance terminal
    all reach here with the marker set. ``cost_accounting.retain`` transitions the
    reservation from ``reserved`` and folds ``cost_estimated``/``cost_unresolved``
    so the attempt's audit columns reflect the possible spend for incident
    reconciliation (ADR 0017), instead of looking like a clean pre-spend release.

    Idempotent and conservative: retaining a stage that the in-stage path already
    reconciled/retained, or whose reservation expired, is a no-op (the ledger
    returns ``already``/``missing`` and nothing is folded). The exact in-flight
    structured stage (initial vs retry) is not recorded on the single marker, so
    both candidate stages of the relevant family are retained — only the
    genuinely-``reserved`` one transitions.

    A swallowed retain (ledger outage) durably clears the attempt's
    ``cost_accounting_settled`` flag via the accounting bridge, so completion is
    not later falsely claimed. The reservation is reconciled against the
    pricing-profile version FROZEN on the attempt at reserve time
    (``cost_pricing_profile_version``), NOT the currently active one: a profile
    rotation between reserve and this terminal retain would otherwise derive a
    different reservation id and silently miss the reservation."""
    if not cost_accounting.cost_enabled(attempt) or not (image_unresolved or text_unresolved):
        return
    profile = cost_control.active_pricing_profile()
    frozen_version = attempt.cost_pricing_profile_version
    if frozen_version:
        profile = dataclasses.replace(profile, version=frozen_version)
    if image_unresolved:
        cost_accounting.retain(attempt, cost_control.STAGE_IMAGE_SUBMISSION, profile)
    if text_unresolved:
        if attempt.source_design_version_id is not None:
            stages = (
                cost_control.STAGE_STRUCTURED_REFINEMENT_INITIAL,
                cost_control.STAGE_STRUCTURED_REFINEMENT_RETRY,
            )
        else:
            stages = (cost_control.STAGE_STRUCTURED_INITIAL, cost_control.STAGE_STRUCTURED_RETRY)
        for stage in stages:
            cost_accounting.retain(attempt, stage, profile)


def _finalise_failure(attempt, code: str, *, clear_text_marker: bool = False) -> None:
    with transaction.atomic():
        locked = GenerationAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status in (_Status.SUCCEEDED, _Status.FAILED):
            return
        # A submission marker still set at terminalisation is possible unresolved
        # spend. Capture BEFORE optionally clearing the text marker below (a
        # cleared text marker means the provider definitively answered and the
        # in-stage path already reconciled that reservation to its actual).
        image_unresolved = locked.image_submission_in_flight
        text_unresolved = locked.text_submission_in_flight and not clear_text_marker
        locked.status = _Status.FAILED
        locked.error_code = code
        locked.completed_at = timezone.now()
        update_fields = ["status", "error_code", "completed_at", "updated_at"]
        if clear_text_marker:
            # A definitively-answered text submission clears its marker IN
            # THIS SAME atomic write — a crash can never land between a
            # cleared marker and the terminal state.
            locked.text_submission_in_flight = False
            update_fields.append("text_submission_in_flight")
        locked.save(update_fields=update_fields)
        # Preserve any linked DesignVersion, prompt, prediction id and staged
        # data — never delete or rewrite newer design work.
        Design.objects.filter(pk=locked.design_id).update(
            status=Design.Status.GENERATION_FAILED, updated_at=timezone.now()
        )
        # Record any still-in-flight submission as unresolved (assume-spent) audit
        # cost, then mark no reservation left dangling. Retain is idempotent, so a
        # reservation already reconciled/retained/released in-stage is untouched.
        # DELIBERATELY inside the terminal transaction (unlike the enqueue-time
        # ledger calls, which run outside any lock): folding the audit cost
        # atomically with the FAILED transition avoids a split-brain window where
        # a crash between two transactions could durably fail an attempt whose
        # ambiguous spend is never recorded. The ledger call is bounded by
        # LIVE_GENERATION_BUDGET_REDIS_TIMEOUT_SECONDS and its failure is swallowed
        # (the reservation stays conservatively counted), so a Redis outage only
        # briefly extends the row-lock hold and never blocks terminalisation.
        _retain_unresolved_submissions(
            locked, image_unresolved=image_unresolved, text_unresolved=text_unresolved
        )
        # Claim accounting complete only when every reconcile/retain/release for
        # this attempt settled at the ledger (mark_complete reads the durable
        # cost_accounting_settled flag, cleared by any swallowed ledger op).
        cost_accounting.mark_complete(locked)


def fail_attempt(attempt_id, code: str) -> None:
    """Mark an attempt terminally failed with a stable code (used by the task
    when a bounded transient retry is exhausted). No-op if already terminal."""
    if not errors.is_valid_error_code(code):
        code = errors.INTERNAL_GENERATION_ERROR
    attempt = GenerationAttempt.objects.filter(pk=attempt_id).first()
    if attempt is not None:
        _finalise_failure(attempt, code)


def reconcile_if_stuck(attempt_id, cutoff) -> str:
    """Reconcile ONE possibly-stuck attempt (Phase 16, Part C stuck-job reaper).

    Uses the SAME non-blocking attempt advisory lock the pipeline execution
    holds, so an attempt a live worker is actively running is never touched
    (returns "skipped"). Under the lock it re-reads the attempt: only one still
    in a non-terminal state AND still idle since ``cutoff`` (its ``updated_at``
    did not advance after the caller's query) is marked failed with the stable
    ``generation_stuck`` code — via the shared ``_finalise_failure``, which
    preserves every submission marker, prediction id, staged/permanent output
    and the (unresolved) cost reservation, moves the Design to
    ``generation_failed`` and NEVER enqueues replacement paid work.

    Returns "reconciled", "skipped" (lock held by an active worker) or
    "progressed" (no longer stuck / already terminal / gone)."""
    if not isinstance(attempt_id, uuid.UUID):
        attempt_id = uuid.UUID(str(attempt_id))
    with _attempt_advisory_lock(attempt_id) as acquired:
        if not acquired:
            return "skipped"
        fresh = GenerationAttempt.objects.filter(pk=attempt_id).first()
        if fresh is None or fresh.status not in GenerationAttempt.IN_PROGRESS_STATUSES:
            return "progressed"
        if fresh.updated_at > cutoff:
            # Progressed since the batch query — a live worker is advancing it.
            return "progressed"
        _finalise_failure(fresh, errors.GENERATION_STUCK)
        # Release the global daily count slot ONLY when durable state proves no
        # provider call could have occurred (no submission marker, no accepted
        # prediction id, no staged output) — otherwise the reservation is kept,
        # conservative, because the attempt may already have entered provider
        # processing. The cost reservation itself is always retained: it is made
        # inside the pipeline only after a marker is set, so a provably-pre-
        # provider attempt has none to release.
        if not fresh.is_demo and _provably_no_provider_call(fresh):
            _release_live_count(fresh.design_id, fresh.idempotency_key)
        return "reconciled"


def _provably_no_provider_call(attempt) -> bool:
    """True only when durable attempt state proves no provider request was ever
    made: no text/image submission-in-flight marker, no accepted prediction id
    and no staged output. Each provider call persists its marker BEFORE the
    request, so their combined absence is conclusive."""
    return (
        not attempt.text_submission_in_flight
        and not attempt.image_submission_in_flight
        and not attempt.image_prediction_id
        and not attempt.staged_image_storage_key
    )


def _generate_seed() -> int:
    """A cryptographically-generated non-negative 32-bit seed (zero allowed)."""
    return secrets.randbelow(2**32)


def _find_source_attempt_seed(attempt: GenerationAttempt) -> int | None:
    """The persisted, non-negative seed of the SUCCEEDED initial attempt that
    produced ``attempt``'s source version, or ``None`` when unavailable
    (Phase 14 §18).

    Deliberately never derived from user data, an id, prompt text or a hash —
    only ever copied verbatim from a prior attempt's own securely-generated
    seed, matching the SAME Design as ``attempt``."""
    source_version_id = attempt.source_design_version_id
    if source_version_id is None:
        return None
    succeeded = (
        GenerationAttempt.objects.filter(
            design_id=attempt.design_id,
            design_version_id=source_version_id,
            generation_kind=GenerationAttempt.GenerationKind.INITIAL,
            status=_Status.SUCCEEDED,
        )
        .exclude(image_seed__isnull=True)
        .order_by("-created_at", "-id")
        .first()
    )
    return succeeded.image_seed if succeeded is not None else None


# ---------------------------------------------------------------------------
# Live provider factories (Phase 10 Part B): the gated Replicate provider and
# the hardened output downloader. Both fail closed — if the paid-image gate is
# not open they map to a terminal image_provider_unavailable rather than
# constructing any network client.
# ---------------------------------------------------------------------------


def _live_image_provider(config):
    # ``config`` is accepted only for call-site symmetry with
    # ``_live_image_downloader`` (which does read it); the model flows into the
    # request separately via ``_ensure_prediction``'s ``config.model``.
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
