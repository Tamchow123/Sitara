"""Auditable, balanced review scopes for the blind visual evaluation.

A review scope formally records which models progress to blind visual
scoring and which are operationally disqualified — WITHOUT rewriting the
original screening plan or hiding that a disqualified model was evaluated.
The scope is valid only when the selected matrix is balanced: every included
model covers exactly the same logical brief set with one usable output per
cell (original success or earliest successful retry), and no included cell
is unresolved. Unresolved cells may belong only to explicitly excluded
models, and every exclusion carries a recorded reason.

An unbalanced scope is rejected outright: a model whose partial successes
would produce an uneven candidate matrix (e.g. 10/12 cells) cannot keep its
images in the formal blind comparison, because reviewers would compare
unequal sample sizes.

Artefacts:
- ``review_scope.json``      the deterministic, validated scope (auditable).
- ``review_scope_report.md`` a NON-BLIND disposition report explaining the
                             exclusion and preserving the excluded model's
                             reliability facts. Never open it during blind
                             scoring.
Scoped blind artefacts live in ``blind-scoped/`` with their own protected
mapping (``candidate_key_scoped.json``) that names only included models.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from .budget import atomic_write_json
from .result_store import ResultRecord, ResultStore, utc_now_iso
from .retry import RetryStore, select_logical_outputs


class ReviewScopeError(Exception):
    """The requested scope is invalid, unbalanced, or stale."""


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _logical_id(record: ResultRecord) -> str:
    return record.logical_request_id or record.request_id


def build_review_scope(
    run_dir: Path,
    include_models: list[str],
    exclude_models: list[str],
    exclusion_reason: str,
) -> dict[str, Any]:
    """Derive and validate a scope from stored records. Pure computation —
    writes nothing; raises ReviewScopeError on any violation."""
    originals = ResultStore(run_dir).load_all()
    if not originals:
        raise ReviewScopeError(f"run {run_dir.name!r} has no result records")
    retries = RetryStore(run_dir).load_all()

    included = sorted(set(include_models))
    excluded = sorted(set(exclude_models))
    if not included:
        raise ReviewScopeError("a review scope must include at least one model")
    overlap = set(included) & set(excluded)
    if overlap:
        raise ReviewScopeError(f"models both included and excluded: {sorted(overlap)}")
    models_in_run = sorted({r.model_key for r in originals})
    unknown = (set(included) | set(excluded)) - set(models_in_run)
    if unknown:
        raise ReviewScopeError(f"models not present in this run: {sorted(unknown)}")
    unaccounted = set(models_in_run) - set(included) - set(excluded)
    if unaccounted:
        raise ReviewScopeError(
            "every evaluated model must be explicitly included or excluded; "
            f"unaccounted: {sorted(unaccounted)} (do not silently drop a model)"
        )
    if excluded and not exclusion_reason.strip():
        raise ReviewScopeError(
            "an explicit --exclusion-reason is required when excluding models "
            "(exclusions must be auditable)"
        )

    # Balance: every included model must cover the same planned brief set.
    briefs_by_model: dict[str, set[str]] = {}
    for r in originals:
        briefs_by_model.setdefault(r.model_key, set()).add(r.brief_id)
    expected_briefs = briefs_by_model[included[0]]
    for model in included[1:]:
        if briefs_by_model[model] != expected_briefs:
            diff = sorted(briefs_by_model[model] ^ expected_briefs)
            raise ReviewScopeError(
                f"unbalanced scope: {model!r} does not cover the same briefs "
                f"as {included[0]!r} (differing cells: {diff})"
            )

    # Unresolved cells may belong only to excluded models.
    recovered_ids = {
        r.logical_request_id for r in retries if r.status == "succeeded" and r.logical_request_id
    }
    unresolved = [
        r for r in originals if r.status == "failed" and r.request_id not in recovered_ids
    ]
    included_unresolved = [r for r in unresolved if r.model_key in included]
    if included_unresolved:
        cells = sorted(f"{r.model_key}/{r.brief_id}" for r in included_unresolved)
        raise ReviewScopeError(
            f"scope is incomplete: included model(s) have {len(included_unresolved)} "
            f"unresolved logical cell(s): {cells}. Either resolve them or "
            "formally exclude the model with a reason."
        )

    # One usable output for every included logical cell.
    selected = select_logical_outputs(ResultStore(run_dir))
    selected_by_model: dict[str, list[ResultRecord]] = {}
    for r in selected:
        selected_by_model.setdefault(r.model_key, []).append(r)
    for model in included:
        outputs = selected_by_model.get(model, [])
        covered = {r.brief_id for r in outputs}
        if covered != expected_briefs or len(outputs) != len(expected_briefs):
            missing = sorted(expected_briefs - covered)
            raise ReviewScopeError(
                f"unbalanced scope: {model!r} has {len(outputs)} usable outputs "
                f"for {len(expected_briefs)} planned cells (missing: {missing})"
            )

    selected_cells = sorted(
        _logical_id(r) for r in selected if r.model_key in included
    )
    if len(selected_cells) != len(set(selected_cells)):
        raise ReviewScopeError("duplicate logical cells in selection")

    return {
        "run_id": run_dir.name,
        "created_at": utc_now_iso(),
        "included_models": included,
        "excluded_models": excluded,
        "exclusion_reason": exclusion_reason.strip(),
        "planned_cells_per_included_model": {
            m: len(expected_briefs) for m in included
        },
        "selected_logical_cells": selected_cells,
        "excluded_logical_cells": sorted(
            r.request_id for r in originals if r.model_key in excluded
        ),
        "unresolved_excluded_cells": sorted(
            r.request_id for r in unresolved if r.model_key in excluded
        ),
        "source_run_summary_hash": _sha256_file(run_dir / "run_summary.json"),
    }


def write_review_scope(run_dir: Path, scope: dict[str, Any]) -> Path:
    path = run_dir / "review_scope.json"
    atomic_write_json(path, scope)
    return path


def load_and_validate_review_scope(run_dir: Path, scope_path: Path) -> dict[str, Any]:
    """Load a stored scope and re-validate it against the CURRENT records.

    The stored selection must still be derivable from the artefacts: if the
    records changed since creation, the scope is stale and refused."""
    try:
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReviewScopeError(f"review scope not found: {scope_path}") from None
    except ValueError as exc:
        raise ReviewScopeError(f"review scope is not valid JSON: {exc}") from None
    rebuilt = build_review_scope(
        run_dir,
        scope.get("included_models", []),
        scope.get("excluded_models", []),
        scope.get("exclusion_reason", ""),
    )
    for key in (
        "selected_logical_cells",
        "excluded_logical_cells",
        "unresolved_excluded_cells",
        "planned_cells_per_included_model",
    ):
        if scope.get(key) != rebuilt[key]:
            raise ReviewScopeError(
                f"review scope is stale: {key} no longer matches the stored "
                "records; recreate the scope with create-review-scope"
            )
    return scope


def scoped_logical_outputs(store: ResultStore, scope: dict[str, Any]) -> list[ResultRecord]:
    selected_set = set(scope["selected_logical_cells"])
    return [
        r for r in select_logical_outputs(store) if _logical_id(r) in selected_set
    ]


def build_review_scope_report(run_dir: Path, scope: dict[str, Any]) -> Path:
    """NON-BLIND disposition report. Preserves excluded models' reliability
    facts; draws no visual conclusions. Never open during blind scoring."""
    originals = ResultStore(run_dir).load_all()
    retries = RetryStore(run_dir).load_all()
    retry_limit = max((r.attempt_index or 0 for r in retries), default=0)
    lines = [
        "# Review scope disposition report",
        "",
        f"Run: `{scope['run_id']}` — scope created {scope['created_at']}",
        "",
        "> NON-BLIND report. Do NOT open this during blind visual scoring.",
        "",
        f"Included in blind visual evaluation: {', '.join(scope['included_models'])}",
        f"(each contributing {list(scope['planned_cells_per_included_model'].values())[0]} "
        f"logical cells; {len(scope['selected_logical_cells'])} images in total).",
        "",
    ]
    for model in scope["excluded_models"]:
        first = [r for r in originals if r.model_key == model]
        ok = [r for r in first if r.status == "succeeded"]
        failed = [r for r in first if r.status == "failed"]
        model_retries = [r for r in retries if r.model_key == model]
        recovered_ids = {
            r.logical_request_id for r in model_retries if r.status == "succeeded"
        }
        recovered = [r for r in failed if r.request_id in recovered_ids]
        unresolved = [r for r in failed if r.request_id not in recovered_ids]
        attempts = len(first) + len(model_retries)
        successes = len(ok) + sum(1 for r in model_retries if r.status == "succeeded")
        lines += [
            f"## Excluded: {model} — operationally disqualified",
            "",
            f"Reason: {scope['exclusion_reason']}",
            "",
            f"Retry limit applied: {retry_limit} targeted retries per logical "
            "cell (the screening's stop-after-retry-2 rule).",
            "",
            f"- planned logical cells: {len(first)}",
            f"- first-attempt successes: {len(ok)}",
            f"- first-attempt failures: {len(failed)}",
            f"- first-attempt success rate: "
            f"{len(ok) / len(first):.1%}" if first else "- first-attempt success rate: n/a",
        ]
        for round_index in sorted({r.attempt_index for r in model_retries if r.attempt_index}):
            batch = [r for r in model_retries if r.attempt_index == round_index]
            ok_n = sum(1 for r in batch if r.status == "succeeded")
            lines.append(
                f"- retry-{round_index}: {len(batch)} attempts, {ok_n} successes, "
                f"{len(batch) - ok_n} failures"
            )
        lines += [
            f"- logical cells eventually recovered: {len(recovered)}",
            f"- logical cells still unresolved: {len(unresolved)}"
            + (
                " (" + ", ".join(sorted(f"`{r.brief_id}`" for r in unresolved)) + ")"
                if unresolved
                else ""
            ),
            f"- logical cells with output: {len(ok) + len(recovered)}/{len(first)}",
            f"- total provider attempts: {attempts}",
            f"- total successful provider attempts: {successes}",
            f"- total failed provider attempts: {attempts - successes}",
            "",
            "No visual-quality conclusion about this model is drawn or implied "
            "by this exclusion. It is excluded because it could not reliably "
            "produce a complete, balanced evaluation set within the retry "
            "limit, and an uneven candidate matrix would bias the blind "
            "comparison. Its successful outputs remain on disk as evidence "
            "but take no part in the formal blind scoring.",
            "",
        ]
    path = run_dir / "review_scope_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
