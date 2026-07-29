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

Reference-image conditioning is ENABLED (Phase 16B, ADR 0019, deliberately
overriding ADR 0014). ``reference_image_urls`` carries short-TTL presigned GET
URLs for the references the user selected, minted inside the generation job by
:mod:`sitara.generation.reference_images` and never persisted, cached or
logged. They are bounds-checked here, before any provider call: a malformed or
over-long reference is rejected outright rather than silently dropped.

This ASYNCHRONOUS prediction contract (``ImageProvider``) is the authoritative
image-generation boundary for any real/live path from Phase 10 onward, and
(via :mod:`sitara.generation.demo.image_provider`'s ``DemoImageProvider``,
added in Phase 15) for the deterministic zero-cost demo path too — both
implement this exact protocol. The older synchronous
``ImageGenerationProvider`` / ``get_image_generation_provider`` (Phase 3A demo
scaffolding, never used by the generation pipeline) was removed in Phase 15.
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


# The provider's ceiling on reference images. Sitara sends at most
# MAX_INSPIRATION_IMAGES (3); this is the backstop that a future cap increase
# cannot silently exceed.
MAX_REFERENCE_IMAGES = 8

# Generous, but bounded: a presigned S3/MinIO GET URL carries a signature and
# several query parameters, so it is long — an unbounded one is not.
_MAX_REFERENCE_URL_LENGTH = 4096


class ReferenceImagesRejected(Exception):
    """A request's reference images failed the bounds check.

    Raised BEFORE any provider call, so a malformed or over-long reference is
    never sent and never silently dropped. Safe message; carries no user data
    and never echoes a URL — a presigned URL is a bearer credential."""


@dataclass(frozen=True)
class ImageGenerationRequest:
    """One fully-assembled image request.

    ``prompt`` is the exact persisted ``DesignVersion.image_prompt``; ``seed``
    is generated and persisted once before submission. The remaining fields are
    the rendering profile.

    ``reference_image_urls`` carries the short-TTL presigned URLs of the
    references the user selected (ADR 0019). It is validated here, at the
    boundary, rather than trusted from the caller: bounded in number, https
    only, bounded in length. ``prompt_upsampling`` is ``None`` for a model that
    does not accept the parameter, and is then omitted from the payload
    entirely rather than sent as a default the model would reject."""

    prompt: str
    model: str
    seed: int
    aspect_ratio: str
    output_format: str
    output_quality: int
    safety_tolerance: int
    prompt_upsampling: bool | None
    reference_image_urls: tuple[str, ...] = field(default=())

    def __post_init__(self):
        if len(self.reference_image_urls) > MAX_REFERENCE_IMAGES:
            raise ReferenceImagesRejected(
                f"reference_images_rejected: at most {MAX_REFERENCE_IMAGES} references"
            )
        for url in self.reference_image_urls:
            # https only: a signed URL is a bearer credential and must never be
            # handed to a provider over plaintext, nor be some other scheme
            # (file://, s3://) that could point at something local.
            if (
                not isinstance(url, str)
                or not url.startswith("https://")
                or len(url) > _MAX_REFERENCE_URL_LENGTH
            ):
                raise ReferenceImagesRejected(
                    "reference_images_rejected: a reference URL is malformed or too long"
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
