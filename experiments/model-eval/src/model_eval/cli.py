"""Command-line interface for the Phase 2 model evaluation.

Run from experiments/model-eval:

    python -m model_eval.cli inspect
    python -m model_eval.cli plan --config configs/screening.yaml
    python -m model_eval.cli run --config configs/screening.yaml --dry-run --budget-usd 10
    python -m model_eval.cli contact-sheet --run-id <run-id>
    python -m model_eval.cli scoring-sheet --run-id <run-id>
    python -m model_eval.cli budget-status --run-id <run-id>
    python -m model_eval.cli terms

A LIVE run additionally requires ALL of:
    ALLOW_PROVIDER_CALLS=true  REPLICATE_API_TOKEN=...  --budget-usd N  --confirm-live
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .budget import BudgetError
from .config import ConfigError, load_candidates
from .contact_sheet import build_contact_sheet
from .replicate_client import ProviderGateError, default_adapter_factory
from .result_store import ResultStore, assess_run_completeness
from .retry import (
    build_reliability_report,
    compute_logical_summary,
    compute_retry_status,
    execute_retries,
    plan_retries,
)
from .runner import Runner, RunnerError, load_stage_bundle, plan_summary
from .scoring import build_scoring_sheet
from .terms import write_terms_snapshot

EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATES = EXPERIMENT_ROOT / "configs" / "model_candidates.yaml"
OUTPUTS_DIR = EXPERIMENT_ROOT / "outputs"
REFERENCES_DIR = EXPERIMENT_ROOT / "references"


def _print_plan(summary: dict) -> None:
    print(f"Stage:                    {summary['stage']}")
    print(f"Planned provider requests: {summary['planned_requests']}")
    print(f"Skipped combinations:      {summary['skipped_requests']}")
    print(f"By model:                  {summary['models']}")
    print(f"By prompt format:          {summary['prompt_formats']}")
    print(f"By inspiration mode:       {summary['inspiration_modes']}")
    print(f"By request kind:           {summary['request_kinds']}")
    print(f"Conservative max spend:    {summary['conservative_max_spend_usd']:.4f} USD")
    if summary["budget_usd"] is not None:
        verdict = "WITHIN" if summary["within_budget"] else "EXCEEDS"
        print(f"Budget:                    {summary['budget_usd']:.2f} USD ({verdict} budget)")
    for warning in summary.get("preflight_warnings", []):
        print(f"PREFLIGHT WARNING: {warning}")
    if summary["skips"]:
        print("Skips:")
        for s in summary["skips"][:25]:
            print(f"  - {s['request_id']}: {s['reason']}")
        remaining = len(summary["skips"]) - 25
        if remaining > 0:
            print(f"  ... and {remaining} more (see plan.json for the full list)")


def cmd_inspect(args: argparse.Namespace) -> int:
    config = load_candidates(Path(args.candidates))
    if config.requires_manual_verification:
        print("!! This candidates file contains UNVERIFIED placeholder data.\n")
    for c in config.candidates:
        caps = c.capabilities
        print(f"{c.key}: {c.name}")
        print(f"  replicate_id:   {c.replicate_id}" + (f" @ {c.version}" if c.version else " (latest)"))
        print(f"  categories:     {', '.join(c.categories)}")
        print(
            "  capabilities:   "
            f"seed={caps.seed} negative_prompt={caps.negative_prompt} "
            f"reference_image={caps.reference_image} image_editing={caps.image_editing} "
            f"json_prompting={caps.json_prompting}"
        )
        if caps.aspect_ratios:
            print(f"  aspect ratios:  {', '.join(caps.aspect_ratios)}")
        print(
            f"  pricing:        ~{c.pricing.expected_cost_per_generation_usd:.4f} USD/gen "
            f"(reserve {c.pricing.max_cost_per_generation_usd:.4f}), "
            f"checked {c.pricing.checked_on}"
        )
        print(f"  terms verified: {c.terms.verified_on}")
        if c.terms.unresolved:
            print(f"  UNRESOLVED:     {'; '.join(c.terms.unresolved)}")
        print()
    print(
        "Reminder: pricing and terms are time-sensitive. Re-verify official\n"
        "provider pages immediately before any live run."
    )
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    bundle = load_stage_bundle(Path(args.config))
    _print_plan(plan_summary(bundle, args.budget_usd))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    bundle = load_stage_bundle(Path(args.config))
    summary = plan_summary(bundle, args.budget_usd)

    run_id = args.run_id or (
        f"{bundle.stage.stage}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )

    if args.dry_run:
        print(f"DRY RUN - no network calls will be made. (run id would be: {run_id})\n")
        _print_plan(summary)
        if summary["budget_usd"] is not None and not summary["within_budget"]:
            print("\nWARNING: conservative max spend exceeds the given budget; a live")
            print("run would halt at the budget ceiling rather than exceed it.")
        return 0

    runner = Runner(
        bundle,
        run_id,
        OUTPUTS_DIR,
        adapter_factory=default_adapter_factory,
        env=os.environ,
    )
    try:
        outcome = runner.execute(
            dry_run=False,
            confirm_live=args.confirm_live,
            budget_usd=args.budget_usd,
            references_dir=REFERENCES_DIR,
        )
    except (ProviderGateError, BudgetError, RunnerError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    print(f"\nRun {outcome.run_id} finished.")
    print(f"  succeeded: {len(outcome.succeeded)}")
    print(f"  failed:    {len(outcome.failed)}")
    print(f"  skipped:   {len(outcome.skipped)}")
    print(f"  resumed:   {len(outcome.resumed)} (already complete before this invocation)")
    if outcome.previously_failed:
        print(
            f"  not retried: {len(outcome.previously_failed)} previously failed "
            "(delete their result records and use a new run id to retry)"
        )
    for model, reason in outcome.disabled_models.items():
        print(
            f"  DISABLED: {model} after a deterministic provider rejection — "
            f"{reason}. Fix its input configuration before the next run."
        )
    if outcome.halted_reason:
        print(f"  HALTED: {outcome.halted_reason}")
    print(f"Results: {runner.run_dir}")
    return 0 if not outcome.halted_reason else 1


def _store_for(run_id: str, outputs_dir: str | None = None) -> ResultStore:
    base = Path(outputs_dir) if outputs_dir else OUTPUTS_DIR
    run_dir = base / "runs" / run_id
    if not run_dir.exists():
        raise SystemExit(f"no such run: {run_id} (looked in {run_dir})")
    return ResultStore(run_dir)


def _check_reviewable(store: ResultStore, allow_partial: bool) -> bool:
    """Refuse review artefacts for incomplete runs unless --allow-partial.

    Returns True when the run is partial (artefacts must carry the PARTIAL
    banner)."""
    problems = assess_run_completeness(store.run_dir)
    if not problems:
        return False
    if not allow_partial:
        bullet = "\n  - ".join(problems)
        raise SystemExit(
            "REFUSED: this run is incomplete and NOT valid for model "
            f"selection:\n  - {bullet}\n"
            "Finish or resume the run first. For debugging artefacts only, "
            "rerun with --allow-partial (outputs are prominently marked "
            "PARTIAL / NOT VALID FOR MODEL SELECTION)."
        )
    print("WARNING: generating PARTIAL artefacts — not valid for model selection:")
    for p in problems:
        print(f"  - {p}")
    return True


def cmd_contact_sheet(args: argparse.Namespace) -> int:
    store = _store_for(args.run_id, args.outputs_dir)
    partial = _check_reviewable(store, args.allow_partial)
    sheet, mapping = build_contact_sheet(
        store, args.run_id, axis=args.by, reveal=args.reveal, partial=partial
    )
    print(f"Contact sheet: {sheet}")
    print(f"Protected mapping (do not open during blind scoring): {mapping}")
    return 0


def cmd_scoring_sheet(args: argparse.Namespace) -> int:
    store = _store_for(args.run_id, args.outputs_dir)
    partial = _check_reviewable(store, args.allow_partial)
    path = build_scoring_sheet(store, args.run_id, partial=partial)
    print(f"Scoring sheet: {path}")
    return 0


def cmd_budget_status(args: argparse.Namespace) -> int:
    ledger_path = OUTPUTS_DIR / "runs" / args.run_id / "budget_ledger.json"
    if not ledger_path.exists():
        raise SystemExit(f"no budget ledger for run {args.run_id}")
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    totals = data.get("totals", {})
    print(f"Run:        {args.run_id}")
    print(f"Budget:     {data['budget_usd']:.4f} USD")
    print(f"Spent:      {totals.get('spent_usd', 0):.4f} USD")
    print(f"Reserved:   {totals.get('reserved_usd', 0):.4f} USD")
    print(f"Remaining:  {totals.get('remaining_usd', 0):.4f} USD")
    print(f"Entries:    {len(data.get('entries', {}))}")
    return 0


def cmd_retry_failures(args: argparse.Namespace) -> int:
    store = _store_for(args.run_id, args.outputs_dir)
    candidates = load_candidates(Path(args.candidates))
    report = plan_retries(store.run_dir, candidates, args.max_retries_per_request)

    print(f"Run: {args.run_id}")
    print(
        f"Original results: {report.succeeded_originals} succeeded, "
        f"{report.failed_originals} failed"
    )
    print("Successful requests selected for regeneration: 0 (never permitted)")
    print("Recovered retry requests selected again: 0 (never permitted)")
    print(f"Eligible transient failures: {len(report.eligible)}")
    for c in report.eligible:
        print(
            f"  - {c.attempt_id}  ({c.original.model_key}, reserve "
            f"{c.max_cost_usd:.4f} USD)"
        )
    if report.eligible:
        print(f"  By model: {report.counts_by_model()}")
    if report.ineligible:
        print(f"Ineligible failed requests: {len(report.ineligible)}")
        for i in report.ineligible:
            print(f"  - {i.request_id}: {i.reason}")
    print(
        f"Additional maximum reservation: "
        f"{report.additional_max_reservation_usd:.4f} USD"
    )
    ledger_path = store.run_dir / "budget_ledger.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        totals = ledger.get("totals", {})
        remaining = totals.get("remaining_usd", 0.0)
        verdict = "FITS" if report.additional_max_reservation_usd <= remaining else "EXCEEDS"
        print(
            f"Run budget {ledger['budget_usd']:.2f} USD; spent "
            f"{totals.get('spent_usd', 0):.4f}; remaining {remaining:.4f} "
            f"({verdict} remaining budget)"
        )

    if args.dry_run:
        print("\nDRY RUN - no provider calls were made and nothing was written.")
        return 0
    if not report.eligible:
        print("\nNothing to retry.")
        return 0

    try:
        outcome = execute_retries(
            Path(args.outputs_dir) if args.outputs_dir else OUTPUTS_DIR,
            args.run_id,
            candidates,
            report,
            budget_usd=args.budget_usd,
            env=os.environ,
            confirm_live=args.confirm_live,
            adapter_factory=default_adapter_factory,
        )
    except (ProviderGateError, BudgetError, RunnerError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"\nRetry pass finished for {args.run_id}.")
    print(f"  recovered: {len(outcome.succeeded)}")
    print(f"  failed again: {len(outcome.failed)}")
    if outcome.halted_reason:
        print(f"  HALTED: {outcome.halted_reason}")
    summary = compute_logical_summary(store.run_dir)
    print(
        f"  logical requests with output: "
        f"{summary['logical_requests_with_output']} of "
        f"{summary['planned_logical_requests']}"
    )
    print(
        f"  first-attempt failures preserved: {summary['first_attempt_failed']} "
        f"(spend: first {summary['first_attempt_spend_usd']:.4f} + retries "
        f"{summary['retry_spend_usd']:.4f} = "
        f"{summary['combined_conservative_spend_usd']:.4f} USD)"
    )
    return 0 if not outcome.halted_reason else 1


def cmd_retry_status(args: argparse.Namespace) -> int:
    """Read-only: zero network calls, zero writes."""
    store = _store_for(args.run_id, args.outputs_dir)
    candidates = load_candidates(Path(args.candidates))
    status = compute_retry_status(store.run_dir, candidates)
    s = status["summary"]
    print(f"Run: {s['run_id']}")
    print(f"Planned logical requests:   {s['planned_logical_requests']}")
    print(f"First-attempt succeeded:    {s['first_attempt_succeeded']}")
    print(f"First-attempt failed:       {s['first_attempt_failed']}")
    print(f"Retry attempts:             {s['retry_attempts']} "
          f"(succeeded {s['retry_succeeded']}, failed {s['retry_failed']})")
    print(f"Recovered by retry:         {s['recovered_by_retry']}")
    print(f"Unresolved logical requests: {s['unresolved_logical_requests']}")
    if s["unresolved_by_model"]:
        print(f"Unresolved by model:        {s['unresolved_by_model']}")
    print(f"Logical requests w/ output: {s['logical_requests_with_output']}")
    print(f"Total provider attempts:    {s['total_provider_attempts']}")
    for item in status["unresolved"]:
        eligibility = "eligible" if item["allowlist_eligible"] else "NOT allowlist-eligible"
        print(
            f"  - {item['logical_request_id']}\n"
            f"      retries used: {item['retries_used']}; next attempt: "
            f"{item['next_attempt']}; {eligibility}; next reservation "
            f"{item['next_reservation_usd']:.4f} USD"
        )
    print(
        f"Additional max reservation for one further retry pass: "
        f"{status['next_pass_max_reservation_usd']:.4f} USD"
    )
    totals = status["ledger_totals"]
    if status["budget_usd"] is not None:
        print(
            f"Ledger: budget {status['budget_usd']:.2f} USD; spent "
            f"{totals.get('spent_usd', 0):.4f}; reserved "
            f"{totals.get('reserved_usd', 0):.4f}; remaining "
            f"{totals.get('remaining_usd', 0):.4f}"
        )
    return 0


def cmd_reliability_report(args: argparse.Namespace) -> int:
    store = _store_for(args.run_id, args.outputs_dir)
    path = build_reliability_report(store.run_dir)
    print(f"Reliability report: {path}")
    print(
        "NOTE: operational report — do NOT consult it during blind visual "
        "scoring; retry status could bias scores."
    )
    return 0


def cmd_terms(args: argparse.Namespace) -> int:
    config = load_candidates(Path(args.candidates))
    dest = write_terms_snapshot(config, EXPERIMENT_ROOT / "TERMS_SNAPSHOT.md")
    print(f"Terms snapshot written: {dest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="model_eval", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="show validated candidate models and their facts")
    p.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("plan", help="expand a stage config and show the planned matrix")
    p.add_argument("--config", required=True)
    p.add_argument("--budget-usd", type=float, default=None)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run", help="execute a stage (dry-run by default refuses live)")
    p.add_argument("--config", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--budget-usd", type=float, default=None)
    p.add_argument("--confirm-live", action="store_true")
    p.add_argument("--run-id", default=None, help="reuse a run id to resume it")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("contact-sheet", help="build a blind HTML contact sheet for a run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--by", choices=["model", "mode", "format", "refinement"], default="model")
    p.add_argument("--reveal", action="store_true", help="show real model names (not blind)")
    p.add_argument(
        "--allow-partial",
        action="store_true",
        help="debugging only: build artefacts for an incomplete run, "
        "prominently marked PARTIAL / NOT VALID FOR MODEL SELECTION",
    )
    p.add_argument("--outputs-dir", default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_contact_sheet)

    p = sub.add_parser("scoring-sheet", help="build the blind CSV scoring template for a run")
    p.add_argument("--run-id", required=True)
    p.add_argument(
        "--allow-partial",
        action="store_true",
        help="debugging only: build artefacts for an incomplete run, "
        "prominently marked PARTIAL / NOT VALID FOR MODEL SELECTION",
    )
    p.add_argument("--outputs-dir", default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_scoring_sheet)

    p = sub.add_parser("budget-status", help="show the budget ledger for a run")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_budget_status)

    p = sub.add_parser(
        "retry-failures",
        help="retry allowlisted transient terminal failures of an existing run "
        "(original records and ledger entries are preserved as evidence)",
    )
    p.add_argument("--run-id", required=True)
    p.add_argument("--budget-usd", type=float, default=None, help="the ORIGINAL run budget")
    p.add_argument("--max-retries-per-request", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm-live", action="store_true")
    p.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    p.add_argument("--outputs-dir", default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_retry_failures)

    p = sub.add_parser(
        "retry-status",
        help="read-only logical/retry state of a run (no network, no writes)",
    )
    p.add_argument("--run-id", required=True)
    p.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    p.add_argument("--outputs-dir", default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_retry_status)

    p = sub.add_parser(
        "reliability-report",
        help="per-model first-attempt reliability (non-blind; read only after "
        "visual scoring)",
    )
    p.add_argument("--run-id", required=True)
    p.add_argument("--outputs-dir", default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_reliability_report)

    p = sub.add_parser("terms", help="regenerate TERMS_SNAPSHOT.md from the candidates config")
    p.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    p.set_defaults(func=cmd_terms)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
