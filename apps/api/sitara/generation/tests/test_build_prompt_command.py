"""The build_image_prompt management command: offline, safe, idempotent."""

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from sitara.designs.models import DesignVersion
from sitara.generation.context import build_generation_context
from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION
from sitara.generation.services import generate_design_spec_for_design

from . import fakes
from .factory import COMPLETE_ANSWERS, make_complete_design

pytestmark = pytest.mark.django_db


def _run(*args):
    out = io.StringIO()
    call_command("build_image_prompt", *args, stdout=out)
    return out.getvalue()


def _version_with_spec() -> DesignVersion:
    design = make_complete_design()
    ss = build_generation_context(design).source_selections
    return generate_design_spec_for_design(
        design, provider=fakes.SequenceProvider([fakes.valid_result(ss)])
    )


def test_builds_and_reports_without_leaking_user_data():
    version = _version_with_spec()
    output = _run("--design-version", str(version.id))
    version.refresh_from_db()
    assert str(version.id) in output
    assert f"prompt_builder_version={PROMPT_BUILDER_VERSION}" in output
    assert f"prompt_chars={len(version.image_prompt)}" in output
    # Without --show-prompt the prompt body is not printed, and no user answer
    # free text or provider markers leak.
    assert COMPLETE_ANSWERS["final_notes"] not in output
    assert "anthropic" not in output.lower()


def test_show_prompt_prints_only_the_persisted_prompt():
    version = _version_with_spec()
    output = _run("--design-version", str(version.id), "--show-prompt")
    version.refresh_from_db()
    assert version.image_prompt in output


def test_idempotent_for_already_matching_prompt():
    version = _version_with_spec()
    _run("--design-version", str(version.id))
    version.refresh_from_db()
    first = version.image_prompt
    _run("--design-version", str(version.id))  # must not raise
    version.refresh_from_db()
    assert version.image_prompt == first


def test_refuses_when_version_has_no_design_spec():
    design = make_complete_design()
    version = DesignVersion.objects.create(design=design, version_number=1)
    with pytest.raises(CommandError):
        _run("--design-version", str(version.id))


def test_unknown_version_is_a_command_error():
    with pytest.raises(CommandError):
        _run("--design-version", "00000000-0000-0000-0000-000000000000")


def test_invalid_uuid_is_a_command_error():
    with pytest.raises(CommandError):
        _run("--design-version", "not-a-uuid")
