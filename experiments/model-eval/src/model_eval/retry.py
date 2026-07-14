"""Auditable targeted retries for transient terminal prediction failures.

First-attempt failures are model-reliability EVIDENCE: original result
records and their settled ledger entries are never overwritten, deleted or
mutated, and a successful retry never converts the original request into a
first-attempt success. Retries exist only to recover the missing comparison
image for a logical evaluation cell.

Every retry is a new attempt with its own id and budget-ledger key
(``<original-request-id>--retry-N``), full lineage provenance, identical
inputs (same model, prompt, format, mode, parameters, aspect ratio and
seed), and the same crash-recovery, 429 and reserve-before-call guarantees
as ordinary requests. Retry artefacts live in clearly separated directories
(``retry-results/``, ``retry-images/``, ``retry-attempts/``) so the original
run stays byte-identical.

Only errors on an explicit transient allowlist are eligible — never safety
or moderation rejections, invalid inputs, auth/credit failures, schema
failures, or unknown terminal errors.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .budget import BudgetLedger
from .config import CandidatesConfig, ReferenceManifest
from .prompt_matrix import PlannedRequest
from .replicate_client import ProviderGateError, ReplicateAdapter, live_gate_failures
from .result_store import ResultRecord, ResultStore, utc_now_iso
from .runner import LoadedStage, RunOutcome, Runner

# ---------------------------------------------------------------------------
# Transient-error allowlist
# ---------------------------------------------------------------------------

# (normalised substring, why it is considered retryable). Matching is
# conservative: an error is eligible ONLY if its normalised message contains
# one of these exact substrings. Everything else — safety/moderation
# rejections, invalid inputs, auth/credit, output-policy, schema failures,
# unknown errors — is ineligible by default. Extend this list only with
# errors that Replicate itself attributes to its infrastructure rather than
# to the model or the input.
TRANSIENT_TERMINAL_ERRORS: tuple[tuple[str, str], ...] = (
    (
        "prediction interrupted; please retry (code: pa)",
        "Replicate explicitly instructs a retry: the prediction was "
        "interrupted by infrastructure preemption, unrelated to the model "
        "or the input.",
    ),
    (
        "director: unexpected error handling prediction (e9828)",
        "Replicate-internal orchestration fault (Director E9828): the "
        "prediction never ran to completion for provider-side reasons "
        "unrelated to the model or the input.",
    ),
)


def _normalise(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def transient_match(error_message: str | None) -> tuple[str, str] | None:
    """The (pattern, rationale) that makes this error retryable, or None."""
    normalised = _normalise(error_message)
    for pattern, why in TRANSIENT_TERMINAL_ERRORS:
        if pattern in normalised:
            return pattern, why
    return None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class RetryStore(ResultStore):
    """Result storage for retry attempts, in clearly separated directories
    so original first-attempt records remain the authoritative, untouched
    evidence."""

    def __init__(self, run_dir: Path):
        super().__init__(run_dir)
        self.results_dir = run_dir / "retry-results"
        self.images_dir = run_dir / "retry-images"
        self.attempts_dir = run_dir / "retry-attempts"


# ---------------------------------------------------------------------------
# Eligibility planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryCandidate:
    original: ResultRecord
    attempt_id: str
    attempt_index: int
    reason: str
    max_cost_usd: float


@dataclass(frozen=True)
class IneligibleFailure:
    request_id: str
    model_key: str
    reason: str


@dataclass
class RetryPlanReport:
    run_id: str
    eligible: list[RetryCandidate] = field(default_factory=list)
    ineligible: list[IneligibleFailure] = field(default_factory=list)
    succeeded_originals: int = 0
    failed_originals: int = 0

    @property
    def additional_max_reservation_usd(self) -> float:
        return round(sum(c.max_cost_usd for c in self.eligible), 6)

    def counts_by_model(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.eligible:
            counts[c.original.model_key] = counts.get(c.original.model_key, 0) + 1
        return dict(sorted(counts.items()))


def plan_retries(
    run_dir: Path,
    candidates: CandidatesConfig,
    max_retries_per_request: int,
) -> RetryPlanReport:
    """Read the original results and any prior retries, and decide which
    failed logical requests are eligible for one more attempt. Pure read —
    a dry run writes nothing."""
    originals = ResultStore(run_dir).load_all()
    retries = RetryStore(run_dir).load_all()
    prior: dict[str, list[ResultRecord]] = {}
    for r in retries:
        if r.logical_request_id:
            prior.setdefault(r.logical_request_id, []).append(r)

    report = RetryPlanReport(run_id=run_dir.name)
    report.succeeded_originals = sum(1 for r in originals if r.status == "succeeded")
    report.failed_originals = sum(1 for r in originals if r.status == "failed")

    for record in originals:
        if record.status != "failed":
            continue  # successful/skipped requests are never regenerated
        rid = record.request_id

        def refuse(reason: str) -> None:
            report.ineligible.append(
                IneligibleFailure(request_id=rid, model_key=record.model_key, reason=reason)
            )

        if record.error_category != "prediction_failed":
            refuse(
                f"category {record.error_category!r} is not a terminal "
                "prediction failure (pre-acceptance halts resume via the "
                "ordinary run command)"
            )
            continue
        if not record.provider_prediction_id:
            refuse(
                "no accepted provider prediction id — cannot confirm the "
                "provider ever accepted this input"
            )
            continue
        if record.reference_ids or record.kind == "refinement_edit":
            refuse(
                "reference/edit requests are not retryable here: their "
                "image inputs are injected at run time and are not part of "
                "the stored input parameters"
            )
            continue
        match = transient_match(record.error_message)
        if match is None:
            refuse(
                "error is not on the transient allowlist (unknown terminal "
                f"failure): {(record.error_message or '')[:120]!r}"
            )
            continue
        attempts = sorted(prior.get(rid, []), key=lambda r: r.attempt_index or 0)
        if any(a.status == "succeeded" for a in attempts):
            recovered = next(a for a in attempts if a.status == "succeeded")
            refuse(f"already recovered by retry attempt {recovered.attempt_id}")
            continue
        if len(attempts) >= max_retries_per_request:
            refuse(
                f"retry limit reached ({len(attempts)}/{max_retries_per_request} "
                "attempts used); increase --max-retries-per-request to retry "
                "again deliberately"
            )
            continue
        attempt_index = len(attempts) + 1
        report.eligible.append(
            RetryCandidate(
                original=record,
                attempt_id=f"{rid}--retry-{attempt_index}",
                attempt_index=attempt_index,
                reason=match[1],
                max_cost_usd=candidates.by_key(record.model_key).pricing.max_cost_per_generation_usd,
            )
        )
    return report


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _retry_request(candidate: RetryCandidate) -> PlannedRequest:
    """A retry uses EXACTLY the original inputs: same model, prompt, format,
    mode, parameters, aspect ratio and seed. It is submitted versionless,
    exactly as the original submission was."""
    o = candidate.original
    return PlannedRequest(
        request_id=candidate.attempt_id,
        stage=o.stage,
        brief_id=o.brief_id,
        garment=o.garment,
        ceremony=o.ceremony,
        tags=list(o.tags),
        model_key=o.model_key,
        replicate_id=o.replicate_id,
        model_version=None,
        prompt_format=o.prompt_format,
        inspiration_mode=o.inspiration_mode,  # type: ignore[arg-type]
        reference_ids=[],
        seed=o.seed,
        kind=o.kind,  # type: ignore[arg-type]
        refinement_id=o.refinement_id,
        refinement_strategy=o.refinement_strategy,
        base_request_id=o.base_request_id,
        prompt_text=o.prompt_text,
        negative_text=o.negative_text,
        json_payload=o.json_payload,
        input_params=dict(o.input_params),
        aspect_ratio=o.aspect_ratio,
        estimated_max_cost_usd=candidate.max_cost_usd,
        skipped=False,
        skip_reason=None,
    )


def _lineage(candidate: RetryCandidate) -> dict[str, Any]:
    o = candidate.original
    return {
        "logical_request_id": o.request_id,
        "attempt_id": candidate.attempt_id,
        "attempt_index": candidate.attempt_index,
        "retry_of_request_id": o.request_id,
        "retry_reason": candidate.reason,
        "original_error_category": o.error_category,
        "original_error_message": (o.error_message or "")[:500],
        "original_provider_prediction_id": o.provider_prediction_id,
    }


def execute_retries(
    outputs_dir: Path,
    run_id: str,
    candidates: CandidatesConfig,
    report: RetryPlanReport,
    *,
    budget_usd: float,
    env: Mapping[str, str],
    confirm_live: bool,
    adapter_factory: Callable[[Mapping[str, str]], ReplicateAdapter],
    log: Callable[[str], None] = print,
    poll_interval_s: float = 2.0,
    poll_timeout_s: float = 600.0,
    sleep: Callable[[float], None] | None = None,
) -> RunOutcome:
    """Execute the eligible retries against the ORIGINAL run ledger.

    Reuses the ordinary per-request execution path (reserve-before-call with
    a fresh ledger key per attempt, pre-submission attempt journal, 429
    backoff, run-level halts, crash-safe resume by persisted prediction id)
    with storage redirected to the retry-* directories. Original result
    records and settled ledger entries are never touched."""
    failures = live_gate_failures(env, confirm_live=confirm_live, budget_usd=budget_usd)
    if failures:
        raise ProviderGateError(
            "live retry refused; unmet requirements: " + "; ".join(failures)
        )

    bundle = LoadedStage(
        stage=None,  # type: ignore[arg-type] — unused by per-request execution
        candidates=candidates,
        briefs=None,  # type: ignore[arg-type]
        manifest=ReferenceManifest(references=[]),
        plan=None,  # type: ignore[arg-type]
        references_dir=None,
    )
    runner_kwargs: dict[str, Any] = {}
    if sleep is not None:
        runner_kwargs["sleep"] = sleep
    runner = Runner(
        bundle,
        run_id,
        outputs_dir,
        adapter_factory=adapter_factory,
        env=env,
        log=log,
        poll_interval_s=poll_interval_s,
        poll_timeout_s=poll_timeout_s,
        **runner_kwargs,
    )
    runner.store = RetryStore(runner.run_dir)

    outcome = RunOutcome(run_id=run_id)
    adapter = adapter_factory(env)
    try:
        with BudgetLedger.open(runner.run_dir / "budget_ledger.json", budget_usd) as ledger:
            for candidate in report.eligible:
                request = _retry_request(candidate)
                runner.retry_contexts[candidate.attempt_id] = _lineage(candidate)
                runner._execute_one(request, adapter, ledger, runner.run_dir, outcome)
                if outcome.halted_reason:
                    break
    finally:
        adapter.close()
    write_logical_summary(runner.run_dir)
    return outcome


# ---------------------------------------------------------------------------
# Logical-cell selection and summaries
# ---------------------------------------------------------------------------


def select_logical_outputs(store: ResultStore) -> list[ResultRecord]:
    """One successful record per logical evaluation cell: the original
    success where it exists, otherwise the EARLIEST successful retry.
    Never more than one image per logical cell."""
    originals = store.load_all()
    selected = [r for r in originals if r.status == "succeeded" and r.output_path]
    failed = [r for r in originals if r.status == "failed"]
    if failed:
        retries = RetryStore(store.run_dir).load_all()
        for record in failed:
            recoveries = sorted(
                (
                    r for r in retries
                    if r.logical_request_id == record.request_id
                    and r.status == "succeeded"
                    and r.output_path
                ),
                key=lambda r: r.attempt_index or 0,
            )
            if recoveries:
                selected.append(recoveries[0])
    return selected


def compute_logical_summary(run_dir: Path) -> dict[str, Any]:
    """Item-level truth that never rewrites history: first-attempt outcomes
    stay first-attempt outcomes, recoveries are counted separately."""
    originals = ResultStore(run_dir).load_all()
    retries = RetryStore(run_dir).load_all()
    first_succeeded = sum(1 for r in originals if r.status == "succeeded")
    first_failed = sum(1 for r in originals if r.status == "failed")
    recovered_ids = {
        r.logical_request_id for r in retries if r.status == "succeeded" and r.logical_request_id
    }
    recovered = sum(
        1 for r in originals if r.status == "failed" and r.request_id in recovered_ids
    )
    plan_path = run_dir / "plan.json"
    planned = None
    if plan_path.exists():
        planned = json.loads(plan_path.read_text(encoding="utf-8")).get("planned_requests")

    first_spend, retry_spend = _spend_split(run_dir, originals, retries)
    attempted_first = sum(1 for r in originals if r.status in ("succeeded", "failed"))
    summary = {
        "run_id": run_dir.name,
        "planned_logical_requests": planned,
        "first_attempt_succeeded": first_succeeded,
        "first_attempt_failed": first_failed,
        "retry_attempts": len(retries),
        "recovered_by_retry": recovered,
        "unresolved_logical_requests": first_failed - recovered,
        "logical_requests_with_output": first_succeeded + recovered,
        "total_provider_attempts": attempted_first + len(retries),
        "first_attempt_failure_rate": (
            round(first_failed / attempted_first, 4) if attempted_first else None
        ),
        "first_attempt_spend_usd": first_spend,
        "retry_spend_usd": retry_spend,
        "combined_conservative_spend_usd": round(first_spend + retry_spend, 6),
        "generated_at": utc_now_iso(),
    }
    return summary


def write_logical_summary(run_dir: Path) -> Path:
    path = run_dir / "logical_summary.json"
    path.write_text(
        json.dumps(compute_logical_summary(run_dir), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _spend_split(
    run_dir: Path,
    originals: list[ResultRecord],
    retries: list[ResultRecord],
) -> tuple[float, float]:
    """Conservative (ledger-accounted) spend split into first-attempt and
    retry portions. Ledger keys for retries are the attempt ids."""
    ledger_path = run_dir / "budget_ledger.json"
    if not ledger_path.exists():
        return 0.0, 0.0
    entries = json.loads(ledger_path.read_text(encoding="utf-8")).get("entries", {})
    original_ids = {r.request_id for r in originals}

    def spent(rid: str) -> float:
        entry = entries.get(rid)
        if entry and entry.get("status") in ("reconciled", "assumed_spent"):
            return float(entry.get("final_usd") or 0.0)
        return 0.0

    first = sum(spent(rid) for rid in original_ids)
    retry = sum(
        spent(rid) for rid in entries
        if rid not in original_ids and "--retry-" in rid
    )
    return round(first, 6), round(retry, 6)


# ---------------------------------------------------------------------------
# Reliability report (non-blind; keep away from visual scoring)
# ---------------------------------------------------------------------------


def build_reliability_report(run_dir: Path) -> Path:
    """Per-model operational reliability, preserving the first-pass signal
    even when retries recovered every missing image. NOT part of the blind
    review workflow: examine it only after visual scoring is complete (or
    via a separate evaluator)."""
    originals = ResultStore(run_dir).load_all()
    retries = RetryStore(run_dir).load_all()
    ledger_entries: dict[str, Any] = {}
    ledger_path = run_dir / "budget_ledger.json"
    if ledger_path.exists():
        ledger_entries = json.loads(ledger_path.read_text(encoding="utf-8")).get("entries", {})

    def settled(rid: str) -> float:
        entry = ledger_entries.get(rid)
        if entry and entry.get("status") in ("reconciled", "assumed_spent"):
            return float(entry.get("final_usd") or 0.0)
        return 0.0

    models = sorted({r.model_key for r in originals})
    lines = [
        "# Model reliability report",
        "",
        f"Run: `{run_dir.name}`",
        "",
        "> OPERATIONAL report — not part of the blind review. Do not consult",
        "> this while visually scoring outputs: retry status could bias",
        "> scores. First-attempt failures below are preserved reliability",
        "> evidence even where a targeted retry recovered the image.",
        "",
        "| Model | Planned | 1st-attempt OK | 1st-attempt failed | 1st-attempt success rate | Retry attempts | Recovered | Unresolved | Median success latency (s) | Max success latency (s) | 1st-attempt spend (USD) | Retry spend (USD) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model in models:
        first = [r for r in originals if r.model_key == model]
        ok = [r for r in first if r.status == "succeeded"]
        failed = [r for r in first if r.status == "failed"]
        model_retries = [r for r in retries if r.model_key == model]
        recovered_ids = {
            r.logical_request_id for r in model_retries if r.status == "succeeded"
        }
        recovered = sum(1 for r in failed if r.request_id in recovered_ids)
        latencies = sorted(
            r.latency_seconds
            for r in ok + [r for r in model_retries if r.status == "succeeded"]
            if r.latency_seconds is not None
        )
        attempted = len(ok) + len(failed)
        rate = f"{len(ok) / attempted:.1%}" if attempted else "n/a"
        first_spend = round(sum(settled(r.request_id) for r in first), 4)
        retry_spend = round(sum(settled(r.request_id) for r in model_retries), 4)
        median_latency = f"{statistics.median(latencies):.1f}" if latencies else "n/a"
        max_latency = f"{max(latencies):.1f}" if latencies else "n/a"
        lines.append(
            f"| {model} | {len(first)} | {len(ok)} | {len(failed)} | {rate} "
            f"| {len(model_retries)} | {recovered} | {len(failed) - recovered} "
            f"| {median_latency} | {max_latency} "
            f"| {first_spend:.4f} | {retry_spend:.4f} |"
        )
    lines += [
        "",
        "First-attempt failure messages:",
        "",
    ]
    for r in originals:
        if r.status == "failed":
            lines.append(
                f"- `{r.model_key}` / `{r.brief_id}`: {(r.error_message or '')[:160]}"
            )
    path = run_dir / "reliability_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
