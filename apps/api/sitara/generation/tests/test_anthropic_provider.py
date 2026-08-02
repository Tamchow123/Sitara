"""The Anthropic provider wrapper — with an injected fake client (no network)."""

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from sitara.ai_gateway.anthropic_provider import AnthropicStructuredDesignProvider
from sitara.ai_gateway.output_schema import STRIPPED_KEYWORDS
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

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return self._result


def _sent_schema(messages) -> dict:
    return messages.kwargs["output_config"]["format"]["schema"]


def _keywords_present(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in STRIPPED_KEYWORDS:
                found.add(key)
            found |= _keywords_present(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keywords_present(item)
    return found


def _client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def _request(schema_version: int = 1, source_selections=None):
    # A COMPLETE echo, not a stub: the provider injects it into the response
    # before strict validation, so a partial one would fail validation for
    # reasons that have nothing to do with the test.
    if source_selections is None:
        source_selections = a_valid_spec_dict()["source_selections"]
    return StructuredDesignRequest(
        system_prompt="SYSTEM",
        user_message="USER",
        source_selections=source_selections,
        max_output_tokens=4096,
        attempt=1,
        schema_version=schema_version,
    )


def _v2_spec():
    data = a_valid_spec_dict()
    data["schema_version"] = 2
    data["source_selections"]["neckline_style"] = "high_neck"
    return validate_design_spec(data)


def _message(body, stop_reason, input_tokens=100, output_tokens=200):
    """A response whose text block carries ``body`` (a str, or a dict to dump)."""
    if isinstance(body, dict):
        body = json.dumps(body)
    content = [] if body is None else [SimpleNamespace(type="text", text=body)]
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class TestSuccess:
    def test_valid_output_returns_payload_and_usage(self):
        spec = DesignSpec.model_validate(a_valid_spec_dict())
        messages = _FakeMessages(result=_message(spec.model_dump(mode="json"), "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(_request())
        assert result.payload == spec.model_dump(mode="json")
        assert result.input_tokens == 100
        assert result.output_tokens == 200
        assert result.stop_reason == "end_turn"
        assert result.refused is False
        assert result.provider == "anthropic"
        assert "stream" not in messages.kwargs

    def test_request_uses_configured_model_and_token_cap(self, settings):
        spec = DesignSpec.model_validate(a_valid_spec_dict())
        messages = _FakeMessages(result=_message(spec.model_dump(mode="json"), "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        provider.generate(_request())
        assert messages.kwargs["model"] == settings.ANTHROPIC_MODEL
        assert messages.kwargs["max_tokens"] == 4096

    def test_the_schema_sent_is_the_constraint_free_copy(self):
        """The fix for the too-large compiled grammar, asserted at the boundary."""
        spec = DesignSpec.model_validate(a_valid_spec_dict())
        messages = _FakeMessages(result=_message(spec.model_dump(mode="json"), "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        provider.generate(_request())
        sent = _sent_schema(messages)
        assert messages.kwargs["output_config"]["format"]["type"] == "json_schema"
        assert _keywords_present(sent) == set()
        # The echoed input is not asked for; everything else still is.
        expected = set(DesignSpec.model_json_schema()["properties"]) - {"source_selections"}
        assert set(sent["properties"]) == expected
        # And the deprecated parameter is gone entirely.
        assert "output_format" not in messages.kwargs

    def test_v2_request_sends_the_v2_schema(self):
        spec = _v2_spec()
        messages = _FakeMessages(result=_message(spec.model_dump(mode="json"), "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(
            _request(schema_version=2, source_selections=spec.source_selections.model_dump())
        )
        # The provider selects the target structure version's model class.
        sent = _sent_schema(messages)
        expected = set(DesignSpecV2.model_json_schema()["properties"]) - {"source_selections"}
        assert set(sent["properties"]) == expected
        assert result.payload["schema_version"] == 2


class TestEchoedSelections:
    """``source_selections`` comes from the request, never from the model."""

    def _request_with(self, selections):
        return StructuredDesignRequest(
            system_prompt="SYSTEM",
            user_message="USER",
            source_selections=selections,
            max_output_tokens=4096,
            attempt=1,
            schema_version=1,
        )

    def test_the_request_echo_is_used_when_the_model_omits_it(self):
        data = a_valid_spec_dict()
        truth = data["source_selections"]
        body = {k: v for k, v in data.items() if k != "source_selections"}
        messages = _FakeMessages(result=_message(body, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(self._request_with(truth))
        assert result.payload["source_selections"] == truth

    def test_a_miscopied_echo_from_the_model_is_overwritten(self):
        """The integrity gain: the model cannot corrupt the user's own answers."""
        data = a_valid_spec_dict()
        truth = data["source_selections"]
        tampered = dict(data)
        tampered["source_selections"] = {**truth, "garment_type": "saree"}
        messages = _FakeMessages(result=_message(tampered, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(self._request_with(truth))
        assert result.payload["source_selections"] == truth
        assert result.payload["source_selections"]["garment_type"] != "saree"

    def test_cross_field_rules_still_run_against_the_merged_whole(self):
        """Injecting the echo must not smuggle past validation.

        no_specific_direction requires a null regional direction; a response
        that pairs it with a real one is still rejected.
        """
        data = a_valid_spec_dict()
        truth = {**data["source_selections"], "regional_style": "no_specific_direction"}
        body = {k: v for k, v in data.items() if k != "source_selections"}
        body["cultural_context"] = {
            **body["cultural_context"],
            "regional_direction": "Punjabi bridal influences",
        }
        messages = _FakeMessages(result=_message(body, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(self._request_with(truth))
        assert result.payload is None
        assert result.stop_reason == "parse_error"

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

    def test_empty_body_is_none_payload(self):
        messages = _FakeMessages(result=_message(None, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(_request())
        assert result.payload is None
        assert result.refused is False
        assert result.stop_reason == "parse_error"

    def test_malformed_json_is_a_retryable_parse_error(self):
        messages = _FakeMessages(result=_message("{not json", "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(_request())
        assert result.payload is None
        assert result.refused is False
        assert result.stop_reason == "parse_error"

    def test_a_truncated_response_is_a_parse_error_not_a_payload(self):
        # stop_reason="max_tokens" leaves the JSON cut mid-object.
        body = json.dumps(a_valid_spec_dict())[:200]
        messages = _FakeMessages(result=_message(body, "max_tokens"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(_request())
        assert result.payload is None
        assert result.stop_reason == "parse_error"

    def test_output_violating_the_strict_bounds_is_rejected(self):
        """The constraints removed from the PROVIDER schema still bind here.

        This is the safety property of the too-large-grammar fix: the decoder no
        longer enforces ``maxLength``, so output that exceeds it must be caught
        by our own validation rather than accepted.
        """
        data = a_valid_spec_dict()
        data["concept_summary"] = "x" * 900  # schema caps this at 700
        messages = _FakeMessages(result=_message(data, "end_turn"))
        provider = AnthropicStructuredDesignProvider(client=_client(messages))
        result = provider.generate(_request())
        assert result.payload is None
        assert result.refused is False
        assert result.stop_reason == "parse_error"


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
