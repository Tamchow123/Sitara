"""Fail-closed provider policy — the safety properties Phase 3A must prove.

No test here touches the network: a socket guard makes any accidental
connection attempt fail loudly."""

import socket

import pytest

from sitara.ai_gateway.policy import (
    PAID_PROVIDERS_IMPLEMENTED,
    GenerationPolicy,
    PaidGenerationDisabled,
    generation_is_available,
    get_image_generation_provider,
    get_structured_design_provider,
)
from sitara.ai_gateway.providers import (
    DemoImageGenerationProvider,
    DemoStructuredDesignProvider,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any socket connection in these tests is a bug."""

    def guard(*args, **kwargs):
        raise AssertionError("network access attempted during ai_gateway tests")

    monkeypatch.setattr(socket.socket, "connect", guard)


class TestDemoModeAlwaysWins:
    def test_demo_mode_selects_demo_providers(self, settings):
        settings.DEMO_MODE = True
        settings.ALLOW_PAID_AI_CALLS = False
        assert isinstance(get_structured_design_provider(), DemoStructuredDesignProvider)
        assert isinstance(get_image_generation_provider(), DemoImageGenerationProvider)

    def test_a_token_does_not_bypass_demo_mode(self, settings):
        settings.DEMO_MODE = True
        settings.ALLOW_PAID_AI_CALLS = True  # even with the second gate open
        settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
        settings.REPLICATE_API_TOKEN = "r8_test_not_a_real_token"
        assert isinstance(get_structured_design_provider(), DemoStructuredDesignProvider)
        assert isinstance(get_image_generation_provider(), DemoImageGenerationProvider)


class TestPaidGatesFailClosed:
    def test_allow_paid_false_blocks_paid_providers(self, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = False
        with pytest.raises(PaidGenerationDisabled, match="ALLOW_PAID_AI_CALLS"):
            get_structured_design_provider()
        with pytest.raises(PaidGenerationDisabled, match="ALLOW_PAID_AI_CALLS"):
            get_image_generation_provider()

    def test_both_gates_are_required_and_phase_3a_has_no_paid_path(self, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        # Both gates open, but Phase 3A implements no paid provider:
        with pytest.raises(PaidGenerationDisabled, match="not implemented"):
            get_image_generation_provider()

    def test_policy_paid_calls_permitted_needs_both_gates(self, settings):
        cases = [
            (True, True, False),
            (True, False, False),
            (False, False, False),
            (False, True, True),
        ]
        for demo, allow, expected in cases:
            settings.DEMO_MODE = demo
            settings.ALLOW_PAID_AI_CALLS = allow
            assert GenerationPolicy.from_settings().paid_calls_permitted is expected

    def test_refusal_messages_never_contain_tokens(self, settings):
        token = "r8_super_secret_should_never_appear"
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = False
        settings.REPLICATE_API_TOKEN = token
        settings.ANTHROPIC_API_KEY = "sk-ant-" + token
        with pytest.raises(PaidGenerationDisabled) as excinfo:
            get_image_generation_provider()
        assert token not in str(excinfo.value)


class TestGenerationCapability:
    """The capability policy combines environment authorisation with
    implementation availability, so the public endpoint and the provider
    factory can never contradict each other."""

    ALL_GATE_COMBINATIONS = [(True, False), (True, True), (False, False), (False, True)]

    def test_capability_flag_is_code_level_and_off_in_phase_3a(self):
        assert PAID_PROVIDERS_IMPLEMENTED is False

    @pytest.mark.parametrize("demo,allow", ALL_GATE_COMBINATIONS)
    def test_generation_unavailable_for_every_gate_combination(self, settings, demo, allow):
        settings.DEMO_MODE = demo
        settings.ALLOW_PAID_AI_CALLS = allow
        assert generation_is_available() is False

    @pytest.mark.parametrize("demo,allow", ALL_GATE_COMBINATIONS)
    def test_endpoint_and_provider_policy_agree(self, client, settings, demo, allow):
        """generation_enabled=true would REQUIRE the factory to hand out a
        paid provider without raising; while that is impossible, the
        endpoint must say false. Checked for every gate combination."""
        settings.DEMO_MODE = demo
        settings.ALLOW_PAID_AI_CALLS = allow
        reported = client.get("/api/v1/config/public").json()["generation_enabled"]
        assert reported == generation_is_available()
        if demo:
            # Demo path serves fixtures; that is not "generation enabled".
            assert isinstance(get_image_generation_provider(), DemoImageGenerationProvider)
            assert reported is False
        else:
            with pytest.raises(PaidGenerationDisabled):
                get_image_generation_provider()
            assert reported is False

    def test_both_gates_open_endpoint_still_reports_disabled(self, client, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        body = client.get("/api/v1/config/public").json()
        assert body["generation_enabled"] is False
        with pytest.raises(PaidGenerationDisabled, match="not implemented"):
            get_structured_design_provider()


class TestDemoProvidersAreDeterministicAndLocal:
    def test_design_spec_fixture_is_deterministic(self):
        provider = DemoStructuredDesignProvider()
        brief = {"garment": "lehenga", "ceremony": "walima", "palette": "ivory and gold"}
        first = provider.generate_design_spec(brief)
        second = provider.generate_design_spec(dict(reversed(list(brief.items()))))
        assert first == second
        assert first["provider"] == "demo"
        assert first["paid_call"] is False

    def test_image_fixture_is_deterministic_and_records_model(self, settings):
        provider = DemoImageGenerationProvider()
        result = provider.generate_image("a prompt", model=settings.DEFAULT_IMAGE_MODEL)
        again = provider.generate_image("a prompt", model=settings.DEFAULT_IMAGE_MODEL)
        assert result == again
        assert result["model_requested"] == "black-forest-labs/flux-1.1-pro"
        assert result["paid_call"] is False
        assert result["image_ref"].startswith("demo-fixtures/")
