"""Provenance records: one JSON file per attempted generation.

Interrupted runs stay recoverable because every result is durable the moment
it happens, and the runner treats an existing record for a request ID as
"already done" on resume. Existing records and output files are never
silently overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

ResultStatus = Literal["succeeded", "failed", "skipped"]

# Every provenance field required of a result record. Tests assert this set.
REQUIRED_PROVENANCE_FIELDS = frozenset(
    {
        "run_id",
        "stage",
        "request_id",
        "brief_id",
        "garment",
        "ceremony",
        "tags",
        "model_key",
        "replicate_id",
        "model_version",
        "provider_prediction_id",
        "prompt_format",
        "prompt_text",
        "negative_text",
        "json_payload",
        "inspiration_mode",
        "reference_ids",
        "kind",
        "refinement_id",
        "refinement_strategy",
        "base_request_id",
        "seed",
        "input_params",
        "aspect_ratio",
        "width",
        "height",
        "started_at",
        "completed_at",
        "latency_seconds",
        "status",
        "error_category",
        "error_message",
        "estimated_max_cost_usd",
        "reconciled_cost_usd",
        "cost_basis",
        "provider_create_retries",
        "rate_limit_wait_seconds",
        "output_path",
        "output_mime_type",
        "output_sha256",
        "pricing_checked_on",
        "git_commit",
    }
)


class ResultRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    stage: str
    request_id: str
    brief_id: str
    garment: str
    ceremony: str | None
    tags: list[str]
    model_key: str
    replicate_id: str
    model_version: str | None
    provider_prediction_id: str | None
    prompt_format: str
    prompt_text: str | None
    negative_text: str | None
    json_payload: dict[str, Any] | None
    inspiration_mode: str
    reference_ids: list[str]
    kind: str
    refinement_id: str | None
    refinement_strategy: str | None
    base_request_id: str | None
    seed: int | None
    input_params: dict[str, Any]
    aspect_ratio: str
    width: int | None
    height: int | None
    started_at: str | None
    completed_at: str | None
    latency_seconds: float | None
    status: ResultStatus
    error_category: str | None
    error_message: str | None
    estimated_max_cost_usd: float
    # The ledger-accounted figure. NOT a provider-reported charge; see
    # cost_basis for how it was derived.
    reconciled_cost_usd: float | None
    # provider_reported | calculated | reserved_conservative (None for
    # skips/pre-spend failures where nothing was accounted).
    cost_basis: str | None
    # 429 retry metadata: how many creation retries the provider throttling
    # forced, and the total seconds spent waiting on rate limits (both None
    # on skips and on failures where creation never succeeded).
    provider_create_retries: int | None
    rate_limit_wait_seconds: float | None
    output_path: str | None
    output_mime_type: str | None
    output_sha256: str | None
    pricing_checked_on: str
    git_commit: str | None


class ResultStoreError(Exception):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_git_commit(repo_dir: Path) -> str | None:
    """Best-effort experiment provenance; None when git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


class ResultStore:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.results_dir = run_dir / "results"
        self.images_dir = run_dir / "images"
        self.attempts_dir = run_dir / "attempts"

    def record_path(self, request_id: str) -> Path:
        return self.results_dir / f"{request_id}.json"

    def image_path(self, request_id: str, extension: str) -> Path:
        return self.images_dir / f"{request_id}{extension}"

    def exists(self, request_id: str) -> bool:
        return self.record_path(request_id).exists()

    def load(self, request_id: str) -> ResultRecord:
        path = self.record_path(request_id)
        return ResultRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def load_all(self) -> list[ResultRecord]:
        if not self.results_dir.exists():
            return []
        records = [
            ResultRecord.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(self.results_dir.glob("*.json"))
        ]
        return records

    def save(self, record: ResultRecord, *, allow_replace_failed: bool = False) -> Path:
        """Persist a record. Never silently overwrites: an existing record may
        only be replaced when it is a failed attempt being retried, and only
        when the caller passes allow_replace_failed explicitly."""
        path = self.record_path(record.request_id)
        if path.exists():
            existing = self.load(record.request_id)
            if not (allow_replace_failed and existing.status == "failed"):
                raise ResultStoreError(
                    f"result record already exists for {record.request_id!r}; "
                    "refusing to overwrite"
                )
        self.results_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return path

    def write_json(self, name: str, payload: Any) -> Path:
        """Write an auxiliary run artefact (plan snapshot, key mapping)."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.run_dir / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path

    # -- attempt journal (crash-safe resume) ---------------------------------
    # An attempt record is persisted BEFORE submission ({"state": "reserved"})
    # and updated with the provider prediction id IMMEDIATELY after Replicate
    # accepts the request ({"state": "submitted", "prediction_id": ...}).
    # Once the id is persisted, resume polls the accepted prediction instead
    # of submitting a duplicate. Around the acceptance boundary itself this
    # is best-effort, not exactly-once: a crash between acceptance and the
    # id write leaves no local evidence of the accepted prediction.

    def attempt_path(self, request_id: str) -> Path:
        return self.attempts_dir / f"{request_id}.json"

    def save_attempt(self, request_id: str, payload: dict[str, Any]) -> None:
        self.attempts_dir.mkdir(parents=True, exist_ok=True)
        path = self.attempt_path(request_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def load_attempt(self, request_id: str) -> dict[str, Any] | None:
        path = self.attempt_path(request_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def clear_attempt(self, request_id: str) -> None:
        self.attempt_path(request_id).unlink(missing_ok=True)


def assess_run_completeness(run_dir: Path) -> list[str]:
    """Why this run is NOT complete (empty list = complete and reviewable).

    A run is complete only when it finished without halting (run_summary.json
    exists with no halted_reason), every planned runnable request succeeded,
    nothing failed, and the budget ledger holds no active reservations.
    Incomplete runs must not be used for model selection."""
    problems: list[str] = []
    store = ResultStore(run_dir)

    summary_path = run_dir / "run_summary.json"
    if not summary_path.exists():
        problems.append(
            "run_summary.json is missing — the run was interrupted before it finished"
        )
    else:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("halted_reason"):
            problems.append(f"the run halted early: {summary['halted_reason']}")
        if summary.get("disabled_models"):
            disabled = ", ".join(sorted(summary["disabled_models"]))
            problems.append(f"model(s) disabled after provider rejection: {disabled}")

    records = store.load_all()
    failed = sum(1 for r in records if r.status == "failed")
    succeeded = sum(1 for r in records if r.status == "succeeded")
    if failed:
        problems.append(f"{failed} request(s) failed")

    plan_path = run_dir / "plan.json"
    if not plan_path.exists():
        problems.append("plan.json is missing")
    else:
        planned = json.loads(plan_path.read_text(encoding="utf-8")).get("planned_requests")
        if planned is not None and succeeded != planned:
            problems.append(
                f"only {succeeded} of {planned} planned requests succeeded"
            )

    ledger_path = run_dir / "budget_ledger.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        active = [
            rid for rid, e in ledger.get("entries", {}).items()
            if e.get("status") == "reserved"
        ]
        if active:
            problems.append(
                f"{len(active)} unresolved active reservation(s) in the budget ledger"
            )
    return problems
