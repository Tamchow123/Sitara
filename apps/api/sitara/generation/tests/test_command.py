"""The generate_spec management command: offline fixture and live gates."""

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from sitara.designs.models import DesignVersion

from .factory import make_complete_design

pytestmark = pytest.mark.django_db


def _run(*args):
    out = io.StringIO()
    call_command("generate_spec", *args, stdout=out)
    return out.getvalue()


class TestFixtureMode:
    def test_fixture_mode_creates_one_version_labelled_fixture(self):
        design = make_complete_design()
        output = _run("--design", str(design.id), "--fixture", "valid")
        version = DesignVersion.objects.get(design=design)
        assert version.design_spec_provider == "fixture"
        assert "no network calls" in output
        assert str(version.id) in output

    def test_show_spec_prints_the_persisted_spec_only(self):
        design = make_complete_design()
        output = _run("--design", str(design.id), "--fixture", "valid", "--show-spec")
        assert '"schema_version"' in output
        # No prompt or key markers.
        assert "You are helping Sitara" not in output

    def test_rerun_on_the_same_design_is_refused(self):
        design = make_complete_design()
        _run("--design", str(design.id), "--fixture", "valid")
        with pytest.raises(CommandError):
            _run("--design", str(design.id), "--fixture", "valid")
        assert DesignVersion.objects.filter(design=design).count() == 1


class TestModeGuards:
    def test_no_mode_makes_zero_calls_and_no_version(self):
        design = make_complete_design()
        output = _run("--design", str(design.id))
        assert "Zero provider calls" in output
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_confirm_live_with_closed_gates_errors_and_persists_nothing(self, settings):
        settings.DEMO_MODE = True  # gates closed
        design = make_complete_design()
        with pytest.raises(CommandError):
            _run("--design", str(design.id), "--confirm-live")
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_confirm_live_missing_key_errors(self, settings):
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = True
        settings.ANTHROPIC_API_KEY = ""  # no key
        design = make_complete_design()
        with pytest.raises(CommandError):
            _run("--design", str(design.id), "--confirm-live")
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_unknown_design_errors(self):
        import uuid

        with pytest.raises(CommandError):
            _run("--design", str(uuid.uuid4()), "--fixture", "valid")
