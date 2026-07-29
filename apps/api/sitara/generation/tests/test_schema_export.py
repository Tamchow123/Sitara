"""The committed DesignSpec JSON Schemas are deterministic and up to date."""

import json

import pytest

from sitara.generation.design_spec import SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS
from sitara.generation.schema_io import render_schema, schema_path

_VERSIONS = sorted(SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS)


@pytest.mark.parametrize("version", _VERSIONS)
def test_render_is_deterministic(version):
    assert render_schema(version) == render_schema(version)


@pytest.mark.parametrize("version", _VERSIONS)
def test_committed_schema_matches_regeneration(version):
    committed = schema_path(version).read_text(encoding="utf-8")
    assert committed == render_schema(version), (
        f"design_spec_v{version}.json is stale; run "
        "`python manage.py export_design_spec_schema` and commit the result."
    )


@pytest.mark.parametrize("version", _VERSIONS)
def test_schema_has_no_forbidden_content(version):
    schema = json.loads(render_schema(version))
    text = json.dumps(schema)
    # No timestamps, machine paths, credentials or provider model name leak
    # into a JSON Schema; assert a few obvious markers are absent.
    for marker in ("/app/", "C:\\\\", "sk-ant", "claude-", "ANTHROPIC", "password"):
        assert marker not in text
    # schema_version is a const literal equal to the version, not an option
    # enum of questionnaire values.
    assert schema["properties"]["schema_version"]["const"] == version
    assert schema["additionalProperties"] is False


def test_v2_adds_the_canonical_neckline():
    schema = json.loads(render_schema(2))
    source = schema["$defs"]["SourceSelectionsV2"]["properties"]
    assert "neckline_style" in source
    # Version 1 must NOT carry the dedicated neckline field.
    v1 = json.loads(render_schema(1))
    assert "neckline_style" not in v1["$defs"]["SourceSelections"]["properties"]
