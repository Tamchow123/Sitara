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
        # Phase 16: public live availability also requires a valid cost config
        # (a positive daily budget ceiling and a valid pricing profile).
        settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 1_000_000
        settings.LIVE_GENERATION_PRICING_PROFILE = "test-profile-1"
        assert generation_is_available() is True

    def test_live_flag_on_without_valid_cost_config_is_unavailable(self, settings):
        # Phase 16: a complete provider configuration is NOT enough — an absent
        # cost ceiling / pricing profile keeps public live generation unavailable.
        _open_gates(settings)
        settings.ANTHROPIC_API_KEY = "sk-ant-test"
        settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        settings.LIVE_GENERATION_ENABLED = True
        settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 0
        settings.LIVE_GENERATION_PRICING_PROFILE = ""
        assert generation_is_available() is False

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

    def test_multi_url_output_is_rejected_not_truncated(self, settings):
        # Spec §24: a succeeded prediction must contain EXACTLY ONE output URL.
        # A multi-element list is an unexpected provider shape — never silently
        # take the first element; the missing output_url makes the pipeline
        # fail the attempt as image_output_invalid.
        _open_gates(settings)
        preds = _FakePredictions(
            get_result=_FakePrediction(
                status="succeeded",
                output=[
                    "https://replicate.delivery/x/raw.webp",
                    "https://replicate.delivery/y/raw.webp",
                ],
            )
        )
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        assert provider.get_prediction("p").output_url is None

    def test_empty_list_output_yields_no_url(self, settings):
        _open_gates(settings)
        preds = _FakePredictions(get_result=_FakePrediction(status="succeeded", output=[]))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        assert provider.get_prediction("p").output_url is None

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

    def test_generic_sdk_error_on_create_is_ambiguous(self, settings):
        # An SDK error can occur AFTER the provider accepted (and billed) the
        # request — e.g. while parsing the response. ONLY the provably
        # pre-acceptance connect failures may ever retry; every other create
        # exception resolves conservatively as ambiguous (never resubmit).
        _open_gates(settings)

        class _ReplicateError(Exception):
            pass

        preds = _FakePredictions(create_error=_ReplicateError("model error"))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(ImageProviderError) as exc:
            provider.create_prediction(_request())
        assert exc.value.ambiguous_acceptance is True

    def test_generic_timeout_named_error_on_create_is_ambiguous(self, settings):
        _open_gates(settings)

        class _SDKTimeout(Exception):
            pass

        preds = _FakePredictions(create_error=_SDKTimeout("deadline"))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(ImageProviderError) as exc:
            provider.create_prediction(_request())
        assert exc.value.ambiguous_acceptance is True

    def test_create_returning_no_id_is_ambiguous(self, settings):
        # An accepted create whose response carries no usable prediction id can
        # never be reconciled — resolve as ambiguous, never let a retry create
        # a second billed prediction.
        _open_gates(settings)
        preds = _FakePredictions(create_result=_FakePrediction(id="", status="starting"))
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(ImageProviderError) as exc:
            provider.create_prediction(_request())
        assert exc.value.ambiguous_acceptance is True

    def test_create_returning_none_is_ambiguous(self, settings):
        _open_gates(settings)
        preds = _FakePredictions(create_result=None)
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(ImageProviderError) as exc:
            provider.create_prediction(_request())
        assert exc.value.ambiguous_acceptance is True

    def test_create_returning_overlong_id_is_ambiguous(self, settings):
        # An id that cannot be persisted (column bound 128) cannot be polled.
        _open_gates(settings)
        preds = _FakePredictions(create_result=_FakePrediction(id="p" * 129, status="starting"))
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
    """Reference-image conditioning stays disabled (Phase 13 §17): no
    implementation exists to enable, so this remains fail-closed regardless
    of any gate state."""

    def test_non_empty_reference_collection_is_rejected_at_construction(self):
        with pytest.raises(ReferenceImagesNotEnabled):
            _request(reference_image_urls=("https://replicate.delivery/x",))

    def test_default_request_always_carries_an_empty_tuple(self):
        assert _request().reference_image_urls == ()

    def test_rejected_even_when_every_live_gate_is_open(self, settings):
        _open_gates(settings)
        with pytest.raises(ReferenceImagesNotEnabled):
            _request(reference_image_urls=("https://replicate.delivery/catalogue-image.webp",))

    def test_zero_provider_clients_instantiated_on_rejection(self, monkeypatch):
        def _fail_if_constructed(*args, **kwargs):
            raise AssertionError("no provider client may be constructed before the reject")

        monkeypatch.setattr(ReplicateImageProvider, "__init__", _fail_if_constructed)
        with pytest.raises(ReferenceImagesNotEnabled):
            _request(reference_image_urls=("https://replicate.delivery/x",))

    def test_create_prediction_payload_has_no_reference_field(self, settings):
        # A request that reached create_prediction already carries an empty
        # tuple (construction rejects any other value) — confirm the
        # Replicate payload builder never reads a reference-image key even
        # structurally, so a future field addition to the request dataclass
        # could not silently start forwarding one.
        _open_gates(settings)
        preds = _FakePredictions(create_result=_FakePrediction(id="p", status="starting"))
        client = _FakeClient(preds)
        provider = ReplicateImageProvider(client=client)
        provider.create_prediction(_request())
        model_input = preds.create_calls[0]["input"]
        assert "reference_image_urls" not in model_input
        assert not any("reference" in str(key).lower() for key in model_input)


class TestWorkerInterruption:
    def test_soft_time_limit_propagates_from_create_not_misclassified(self, settings):
        # A worker soft-time-limit interruption mid-create must propagate (the
        # pipeline handles it as a retry) — NEVER be classified as a safe
        # pre-acceptance create failure, which would clear the submit-once
        # marker and permit a duplicate paid submission.
        from celery.exceptions import SoftTimeLimitExceeded

        _open_gates(settings)
        preds = _FakePredictions(create_error=SoftTimeLimitExceeded())
        provider = ReplicateImageProvider(client=_FakeClient(preds))
        with pytest.raises(SoftTimeLimitExceeded):
            provider.create_prediction(_request())

    def test_soft_time_limit_propagates_from_poll(self, settings):
        from celery.exceptions import SoftTimeLimitExceeded

        _open_gates(settings)

        class _Interrupted:
            def get(self, _id):
                raise SoftTimeLimitExceeded()

        provider = ReplicateImageProvider(client=_FakeClient(_Interrupted()))
        with pytest.raises(SoftTimeLimitExceeded):
            provider.get_prediction("p")

    def test_soft_time_limit_propagates_from_cancel(self, settings):
        from celery.exceptions import SoftTimeLimitExceeded

        _open_gates(settings)

        class _InterruptedCancel:
            def cancel(self, _id):
                raise SoftTimeLimitExceeded()

        provider = ReplicateImageProvider(client=_FakeClient(_InterruptedCancel()))
        # Propagates rather than being silently returned from (best-effort
        # absorption applies to every OTHER failure only).
        with pytest.raises(SoftTimeLimitExceeded):
            provider.cancel_prediction("p")

    def test_soft_time_limit_propagates_from_lazy_client_construction(self, settings, monkeypatch):
        from celery.exceptions import SoftTimeLimitExceeded

        _open_gates(settings)

        def interrupted_client(**kwargs):
            raise SoftTimeLimitExceeded()

        monkeypatch.setattr("replicate.client.Client", interrupted_client)
        provider = ReplicateImageProvider()  # no injected client — lazy path
        with pytest.raises(SoftTimeLimitExceeded):
            provider.get_prediction("p")

    def test_soft_time_limit_propagates_from_anthropic_client_construction(
        self, settings, monkeypatch
    ):
        # The twin structured-text provider gets the same guarantee: a worker
        # interruption during client construction propagates, never becoming a
        # terminal StructuredDesignProviderError.
        from celery.exceptions import SoftTimeLimitExceeded

        from sitara.ai_gateway.anthropic_provider import AnthropicStructuredDesignProvider

        def interrupted_client(**kwargs):
            raise SoftTimeLimitExceeded()

        monkeypatch.setattr("anthropic.Anthropic", interrupted_client)
        provider = AnthropicStructuredDesignProvider()
        with pytest.raises(SoftTimeLimitExceeded):
            provider._client()


class TestPinnedSdkContract:
    """A genuine contract test against the REAL pinned replicate==1.0.7 SDK:
    the adapter's calls must match the installed package's public signatures.
    Pure introspection — no client use, no network (socket guard active)."""

    def test_client_init_accepts_api_token_and_timeout(self):
        import inspect

        from replicate.client import Client

        params = inspect.signature(Client.__init__).parameters
        assert "api_token" in params
        assert "timeout" in params

    def test_predictions_namespace_supports_create_get_cancel(self):
        import inspect

        from replicate.prediction import Predictions

        create_params = inspect.signature(Predictions.create).parameters
        # The adapter calls create(model=..., input=...) — keyword form.
        assert "model" in create_params
        assert "input" in create_params
        get_params = inspect.signature(Predictions.get).parameters
        assert "id" in get_params
        cancel_params = inspect.signature(Predictions.cancel).parameters
        assert "id" in cancel_params

    def test_prediction_object_exposes_the_fields_the_adapter_reads(self):
        from replicate.prediction import Prediction

        annotations = getattr(Prediction, "__annotations__", {})
        for field in ("id", "status", "output", "model"):
            assert field in annotations
