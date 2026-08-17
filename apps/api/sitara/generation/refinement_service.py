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
from .prompt_builder import ImagePromptBuildError, build_image_prompt
from .refinement import (
    REFINEMENT_IMMUTABLE_ROOTS,
    REFINEMENT_IMMUTABLE_SELECTION_FIELDS,
    REFINEMENT_REQUEST_SCHEMA_VERSION,
    RefinementRequest,
    canonical_refinement_fields,
    changed_selection_field,
    diff_design_spec_paths,
    path_is_allowed,
    refinement_allowed_paths,
    refinement_request_sha256,
)
from .refinement_prompting import (
    REFINEMENT_SYSTEM_PROMPT,
    REFINEMENT_TEMPLATE_VERSION,
    RETRY_DISALLOWED_FIELD,
    RETRY_IMMUTABLE_FIELD,
    RETRY_INVALID_SELECTION_VALUE,
    RETRY_NO_CHANGE,
    RETRY_PROCESS_MENTIONED,
    RETRY_REASON_UNSPECIFIED,
    RETRY_SELECTION_OUT_OF_CATEGORY,
    build_refinement_user_message,
)
from .refinement_selections import (
    RefinedSelectionsInvalid,
    RefinementQuestionnaireUnavailable,
    answerable_selection_alternatives,
    assert_refined_selections_are_answerable,
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
    """The source DesignVersion is not a valid, complete, refinable version.

    A refinement is now refinable in turn, so "the source is the latest version
    of its design" replaced "the source is version 1" — the chain still may not
    branch, but it may be longer than two.

    Safe message; never reveals the specific structural defect."""


class RefinementLimitReached(Exception):
    """This design has already used its ``MAX_REFINEMENTS`` refinements, this
    particular version has already been refined, or the application-level
    MAX_DESIGN_VERSIONS ceiling is already reached."""


def refinements_used(design_id) -> int:
    """How many refinements this design has spent.

    Counted from the durable lineage — a version with a parent IS a refinement —
    rather than from an attempt count or a stored tally. A failed attempt
    persists no version and so costs the customer nothing, which is the
    behaviour a failed refinement must have: this phase has already made her pay
    for two attempts that saved nothing.

    Deliberately NOT `version_number - 1`: those agree today only because a
    lineage is a chain, and this counts the thing the rule is actually about."""
    return DesignVersion.objects.filter(design_id=design_id, parent_version__isnull=False).count()


def assert_refinement_budget_available(design_id) -> None:
    """Refuse once the design has spent every refinement it is allowed."""
    if refinements_used(design_id) >= settings.MAX_REFINEMENTS:
        raise RefinementLimitReached("this design has used all of its refinements")


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

    Raises :class:`RefinementSourceUnavailable` when the source is not a
    complete, valid, refinable DesignVersion: not the design's latest version,
    missing/invalid DesignSpec, unsupported schema version, a failed safety
    scan, incomplete permanent-image provenance, or corrupt/unsupported
    persisted inspiration-context provenance. Never rebuilds inspiration
    metadata from the live catalogue — the persisted historical snapshot (or
    its absence, for a legacy version) is authoritative and is only
    integrity-checked here, never refreshed.

    This used to demand ``version_number == 1``, which was the whole of the
    one-refinement rule. With three, a refinement's own output must be
    refinable in turn, so the check becomes "the LATEST version" instead: a
    lineage is a chain, never a tree. Refining an older version would branch it,
    and two children of one parent have equal claim to being "the" next
    concept — a question the version numbering, the result page and the
    `refined_versions` guard all have no answer for. The per-design budget is
    counted separately, in :func:`refinements_used`."""
    latest = (
        DesignVersion.objects.filter(design_id=source_version.design_id)
        .order_by("-version_number")
        .values_list("version_number", flat=True)
        .first()
    )
    if latest is not None and source_version.version_number != latest:
        raise RefinementSourceUnavailable("only the latest version of a design may be refined")
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

    # A refinement may now change a canonical selection, and that change is only
    # safe because it is revalidated against the design's own PINNED
    # questionnaire (ADR 0028). A design with no pinned version has nothing to
    # validate against, so it is refused here — before any provider is selected
    # — rather than accepted on trust. Pinned, not active: assign-once, and a
    # design pinned to a RETIRED version must stay refinable.
    questionnaire = source_version.design.questionnaire_version
    if questionnaire is None or not isinstance(questionnaire.schema, dict):
        raise RefinementSourceUnavailable("the source design has no usable questionnaire version")

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


# Every rejection category, mapped to the correction the retry carries. Total by
# construction — a KeyError here would be a missing decision, not a fallback, so
# a new category must choose its instruction rather than silently inherit the
# generic one. A test asserts the table stays total.
_REJECTION_RETRY_REASONS = {
    RefinementOutputCategory.SOURCE_SELECTIONS_CHANGED: RETRY_SELECTION_OUT_OF_CATEGORY,
    RefinementOutputCategory.IMMUTABLE_FIELD_CHANGED: RETRY_IMMUTABLE_FIELD,
    RefinementOutputCategory.DISALLOWED_FIELD_CHANGED: RETRY_DISALLOWED_FIELD,
    RefinementOutputCategory.PROCESS_MENTIONED: RETRY_PROCESS_MENTIONED,
}


def _rejection_detail(exc: Exception) -> str:
    """A safe machine-readable reason for a rejected refinement attempt.

    Read from the exception's own structured ATTRIBUTE, never from ``str(exc)``.
    The distinction is the whole point: `RefinementOutputRejected` and
    `GeneratedContentRejected` both park their category in the exception
    message as well, and a message is the one channel §15 forbids logging,
    because a future exception in the same ``except`` tuple may carry model
    output or user text in its own.

    Every value returned here is a source-controlled machine name — an enum
    value, or a sorted list of questionnaire question ids, which are machine
    names in the schema and never anything the customer wrote. The rejected
    VALUES are never carried by these exceptions and so cannot leak through.

    Falls back to the exception type alone for anything with no structured
    reason, which is what the whole handler used to do for all six types."""
    category = getattr(exc, "category", None)
    if category is not None:
        return f"{type(exc).__name__}:{getattr(category, 'value', category)}"
    fields = getattr(exc, "fields", None)
    if fields:
        return f"{type(exc).__name__}:{','.join(fields)}"
    return type(exc).__name__


def _retry_reason(exc: Exception) -> str:
    """Which correction instruction the single retry should carry.

    Never ``None``: that value means "first attempt, append nothing", and a
    rejection whose reason we choose not to name must still get the generic
    correction rather than silently going out uncorrected.

    An EXPLICIT table, deliberately not the duck-typed probe above: this value is
    sent TO THE PROVIDER, so an exception type opting itself in by having an
    attribute named ``category`` would be putting words in a paid request. Only
    the two types whose categories are our own closed enums are mapped.

    The four content-dependent failures — a failed safety scan
    (`GeneratedContentRejected`), an invalid shape, an unsupported schema
    version, an unrenderable prompt — get the unspecified reason on purpose.
    Telling a model "your text was refused by a safety check" invites it to word
    its way around the denylist on the one retry available, which is worse than a
    generic instruction to produce a fresh, compliant specification."""
    if isinstance(exc, RefinementOutputRejected):
        return _REJECTION_RETRY_REASONS[exc.category]
    if isinstance(exc, RefinedSelectionsInvalid):
        return RETRY_INVALID_SELECTION_VALUE
    return RETRY_REASON_UNSPECIFIED


class RefinementCategoryUnavailable(Exception):
    """This refinement category has nothing to change on this DesignSpec version.

    Version dispatch is mandatory, not optional (ADR 0028): a version-1 spec
    carries no ``neckline_style`` at all, because the questionnaire that produced
    it had no neckline question. Refusing up front with a controlled code is the
    honest answer — the alternative is offering a control that can only ever
    produce an unchanged concept. Safe message; never echoes the spec."""

    code = "refinement_category_unavailable"


def assert_category_refinable(schema_version: object, change_type: str) -> None:
    """Raise :class:`RefinementCategoryUnavailable` when ``change_type`` owns no
    canonical selection on a spec of ``schema_version``.

    Called at the enqueue boundary, so an impossible refinement is refused before
    an attempt row exists and before any provider is selected, and again inside
    the service as defence in depth."""
    if not canonical_refinement_fields(change_type, schema_version):
        raise RefinementCategoryUnavailable(
            "this change cannot be applied to this design's questionnaire version"
        )


def _assert_selection_changes_allowed(
    changed_paths: frozenset[str], change_type: str, schema_version: object
) -> None:
    """Every changed canonical selection must be one this category owns.

    Two checks, deliberately not one. The immutable set is tested EXPLICITLY
    rather than left to the allowlist's silence, so a future allowlist entry
    cannot grant ``garment_type`` or ``ceremony`` by accident; only then does
    membership of the category's own group decide the rest."""
    allowed = set(canonical_refinement_fields(change_type, schema_version))
    for path in sorted(changed_paths):
        field = changed_selection_field(path)
        if field is None:
            continue  # not a selection path at all; the allowlist check owns it
        if not field:
            # The bare ``source_selections`` root: a change this diff could not
            # attribute to any one field. A well-formed diff of two valid specs
            # cannot produce it (both carry every declared key), so reaching
            # here means something is wrong with the payload's shape — refuse
            # rather than let it fall through to a membership test that would
            # reject it only incidentally.
            raise RefinementOutputRejected(RefinementOutputCategory.IMMUTABLE_FIELD_CHANGED)
        if field in REFINEMENT_IMMUTABLE_SELECTION_FIELDS:
            raise RefinementOutputRejected(RefinementOutputCategory.IMMUTABLE_FIELD_CHANGED)
        if field not in allowed:
            raise RefinementOutputRejected(RefinementOutputCategory.SOURCE_SELECTIONS_CHANGED)


def _assert_no_refinement_process_leakage(spec: DesignSpec) -> None:
    """The refined output must never describe the refinement process itself
    — it must read as one complete, self-contained specification."""
    haystack = " ".join(iter_strings(spec.model_dump(mode="python")))
    if any(contains_phrase(haystack, phrase) for phrase in _REFINEMENT_PROCESS_PHRASES):
        raise RefinementOutputRejected(RefinementOutputCategory.PROCESS_MENTIONED)


def _assert_the_image_prompt_actually_moved(source_spec: DesignSpec, spec: DesignSpec) -> None:
    """Raise :class:`_NoChangeInAttempt` when the refined spec renders to the
    SAME image prompt as the source.

    The check that was missing, and the reason ADR 0028 did not land in the live
    path. Everything above decides whether an edit was *permitted*; nothing made
    it *effective*. A spec-level diff is satisfied by any narrative tweak — a
    reworded ``concept_summary``, a fresh ``image_alt_text`` — and prompt builder
    8.x deliberately renders almost none of that narrative, so an attempt could
    pass every check above and still produce a byte-identical prompt. Observed
    live: a neckline refinement that rewrote four narrative fields, left
    ``source_selections.neckline_style`` at ``square_neck``, and rendered a
    1423-character prompt identical to its source. The image differed only
    because the provider is non-deterministic, which is exactly the "nothing
    changed" the phase exists to end.

    Compares RENDERED PROMPTS rather than requiring a canonical selection to
    move, because the canonical field is not always the path that reaches the
    image: builder 8.x drops ``fabrics_and_texture`` only *when canonical
    fabrics exist*, so for a concept without them the narrative genuinely is
    what renders. Requiring a canonical change would refuse those legitimately;
    requiring the prompt to differ is the exact promise the product makes.

    Both prompts are built from specs with the CURRENT builder — never compared
    against the source version's persisted ``image_prompt``, which may have been
    built by an earlier ``PROMPT_BUILDER_VERSION`` and would make an unchanged
    render look changed.

    Raises the retryable "no change" signal rather than a rejection, so the
    model gets its one corrected attempt and an exhausted refinement fails with
    the honest ``RefinementNoChangeProduced`` instead of charging for a concept
    that looks identical.

    May also raise :class:`~sitara.generation.prompt_builder.ImagePromptBuildError`
    — a refined spec whose every field is individually clean can still assemble
    into a prompt the final scan refuses. The caller treats that as one more
    invalid attempt, deliberately: a spec that cannot be rendered must never be
    persisted, and it must not cost the corrected attempt either."""
    if build_image_prompt(spec) == build_image_prompt(source_spec):
        raise _NoChangeInAttempt()


def _validate_refined_output(
    payload: dict, source_spec: DesignSpec, change_type: str, design=None
) -> DesignSpec:
    """Fresh Django-side revalidation, exact-diff and safety checks. Raises
    on any failure (all treated as retryable by the caller, EXCEPT an empty
    diff, which is tracked separately so the caller can distinguish "no
    change produced" from every other invalid-output reason).

    ``design`` supplies the pinned questionnaire a changed canonical selection
    is revalidated against (ADR 0028). It is optional ONLY so unit tests can
    exercise the pure diff/allowlist half in isolation; every production caller
    passes it, and the service refuses a design with no pinned questionnaire
    long before reaching here."""
    spec = validate_design_spec(payload)
    # A refinement never changes the DesignSpec structure version — a mismatch
    # is treated the same as any other immutable change.
    if spec.schema_version != source_spec.schema_version:
        raise RefinementOutputRejected(RefinementOutputCategory.IMMUTABLE_FIELD_CHANGED)
    scan_design_spec_or_raise(spec)
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
    _assert_selection_changes_allowed(changed_paths, change_type, source_spec.schema_version)
    allowed_roots = refinement_allowed_paths(change_type, source_spec.schema_version)
    if any(not path_is_allowed(path, allowed_roots) for path in changed_paths):
        raise RefinementOutputRejected(RefinementOutputCategory.DISALLOWED_FIELD_CHANGED)
    _assert_the_image_prompt_actually_moved(source_spec, spec)
    # The replacement for ADR 0015's exact-echo guarantee. Everything above
    # decides WHICH canonical field may move; this decides whether the value it
    # moved to is one the user could have chosen — under the design's own pinned
    # questionnaire version, never the active one. Last, because it is the only
    # check here that reads the database, and there is no sense querying for an
    # output the cheap pure checks already refused.
    if design is not None and any(changed_selection_field(path) for path in changed_paths):
        assert_refined_selections_are_answerable(design, spec)
    return spec


def _generate_valid_refined_spec(
    provider,
    source_spec: DesignSpec,
    change_type: str,
    note: str,
    design_id,
    generation_attempt: GenerationAttempt | None = None,
    design=None,
):
    """Make at most :data:`MAX_REFINEMENT_PROVIDER_REQUESTS` controlled
    requests. Returns ``(spec, usage, attempts)``. A provider transport error
    or refusal aborts immediately (no retry). Raises
    :class:`RefinementNoChangeProduced` when every attempt's output was
    identical to the source, or :class:`RefinementGenerationFailed` for any
    other exhausted-retry reason.

    ``design`` carries the pinned questionnaire a changed canonical selection is
    revalidated against; ``design_id`` stays a separate parameter because it is
    the only thing that reaches a log line."""
    responses: list = []
    attempts = 0
    no_change_only = True
    # Why the PREVIOUS attempt was refused, carried into the retry's correction
    # instruction. `None` on the first attempt, so nothing is appended. Only ever
    # a source-controlled machine name — never a field, a value or the note.
    retry_reason: str | None = None
    # Computed ONCE, before the loop: it depends only on the source spec and the
    # design's pinned questionnaire, neither of which the loop can change, and it
    # reads the schema from the database. An empty mapping is an honest answer —
    # genuinely no legal alternative — and is what the model was effectively
    # given before this existed.
    #
    # `RefinementQuestionnaireUnavailable` is deliberately NOT caught. It cannot
    # reach here through the public entry point: `validate_source_version`
    # refuses a design with no usable pinned questionnaire before a provider is
    # even selected. If it ever did, `pipeline.py` already maps it to a
    # controlled code, and swallowing it would hide a real defect behind a
    # silently emptier prompt. `design` is still guarded because the parameter is
    # optional for callers that bypass that entry point.
    changeable_values: dict[str, tuple] = {}
    if design is not None:
        changeable_values = answerable_selection_alternatives(design, source_spec, change_type)
    cost_on = cost_accounting.cost_enabled(generation_attempt)
    profile = cost_control.active_pricing_profile()
    for attempt in range(1, MAX_REFINEMENT_PROVIDER_REQUESTS + 1):
        attempts += 1
        request = StructuredDesignRequest(
            system_prompt=REFINEMENT_SYSTEM_PROMPT,
            user_message=build_refinement_user_message(
                source_spec.model_dump(mode="json"),
                change_type,
                note,
                # The SAME call, with the same arguments, that grades the output
                # at `_validate_refined_output`. It has to be: the model was
                # previously graded against this allowlist and never shown it,
                # left to infer from the category name which fields were "in
                # scope" — and a live refinement failed both attempts because the
                # customer's note asked for two things at once and only one of
                # them belonged to the category she picked.
                editable_paths=tuple(
                    refinement_allowed_paths(change_type, source_spec.schema_version)
                ),
                # Which canonical selections this category may change is decided
                # HERE, from the source spec's own schema version, and told to
                # the model explicitly — never inferred by the model from the
                # category name, and never trusted from its output either (the
                # exact diff below re-checks every path regardless).
                changeable_selection_fields=canonical_refinement_fields(
                    change_type, source_spec.schema_version
                ),
                # And what those fields may be set TO, from the design's own
                # pinned questionnaire, each candidate proved by substitution
                # through the validator that will judge the answer. The demo
                # engine has been handed this since ADR 0028; the live path was
                # told "never invent a selection value" and shown no values.
                changeable_selection_values=changeable_values,
                retry_reason=retry_reason,
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
            logger.warning(
                "design refinement refused design=%s provider_request=%s change_type=%s",
                design_id,
                attempt,
                change_type,
            )
            raise GenerationRefused("the provider refused to refine the specification")
        if result.payload is not None:
            try:
                spec = _validate_refined_output(result.payload, source_spec, change_type, design)
            except _NoChangeInAttempt:
                retry_reason = RETRY_NO_CHANGE
                logger.warning(
                    "refinement output unchanged design=%s provider_request=%s change_type=%s",
                    design_id,
                    attempt,
                    change_type,
                )
            except (
                ValidationError,
                UnsupportedDesignSpecVersion,
                GeneratedContentRejected,
                RefinementOutputRejected,
                # A canonical value the design's own questionnaire never
                # offered. Retryable like every other invalid output: the model
                # gets one corrected attempt, then the whole refinement fails.
                RefinedSelectionsInvalid,
                # The refined spec cannot be RENDERED. Reachable only through
                # the guard below, and only through the one check the earlier
                # per-field scan structurally cannot make: `build_image_prompt`
                # scans the ASSEMBLED prompt, so a denylisted phrase or URL
                # formed across the join between two individually-clean fields
                # is caught here and nowhere before. Retryable like every other
                # invalid output — without this the attempt would escape as an
                # unclassified error, skip the remaining retry, and lose the
                # corrected attempt this guard exists to guarantee.
                ImagePromptBuildError,
            ) as exc:
                no_change_only = False
                retry_reason = _retry_reason(exc)
                # `provider_request`, not `attempt`: pipeline.py logs the
                # GenerationAttempt UUID under that key and the correlation
                # filter adds `attempt_id`, so three different things shared one
                # name. This one is the ordinal within the retry budget.
                #
                # `reason` is the discriminator this handler already had and
                # threw away. The bare type was not enough: a real incident
                # logged `exception_type=RefinementOutputRejected` twice, and
                # that type covers four distinct causes with four different
                # fixes. Safe by construction — see :func:`_rejection_detail`.
                logger.warning(
                    "refinement output rejected design=%s provider_request=%s "
                    "change_type=%s reason=%s",
                    design_id,
                    attempt,
                    change_type,
                    _rejection_detail(exc),
                )
            else:
                return spec, aggregate_usage(responses), attempts
        else:
            # A response that parsed to nothing usable — not a refusal (handled
            # above and aborted), just no payload. It used to fall through
            # silently, leaving `no_change_only` True, so two payload-less
            # responses reported `refinement_no_change` — "your request produced
            # no change" — when the model had produced no OUTPUT. Two different
            # facts, two different operator actions, and two billed calls with
            # nothing in the log to tell them apart.
            no_change_only = False
            retry_reason = RETRY_REASON_UNSPECIFIED
            # `stop_reason` because the whole point of this branch is that a
            # billed call produced nothing, and it is the only field that says
            # WHY — `max_tokens` (raise the output budget) reads nothing like a
            # parse failure. It is enumerated provenance on
            # `StructuredDesignResult`, which documents itself as carrying ONLY
            # that: never the prompt, the response body, headers or a key.
            logger.warning(
                "refinement output missing design=%s provider_request=%s "
                "change_type=%s stop_reason=%s",
                design_id,
                attempt,
                change_type,
                result.stop_reason,
            )
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
    """Generate, validate and persist one refined DesignVersion.

    The source is the design's current latest version, so a second refinement
    refines the first one's output rather than the original concept.

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
    # Defence in depth: the enqueue guard already refused a category with no
    # canonical field on this spec version, so reaching here means a caller
    # bypassed it. Refuse before spending rather than produce a concept the user
    # asked to change and did not.
    assert_category_refinable(source_context.spec.schema_version, refinement_request.change_type)
    # Two different limits, both enforced. `refined_versions` stops THIS version
    # being refined twice (which would branch the lineage); the budget stops the
    # DESIGN exceeding MAX_REFINEMENTS however the chain is walked.
    if source_version.refined_versions.exists():
        raise RefinementLimitReached("this version has already been refined")
    assert_refinement_budget_available(design.id)

    refinement_request_hash = refinement_request_sha256(refinement_request)

    with advisory_lock(design.id):
        # Close the race: another holder may have refined between the
        # pre-check and acquiring the lock.
        if source_version.refined_versions.exists():
            raise RefinementLimitReached("this version has already been refined")
        assert_refinement_budget_available(design.id)
        selected = provider if provider is not None else get_structured_design_generation_provider()
        try:
            spec, usage, refine_attempts = _generate_valid_refined_spec(
                selected,
                source_context.spec,
                refinement_request.change_type,
                refinement_request.note,
                design.id,
                generation_attempt=attempt,
                design=design,
            )
        except RefinementQuestionnaireUnavailable as exc:
            # Unreachable: validate_source_version refused a design with no
            # pinned questionnaire before any provider was selected. Kept so a
            # future caller that skips that check still ends on a stable code
            # rather than an unclassified internal error.
            raise RefinementSourceUnavailable(
                "the source design has no usable questionnaire version"
            ) from exc
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
    "RefinementCategoryUnavailable",
    "GenerationLocked",
    "GenerationRefused",
    "ProviderIdentityChanged",
    "RefinementGenerationFailed",
    "RefinementLimitReached",
    "RefinementNoChangeProduced",
    "RefinementOutputCategory",
    "RefinementOutputRejected",
    "RefinementSourceUnavailable",
    "assert_category_refinable",
    "assert_refinement_budget_available",
    "generate_refined_design_spec_for_design",
    "refinements_used",
    "validate_source_version",
]
