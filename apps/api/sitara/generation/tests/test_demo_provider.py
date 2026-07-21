"""Local deterministic structured-design provider adapters (Phase 15 Part B)."""

from sitara.ai_gateway.structured_design import StructuredDesignRequest
from sitara.generation.demo.design_spec_engine import build_demo_design_spec
from sitara.generation.demo.provider import (
    DEMO_REFINEMENT_MODEL,
    DEMO_SPEC_MODEL,
    DemoRefinementStructuredDesignProvider,
    DemoStructuredDesignProvider,
)
from sitara.generation.design_spec import DesignSpec
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
        assert result.model == DEMO_SPEC_MODEL == "demo-spec-1.0.0"

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
        assert result.model == DEMO_REFINEMENT_MODEL == "demo-refinement-1.0.0"
        assert result.model != DEMO_SPEC_MODEL

    def test_never_labelled_as_a_live_provider(self):
        source = self._a_source_dict()
        request = RefinementRequest.model_validate(
            {"schema_version": 1, "change_type": "neckline", "note": ""}
        )
        provider = DemoRefinementStructuredDesignProvider(
            source_spec=source, refinement_request=request
        )
        result = provider.generate(_a_request(source["source_selections"]))
        assert result.provider not in {"anthropic", "replicate", "claude"}
        assert "claude" not in result.model.lower()
        assert "anthropic" not in result.model.lower()
