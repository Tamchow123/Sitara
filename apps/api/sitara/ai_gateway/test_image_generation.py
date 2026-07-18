"""Gated Replicate image provider (Phase 10 Part B) — gate matrix and a
contract test against the pinned SDK interface. No real network: a socket guard
fails loudly on any connection attempt, and the SDK client is a fake.
"""

import socket

import httpx
import pytest

from sitara.ai_gateway.image_generation import (
    ImageGenerationRequest,
    ImagePrediction,
    ImageProviderError,
    ReferenceImagesNotEnabled,
)
from sitara.ai_gateway.policy import (
    PaidGenerationDisabled,
    generation_is_available,
    get_image_generation_provider_async,
    image_generation_is_available,
)
from sitara.ai_gateway.replicate_provider import ReplicateImageProvider

_TOKEN = "r8_test_not_a_real_token"
_MODEL = "black-forest-labs/flux-1.1-pro"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("network access attempted during image tests")

    monkeypatch.setattr(socket.socket, "connect", guard)


def _open_gates(settings):
    settings.DEMO_MODE = False
    settings.ALLOW_PAID_AI_CALLS = True
    settings.REPLICATE_API_TOKEN = _TOKEN
    settings.DEFAULT_IMAGE_MODEL = _MODEL


def _request(**overrides):
    base = dict(
        prompt="a bridal concept",
        model=_MODEL,
        seed=7,
        aspect_ratio="3:4",
        output_format="webp",
        output_quality=80,
        safety_tolerance=2,
        prompt_upsampling=False,
    )
    base.update(overrides)
    return ImageGenerationRequest(**base)


class TestImageGate:
    def test_demo_mode_is_never_available_even_with_token(self, settings):
        settings.DEMO_MODE = True
        settings.ALLOW_PAID_AI_CALLS = True
        settings.REPLICATE_API_TOKEN = _TOKEN
        settings.DEFAULT_IMAGE_MODEL = _MODEL
        assert image_generation_is_available() is False

    def test_paid_disabled_is_unavailable(self, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = False
        settings.REPLICATE_API_TOKEN = _TOKEN
        assert image_generation_is_available() is False

    def test_missing_token_is_unavailable(self, settings):
        _open_gates(settings)
        settings.REPLICATE_API_TOKEN = "   "
        assert image_generation_is_available() is False

    def test_oversized_model_is_unavailable(self, settings):
        _open_gates(settings)
        settings.DEFAULT_IMAGE_MODEL = "m" * 101
        assert image_generation_is_available() is False

    def test_all_gates_open_is_available(self, settings):
        _open_gates(settings)
        assert image_generation_is_available() is True


class TestPublicGenerationGate:
    def test_live_flag_off_keeps_public_generation_unavailable(self, settings):
        _open_gates(settings)
        settings.ANTHROPIC_API_KEY = "sk-ant-test"
        settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        settings.LIVE_GENERATION_ENABLED = False
        assert generation_is_available() is False

    def test_live_flag_on_with_everything_configured_is_available(self, settings):
        _open_gates(settings)
        settings.ANTHROPIC_API_KEY = "sk-ant-test"
        settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        settings.LIVE_GENERATION_ENABLED = True
        assert generation_is_available() is True

    def test_demo_mode_keeps_public_generation_unavailable(self, settings):
        settings.DEMO_MODE = True
        settings.ALLOW_PAID_AI_CALLS = True
        settings.LIVE_GENERATION_ENABLED = True
        assert generation_is_available() is False


class TestProviderFactory:
    def test_factory_refuses_when_gates_closed(self, settings):
        settings.DEMO_MODE = True
        with pytest.raises(PaidGenerationDisabled):
            get_image_generation_provider_async()

    def test_factory_refuses_when_token_missing(self, settings):
        _open_gates(settings)
        settings.REPLICATE_API_TOKEN = ""
        with pytest.raises(PaidGenerationDisabled):
            get_image_generation_provider_async()

    def test_factory_returns_provider_without_constructing_a_client(self, settings):
        _open_gates(settings)
        provider = get_image_generation_provider_async()
        assert isinstance(provider, ReplicateImageProvider)
        # Lazy: no network client built yet (no socket used, guarded above).
        assert provider._cached_client is None


class _FakePrediction:
    def __init__(self, *, id="pred-1", status="starting", output=None, model=_MODEL):
        self.id = id
        self.status = status
        self.output = output
        self.model = model


class _FakePredictions:
    def __init__(self, *, create_result=None, create_error=None, get_result=None):
        self._create_result = create_result
        self._create_error = create_error
        self._get_result = get_result
        self.create_calls = []
        self.get_calls = []
        self.cancel_calls = []

    def create(self, *args, model=None, version=None, input=None, **kwargs):
        self.create_calls.append({"model": model, "version": version, "input": input})
        if self._create_error is not None:
            raise self._create_error
        return self._create_result

    def get(self, prediction_id):
        self.get_calls.append(prediction_id)
        return self._get_result

    def cancel(self, prediction_id):
        self.cancel_calls.append(prediction_id)
        return _FakePrediction(id=prediction_id, status="canceled")


class _FakeClient:
    def __init__(self, predictions):
        self.predictions = predictions


class TestReplicateContract:
    def test_create_uses_model_not_version_and_the_reviewed_input(self, settings):
        _open_gates(settings)
        preds = _FakePredictions(create_result=_FakePrediction(id="pred-9", status="starting"))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        result = provider.create_prediction(_request())
        assert isinstance(result, ImagePrediction)
        assert result.prediction_id == "pred-9"
        assert result.provider == "replicate"
        call = preds.create_calls[0]
        assert call["model"] == _MODEL  # official model, not a pinned version
        assert call["version"] is None
        assert set(call["input"]) == {
            "prompt",
            "seed",
            "aspect_ratio",
            "output_format",
            "output_quality",
            "safety_tolerance",
            "prompt_upsampling",
        }
        assert call["input"]["aspect_ratio"] == "3:4"
        assert call["input"]["output_format"] == "webp"

    def test_create_rechecks_the_gate(self, settings):
        # Provider constructed while gates were open, but gate now closed:
        settings.DEMO_MODE = True
        preds = _FakePredictions(create_result=_FakePrediction())
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(ImageProviderError):
            provider.create_prediction(_request())
        assert preds.create_calls == []  # never reached the client

    def test_get_extracts_output_url(self, settings):
        _open_gates(settings)
        preds = _FakePredictions(
            get_result=_FakePrediction(
                id="pred-1", status="succeeded", output="https://replicate.delivery/x/raw.webp"
            )
        )
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        result = provider.get_prediction("pred-1")
        assert result.status == "succeeded"
        assert result.output_url == "https://replicate.delivery/x/raw.webp"

    def test_get_extracts_output_url_from_list(self, settings):
        _open_gates(settings)
        preds = _FakePredictions(
            get_result=_FakePrediction(
                status="succeeded", output=["https://replicate.delivery/x/raw.webp"]
            )
        )
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        assert provider.get_prediction("p").output_url == "https://replicate.delivery/x/raw.webp"

    def test_cancel_calls_the_sdk(self, settings):
        _open_gates(settings)
        preds = _FakePredictions()
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        provider.cancel_prediction("pred-1")
        assert preds.cancel_calls == ["pred-1"]

    def test_read_timeout_on_create_is_ambiguous(self, settings):
        _open_gates(settings)
        preds = _FakePredictions(create_error=httpx.ReadTimeout("t"))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(ImageProviderError) as exc:
            provider.create_prediction(_request())
        assert exc.value.ambiguous_acceptance is True

    def test_connect_error_on_create_is_pre_acceptance(self, settings):
        _open_gates(settings)
        preds = _FakePredictions(create_error=httpx.ConnectError("c"))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(ImageProviderError) as exc:
            provider.create_prediction(_request())
        assert exc.value.ambiguous_acceptance is False

    def test_poll_transport_error_is_retryable(self, settings):
        _open_gates(settings)

        class _Boom:
            def get(self, _id):
                raise httpx.ConnectError("x")

        provider = ReplicateImageProvider(client=_FakeClient(_Boom()))
        with pytest.raises(ImageProviderError):
            provider.get_prediction("p")

    def test_generic_sdk_error_on_create_is_non_ambiguous(self, settings):
        # A non-httpx, non-timeout SDK error means the request did not reach an
        # accepted state — classify as pre-acceptance (safe to retry).
        _open_gates(settings)

        class _ReplicateError(Exception):
            pass

        preds = _FakePredictions(create_error=_ReplicateError("model error"))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(ImageProviderError) as exc:
            provider.create_prediction(_request())
        assert exc.value.ambiguous_acceptance is False

    def test_generic_timeout_named_error_on_create_is_ambiguous(self, settings):
        # An SDK exception whose class name contains "timeout" is treated as an
        # ambiguous acceptance (conservative spend — never resubmit).
        _open_gates(settings)

        class _SDKTimeout(Exception):
            pass

        preds = _FakePredictions(create_error=_SDKTimeout("deadline"))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(ImageProviderError) as exc:
            provider.create_prediction(_request())
        assert exc.value.ambiguous_acceptance is True

    @pytest.mark.parametrize("status", ["failed", "canceled", "aborted"])
    def test_terminal_statuses_pass_through_with_no_output_url(self, settings, status):
        _open_gates(settings)
        preds = _FakePredictions(get_result=_FakePrediction(id="p", status=status, output=None))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        result = provider.get_prediction("p")
        assert result.status == status
        assert result.output_url is None
        assert result.prediction_id == "p"


class TestReferenceImagesRejected:
    def test_non_empty_reference_collection_is_rejected_at_construction(self):
        with pytest.raises(ReferenceImagesNotEnabled):
            _request(reference_image_urls=("https://replicate.delivery/x",))
