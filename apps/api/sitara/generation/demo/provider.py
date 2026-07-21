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

from sitara.ai_gateway.structured_design import StructuredDesignRequest, StructuredDesignResult

from .design_spec_engine import DEMO_SPEC_TEMPLATE_VERSION, build_demo_design_spec
from .refinement_engine import DEMO_REFINEMENT_TEMPLATE_VERSION, build_demo_refined_spec

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

    def __init__(self, *, source_spec: dict, refinement_request):
        self._source_spec = source_spec
        self._refinement_request = refinement_request

    def generate(self, request: StructuredDesignRequest) -> StructuredDesignResult:
        payload = build_demo_refined_spec(self._source_spec, self._refinement_request)
        return StructuredDesignResult(
            payload=payload,
            provider=self.name,
            model=DEMO_REFINEMENT_MODEL,
            input_tokens=None,
            output_tokens=None,
            stop_reason=_DEMO_STOP_REASON,
            refused=False,
        )
