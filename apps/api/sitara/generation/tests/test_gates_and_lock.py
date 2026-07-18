"""Provider gating for structured generation, and the advisory-lock contention
that prevents two commands from both spending for one design."""

import threading

import pytest

from sitara.ai_gateway.anthropic_provider import AnthropicStructuredDesignProvider
from sitara.ai_gateway.policy import (
    PaidGenerationDisabled,
    get_structured_design_generation_provider,
)
from sitara.designs.models import DesignVersion
from sitara.generation.services import (
    GenerationLocked,
    _lock_key,
    generate_design_spec_for_design,
)

from . import fakes
from .factory import make_complete_design


class TestGating:
    @pytest.mark.parametrize(
        "demo,allow",
        [(True, False), (True, True), (False, False)],
    )
    def test_refused_unless_both_gates_open(self, settings, demo, allow):
        settings.DEMO_MODE = demo
        settings.ALLOW_PAID_AI_CALLS = allow
        # Even a present key never enables it.
        settings.ANTHROPIC_API_KEY = "sk-ant-not-a-real-key"
        with pytest.raises(PaidGenerationDisabled):
            get_structured_design_generation_provider()

    def test_both_gates_open_and_configured_returns_provider_without_network(self, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
        settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        provider = get_structured_design_generation_provider()
        # Constructed but NOT connected — no network client created yet.
        assert isinstance(provider, AnthropicStructuredDesignProvider)
        assert provider.name == "anthropic"

    def test_missing_key_refuses_before_constructing_a_client(self, settings):
        # Gates open and the capability exists, but no API key is configured:
        # the factory must fail closed BEFORE any Anthropic client is built.
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        settings.ANTHROPIC_API_KEY = ""
        settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        with pytest.raises(PaidGenerationDisabled):
            get_structured_design_generation_provider()

    def test_model_exceeding_field_bound_refuses(self, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
        settings.ANTHROPIC_MODEL = "m" * 101
        with pytest.raises(PaidGenerationDisabled):
            get_structured_design_generation_provider()

    def test_placeholder_credentials_are_never_ready(self, settings):
        # Round-4 CDX-002: a placeholder-marked credential or model is treated
        # as ABSENT configuration — the availability gates stay closed even
        # with both explicit gates open.
        from sitara.ai_gateway.policy import (
            image_generation_is_available,
            structured_design_generation_is_available,
        )

        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"
        settings.DEFAULT_IMAGE_MODEL = "black-forest-labs/flux-1.1-pro"
        settings.ANTHROPIC_API_KEY = "change-me-key"
        settings.REPLICATE_API_TOKEN = "__REPLACE_ME__"
        assert structured_design_generation_is_available() is False
        assert image_generation_is_available() is False
        # A placeholder MODEL is equally unconfigured.
        settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
        settings.REPLICATE_API_TOKEN = "r8-test-not-a-real-token"
        settings.DEFAULT_IMAGE_MODEL = "change-me-model"
        assert image_generation_is_available() is False
        # Genuine-looking values are ready (both gates + config complete).
        settings.DEFAULT_IMAGE_MODEL = "black-forest-labs/flux-1.1-pro"
        assert image_generation_is_available() is True


@pytest.mark.django_db(transaction=True)
def test_lock_contention_blocks_before_provider():
    design = make_complete_design()
    key = _lock_key(design.id)

    holding = threading.Event()
    release = threading.Event()

    def holder():
        from django.db import connection as thread_connection

        try:
            with thread_connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s)", [key])
                holding.set()
                release.wait(timeout=10)
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
        finally:
            thread_connection.close()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert holding.wait(timeout=10)
        provider = fakes.SequenceProvider([])
        with pytest.raises(GenerationLocked):
            generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 0
        assert DesignVersion.objects.filter(design=design).count() == 0
    finally:
        release.set()
        thread.join(timeout=10)


def test_placeholder_marker_lists_stay_in_sync():
    # The startup validation (config.settings) and the runtime availability
    # gates (policy) each hold a frozen copy of the placeholder markers; a
    # marker added to one and not the other would silently split enforcement.
    import config.settings as settings_module
    from sitara.ai_gateway import policy

    assert tuple(policy._PLACEHOLDER_MARKERS) == tuple(settings_module._PLACEHOLDER_MARKERS)
