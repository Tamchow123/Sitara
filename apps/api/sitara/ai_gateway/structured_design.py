"""The narrow structured-design provider contract (Phase 8).

Network concerns live in ``sitara.ai_gateway``; domain orchestration lives in
``sitara.generation``. These small structures are the ONLY thing that crosses
the boundary. A provider receives an already-assembled request (trusted system
prompt + delimited user message) and returns a result carrying only the
validated payload and safe usage metadata.

Deliberately absent everywhere here: the raw prompt, the raw response, request
headers, API keys and any hidden reasoning."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StructuredDesignRequest:
    """One assembled request for a structured DesignSpec.

    ``system_prompt`` and ``user_message`` are fully built by the generation
    service (the user message wraps untrusted free text in a delimited
    section). ``source_selections`` is the canonical machine-value echo the
    output must reproduce exactly — passed so offline fixture providers can
    build a matching result without a network call. ``attempt`` is 1 for the
    initial request and 2 for the single allowed retry."""

    system_prompt: str
    user_message: str
    source_selections: dict
    max_output_tokens: int
    attempt: int


@dataclass(frozen=True)
class StructuredDesignResult:
    """A provider's outcome. ``payload`` is the parsed DesignSpec as a plain
    dict (or None when the model returned nothing usable / refused). Carries
    ONLY safe provenance — never the prompt, response body, headers or key."""

    payload: dict | None
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str | None
    refused: bool = False


class StructuredDesignProviderError(Exception):
    """A provider transport/API failure (auth, permission, rate limit,
    timeout, connection or server error). Carries only a generic category —
    never a provider error body — and is NEVER retried (spend may already have
    occurred)."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(f"structured design provider error: {category}")


class StructuredDesignGenerationProvider(Protocol):
    """Turns an assembled request into a structured DesignSpec result."""

    name: str

    def generate(self, request: StructuredDesignRequest) -> StructuredDesignResult: ...
