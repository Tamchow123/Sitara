"""Loader/validator for the committed canonical model decision.

The CANONICAL machine-readable Phase 2 decision is version-controlled at
``docs/decisions/0001-image-model.json`` (repository root). The run-local
``outputs/runs/<run-id>/model_decision.json`` is an evidence-run mirror:
useful when the gitignored evidence is present, but never the only source of
truth — a fresh clone or CI checkout must be able to machine-verify the
decision from committed state alone.

Everything here is deterministic local file reading; no network access.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
CANONICAL_DECISION_PATH = REPO_ROOT / "docs" / "decisions" / "0001-image-model.json"

SUPPORTED_SCHEMA_VERSIONS = {1}
ALLOWED_STATUSES = {"accepted", "superseded"}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")

REQUIRED_FIELDS = (
    "schema_version",
    "decision_id",
    "status",
    "decided_at",
    "screening_run_id",
    "scoring_sha256",
    "default_model_key",
    "default_provider_model",
    "fast_model_key",
    "fast_provider_model",
    "demo_mode_provider_calls",
    "demo_mode_policy",
    "configuration_defaults",
    "visual_results",
    "operational_results",
    "excluded_models",
    "retained_challengers",
    "decision_rationale",
    "limitations",
    "open_questions",
    "next_evaluation_stages",
    "evidence_manifest",
)

# Fields on which the committed canonical decision and any run-local mirror
# must agree exactly.
DECISION_CRITICAL_FIELDS = (
    "scoring_sha256",
    "default_model_key",
    "default_provider_model",
    "fast_model_key",
    "fast_provider_model",
    "demo_mode_provider_calls",
    "configuration_defaults",
)


class DecisionValidationError(Exception):
    """The decision artefact is missing, malformed, or inconsistent."""


def validate_decision(decision: Any) -> dict[str, Any]:
    """Deterministic consistency validation. Returns the decision on success;
    raises DecisionValidationError naming the first violation."""
    if not isinstance(decision, dict):
        raise DecisionValidationError("decision document must be a JSON object")

    missing = [f for f in REQUIRED_FIELDS if f not in decision]
    if missing:
        raise DecisionValidationError(f"missing required fields: {missing}")

    if decision["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
        raise DecisionValidationError(
            f"unsupported schema_version {decision['schema_version']!r} "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )
    if decision["status"] not in ALLOWED_STATUSES:
        raise DecisionValidationError(
            f"status {decision['status']!r} is not one of {sorted(ALLOWED_STATUSES)}"
        )
    if not _SHA256_RE.match(decision["scoring_sha256"]):
        raise DecisionValidationError(
            "scoring_sha256 must be exactly 64 lowercase hexadecimal characters"
        )

    for role in ("default", "fast"):
        key = decision[f"{role}_model_key"]
        provider = decision[f"{role}_provider_model"]
        if not _PROVIDER_MODEL_RE.match(provider):
            raise DecisionValidationError(
                f"{role}_provider_model {provider!r} is not an owner/model id"
            )
        if key in decision["excluded_models"]:
            raise DecisionValidationError(
                f"{role} model {key!r} is listed in excluded_models — an "
                "excluded model can never be selected"
            )
    if decision["default_model_key"] == decision["fast_model_key"]:
        if decision["default_provider_model"] != decision["fast_provider_model"]:
            raise DecisionValidationError(
                "default and fast share a model key but disagree on the "
                "provider model id"
            )

    if decision["demo_mode_provider_calls"] != 0:
        raise DecisionValidationError(
            "demo mode must permit ZERO provider calls (fixture-only policy)"
        )
    if "fixture" not in str(decision["demo_mode_policy"]).lower():
        raise DecisionValidationError(
            "demo_mode_policy must state the fixture-only approach"
        )

    config = decision["configuration_defaults"]
    if config.get("DEFAULT_IMAGE_MODEL") != decision["default_provider_model"]:
        raise DecisionValidationError(
            "configuration_defaults.DEFAULT_IMAGE_MODEL disagrees with "
            "default_provider_model"
        )
    if config.get("FAST_IMAGE_MODEL") != decision["fast_provider_model"]:
        raise DecisionValidationError(
            "configuration_defaults.FAST_IMAGE_MODEL disagrees with "
            "fast_provider_model"
        )
    if str(config.get("DEMO_MODE", "")).lower() != "true":
        raise DecisionValidationError("configuration_defaults.DEMO_MODE must be 'true'")

    if not decision["excluded_models"]:
        raise DecisionValidationError("excluded_models must record the disqualifications")
    for model, reason in decision["excluded_models"].items():
        if not str(reason).strip():
            raise DecisionValidationError(f"excluded model {model!r} has no reason")

    open_questions = decision["open_questions"]
    if not isinstance(open_questions, list) or not open_questions:
        raise DecisionValidationError(
            "open_questions must be a non-empty list — unevaluated stages must "
            "remain visibly unresolved"
        )
    if not isinstance(decision["limitations"], list) or not decision["limitations"]:
        raise DecisionValidationError("limitations must be a non-empty list")

    entries = decision["evidence_manifest"].get("entries")
    if not entries:
        raise DecisionValidationError("evidence_manifest.entries must be non-empty")
    for entry in entries:
        path = entry.get("path", "")
        if not path or Path(path).is_absolute() or ":" in path.split("/")[0]:
            raise DecisionValidationError(
                f"evidence path must be repository-relative: {path!r}"
            )
    return decision


def load_canonical_decision(path: Path | None = None) -> dict[str, Any]:
    path = path or CANONICAL_DECISION_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DecisionValidationError(
            f"canonical decision not found at {path} — it must be committed, "
            "never only in gitignored outputs"
        ) from None
    try:
        decision = json.loads(raw)
    except ValueError as exc:
        raise DecisionValidationError(f"canonical decision is not valid JSON: {exc}") from None
    return validate_decision(decision)


def assert_mirror_agrees(canonical: dict[str, Any], mirror: dict[str, Any]) -> None:
    """The run-local evidence mirror must agree with the committed canonical
    decision on every decision-critical field."""
    for field in DECISION_CRITICAL_FIELDS:
        if canonical.get(field) != mirror.get(field):
            raise DecisionValidationError(
                f"run-local decision mirror disagrees with the canonical "
                f"decision on {field!r}: "
                f"{mirror.get(field)!r} != {canonical.get(field)!r}"
            )
