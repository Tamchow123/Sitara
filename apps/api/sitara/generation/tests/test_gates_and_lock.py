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
