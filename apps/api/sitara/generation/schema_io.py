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

from .design_spec import SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS, design_spec_json_schema

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
# Version 1 is kept as a module-level constant for the many existing callers /
# tests that reference it directly; every supported version is derived below.
SCHEMA_PATH = SCHEMA_DIR / "design_spec_v1.json"


def schema_path(version: int) -> Path:
    """The committed schema file path for a supported DesignSpec version."""
    return SCHEMA_DIR / f"design_spec_v{version}.json"


def render_schema(version: int = 1) -> str:
    """The canonical, deterministic JSON Schema text (with trailing newline)."""
    schema = design_spec_json_schema(version)
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_schema(version: int = 1) -> Path:
    """Atomically write one version's canonical schema to its committed path."""
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    path = schema_path(version)
    text = render_schema(version)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def write_all_schemas() -> list[Path]:
    """Write every supported version's committed schema; returns the paths."""
    return [write_schema(version) for version in sorted(SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS)]
