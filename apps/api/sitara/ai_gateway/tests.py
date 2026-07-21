"""Fail-closed live-provider policy safety properties.

No test here touches the network: a socket guard makes any accidental
connection attempt fail loudly."""

import socket

import pytest

from sitara.ai_gateway.policy import (
    IMAGE_PROVIDER_IMPLEMENTED,
    STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED,
    GenerationPolicy,
    PaidGenerationDisabled,
    generation_is_available,
    get_image_generation_provider_async,
    structured_design_generation_is_available,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any socket connection in these tests is a bug."""

    def guard(*args, **kwargs):
        raise AssertionError("network access attempted during ai_gateway tests")

    monkeypatch.setattr(socket.socket, "connect", guard)


class TestGenerationCapability:
    """The capability policy combines environment authorisation with
    implementation availability, so the public endpoint and the provider
    factory can never contradict each other."""

    ALL_GATE_COMBINATIONS = [(True, False), (True, True), (False, False), (False, True)]

    def test_capability_flags_are_code_level(self):
        # Structured-text (Phase 8) and image generation (Phase 10 Part B) are
        # both implemented; the public gate stays closed via LIVE_GENERATION_ENABLED.
        assert STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED is True
        assert IMAGE_PROVIDER_IMPLEMENTED is True

    @pytest.mark.parametrize("demo,allow", ALL_GATE_COMBINATIONS)
    def test_structured_generation_availability_needs_both_gates(self, settings, demo, allow):
        settings.DEMO_MODE = demo
        settings.ALLOW_PAID_AI_CALLS = allow
        # A complete Anthropic configuration is also required; provide it so this
        # parametrisation isolates the two environment gates.
        settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
        settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        expected = (not demo) and allow  # structured capability is implemented
        assert structured_design_generation_is_available() is expected

    def test_availability_requires_a_configured_key(self, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        settings.ANTHROPIC_API_KEY = "   "  # blank after stripping
        assert structured_design_generation_is_available() is False

    def test_availability_requires_a_model_within_field_bound(self, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
        settings.ANTHROPIC_MODEL = "m" * 101  # exceeds the persisted 100-char bound
        assert structured_design_generation_is_available() is False

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
        assert reported is False

    def test_both_gates_open_endpoint_still_reports_disabled(self, client, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        body = client.get("/api/v1/config/public").json()
        assert body["generation_enabled"] is False

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
            get_image_generation_provider_async()
        assert token not in str(excinfo.value)
