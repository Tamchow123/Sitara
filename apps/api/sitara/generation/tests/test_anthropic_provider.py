"""The Anthropic provider wrapper — with an injected fake client (no network)."""

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from sitara.ai_gateway.anthropic_provider import AnthropicStructuredDesignProvider
from sitara.ai_gateway.structured_design import (
    StructuredDesignProviderError,
    StructuredDesignRequest,
)
from sitara.generation.design_spec import DesignSpec, DesignSpecV2, validate_design_spec

from .utils import a_valid_spec_dict


class _FakeMessages:
    def __init__(self, *, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = 0
        self.kwargs = None

    def parse(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return self._result


def _client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def _request(schema_version: int = 1):
    return StructuredDesignRequest(
        system_prompt="SYSTEM",
        user_message="USER",
        source_selections={"garment_type": "lehenga"},
        max_output_tokens=4096,
        attempt=1,
        schema_version=schema_version,
    )


def _v2_spec():
    data = a_valid_spec_dict()
    data["schema_version"] = 2
    data["source_selections"]["neckline_style"] = "high_neck"
    return validate_design_spec(data)


def _message(parsed, stop_reason, input_tokens=100, output_tokens=200):
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class TestSuccess:
    def test_valid_parsed_output_returns_payload_and_usage(self):
        spec = DesignSpec.model_validate(a_valid_spec_dict())
        messages = _FakeMessages(result=_message(spec, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(_request())
        assert result.payload == spec.model_dump(mode="json")
        assert result.input_tokens == 100
        assert result.output_tokens == 200
        assert result.stop_reason == "end_turn"
        assert result.refused is False
        assert result.provider == "anthropic"
        # output_format was passed for first-class structured output.
        assert messages.kwargs["output_format"] is DesignSpec
        assert "stream" not in messages.kwargs

    def test_request_uses_configured_model_and_token_cap(self, settings):
        spec = DesignSpec.model_validate(a_valid_spec_dict())
        messages = _FakeMessages(result=_message(spec, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        provider.generate(_request())
        assert messages.kwargs["model"] == settings.ANTHROPIC_MODEL
        assert messages.kwargs["max_tokens"] == 4096

    def test_v2_request_uses_the_v2_output_format(self):
        spec = _v2_spec()
        messages = _FakeMessages(result=_message(spec, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(_request(schema_version=2))
        # The provider selects the target structure version's model class.
        assert messages.kwargs["output_format"] is DesignSpecV2
        assert result.payload["schema_version"] == 2

    def test_unsupported_schema_version_fails_closed_without_a_request(self):
        # An out-of-registry version must fail closed as a definitively
        # spend-free provider error, and no request may be sent.
        messages = _FakeMessages(result=_message(None, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        with pytest.raises(StructuredDesignProviderError) as excinfo:
            provider.generate(_request(schema_version=99))
        assert excinfo.value.category == "unsupported_schema_version"
        assert excinfo.value.ambiguous_acceptance is False
        assert messages.calls == 0


class TestUnusableOutputs:
    def test_refusal_maps_to_refused(self):
        messages = _FakeMessages(result=_message(None, "refusal"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(_request())
        assert result.refused is True
        assert result.payload is None

    def test_missing_parsed_output_is_none_payload(self):
        messages = _FakeMessages(result=_message(None, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(_request())
        assert result.payload is None
        assert result.refused is False


class TestErrorMapping:
    @pytest.mark.parametrize(
        "exc,category",
        [
            (anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")), "timeout"),
            (
                anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
                "connection",
            ),
            (anthropic.APIError("boom", httpx.Request("POST", "https://x"), body=None), "unknown"),
        ],
    )
    def test_transport_errors_map_to_safe_categories(self, exc, category):
        messages = _FakeMessages(exc=exc)
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        with pytest.raises(StructuredDesignProviderError) as excinfo:
            provider.generate(_request())
        assert excinfo.value.category == category
        # Transport failures (timeout/connection/unknown) can fire AFTER the
        # request bytes were sent — the gateway marks them AMBIGUOUS so the
        # pipeline never clears the text-submission marker for them.
        assert excinfo.value.ambiguous_acceptance is True

    def test_client_initialisation_failure_is_not_ambiguous(self, monkeypatch):
        # A client that never constructed provably sent no request — the
        # gateway marks it non-ambiguous so a $0-spend failure never strands
        # the design behind the fail-closed guard.
        def _broken_constructor(**kwargs):
            raise RuntimeError("sdk construction exploded")

        monkeypatch.setattr(anthropic, "Anthropic", _broken_constructor)
        provider = AnthropicStructuredDesignProvider()  # no injected client
        with pytest.raises(StructuredDesignProviderError) as excinfo:
            provider.generate(_request())
        assert excinfo.value.category == "client_initialisation"
        assert excinfo.value.ambiguous_acceptance is False

    def test_definitive_api_answers_are_not_ambiguous(self):
        # An HTTP status response is the provider's definitive answer: the
        # spend question is resolved, so the gateway clears ambiguity.
        request = httpx.Request("POST", "https://x")
        response = httpx.Response(429, request=request)
        exc = anthropic.RateLimitError("rate limited", response=response, body=None)
        messages = _FakeMessages(exc=exc)
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        with pytest.raises(StructuredDesignProviderError) as excinfo:
            provider.generate(_request())
        assert excinfo.value.category == "rate_limit"
        assert excinfo.value.ambiguous_acceptance is False
