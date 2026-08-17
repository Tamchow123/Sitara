"""Local deterministic structured-design provider adapters (Phase 15 Part B).

Each class implements the same
:class:`~sitara.ai_gateway.structured_design.StructuredDesignGenerationProvider`
protocol as the live Anthropic provider and the test-only
:class:`~sitara.generation.fixture_provider.FixtureStructuredDesignProvider`
— ``name`` plus ``generate(request) -> StructuredDesignResult`` — so it can
be injected at the same pipeline call sites, but makes zero network calls
and never constructs a provider SDK client. Context (a
:class:`~sitara.generation.context.GenerationContext` for the initial
adapter, or a source spec + refinement request for the refinement adapter)
is supplied via the constructor rather than parsed from the request's
rendered prompt text, per the deterministic-DesignSpec-engine contract.

Usage metadata is honest: ``input_tokens``/``output_tokens`` are always
``None`` (nothing was billed), and ``stop_reason`` is a clearly local value
that can never be mistaken for a live provider's stop reason."""

from pydantic import ValidationError

from sitara.ai_gateway.structured_design import (
    StructuredDesignProviderError,
    StructuredDesignRequest,
    StructuredDesignResult,
)
from sitara.generation.design_spec import UnsupportedDesignSpecVersion
from sitara.generation.prompt_builder import ImagePromptBuildError

from .design_spec_engine import DEMO_SPEC_TEMPLATE_VERSION, build_demo_design_spec
from .refinement_engine import DEMO_REFINEMENT_TEMPLATE_VERSION, build_demo_refined_spec

# What the refinement engine's own rendered-prompt self-check can raise on a
# candidate it has just built. Every one of them means the same thing — this
# engine produced something the pipeline could not have used — and every one is
# an ENGINE defect rather than a statement about the customer's design, so they
# must not be dressed up as `DemoRefinementInert` ("this change cannot be
# applied to this design"), which would blame the concept for a bug in here.
#
# They are converted at this boundary because that is what a provider adapter is
# for: the engine stays a pure function, and the protocol's own failure type is
# the one the caller already handles, with cost accounting released correctly.
# `DemoRefinementInert` is deliberately NOT in this tuple — it is a real,
# expected outcome and travels on to its own pipeline handler.
_ENGINE_OUTPUT_UNUSABLE = (ValidationError, UnsupportedDesignSpecVersion, ImagePromptBuildError)

DEMO_SPEC_MODEL = f"demo-spec-{DEMO_SPEC_TEMPLATE_VERSION}"
DEMO_REFINEMENT_MODEL = f"demo-refinement-{DEMO_REFINEMENT_TEMPLATE_VERSION}"

_DEMO_STOP_REASON = "deterministic_local"


class DemoStructuredDesignProvider:
    """Deterministic local structured-design provider for initial generation.

    Never constructs a live provider client and never touches
    ``request.system_prompt``/``request.user_message`` — the DesignSpec is
    built entirely from the ``context`` supplied at construction time."""

    name = "demo"

    def __init__(self, *, context):
        self._context = context

    def generate(self, request: StructuredDesignRequest) -> StructuredDesignResult:
        payload = build_demo_design_spec(self._context)
        return StructuredDesignResult(
            payload=payload,
            provider=self.name,
            model=DEMO_SPEC_MODEL,
            input_tokens=None,
            output_tokens=None,
            stop_reason=_DEMO_STOP_REASON,
            refused=False,
        )


class DemoRefinementStructuredDesignProvider:
    """Deterministic local structured-design provider for refinement.

    Never constructs a live provider client and never touches
    ``request.system_prompt``/``request.user_message`` — the refined
    DesignSpec is built entirely from the ``source_spec``/``refinement_request``
    supplied at construction time."""

    name = "demo"

    def __init__(self, *, source_spec: dict, refinement_request, selection_alternatives=None):
        self._source_spec = source_spec
        self._refinement_request = refinement_request
        # The canonical values this design's own pinned questionnaire would
        # accept (ADR 0028). Computed by the caller, never by the engine: the
        # demo phrase vocabularies are supersets of any one questionnaire, so a
        # value picked from them could be one the customer was never offered.
        self._selection_alternatives = selection_alternatives

    def generate(self, request: StructuredDesignRequest) -> StructuredDesignResult:
        try:
            payload = build_demo_refined_spec(
                self._source_spec,
                self._refinement_request,
                selection_alternatives=self._selection_alternatives,
            )
        except _ENGINE_OUTPUT_UNUSABLE as exc:
            # Narrow on purpose, not `except Exception`: an unexpected bug should
            # still surface as itself during development. The category is a
            # source-controlled machine name — never the candidate spec, never
            # the note.
            #
            # `ambiguous_acceptance=False` is load-bearing, not decoration. That
            # flag defaults to True so an unclassified raise fails closed and
            # reads as "money may already have been spent"; the pipeline then
            # terminates the attempt as STRUCTURED_SUBMISSION_AMBIGUOUS and
            # deliberately LEAVES `text_submission_in_flight` set, which the
            # enqueue guard reads as unresolved spend and refuses every later
            # refinement of that design. Correct for a network call that may have
            # landed. Categorically wrong here: this engine is local and
            # deterministic and sends nothing anywhere, so the failure is
            # provably before any request — exactly the case the flag's own
            # docstring names — and inheriting the default would let a zero-cost
            # local defect permanently strand a design's remaining refinements.
            raise StructuredDesignProviderError(
                "demo_engine_output_unusable", ambiguous_acceptance=False
            ) from exc
        return StructuredDesignResult(
            payload=payload,
            provider=self.name,
            model=DEMO_REFINEMENT_MODEL,
            input_tokens=None,
            output_tokens=None,
            stop_reason=_DEMO_STOP_REASON,
            refused=False,
        )
