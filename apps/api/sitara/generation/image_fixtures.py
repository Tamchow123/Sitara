"""Deterministic, zero-network image fixtures (Phase 10).

Used by the Part A pipeline tests and the offline ``run_generation_fixture``
management command. Nothing here uses a network URL or a provider SDK: the fake
provider returns synthetic prediction states and the synthetic image is built
locally with Pillow. A persisted attempt driven by these fixtures can never be
mistaken for live provider output (the provider labels itself ``fixture``).
"""

import io

from PIL import Image

from sitara.ai_gateway.image_generation import (
    PREDICTION_STARTING,
    PREDICTION_SUCCEEDED,
    ImageGenerationRequest,
    ImagePrediction,
    ImageProviderError,
)

# A synthetic output URL on the allowed provider host. It is NEVER fetched — the
# fixture downloader ignores it and returns local synthetic bytes.
FIXTURE_OUTPUT_URL = "https://replicate.delivery/fixture/raw.webp"


def make_synthetic_webp(width: int = 768, height: int = 1024) -> bytes:
    """A locally-generated 3:4 RGB WebP (no network, no external asset)."""
    image = Image.new("RGB", (width, height), (198, 160, 122))
    # A couple of blocks so the bytes are non-trivial but still deterministic.
    for x in range(0, width, 64):
        for y in range(0, height, 64):
            if (x // 64 + y // 64) % 2 == 0:
                for i in range(x, min(x + 64, width)):
                    for j in range(y, min(y + 64, height)):
                        image.putpixel((i, j), (120, 40, 60))
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=80)
    return buffer.getvalue()


def make_synthetic_png(width: int = 768, height: int = 1024) -> bytes:
    """A locally-generated valid PNG (exercises non-webp staging branches)."""
    image = Image.new("RGB", (width, height), (90, 60, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def synthetic_webp_downloader(_output_url: str) -> bytes:
    """A downloader that ignores the URL and returns local synthetic WebP."""
    return make_synthetic_webp()


def invalid_bytes_downloader(_output_url: str) -> bytes:
    """Returns bytes that are not a valid image (exercises output rejection)."""
    return b"this is definitely not an image payload"


class FakeImageProvider:
    """A zero-network image provider returning scripted prediction states.

    ``poll_actions`` is a list where each entry is either a status string
    returned by :meth:`get_prediction`, or an :class:`ImageProviderError`
    instance that :meth:`get_prediction` raises (a transient transport failure).
    The last action repeats once the script is exhausted. ``create_error``, when
    set, is raised by :meth:`create_prediction`."""

    name = "fixture"

    def __init__(
        self,
        *,
        poll_actions=None,
        create_error: ImageProviderError | None = None,
        output_url: str = FIXTURE_OUTPUT_URL,
        prediction_id: str = "fixture-prediction-0001",
    ):
        self._poll_actions = (
            list(poll_actions) if poll_actions is not None else [PREDICTION_SUCCEEDED]
        )
        self._create_error = create_error
        self._output_url = output_url
        self._prediction_id = prediction_id
        self._poll_index = 0
        self.create_calls = 0
        self.get_calls = 0
        self.cancel_calls = 0
        self.last_request: ImageGenerationRequest | None = None

    def create_prediction(self, request: ImageGenerationRequest) -> ImagePrediction:
        self.create_calls += 1
        self.last_request = request
        if self._create_error is not None:
            raise self._create_error
        return ImagePrediction(
            prediction_id=self._prediction_id,
            provider=self.name,
            model=request.model,
            status=PREDICTION_STARTING,
        )

    def get_prediction(self, prediction_id: str) -> ImagePrediction:
        self.get_calls += 1
        index = min(self._poll_index, len(self._poll_actions) - 1)
        action = self._poll_actions[index]
        self._poll_index += 1
        if isinstance(action, ImageProviderError):
            raise action
        return ImagePrediction(
            prediction_id=prediction_id,
            provider=self.name,
            model="fixture-model",
            status=action,
            output_url=self._output_url if action == PREDICTION_SUCCEEDED else None,
        )

    def cancel_prediction(self, prediction_id: str) -> None:
        self.cancel_calls += 1


class InMemoryStorage:
    """A minimal private storage double (no network) with the Django Storage
    surface the pipeline uses: ``exists``, ``open``, ``save``, ``delete``.

    Mirrors ``file_overwrite=False`` semantics: saving to an existing key raises
    (the pipeline treats a distinct existing object as a staging conflict)."""

    def __init__(self):
        self._objects: dict[str, bytes] = {}

    def exists(self, key: str) -> bool:
        return key in self._objects

    def open(self, key: str, mode: str = "rb"):
        return io.BytesIO(self._objects[key])

    def save(self, key: str, content) -> str:
        if key in self._objects:
            raise FileExistsError(key)
        self._objects[key] = content.read()
        return key

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)
