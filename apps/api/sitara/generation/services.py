"""DesignSpec generation orchestration (Phase 8).

The single service that turns a complete Design into one persisted, validated
DesignVersion. It performs EVERY pre-spend validation first, acquires a
non-blocking PostgreSQL advisory lock keyed by the Design UUID before any
provider call, makes at most two controlled provider requests, re-validates
the output through Pydantic and business checks, and persists exactly one
DesignVersion only after a valid result exists.

On any failure nothing is persisted, the Design and answers are unchanged, and
logs carry only the operation, Design UUID, attempt number and exception type
— never a prompt, answer, output, key or provider error body.
"""

import contextlib
import json
import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from pydantic import ValidationError

from sitara.ai_gateway.policy import get_structured_design_generation_provider
from sitara.ai_gateway.structured_design import StructuredDesignRequest
from sitara.designs.models import Design
from sitara.designs.services import create_next_design_version, design_completion_errors

from .context import DesignNotReady, GenerationContext, build_generation_context
from .design_spec import DESIGN_SPEC_SCHEMA_VERSION, SPEC_TEMPLATE_VERSION, DesignSpec
from .input_safety import GeneratedContentRejected
from .prompting import SYSTEM_PROMPT, build_user_message

logger = logging.getLogger(__name__)

MAX_PROVIDER_REQUESTS = 2


class GenerationLocked(Exception):
    """Another spec generation holds the Design's advisory lock. Safe message."""


class GenerationRefused(Exception):
    """The provider refused. No retry, nothing persisted. Safe message."""


class GenerationFailed(Exception):
    """The output was invalid after the allowed attempts. Safe message; carries
    the number of provider requests actually made."""

    def __init__(self, attempts: int):
        self.attempts = attempts
        super().__init__("structured design generation produced no valid output")


class SourceSelectionMismatch(Exception):
    """The generated source_selections did not match the trusted input."""


class DesignChangedDuringGeneration(Exception):
    """The Design's inputs changed between the pre-spend snapshot and
    persistence (a concurrent draft edit or an inspiration becoming
    ineligible). Nothing is persisted, the newer draft is left untouched, and
    the paid provider is NOT retried. Safe message."""


class ProviderIdentityChanged(Exception):
    """The provider or model identity differed across the two attempts, so the
    aggregated provenance would be incoherent. Nothing is persisted. Safe
    message (never carries a model value)."""


@dataclass(frozen=True)
class _AggregatedUsage:
    """Provider/model identity and TOTAL token usage across every returned
    response of one generation operation. A token total is None when ANY
    response lacked that dimension (never a misleading partial)."""

    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


def _aggregate_usage(responses: list) -> _AggregatedUsage:
    """Sum usage across all returned responses. Provider/model identity must be
    consistent; a missing dimension on any response yields None for that
    total."""
    first = responses[0]
    input_total = 0
    output_total = 0
    input_known = True
    output_known = True
    for response in responses:
        if response.provider != first.provider or response.model != first.model:
            raise ProviderIdentityChanged("provider identity changed across attempts")
        if response.input_tokens is None:
            input_known = False
        else:
            input_total += response.input_tokens
        if response.output_tokens is None:
            output_known = False
        else:
            output_total += response.output_tokens
    return _AggregatedUsage(
        provider=first.provider,
        model=first.model,
        # A known-but-zero total would violate the positive-token DB constraint;
        # treat it as absent rather than persist a misleading 0.
        input_tokens=input_total if (input_known and input_total > 0) else None,
        output_tokens=output_total if (output_known and output_total > 0) else None,
    )


def _input_snapshot(design: Design) -> tuple:
    """A deterministic fingerprint of the generation inputs: questionnaire
    version id, normalised persisted answers and ordered selected inspiration
    ids. Compared before persistence to reject a spec built from stale
    inputs."""
    answers = design.answers or {}
    inspiration_ids = list(
        design.inspiration_selections.order_by("position").values_list(
            "inspiration_asset_id", flat=True
        )
    )
    return (
        str(design.questionnaire_version_id),
        json.dumps(answers, sort_keys=True, ensure_ascii=False),
        [str(asset_id) for asset_id in inspiration_ids],
    )


def _lock_key(design_id) -> int:
    # A stable signed 64-bit advisory-lock key from the Design UUID.
    return int.from_bytes(design_id.bytes[:8], "big", signed=True)


@contextlib.contextmanager
def _advisory_lock(design_id):
    """Non-blocking session advisory lock keyed by the Design UUID. Raises
    GenerationLocked if another generation already holds it; always released."""
    key = _lock_key(design_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]
        if not acquired:
            raise GenerationLocked("another spec generation is in progress for this design")
        try:
            yield
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def _assert_source_selections_match(spec: DesignSpec, canonical: dict) -> None:
    if spec.source_selections.model_dump() != canonical:
        raise SourceSelectionMismatch("generated source_selections did not match the input")


def _validate_output(payload: dict, context: GenerationContext) -> DesignSpec:
    """Fresh Django-side revalidation + business checks. Raises on any failure
    (all treated as retryable by the caller)."""
    spec = DesignSpec.model_validate(payload)
    scan_design_spec_or_raise(spec)
    _assert_source_selections_match(spec, context.source_selections)
    return spec


def scan_design_spec_or_raise(spec: DesignSpec) -> None:
    # Imported here to keep the safety module free of Django/domain imports.
    from .input_safety import scan_design_spec

    scan_design_spec(spec)


def _generate_valid_spec(provider, context: GenerationContext, design_id):
    """Make at most MAX_PROVIDER_REQUESTS controlled requests. Returns
    (spec, usage, attempts) where ``usage`` aggregates token counts across
    EVERY returned response (both attempts). A provider transport error or
    refusal aborts immediately (no retry)."""
    responses: list = []  # every StructuredDesignResult actually returned
    attempts = 0
    for attempt in range(1, MAX_PROVIDER_REQUESTS + 1):
        attempts += 1
        request = StructuredDesignRequest(
            system_prompt=SYSTEM_PROMPT,
            user_message=build_user_message(context, retry=attempt > 1),
            source_selections=context.source_selections,
            max_output_tokens=settings.DESIGN_SPEC_MAX_OUTPUT_TOKENS,
            attempt=attempt,
        )
        result = provider.generate(request)  # StructuredDesignProviderError propagates
        responses.append(result)
        if result.refused:
            logger.warning(
                "design spec generation refused design=%s attempt=%s", design_id, attempt
            )
            raise GenerationRefused("the provider refused to generate a specification")
        if result.payload is not None:
            try:
                spec = _validate_output(result.payload, context)
            except (
                ValidationError,
                GeneratedContentRejected,
                SourceSelectionMismatch,
            ) as exc:
                logger.warning(
                    "design spec output rejected design=%s attempt=%s exception_type=%s",
                    design_id,
                    attempt,
                    type(exc).__name__,
                )
            else:
                # Aggregate usage over every response consumed so far (an
                # invalid first attempt still spent tokens).
                return spec, _aggregate_usage(responses), attempts
    raise GenerationFailed(attempts)


def _persist(design, spec: DesignSpec, usage: _AggregatedUsage) -> object:
    with transaction.atomic():
        version = create_next_design_version(design)
        version.design_spec = spec.model_dump(mode="json")
        version.design_spec_schema_version = DESIGN_SPEC_SCHEMA_VERSION
        version.design_spec_template_version = SPEC_TEMPLATE_VERSION
        version.design_spec_provider = usage.provider
        version.design_spec_model = usage.model
        version.design_spec_input_tokens = usage.input_tokens
        version.design_spec_output_tokens = usage.output_tokens
        version.design_spec_generated_at = timezone.now()
        version.save()
    return version


def generate_design_spec_for_design(design, *, provider=None):
    """Generate, validate and persist one DesignVersion for ``design``.

    ``provider`` may be injected (fixtures/fakes in tests and the offline
    command); when omitted the gated live Anthropic provider is selected —
    only after every gate passes. Raises DesignNotReady / GenerationLocked /
    GenerationRefused / GenerationFailed / StructuredDesignProviderError on
    failure, persisting nothing."""
    # Every pre-spend validation FIRST (before any provider selection/call).
    context = build_generation_context(design)
    # Snapshot the exact inputs the context was built from, to detect a
    # concurrent draft edit while the (un-transacted) provider call is running.
    input_snapshot = _input_snapshot(design)

    # The advisory lock is a SESSION-level lock, deliberately NOT a row lock or
    # an open transaction — no database transaction is held across the network
    # request.
    with _advisory_lock(design.id):
        # Close the race: another holder may have generated between the
        # pre-check and acquiring the lock.
        if design.versions.exists():
            raise DesignNotReady(
                "already_generated", "This design already has a generated version."
            )
        selected = provider if provider is not None else get_structured_design_generation_provider()
        spec, usage, attempts = _generate_valid_spec(selected, context, design.id)

        # Freshness re-check AFTER a valid result but BEFORE persisting: re-fetch
        # the Design, rerun completion + inspiration-eligibility validation, and
        # compare the recomputed snapshot. Any change means the draft moved on
        # during the call — persist nothing and never touch the newer draft.
        fresh = Design.objects.get(pk=design.pk)
        if design_completion_errors(fresh) or _input_snapshot(fresh) != input_snapshot:
            logger.warning("design spec discarded (inputs changed) design=%s", design.id)
            raise DesignChangedDuringGeneration(
                "the design changed during generation; no version was created"
            )
        version = _persist(fresh, spec, usage)
    logger.info(
        "design spec generated design=%s version=%s attempts=%s provider=%s",
        design.id,
        version.version_number,
        attempts,
        usage.provider,
    )
    # Transient (not persisted) — the management command reports it. Deliberately
    # NOT "generation_attempts" (that is the GenerationAttempt reverse relation).
    version.spec_generation_attempts = attempts
    return version
