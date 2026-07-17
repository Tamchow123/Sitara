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
import logging

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from pydantic import ValidationError

from sitara.ai_gateway.policy import get_structured_design_generation_provider
from sitara.ai_gateway.structured_design import StructuredDesignRequest
from sitara.designs.services import create_next_design_version

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
    (spec, result, attempts). A provider transport error or refusal aborts
    immediately (no retry)."""
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
                return spec, result, attempts
    raise GenerationFailed(attempts)


def _persist(design, spec: DesignSpec, result) -> object:
    with transaction.atomic():
        version = create_next_design_version(design)
        version.design_spec = spec.model_dump(mode="json")
        version.design_spec_schema_version = DESIGN_SPEC_SCHEMA_VERSION
        version.design_spec_template_version = SPEC_TEMPLATE_VERSION
        version.design_spec_provider = result.provider
        version.design_spec_model = result.model
        version.design_spec_input_tokens = result.input_tokens
        version.design_spec_output_tokens = result.output_tokens
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

    with _advisory_lock(design.id):
        # Close the race: another holder may have generated between the
        # pre-check and acquiring the lock.
        if design.versions.exists():
            raise DesignNotReady(
                "already_generated", "This design already has a generated version."
            )
        selected = provider if provider is not None else get_structured_design_generation_provider()
        spec, result, attempts = _generate_valid_spec(selected, context, design.id)
        version = _persist(design, spec, result)
    logger.info(
        "design spec generated design=%s version=%s attempts=%s provider=%s",
        design.id,
        version.version_number,
        attempts,
        result.provider,
    )
    # Transient (not persisted) — the management command reports it. Deliberately
    # NOT "generation_attempts" (that is the GenerationAttempt reverse relation).
    version.spec_generation_attempts = attempts
    return version
