"""The committed DesignSpec JSON Schema is deterministic and up to date."""

import json

from sitara.generation.design_spec import DESIGN_SPEC_SCHEMA_VERSION
from sitara.generation.schema_io import SCHEMA_PATH, render_schema


def test_render_is_deterministic():
    assert render_schema() == render_schema()


def test_committed_schema_matches_regeneration():
    committed = SCHEMA_PATH.read_text(encoding="utf-8")
    assert committed == render_schema(), (
        "design_spec_v1.json is stale; run "
        "`python manage.py export_design_spec_schema` and commit the result."
    )


def test_schema_has_no_forbidden_content():
    schema = json.loads(render_schema())
    text = json.dumps(schema)
    # No timestamps, machine paths, credentials or provider model name leak
    # into a JSON Schema; assert a few obvious markers are absent.
    for marker in ("/app/", "C:\\\\", "sk-ant", "claude-", "ANTHROPIC", "password"):
        assert marker not in text
    # schema_version is a const literal 1, not an option enum of questionnaire
    # values.
    assert schema["properties"]["schema_version"]["const"] == DESIGN_SPEC_SCHEMA_VERSION
    assert schema["additionalProperties"] is False
