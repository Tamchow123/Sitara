"""Local deterministic structured-design provider adapters (Phase 15 Part B)."""

import pytest
from pydantic import ValidationError

from sitara.ai_gateway.structured_design import (
    StructuredDesignProviderError,
    StructuredDesignRequest,
)
from sitara.generation.demo import refinement_engine
from sitara.generation.demo.design_spec_engine import build_demo_design_spec
from sitara.generation.demo.provider import (
    DEMO_REFINEMENT_MODEL,
    DEMO_SPEC_MODEL,
    DemoRefinementStructuredDesignProvider,
    DemoStructuredDesignProvider,
)
from sitara.generation.demo.refinement_engine import DemoRefinementInert
from sitara.generation.design_spec import DesignSpec, UnsupportedDesignSpecVersion
from sitara.generation.prompt_builder import ImagePromptBuildError
from sitara.generation.refinement import RefinementRequest

from .demo_context_utils import a_context


def _a_request(source_selections: dict) -> StructuredDesignRequest:
    return StructuredDesignRequest(
        system_prompt="unused",
        user_message="unused",
        source_selections=source_selections,
        max_output_tokens=100,
        attempt=1,
    )


class TestDemoStructuredDesignProvider:
    def test_name_is_demo(self):
        context = a_context()
        provider = DemoStructuredDesignProvider(context=context)
        assert provider.name == "demo"

    def test_model_identity_is_honest(self):
        context = a_context()
        provider = DemoStructuredDesignProvider(context=context)
        result = provider.generate(_a_request(context.source_selections))
        assert result.provider == "demo"
        assert result.model == DEMO_SPEC_MODEL == "demo-spec-3.0.0"

    def test_usage_metadata_is_honest(self):
        context = a_context()
        provider = DemoStructuredDesignProvider(context=context)
        result = provider.generate(_a_request(context.source_selections))
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.refused is False

    def test_payload_matches_the_engine_directly(self):
        context = a_context()
        provider = DemoStructuredDesignProvider(context=context)
        result = provider.generate(_a_request(context.source_selections))
        assert result.payload == build_demo_design_spec(context)

    def test_never_reads_request_prompt_text(self):
        context = a_context()
        provider = DemoStructuredDesignProvider(context=context)
        request_a = StructuredDesignRequest(
            system_prompt="one prompt",
            user_message="one message",
            source_selections=context.source_selections,
            max_output_tokens=100,
            attempt=1,
        )
        request_b = StructuredDesignRequest(
            system_prompt="a completely different prompt",
            user_message="a completely different message",
            source_selections=context.source_selections,
            max_output_tokens=999,
            attempt=7,
        )
        assert provider.generate(request_a).payload == provider.generate(request_b).payload


class TestDemoRefinementStructuredDesignProvider:
    def _a_source_dict(self) -> dict:
        context = a_context()
        return DesignSpec.model_validate(build_demo_design_spec(context)).model_dump(mode="json")

    def test_name_is_demo(self):
        source = self._a_source_dict()
        request = RefinementRequest.model_validate(
            {"schema_version": 1, "change_type": "colour_story", "note": ""}
        )
        provider = DemoRefinementStructuredDesignProvider(
            source_spec=source, refinement_request=request
        )
        assert provider.name == "demo"

    def test_model_identity_is_honest_and_distinct_from_initial(self):
        source = self._a_source_dict()
        request = RefinementRequest.model_validate(
            {"schema_version": 1, "change_type": "colour_story", "note": ""}
        )
        provider = DemoRefinementStructuredDesignProvider(
            source_spec=source, refinement_request=request
        )
        result = provider.generate(_a_request(source["source_selections"]))
        assert result.model == DEMO_REFINEMENT_MODEL == "demo-refinement-2.0.0"
        assert result.model != DEMO_SPEC_MODEL

    def test_never_labelled_as_a_live_provider(self):
        source = self._a_source_dict()
        # `silhouette_detail` rather than `neckline`: this fixture is a
        # version-1 spec, which has no `neckline_style` field for the neckline
        # category to move, so since ADR 0028 that request is refused rather than
        # answered. Using a category that owns a canonical field here also means
        # this test exercises the canonical path its two siblings do not.
        request = RefinementRequest.model_validate(
            {"schema_version": 1, "change_type": "silhouette_detail", "note": ""}
        )
        provider = DemoRefinementStructuredDesignProvider(
            source_spec=source,
            refinement_request=request,
            selection_alternatives={"silhouette": ("a_line_lehenga",)},
        )
        result = provider.generate(_a_request(source["source_selections"]))
        assert result.provider not in {"anthropic", "replicate", "claude"}
        assert "claude" not in result.model.lower()
        assert "anthropic" not in result.model.lower()


class TestWhenTheEngineItselfProducesSomethingUnusable:
    """The engine's rendered-prompt self-check (ADR 0028 amendment) is the first
    thing that ever validates or renders a freshly built candidate. If that
    raises, it raises INSIDE `provider.generate()`, whose only caller-side
    handler catches `StructuredDesignProviderError` — so an unconverted
    `ValidationError` or `ImagePromptBuildError` would escape every handler
    between here and the Celery task and surface as an unclassified internal
    error.

    Converted at this boundary, not in the engine, and NOT to
    `DemoRefinementInert`: an engine that builds an invalid spec is a defect in
    the engine, and reporting it as "this change cannot be applied to this
    design" would blame the customer's concept for our bug.
    """

    def _a_provider(self, monkeypatch, boom):
        context = a_context()
        source = DesignSpec.model_validate(build_demo_design_spec(context)).model_dump(mode="json")
        monkeypatch.setattr(refinement_engine, "_renders_the_same", boom)
        return source, DemoRefinementStructuredDesignProvider(
            source_spec=source,
            refinement_request=RefinementRequest.model_validate(
                {"schema_version": 1, "change_type": "silhouette_detail", "note": ""}
            ),
            selection_alternatives={"silhouette": ("a_line_lehenga",)},
        )

    @pytest.mark.parametrize(
        "exception",
        [
            ValidationError.from_exception_data("DesignSpec", []),
            UnsupportedDesignSpecVersion("schema_version 99 is not supported"),
            ImagePromptBuildError("the assembled prompt failed its safety scan"),
        ],
        ids=["invalid-spec", "unsupported-version", "unrenderable-prompt"],
    )
    def test_an_engine_failure_becomes_a_provider_error(self, monkeypatch, exception):
        def boom(candidate, source_spec):
            raise exception

        source, provider = self._a_provider(monkeypatch, boom)
        with pytest.raises(StructuredDesignProviderError):
            provider.generate(_a_request(source["source_selections"]))

    @pytest.mark.parametrize(
        "exception",
        [
            ValidationError.from_exception_data("DesignSpec", []),
            UnsupportedDesignSpecVersion("schema_version 99 is not supported"),
            ImagePromptBuildError("the assembled prompt failed its safety scan"),
        ],
        ids=["invalid-spec", "unsupported-version", "unrenderable-prompt"],
    )
    def test_the_failure_is_marked_definitively_spend_free(self, monkeypatch, exception):
        # `ambiguous_acceptance` defaults to True so an unclassified raise fails
        # closed and reads as "money may already have been spent". The pipeline
        # then ends the attempt as STRUCTURED_SUBMISSION_AMBIGUOUS and
        # deliberately LEAVES `text_submission_in_flight` set, which the enqueue
        # guard reads as unresolved spend and refuses every later refinement of
        # that design — permanently, with no reconciliation path, since stuck-job
        # recovery only visits in-progress rows.
        #
        # Right for a network call that may have landed. Categorically wrong
        # here: this engine is local and deterministic and sends nothing
        # anywhere. Inheriting the default would let a zero-cost local defect
        # permanently cost a design its one refinement, which is a worse outcome
        # than the unclassified error this conversion was added to prevent.
        def boom(candidate, source_spec):
            raise exception

        source, provider = self._a_provider(monkeypatch, boom)
        with pytest.raises(StructuredDesignProviderError) as excinfo:
            provider.generate(_a_request(source["source_selections"]))
        assert excinfo.value.ambiguous_acceptance is False

    def test_the_provider_error_carries_no_spec_content(self, monkeypatch):
        marker = "xyzzy-unique-marker-should-never-appear-verbatim"

        def boom(candidate, source_spec):
            raise ImagePromptBuildError(marker)

        source, provider = self._a_provider(monkeypatch, boom)
        with pytest.raises(StructuredDesignProviderError) as excinfo:
            provider.generate(_a_request(source["source_selections"]))
        assert marker not in str(excinfo.value)
        assert "lehenga" not in str(excinfo.value).lower()

    def test_an_inert_refusal_is_NOT_converted(self):
        # `DemoRefinementInert` is an expected outcome, not an engine defect. It
        # must pass through this boundary untouched to reach its own pipeline
        # handler; swallowing it here would turn an honest, actionable refusal
        # into a generic provider failure.
        context = a_context()
        source = DesignSpec.model_validate(build_demo_design_spec(context)).model_dump(mode="json")
        provider = DemoRefinementStructuredDesignProvider(
            source_spec=source,
            refinement_request=RefinementRequest.model_validate(
                {"schema_version": 1, "change_type": "neckline", "note": ""}
            ),
            selection_alternatives={},
        )
        with pytest.raises(DemoRefinementInert):
            provider.generate(_a_request(source["source_selections"]))
