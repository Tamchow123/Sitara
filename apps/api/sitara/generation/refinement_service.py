"""Constrained DesignSpec refinement orchestration (Phase 14).

The single service that turns an existing, validated version-1 DesignVersion
plus one validated :class:`~sitara.generation.refinement.RefinementRequest`
into one persisted, validated version-2 DesignVersion. Mirrors
:mod:`sitara.generation.services`'s structure closely (every pre-spend
validation first, the same Design-scoped advisory lock, at most two
controlled provider requests, exact-diff + safety revalidation of the
output, and atomic persistence only after a valid result exists) but is a
DELIBERATELY SEPARATE module: a refinement is not "another initial
generation" — its trusted context is the existing DesignSpec, not the raw
questionnaire, and its allowed edit surface is a narrow, category-specific
allowlist rather than an open specification.

On any failure nothing is persisted, the source version is unchanged, and
logs carry only the operation, Design UUID, attempt number and exception
type — never a DesignSpec, a note, an output or a provider error body."""

import logging
from dataclasses import dataclass
from enum import Enum

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import ValidationError

from sitara.ai_gateway.policy import get_structured_design_generation_provider
from sitara.ai_gateway.structured_design import (
    StructuredDesignProviderError,
    StructuredDesignRequest,
)
from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.designs.services import DesignVersionLimitReached, create_next_design_version_locked

from . import cost_accounting, cost_control
from .design_spec import (
    SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS,
    DesignSpec,
    UnsupportedDesignSpecVersion,
    validate_design_spec,
)
from .input_safety import GeneratedContentRejected, contains_phrase, iter_strings
from .inspiration_context import InspirationContextSnapshot, inspiration_context_sha256
from .refinement import (
    REFINEMENT_ALLOWED_PATHS,
    REFINEMENT_IMMUTABLE_ROOTS,
    REFINEMENT_REQUEST_SCHEMA_VERSION,
    RefinementRequest,
    diff_design_spec_paths,
    path_is_allowed,
    refinement_request_sha256,
)
from .refinement_prompting import (
    REFINEMENT_SYSTEM_PROMPT,
    REFINEMENT_TEMPLATE_VERSION,
    build_refinement_user_message,
)
from .services import (
    AggregatedUsage,
    GenerationLocked,
    GenerationRefused,
    ProviderIdentityChanged,
    advisory_lock,
    aggregate_usage,
    scan_design_spec_or_raise,
)

logger = logging.getLogger(__name__)

MAX_REFINEMENT_PROVIDER_REQUESTS = 2

# Deterministic cost-reservation stage per Anthropic request number for a
# REFINEMENT attempt. The controlled validation retry is a distinct billable call.
_REFINEMENT_STRUCTURED_STAGES = {
    1: cost_control.STAGE_STRUCTURED_REFINEMENT_INITIAL,
    2: cost_control.STAGE_STRUCTURED_REFINEMENT_RETRY,
}

# Namespaced so the persisted DesignVersion.design_spec_template_version can
# never be confused with an initial-generation SPEC_TEMPLATE_VERSION value —
# see the module docstring and ADR 0015 for the exact convention.
REFINEMENT_DESIGN_SPEC_TEMPLATE_VERSION = f"refinement-{REFINEMENT_TEMPLATE_VERSION}"

# Phrases indicating the model described the refinement PROCESS itself
# (rather than just producing a clean, self-contained specification) —
# rejected the same way every other safety denylist in this codebase is
# checked (token-boundary contains_phrase, never a raw substring test).
_REFINEMENT_PROCESS_PHRASES = (
    "refined version",
    "previous version",
    "based on your request",
    "based on the request",
    "as requested",
    "per your request",
    "updated based on",
    "refinement request",
    "this refinement",
    "the refinement process",
    "after refinement",
)


class RefinementSourceUnavailable(Exception):
    """The source DesignVersion is not a valid, complete, refinable version 1.

    Safe message; never reveals the specific structural defect."""


class RefinementLimitReached(Exception):
    """This design already has a refined (version 2) DesignVersion, or the
    application-level MAX_DESIGN_VERSIONS ceiling is already reached."""


class RefinementGenerationFailed(Exception):
    """The refined output was invalid after the allowed attempts for a
    reason other than "no change was produced". Safe message; carries the
    number of provider requests actually made."""

    def __init__(self, attempts: int):
        self.attempts = attempts
        super().__init__("refinement generation produced no valid output")


class RefinementNoChangeProduced(Exception):
    """Every attempt's output was identical to the source DesignSpec (or
    reverted to it) — no valid change was ever generated. Safe message;
    carries the number of provider requests actually made."""

    def __init__(self, attempts: int):
        self.attempts = attempts
        super().__init__("refinement produced no actual change")


class _NoChangeInAttempt(Exception):
    """Internal, single-attempt marker: this one attempt's output was
    identical to the source DesignSpec. Never carries an attempt count —
    the retry loop counts attempts itself and raises the public
    :class:`RefinementNoChangeProduced` only once every attempt is
    exhausted."""


class DesignChangedDuringRefinement(Exception):
    """The source version, the refinement request or its canonical hash
    changed between the pre-spend snapshot and persistence. Nothing is
    persisted, and the paid provider is NOT retried. Safe message."""


@dataclass(frozen=True)
class _SourceContext:
    spec: DesignSpec
    inspiration_context: object | None
    inspiration_context_schema_version: int | None
    inspiration_context_sha256: str


def validate_source_version(source_version: DesignVersion) -> _SourceContext:
    """Every pre-spend validation for the refinement SOURCE, strictly before
    any provider is selected.

    Raises :class:`RefinementSourceUnavailable` when the source is not
    exactly a complete, valid version-1 DesignVersion: wrong version number,
    missing/invalid DesignSpec, unsupported schema version, a failed safety
    scan, incomplete permanent-image provenance, or corrupt/unsupported
    persisted inspiration-context provenance. Never rebuilds inspiration
    metadata from the live catalogue — the persisted historical snapshot (or
    its absence, for a legacy version) is authoritative and is only
    integrity-checked here, never refreshed."""
    if source_version.version_number != 1:
        raise RefinementSourceUnavailable("only a version 1 design may be refined")
    if source_version.design_spec is None or source_version.design_spec_schema_version is None:
        raise RefinementSourceUnavailable("the source design has no generated specification")
    if source_version.design_spec_schema_version not in SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS:
        raise RefinementSourceUnavailable("the source specification schema is not supported")
    if not source_version.has_permanent_image:
        raise RefinementSourceUnavailable("the source design has no complete image yet")
    try:
        spec = validate_design_spec(source_version.design_spec)
    except (ValidationError, UnsupportedDesignSpecVersion):
        raise RefinementSourceUnavailable("the source specification failed validation") from None
    try:
        scan_design_spec_or_raise(spec)
    except GeneratedContentRejected:
        raise RefinementSourceUnavailable(
            "the source specification failed the safety scan"
        ) from None

    inspiration_context = None
    if source_version.inspiration_context is not None:
        try:
            inspiration_context = InspirationContextSnapshot.model_validate(
                source_version.inspiration_context
            )
        except ValidationError:
            raise RefinementSourceUnavailable(
                "the source inspiration context failed validation"
            ) from None
        if (
            inspiration_context_sha256(inspiration_context)
            != source_version.inspiration_context_sha256
        ):
            raise RefinementSourceUnavailable(
                "the source inspiration context failed hash verification"
            ) from None

    return _SourceContext(
        spec=spec,
        inspiration_context=source_version.inspiration_context,
        inspiration_context_schema_version=source_version.inspiration_context_schema_version,
        inspiration_context_sha256=source_version.inspiration_context_sha256,
    )


class RefinementOutputCategory(str, Enum):
    """Why one refinement attempt's output was rejected — refinement-specific
    categories, distinct from :class:`~sitara.content_safety.RejectionCategory`
    (which still applies unchanged via :func:`scan_design_spec_or_raise`)."""

    SOURCE_SELECTIONS_CHANGED = "source_selections_changed"
    IMMUTABLE_FIELD_CHANGED = "immutable_field_changed"
    DISALLOWED_FIELD_CHANGED = "disallowed_field_changed"
    PROCESS_MENTIONED = "refinement_process_mentioned"


class RefinementOutputRejected(Exception):
    """One refinement attempt's output failed a refinement-specific check.

    Carries only a generic :class:`RefinementOutputCategory` — never the
    offending text — so it is always safe to surface and log."""

    def __init__(self, category: RefinementOutputCategory):
        self.category = category
        super().__init__(f"refinement output rejected: {category.value}")


def _assert_source_selections_unchanged(spec: DesignSpec, canonical: dict) -> None:
    if spec.source_selections.model_dump() != canonical:
        raise RefinementOutputRejected(RefinementOutputCategory.SOURCE_SELECTIONS_CHANGED)


def _assert_no_refinement_process_leakage(spec: DesignSpec) -> None:
    """The refined output must never describe the refinement process itself
    — it must read as one complete, self-contained specification."""
    haystack = " ".join(iter_strings(spec.model_dump(mode="python")))
    if any(contains_phrase(haystack, phrase) for phrase in _REFINEMENT_PROCESS_PHRASES):
        raise RefinementOutputRejected(RefinementOutputCategory.PROCESS_MENTIONED)


def _validate_refined_output(
    payload: dict, source_spec: DesignSpec, change_type: str
) -> DesignSpec:
    """Fresh Django-side revalidation, exact-diff and safety checks. Raises
    on any failure (all treated as retryable by the caller, EXCEPT an empty
    diff, which is tracked separately so the caller can distinguish "no
    change produced" from every other invalid-output reason)."""
    spec = validate_design_spec(payload)
    # A refinement never changes the DesignSpec structure version — a mismatch
    # is treated the same as any other immutable change.
    if spec.schema_version != source_spec.schema_version:
        raise RefinementOutputRejected(RefinementOutputCategory.IMMUTABLE_FIELD_CHANGED)
    scan_design_spec_or_raise(spec)
    _assert_source_selections_unchanged(spec, source_spec.source_selections.model_dump())
    _assert_no_refinement_process_leakage(spec)

    original = source_spec.model_dump(mode="json")
    refined = spec.model_dump(mode="json")
    changed_paths = diff_design_spec_paths(original, refined)
    if not changed_paths:
        raise _NoChangeInAttempt()
    for path in changed_paths:
        root = path.split(".", 1)[0].split("[", 1)[0]
        if root in REFINEMENT_IMMUTABLE_ROOTS:
            raise RefinementOutputRejected(RefinementOutputCategory.IMMUTABLE_FIELD_CHANGED)
    allowed_roots = REFINEMENT_ALLOWED_PATHS[change_type]
    if any(not path_is_allowed(path, allowed_roots) for path in changed_paths):
        raise RefinementOutputRejected(RefinementOutputCategory.DISALLOWED_FIELD_CHANGED)
    return spec


def _generate_valid_refined_spec(
    provider,
    source_spec: DesignSpec,
    change_type: str,
    note: str,
    design_id,
    generation_attempt: GenerationAttempt | None = None,
):
    """Make at most :data:`MAX_REFINEMENT_PROVIDER_REQUESTS` controlled
    requests. Returns ``(spec, usage, attempts)``. A provider transport error
    or refusal aborts immediately (no retry). Raises
    :class:`RefinementNoChangeProduced` when every attempt's output was
    identical to the source, or :class:`RefinementGenerationFailed` for any
    other exhausted-retry reason."""
    responses: list = []
    attempts = 0
    no_change_only = True
    cost_on = cost_accounting.cost_enabled(generation_attempt)
    profile = cost_control.active_pricing_profile()
    for attempt in range(1, MAX_REFINEMENT_PROVIDER_REQUESTS + 1):
        attempts += 1
        request = StructuredDesignRequest(
            system_prompt=REFINEMENT_SYSTEM_PROMPT,
            user_message=build_refinement_user_message(
                source_spec.model_dump(mode="json"), change_type, note, retry=attempt > 1
            ),
            source_selections=source_spec.source_selections.model_dump(),
            max_output_tokens=settings.DESIGN_SPEC_MAX_OUTPUT_TOKENS,
            attempt=attempt,
            schema_version=source_spec.schema_version,
        )
        stage = _REFINEMENT_STRUCTURED_STAGES[attempt]
        # Reserve BEFORE the submission marker (spec Part A §6); a rejected or
        # unavailable reservation raises and no provider call runs.
        if cost_on:
            cost_accounting.reserve(
                generation_attempt,
                stage,
                cost_control.anthropic_call_max_micro_usd(profile, request.max_output_tokens),
                profile,
            )
        if generation_attempt is not None:
            GenerationAttempt.objects.filter(pk=generation_attempt.pk).update(
                text_submission_in_flight=True, updated_at=timezone.now()
            )
            generation_attempt.text_submission_in_flight = True
        try:
            result = provider.generate(request)  # StructuredDesignProviderError propagates
        except StructuredDesignProviderError as exc:
            if cost_on:
                if getattr(exc, "ambiguous_acceptance", False):
                    cost_accounting.retain(generation_attempt, stage, profile)
                else:
                    cost_accounting.release(generation_attempt, stage, profile)
            raise
        if cost_on:
            # Reconcile to reported usage ONLY when BOTH token counts are present:
            # a partial report (one dimension missing) would reconcile the missing
            # dimension as zero and refund that portion, undercounting spend. Any
            # missing dimension retains the full conservative reservation instead.
            if result.input_tokens is not None and result.output_tokens is not None:
                cost_accounting.reconcile_actual(
                    generation_attempt,
                    stage,
                    profile,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            else:
                cost_accounting.retain(generation_attempt, stage, profile)
        responses.append(result)
        if result.refused:
            logger.warning("design refinement refused design=%s attempt=%s", design_id, attempt)
            raise GenerationRefused("the provider refused to refine the specification")
        if result.payload is not None:
            try:
                spec = _validate_refined_output(result.payload, source_spec, change_type)
            except _NoChangeInAttempt:
                logger.warning(
                    "refinement output unchanged design=%s attempt=%s", design_id, attempt
                )
            except (
                ValidationError,
                UnsupportedDesignSpecVersion,
                GeneratedContentRejected,
                RefinementOutputRejected,
            ) as exc:
                no_change_only = False
                logger.warning(
                    "refinement output rejected design=%s attempt=%s exception_type=%s",
                    design_id,
                    attempt,
                    type(exc).__name__,
                )
            else:
                return spec, aggregate_usage(responses), attempts
    if no_change_only:
        raise RefinementNoChangeProduced(attempts)
    raise RefinementGenerationFailed(attempts)


def _finalise_refinement_atomic(
    design: Design,
    source_version: DesignVersion,
    spec: DesignSpec,
    usage: AggregatedUsage,
    source_context: _SourceContext,
    refinement_request: RefinementRequest,
    refinement_request_hash: str,
    attempt: GenerationAttempt | None = None,
) -> DesignVersion:
    """Re-check freshness and persist the refined DesignVersion in ONE
    transaction under the Design AND source-version row locks.

    The provider call has already completed (no transaction/lock is held
    across it). Locks the Design row, then the source DesignVersion row,
    re-verifies the source is still exactly what the pre-spend snapshot saw
    (same persisted DesignSpec, same inspiration-context hash, no child
    version yet) and that the canonical refinement request still matches —
    then creates version 2 fully populated in one INSERT (parent, refinement
    provenance, refined spec provenance and the SOURCE VERSION'S historical
    inspiration-context snapshot copied verbatim, never rebuilt from the
    live catalogue) and links the attempt in the SAME transaction."""
    with transaction.atomic():
        locked_design = Design.objects.select_for_update().get(pk=design.pk)
        locked_source = DesignVersion.objects.select_for_update().get(pk=source_version.pk)

        fresh_matches = (
            locked_source.design_id == locked_design.pk
            and locked_source.design_spec == source_context.spec.model_dump(mode="json")
            and locked_source.inspiration_context == source_context.inspiration_context
            and locked_source.inspiration_context_sha256
            == source_context.inspiration_context_sha256
            and not locked_source.refined_versions.exists()
            and refinement_request_sha256(refinement_request) == refinement_request_hash
        )
        if not fresh_matches:
            logger.warning("refinement discarded (source changed) design=%s", design.id)
            raise DesignChangedDuringRefinement(
                "the source design changed during refinement; no version was created"
            )

        try:
            version = create_next_design_version_locked(
                locked_design,
                parent_version=locked_source,
                refinement_request=refinement_request.model_dump(mode="json"),
                refinement_request_schema_version=REFINEMENT_REQUEST_SCHEMA_VERSION,
                refinement_request_sha256=refinement_request_hash,
            )
        except DesignVersionLimitReached:
            raise RefinementLimitReached(
                "this design has already reached its maximum number of versions"
            ) from None
        version.design_spec = spec.model_dump(mode="json")
        # The refined spec keeps the source's structure version (enforced above).
        version.design_spec_schema_version = spec.schema_version
        version.design_spec_template_version = REFINEMENT_DESIGN_SPEC_TEMPLATE_VERSION
        version.design_spec_provider = usage.provider
        version.design_spec_model = usage.model
        version.design_spec_input_tokens = usage.input_tokens
        version.design_spec_output_tokens = usage.output_tokens
        version.design_spec_generated_at = timezone.now()
        # A refinement ALWAYS inherits its source version's mode (Phase 15) —
        # never independently chosen — so a demo/live lineage can never mix.
        version.is_demo = locked_source.is_demo
        # The historical inspiration-context snapshot is COPIED verbatim from
        # the source version — never rebuilt from the live catalogue. A later
        # asset retirement, expiry or rights revocation must never rewrite an
        # already-generated concept's stored snapshot or acknowledgement.
        version.inspiration_context = locked_source.inspiration_context
        version.inspiration_context_schema_version = (
            locked_source.inspiration_context_schema_version
        )
        version.inspiration_context_sha256 = locked_source.inspiration_context_sha256
        version.save()
        if attempt is not None:
            if attempt.design_id != locked_design.pk:
                raise DesignChangedDuringRefinement(
                    "the attempt does not belong to this design; no version was linked"
                )
            attempt.design_version = version
            attempt.text_submission_in_flight = False
            attempt.save(
                update_fields=["design_version", "text_submission_in_flight", "updated_at"]
            )
    return version


def generate_refined_design_spec_for_design(
    design: Design,
    source_version: DesignVersion,
    refinement_request: RefinementRequest,
    *,
    provider=None,
    attempt: GenerationAttempt | None = None,
) -> DesignVersion:
    """Generate, validate and persist one refined (version 2) DesignVersion.

    ``provider`` may be injected (fixtures/fakes in tests); when omitted the
    gated live Anthropic provider is selected — only after every gate
    passes. ``attempt`` (Phase 14 pipeline) links the created DesignVersion
    to a GenerationAttempt atomically. Raises
    :class:`RefinementSourceUnavailable` / :class:`RefinementLimitReached` /
    :class:`GenerationRefused` / :class:`RefinementGenerationFailed` /
    :class:`RefinementNoChangeProduced` /
    :class:`~sitara.ai_gateway.structured_design.StructuredDesignProviderError`
    on failure, persisting nothing."""
    # Every pre-spend validation FIRST (before any provider selection/call).
    source_context = validate_source_version(source_version)
    if source_version.refined_versions.exists():
        raise RefinementLimitReached("this design has already been refined")

    refinement_request_hash = refinement_request_sha256(refinement_request)

    with advisory_lock(design.id):
        # Close the race: another holder may have refined between the
        # pre-check and acquiring the lock.
        if source_version.refined_versions.exists():
            raise RefinementLimitReached("this design has already been refined")
        selected = provider if provider is not None else get_structured_design_generation_provider()
        spec, usage, refine_attempts = _generate_valid_refined_spec(
            selected,
            source_context.spec,
            refinement_request.change_type,
            refinement_request.note,
            design.id,
            generation_attempt=attempt,
        )
        version = _finalise_refinement_atomic(
            design,
            source_version,
            spec,
            usage,
            source_context,
            refinement_request,
            refinement_request_hash,
            attempt=attempt,
        )
    logger.info(
        "design refined design=%s version=%s attempts=%s provider=%s",
        design.id,
        version.version_number,
        refine_attempts,
        usage.provider,
    )
    version.spec_generation_attempts = refine_attempts
    return version


__all__ = [
    "MAX_REFINEMENT_PROVIDER_REQUESTS",
    "REFINEMENT_DESIGN_SPEC_TEMPLATE_VERSION",
    "DesignChangedDuringRefinement",
    "GenerationLocked",
    "GenerationRefused",
    "ProviderIdentityChanged",
    "RefinementGenerationFailed",
    "RefinementLimitReached",
    "RefinementNoChangeProduced",
    "RefinementOutputCategory",
    "RefinementOutputRejected",
    "RefinementSourceUnavailable",
    "generate_refined_design_spec_for_design",
    "validate_source_version",
]
