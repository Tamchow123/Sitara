"""Deterministic serialisation of the committed DesignSpec JSON Schema.

One helper, shared by the ``export_design_spec_schema`` management command and
the byte-identity test, so the file the command writes and the file the test
compares against can never disagree. The output has sorted keys, two-space
indentation and a trailing newline — no timestamps, machine paths,
credentials, provider model name or private data (a JSON Schema has none of
these), and the questionnaire's option lists are never duplicated as enums
(source-selection values are a PATTERN in the model)."""

import json
from pathlib import Path

from .design_spec import design_spec_json_schema

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
SCHEMA_PATH = SCHEMA_DIR / "design_spec_v1.json"


def render_schema() -> str:
    """The canonical, deterministic JSON Schema text (with trailing newline)."""
    schema = design_spec_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_schema() -> Path:
    """Atomically write the canonical schema to the committed path."""
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    text = render_schema()
    tmp = SCHEMA_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(SCHEMA_PATH)
    return SCHEMA_PATH
