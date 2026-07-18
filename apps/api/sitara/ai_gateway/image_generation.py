"""The narrow image-generation provider contract (Phase 10).

A provider turns a fully-assembled :class:`ImageGenerationRequest` into an
asynchronous prediction and lets the caller poll or cancel it. The structures
here are the ONLY thing that crosses the boundary between the generation
pipeline (``sitara.generation``) and a concrete provider
(``sitara.ai_gateway.replicate_provider``, added in Part B).

An :class:`ImagePrediction` carries only safe structured metadata — a
prediction id, provider/model identity, a lifecycle status and, once
succeeded, a single output URL. It NEVER carries an API token, the prompt, a
raw provider error body, logs, request headers or a dashboard URL.

Reference-image conditioning is reserved for a later phase: the request field
exists (default empty tuple) but a non-empty collection must be rejected before
any provider call — never silently ignored.

This ASYNCHRONOUS prediction contract (``ImageProvider``) is the authoritative
image-generation boundary for any real/live path from Phase 10 onward. The
older synchronous ``ImageGenerationProvider`` / ``get_image_generation_provider``
in :mod:`sitara.ai_gateway.providers` / :mod:`sitara.ai_gateway.policy` is
Phase 3A demo scaffolding (``generate_image(prompt, model) -> dict``) and is not
used by the generation pipeline; it will be folded in or removed in a later
phase.
"""

from dataclasses import dataclass, field
from typing import Protocol

# Provider lifecycle states (the Replicate/official-model vocabulary).
PREDICTION_STARTING = "starting"
PREDICTION_PROCESSING = "processing"
PREDICTION_SUCCEEDED = "succeeded"
PREDICTION_FAILED = "failed"
PREDICTION_CANCELED = "canceled"
PREDICTION_ABORTED = "aborted"

PENDING_STATES = frozenset({PREDICTION_STARTING, PREDICTION_PROCESSING})
TERMINAL_FAILURE_STATES = frozenset({PREDICTION_FAILED, PREDICTION_CANCELED, PREDICTION_ABORTED})


class ReferenceImagesNotEnabled(Exception):
    """A request carried reference images, which are not enabled yet.

    Raised BEFORE any provider call so a supplied reference is never silently
    dropped. Safe message; carries no user data."""


@dataclass(frozen=True)
class ImageGenerationRequest:
    """One fully-assembled image request.

    ``prompt`` is the exact persisted ``DesignVersion.image_prompt``; ``seed``
    is generated and persisted once before submission. The remaining fields are
    the reviewed Phase 2 rendering profile. ``reference_image_urls`` is reserved
    for a later phase and must be empty."""

    prompt: str
    model: str
    seed: int
    aspect_ratio: str
    output_format: str
    output_quality: int
    safety_tolerance: int
    prompt_upsampling: bool
    reference_image_urls: tuple[str, ...] = field(default=())

    def __post_init__(self):
        if self.reference_image_urls:
            raise ReferenceImagesNotEnabled(
                "reference_images_not_enabled: reference-image conditioning is not available yet"
            )


@dataclass(frozen=True)
class ImagePrediction:
    """A provider prediction's safe, structured state. No secrets, no prompt,
    no raw error body, no dashboard URL."""

    prediction_id: str
    provider: str
    model: str
    status: str
    output_url: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status not in PENDING_STATES

    @property
    def succeeded(self) -> bool:
        return self.status == PREDICTION_SUCCEEDED


class ImageProviderError(Exception):
    """A transient provider transport/API failure on submission or polling
    (timeout, connection or transient server error). Carries only a generic
    category — never a provider error body. Classified as retryable by the
    pipeline."""

    def __init__(self, category: str, *, ambiguous_acceptance: bool = False):
        self.category = category
        # For a CREATE (submission) failure, whether the request may have been
        # accepted by the provider despite the transport failure. An ambiguous
        # acceptance must NEVER be resubmitted (conservative spend semantics).
        self.ambiguous_acceptance = ambiguous_acceptance
        super().__init__(f"image provider error: {category}")


class ImageProvider(Protocol):
    """Create, poll and cancel an asynchronous image prediction."""

    name: str

    def create_prediction(self, request: ImageGenerationRequest) -> ImagePrediction: ...

    def get_prediction(self, prediction_id: str) -> ImagePrediction: ...

    def cancel_prediction(self, prediction_id: str) -> None: ...
