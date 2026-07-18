"""Sanitised, source-controlled provider-result fixtures and fake providers.

These are the Part B "recorded fixtures": deterministic, synthetic
StructuredDesignResults built in code — no real API responses, request IDs,
user submissions, keys, billing metadata, headers or hidden reasoning. Fakes
inject into the generation service so no test ever reaches Anthropic."""

import copy

from sitara.ai_gateway.structured_design import StructuredDesignResult
from sitara.generation.fixture_provider import build_fixture_spec

_USAGE = {"input_tokens": 1234, "output_tokens": 567}


def valid_result(source_selections: dict) -> StructuredDesignResult:
    return StructuredDesignResult(
        payload=build_fixture_spec(source_selections),
        provider="fake",
        model="fake-model",
        input_tokens=_USAGE["input_tokens"],
        output_tokens=_USAGE["output_tokens"],
        stop_reason="end_turn",
    )


def malformed_result() -> StructuredDesignResult:
    # Structurally invalid: parse produced nothing usable.
    return StructuredDesignResult(
        payload=None,
        provider="fake",
        model="fake-model",
        input_tokens=_USAGE["input_tokens"],
        output_tokens=_USAGE["output_tokens"],
        stop_reason="parse_error",
    )


def schema_invalid_result(source_selections: dict) -> StructuredDesignResult:
    # Valid JSON object but not a valid DesignSpec (title too short).
    payload = build_fixture_spec(source_selections)
    payload["title"] = "x"
    return _with_payload(payload)


def source_mismatch_result(source_selections: dict) -> StructuredDesignResult:
    payload = build_fixture_spec(source_selections)
    payload["source_selections"] = copy.deepcopy(source_selections)
    payload["source_selections"]["garment_type"] = "sharara"  # no longer matches
    return _with_payload(payload)


def blocked_designer_result(source_selections: dict) -> StructuredDesignResult:
    payload = build_fixture_spec(source_selections)
    payload["styling_notes"] = ["Style it the way Sabyasachi would."]
    return _with_payload(payload)


def semantic_invalid_result(source_selections: dict) -> StructuredDesignResult:
    # Field bounds are satisfied, but the required construction caveats are
    # absent → the DesignSpec model_validator rejects it (retryable).
    payload = build_fixture_spec(source_selections)
    payload["construction_caveats"] = ["Please review this idea with a tailor before proceeding."]
    return _with_payload(payload)


def result_with_usage(
    source_selections: dict,
    *,
    input_tokens,
    output_tokens,
    valid: bool = True,
) -> StructuredDesignResult:
    """A result carrying explicit per-attempt usage. ``valid=False`` yields an
    invalid (retryable) response that still reports token usage."""
    return StructuredDesignResult(
        payload=build_fixture_spec(source_selections) if valid else None,
        provider="fake",
        model="fake-model",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason="end_turn" if valid else "parse_error",
    )


def refusal_result() -> StructuredDesignResult:
    return StructuredDesignResult(
        payload=None,
        provider="fake",
        model="fake-model",
        input_tokens=None,
        output_tokens=None,
        stop_reason="refusal",
        refused=True,
    )


def _with_payload(payload) -> StructuredDesignResult:
    return StructuredDesignResult(
        payload=payload,
        provider="fake",
        model="fake-model",
        input_tokens=_USAGE["input_tokens"],
        output_tokens=_USAGE["output_tokens"],
        stop_reason="end_turn",
    )


class SequenceProvider:
    """Returns canned results in order and counts calls."""

    name = "fake"

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0
        self.requests = []

    def generate(self, request):
        self.calls += 1
        self.requests.append(request)
        return self._results.pop(0)


class RaisingProvider:
    """Raises a provider transport error; records call count."""

    name = "fake"

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        raise self._exc


class MutatingProvider:
    """Applies a side effect DURING generate() (simulating a concurrent draft
    edit while the network call is in flight), then returns a valid result."""

    name = "fake"

    def __init__(self, source_selections, on_generate):
        self._source_selections = source_selections
        self._on_generate = on_generate
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        self._on_generate()
        return valid_result(self._source_selections)
