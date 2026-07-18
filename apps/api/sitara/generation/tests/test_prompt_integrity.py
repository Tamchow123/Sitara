"""Phase 9 integrity: the prompt path makes no network call and constructs no
provider client. The autouse ``no_network`` guard (conftest) fails any socket
connection, so a passing build proves zero network access."""

import inspect

import pytest

from sitara.generation import prompt_builder, prompt_service
from sitara.generation.context import build_generation_context
from sitara.generation.services import generate_design_spec_for_design

from . import fakes
from .factory import make_complete_design

pytestmark = pytest.mark.django_db


def test_building_and_storing_makes_no_network_call():
    design = make_complete_design()
    ss = build_generation_context(design).source_selections
    version = generate_design_spec_for_design(
        design, provider=fakes.SequenceProvider([fakes.valid_result(ss)])
    )
    # Runs under the autouse socket guard; any provider call would raise.
    prompt_service.build_and_store_image_prompt(version)


def test_prompt_source_imports_no_provider_sdk():
    # The builder/persistence logic must not import a provider SDK.
    source = inspect.getsource(prompt_builder) + inspect.getsource(prompt_service)
    lowered = source.lower()
    for marker in ("import anthropic", "import replicate", "from anthropic", "from replicate"):
        assert marker not in lowered
