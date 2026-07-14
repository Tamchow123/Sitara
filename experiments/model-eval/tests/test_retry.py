"""Auditable targeted retries for transient terminal prediction failures.

The fixture mirrors screening-20260714-001: 60 logical cells, 55
first-attempt successes, 4 Schnell E9828 failures and 1 FLUX.2 Pro PA
failure, all with accepted prediction ids, ledger settled. Every provider
interaction is mocked."""

import hashlib
import json
from pathlib import Path

import pytest

from conftest import EXPERIMENT_ROOT, LIVE_ENV, MockAdapter, tiny_png_bytes
from model_eval.config import load_candidates
from model_eval.contact_sheet import build_contact_sheet, prepare_blind_items
from model_eval.replicate_client import Prediction, ProviderGateError
from model_eval.result_store import ResultRecord, ResultStore, assess_run_completeness
from model_eval.retry import (
    RetryStore,
    build_reliability_report,
    compute_logical_summary,
    compute_retry_status,
    execute_retries,
    plan_retries,
    select_logical_outputs,
    transient_match,
)
from model_eval.replicate_client import ProviderError
from model_eval.budget import BudgetError
from model_eval.scoring import build_scoring_sheet

MODELS = {
    "schnell": ("black-forest-labs/flux-schnell", 0.003, 0.01),
    "klein-4b": ("black-forest-labs/flux-2-klein-4b", 0.05, 0.05),
    "flux-1-1-pro": ("black-forest-labs/flux-1.1-pro", 0.04, 0.08),
    "flux-2-pro": ("black-forest-labs/flux-2-pro", 0.12, 0.12),
    "flux-2-max": ("black-forest-labs/flux-2-max", 0.25, 0.25),
}
BRIEFS = [f"scr-b{i:02d}" for i in range(1, 13)]

PA_ERROR = "Prediction interrupted; please retry (code: PA)"
E9828_ERROR = "Director: unexpected error handling prediction (E9828)"

FAILURES = {
    ("schnell", "scr-b02"): E9828_ERROR,
    ("schnell", "scr-b04"): E9828_ERROR,
    ("schnell", "scr-b09"): E9828_ERROR,
    ("schnell", "scr-b11"): E9828_ERROR,
    ("flux-2-pro", "scr-b05"): PA_ERROR,
}


def rid_for(model: str, brief: str) -> str:
    return f"screening--{brief}--{model}--editorial--text_only--s11--base"


def fixture_record(model: str, brief: str, **overrides) -> ResultRecord:
    rid = rid_for(model, brief)
    replicate_id, final, reserve = MODELS[model]
    data = dict(
        run_id="screening-fixture-001",
        stage="screening",
        request_id=rid,
        brief_id=brief,
        garment="lehenga",
        ceremony="baraat",
        tags=["screening"],
        model_key=model,
        replicate_id=replicate_id,
        model_version="v-observed",
        provider_prediction_id=f"orig-pred-{model}-{brief}",
        prompt_format="editorial",
        prompt_text=f"prompt for {brief} on {model}",
        negative_text=None,
        json_payload=None,
        inspiration_mode="text_only",
        reference_ids=[],
        kind="base",
        refinement_id=None,
        refinement_strategy=None,
        base_request_id=None,
        seed=11,
        input_params={"prompt": f"prompt for {brief} on {model}", "seed": 11, "aspect_ratio": "3:4"},
        aspect_ratio="3:4",
        width=768,
        height=1024,
        started_at="2026-07-14T10:00:00+00:00",
        completed_at="2026-07-14T10:00:09+00:00",
        latency_seconds=9.0,
        status="succeeded",
        error_category=None,
        error_message=None,
        estimated_max_cost_usd=reserve,
        reconciled_cost_usd=final,
        cost_basis="calculated",
        provider_create_retries=0,
        rate_limit_wait_seconds=0.0,
        output_path=f"images/{rid}.png",
        output_mime_type="image/png",
        output_sha256="fixture",
        pricing_checked_on="2026-07-13",
        git_commit="fixture",
    )
    data.update(overrides)
    return ResultRecord(**data)


def build_fixture_run(tmp_path: Path, *, spent_override: float | None = None) -> Path:
    run_dir = tmp_path / "outputs" / "runs" / "screening-fixture-001"
    store = ResultStore(run_dir)
    store.results_dir.mkdir(parents=True)
    store.images_dir.mkdir(parents=True)
    entries = {}
    for model in MODELS:
        for brief in BRIEFS:
            rid = rid_for(model, brief)
            _, final, reserve = MODELS[model]
            error = FAILURES.get((model, brief))
            if error:
                record = fixture_record(
                    model, brief,
                    status="failed",
                    error_category="prediction_failed",
                    error_message=error,
                    reconciled_cost_usd=None,
                    cost_basis=None,
                    output_path=None,
                    output_mime_type=None,
                    output_sha256=None,
                    width=None,
                    height=None,
                )
                entries[rid] = {
                    "status": "assumed_spent", "reserved_usd": reserve,
                    "final_usd": reserve, "note": "terminal status failed",
                }
            else:
                record = fixture_record(model, brief)
                (store.images_dir / f"{rid}.png").write_bytes(tiny_png_bytes())
                entries[rid] = {
                    "status": "reconciled", "reserved_usd": reserve,
                    "final_usd": final, "note": "",
                }
            store.save(record)
    spent = spent_override if spent_override is not None else round(
        sum(e["final_usd"] for e in entries.values()), 6
    )
    (run_dir / "budget_ledger.json").write_text(
        json.dumps(
            {
                "budget_usd": 10.0,
                "entries": entries,
                "totals": {
                    "spent_usd": spent,
                    "reserved_usd": 0,
                    "remaining_usd": round(10.0 - spent, 6),
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "plan.json").write_text(
        json.dumps({"planned_requests": 60, "skipped_requests": 0}), encoding="utf-8"
    )
    (run_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "run_id": "screening-fixture-001", "succeeded": 55, "failed": 5,
                "skipped": 0, "resumed": 0, "previously_failed": 0,
                "disabled_models": {}, "halted_reason": None,
                "completed_at": "2026-07-14T11:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return run_dir


@pytest.fixture(scope="module")
def candidates():
    return load_candidates(EXPERIMENT_ROOT / "configs" / "model_candidates.yaml")


def snapshot_originals(run_dir: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((run_dir / "results").glob("*.json"))
    }


def run_retries(tmp_path, run_dir, candidates, adapter, budget=10.0, max_retries=1):
    report = plan_retries(run_dir, candidates, max_retries)
    outcome = execute_retries(
        tmp_path / "outputs",
        run_dir.name,
        candidates,
        report,
        budget_usd=budget,
        env=LIVE_ENV,
        confirm_live=True,
        adapter_factory=lambda _env: adapter,
        log=lambda m: None,
        poll_interval_s=0.0,
        poll_timeout_s=5.0,
        sleep=lambda s: None,
    )
    return report, outcome


class TestEligibility:
    def test_selects_exactly_the_five_transient_failures(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        report = plan_retries(run_dir, candidates, max_retries_per_request=1)
        assert len(report.eligible) == 5
        assert report.counts_by_model() == {"flux-2-pro": 1, "schnell": 4}
        assert report.succeeded_originals == 55
        for c in report.eligible:
            assert c.attempt_id == f"{c.original.request_id}--retry-1"
            assert c.attempt_index == 1

    def test_successful_requests_are_never_selected(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        report = plan_retries(run_dir, candidates, max_retries_per_request=5)
        succeeded_ids = {
            r.request_id for r in ResultStore(run_dir).load_all() if r.status == "succeeded"
        }
        selected = {c.original.request_id for c in report.eligible}
        assert selected.isdisjoint(succeeded_ids)
        assert len(selected) == 5

    def test_retry_reservation_totals_sixteen_cents(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        report = plan_retries(run_dir, candidates, max_retries_per_request=1)
        assert report.additional_max_reservation_usd == pytest.approx(0.16)
        # 4 x $0.01 Schnell + 1 x $0.12 FLUX.2 Pro
        costs = sorted(c.max_cost_usd for c in report.eligible)
        assert costs == [0.01, 0.01, 0.01, 0.01, 0.12]

    def test_unknown_terminal_errors_are_ineligible(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        rid = rid_for("schnell", "scr-b02")
        record = ResultStore(run_dir).load(rid)
        tainted = record.model_copy(update={"error_message": "CUDA error: device-side assert triggered"})
        (run_dir / "results" / f"{rid}.json").write_text(
            tainted.model_dump_json(indent=2), encoding="utf-8"
        )
        report = plan_retries(run_dir, candidates, 1)
        assert len(report.eligible) == 4
        reasons = {i.request_id: i.reason for i in report.ineligible}
        assert "not on the transient allowlist" in reasons[rid]

    def test_safety_and_moderation_errors_are_ineligible(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        rid = rid_for("flux-2-pro", "scr-b05")
        record = ResultStore(run_dir).load(rid)
        tainted = record.model_copy(
            update={"error_message": "Prediction was flagged as sensitive by the safety checker"}
        )
        (run_dir / "results" / f"{rid}.json").write_text(
            tainted.model_dump_json(indent=2), encoding="utf-8"
        )
        report = plan_retries(run_dir, candidates, 1)
        assert rid not in {c.original.request_id for c in report.eligible}
        assert any(i.request_id == rid for i in report.ineligible)

    def test_failures_without_prediction_id_are_ineligible(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        rid = rid_for("schnell", "scr-b04")
        record = ResultStore(run_dir).load(rid)
        tainted = record.model_copy(update={"provider_prediction_id": None})
        (run_dir / "results" / f"{rid}.json").write_text(
            tainted.model_dump_json(indent=2), encoding="utf-8"
        )
        report = plan_retries(run_dir, candidates, 1)
        reasons = {i.request_id: i.reason for i in report.ineligible}
        assert "no accepted provider prediction id" in reasons[rid]

    def test_allowlist_matching_is_normalised_but_conservative(self):
        assert transient_match("  PREDICTION interrupted;  please retry (code: PA) ")
        assert transient_match("director: Unexpected error handling prediction (E9828)")
        assert transient_match("Director: unexpected error handling prediction (E9999)") is None
        assert transient_match("please retry later") is None
        assert transient_match(None) is None


class TestRetryExecution:
    def test_full_recovery_preserves_evidence_and_accounting(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        before_hashes = snapshot_originals(run_dir)
        ledger_before = json.loads((run_dir / "budget_ledger.json").read_text(encoding="utf-8"))

        adapter = MockAdapter()
        report, outcome = run_retries(tmp_path, run_dir, candidates, adapter)
        assert len(outcome.succeeded) == 5 and outcome.failed == []
        # MockAdapter.get_prediction raises AssertionError, so the original
        # (dead) prediction ids were provably never polled or reused.

        # 4. Identical inputs: same prompt, seed and provider parameters,
        # submitted versionless exactly like the original submissions.
        assert len(adapter.create_calls) == 5
        failed_params = [c.original.input_params for c in report.eligible]
        submitted_params = [call[2] for call in adapter.create_calls]
        assert sorted(p["prompt"] for p in submitted_params) == sorted(
            p["prompt"] for p in failed_params
        )
        for params in submitted_params:
            assert params in failed_params
            assert params["seed"] == 11 and params["aspect_ratio"] == "3:4"
        assert all(call[1] is None for call in adapter.create_calls)
        originals = {r.request_id: r for r in ResultStore(run_dir).load_all()}

        # 5. Original failed records byte-identical.
        assert snapshot_originals(run_dir) == before_hashes

        # 6/7. Original ledger entries unchanged and settled; retries use
        # fresh unique keys.
        ledger_after = json.loads((run_dir / "budget_ledger.json").read_text(encoding="utf-8"))
        for rid, entry in ledger_before["entries"].items():
            assert ledger_after["entries"][rid] == entry
        retry_keys = [k for k in ledger_after["entries"] if "--retry-" in k]
        assert len(retry_keys) == 5
        assert all(ledger_after["entries"][k]["status"] == "reconciled" for k in retry_keys)
        assert len(ledger_after["entries"]) == 65

        # Retry records carry full lineage and live in separated dirs.
        retry_records = RetryStore(run_dir).load_all()
        assert len(retry_records) == 5
        for record in retry_records:
            assert record.retry_of_request_id == record.logical_request_id
            assert record.attempt_index == 1
            assert record.original_provider_prediction_id.startswith("orig-pred-")
            assert record.original_error_category == "prediction_failed"
            assert record.retry_reason
            assert record.output_path.startswith("retry-images")

        # 8/9. Logical summary: recoveries never rewrite first-attempt truth.
        summary = compute_logical_summary(run_dir)
        assert summary["planned_logical_requests"] == 60
        assert summary["first_attempt_succeeded"] == 55
        assert summary["first_attempt_failed"] == 5
        assert summary["recovered_by_retry"] == 5
        assert summary["unresolved_logical_requests"] == 0
        assert summary["logical_requests_with_output"] == 60
        assert summary["total_provider_attempts"] == 65
        assert summary["first_attempt_failure_rate"] == pytest.approx(5 / 60, abs=1e-4)
        # Reserved $0.16; reconciled spend is 4 x $0.003 (schnell, verified
        # formula) + $0.12 (flux-2-pro, unresolved -> full reservation).
        assert summary["retry_spend_usd"] == pytest.approx(0.132)
        assert summary["combined_conservative_spend_usd"] == pytest.approx(
            summary["first_attempt_spend_usd"] + 0.132
        )
        assert (run_dir / "logical_summary.json").exists()
        # Reviewable now that every logical cell has an output.
        assert assess_run_completeness(run_dir) == []

    def test_logical_summary_before_retries(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        summary = compute_logical_summary(run_dir)
        assert summary["planned_logical_requests"] == 60
        assert summary["first_attempt_succeeded"] == 55
        assert summary["first_attempt_failed"] == 5
        assert summary["recovered_by_retry"] == 0
        assert summary["logical_requests_with_output"] == 55

    def test_rerun_after_recovery_submits_nothing(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        run_retries(tmp_path, run_dir, candidates, MockAdapter())
        second = MockAdapter()
        report2, outcome2 = run_retries(tmp_path, run_dir, candidates, second)
        assert report2.eligible == []
        assert second.create_calls == []
        reasons = " ".join(i.reason for i in report2.ineligible)
        assert "already recovered" in reasons

    def test_failed_retry_needs_a_deliberately_raised_limit(self, tmp_path, candidates):
        class TerminalFailure(MockAdapter):
            def create_prediction(self, replicate_id, version, input_params):
                self.create_calls.append((replicate_id, version, input_params))
                return Prediction(
                    id=f"retry-pred-{len(self.create_calls)}",
                    status="failed",
                    output=None,
                    error="CUDA out of memory",
                    model_version="v-mock",
                    raw={},
                )

        run_dir = build_fixture_run(tmp_path)
        _, outcome1 = run_retries(tmp_path, run_dir, candidates, TerminalFailure())
        assert len(outcome1.failed) == 5

        # Same limit: nothing is repeated.
        blocked = MockAdapter()
        report2, _ = run_retries(tmp_path, run_dir, candidates, blocked, max_retries=1)
        assert report2.eligible == [] and blocked.create_calls == []
        assert any("retry limit reached" in i.reason for i in report2.ineligible)

        # Deliberately raised limit: a second attempt (--retry-2) is planned.
        report3 = plan_retries(run_dir, candidates, max_retries_per_request=2)
        assert len(report3.eligible) == 5
        assert all(c.attempt_id.endswith("--retry-2") for c in report3.eligible)

    def test_crash_during_retry_resumes_the_accepted_prediction(self, tmp_path, candidates):
        class AcceptThenCrash(MockAdapter):
            def create_prediction(self, replicate_id, version, input_params):
                self.create_calls.append((replicate_id, version, input_params))
                return Prediction(
                    id="retry-pred-accepted", status="processing", output=None,
                    error=None, model_version="v-mock", raw={},
                )

            def get_prediction(self, prediction_id):
                raise RuntimeError("simulated crash during retry")

        class ResumeCapable(MockAdapter):
            def __init__(self):
                super().__init__()
                self.get_calls: list[str] = []

            def get_prediction(self, prediction_id):
                self.get_calls.append(prediction_id)
                return Prediction(
                    id=prediction_id, status="succeeded",
                    output="https://example.com/output.png",
                    error=None, model_version="v-mock", raw={},
                )

        run_dir = build_fixture_run(tmp_path)
        with pytest.raises(RuntimeError, match="during retry"):
            run_retries(tmp_path, run_dir, candidates, AcceptThenCrash())

        resume_adapter = ResumeCapable()
        report2, outcome2 = run_retries(tmp_path, run_dir, candidates, resume_adapter)
        # The interrupted attempt keeps its attempt id (no record was written)
        # and resume POLLS the accepted retry prediction instead of
        # resubmitting it; the other four retries are fresh creations.
        assert "retry-pred-accepted" in resume_adapter.get_calls
        assert len(resume_adapter.create_calls) == 4
        assert len(outcome2.succeeded) == 5

    def test_budget_must_match_the_original_run_ceiling(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        with pytest.raises(BudgetError, match="original budget"):
            run_retries(tmp_path, run_dir, candidates, MockAdapter(), budget=20.0)

    def test_exhausted_remaining_budget_halts_without_provider_calls(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path, spent_override=9.999)
        # Rewrite entries so spent genuinely equals the override (a single
        # inflated settled entry keeps the ledger arithmetic honest).
        ledger = json.loads((run_dir / "budget_ledger.json").read_text(encoding="utf-8"))
        first_key = next(iter(ledger["entries"]))
        ledger["entries"] = {
            first_key: {"status": "reconciled", "reserved_usd": 9.999, "final_usd": 9.999, "note": ""}
        }
        (run_dir / "budget_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
        adapter = MockAdapter()
        _, outcome = run_retries(tmp_path, run_dir, candidates, adapter)
        assert adapter.create_calls == []
        assert outcome.halted_reason and "exceed" in outcome.halted_reason

    def test_live_gates_still_apply(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        report = plan_retries(run_dir, candidates, 1)
        with pytest.raises(ProviderGateError, match="unmet requirements"):
            execute_retries(
                tmp_path / "outputs", run_dir.name, candidates, report,
                budget_usd=10.0, env={}, confirm_live=False,
                adapter_factory=lambda _env: MockAdapter(),
            )


class TestBlindArtefactsAfterRecovery:
    def _recovered_run(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        run_retries(tmp_path, run_dir, candidates, MockAdapter())
        return run_dir

    def test_contact_sheet_shows_sixty_logical_images_not_sixty_five(self, tmp_path, candidates):
        run_dir = self._recovered_run(tmp_path, candidates)
        store = ResultStore(run_dir)
        items, blind_dir = prepare_blind_items(store, run_dir.name)
        assert len(items) == 60
        assert len(list(blind_dir.glob("image-*"))) == 60
        selected = select_logical_outputs(store)
        retry_selected = [r for r in selected if r.retry_of_request_id]
        assert len(retry_selected) == 5

    def test_blind_artefacts_reveal_no_retry_status_or_identity(self, tmp_path, candidates):
        run_dir = self._recovered_run(tmp_path, candidates)
        store = ResultStore(run_dir)
        sheet, mapping = build_contact_sheet(store, run_dir.name)
        csv_path = build_scoring_sheet(store, run_dir.name)
        html = sheet.read_text(encoding="utf-8")
        csv_text = csv_path.read_text(encoding="utf-8")
        leak_terms = ["retry", "--retry-", "attempt"]
        leak_terms += list(MODELS)  # model keys
        leak_terms += [replicate_id for replicate_id, _, _ in MODELS.values()]
        leak_terms += [r.request_id for r in store.load_all()]
        leak_terms += [f"orig-pred-{m}" for m in MODELS]
        leak_terms += [Path(r.output_path).name for r in store.load_all() if r.output_path]
        for text, name in ((html, "HTML"), (csv_text, "CSV")):
            lowered = text.lower()
            for term in leak_terms:
                assert term.lower() not in lowered, f"{name} leaked {term!r}"
        # The protected mapping may carry the lineage.
        mapping_data = json.loads(mapping.read_text(encoding="utf-8"))
        assert sum(1 for i in mapping_data["items"].values() if i["is_retry_recovery"]) == 5

    def test_incomplete_recovery_still_refuses_review(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)

        class OnlyFirstSucceeds(MockAdapter):
            def create_prediction(self, replicate_id, version, input_params):
                if self.create_calls:
                    self.create_calls.append((replicate_id, version, input_params))
                    return Prediction(
                        id=f"p-{len(self.create_calls)}", status="failed", output=None,
                        error="CUDA out of memory", model_version="v", raw={},
                    )
                return super().create_prediction(replicate_id, version, input_params)

        run_retries(tmp_path, run_dir, candidates, OnlyFirstSucceeds())
        problems = assess_run_completeness(run_dir)
        assert any("failed without a successful retry" in p for p in problems)


class TestReliabilityReport:
    def test_report_preserves_first_attempt_failures(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        run_retries(tmp_path, run_dir, candidates, MockAdapter())
        path = build_reliability_report(run_dir)
        assert path.name == "reliability_report.md"
        assert path.parent == run_dir  # never inside blind/
        text = path.read_text(encoding="utf-8")
        # Schnell keeps its 4 first-attempt failures; FLUX.2 Pro keeps 1 —
        # even though every image was recovered.
        assert "| schnell | 12 | 8 | 4 | 66.7% | 4 | 4 | 0 |" in text
        assert "| flux-2-pro | 12 | 11 | 1 | 91.7% | 1 | 1 | 0 |" in text
        assert "do not consult" in text.lower()
        assert "E9828" in text and "code: PA" in text


# ---------------------------------------------------------------------------
# The post-retry-1 state (mirrors screening-20260714-001 after its first
# retry pass): 2 recoveries (flux-2-pro cell and one schnell cell), 3
# schnell cells still failing with E9828.
# ---------------------------------------------------------------------------

RECOVERED_SCHNELL_BRIEF = "scr-b11"
UNRESOLVED_SCHNELL_BRIEFS = ["scr-b02", "scr-b04", "scr-b09"]


class Retry1Outcome(MockAdapter):
    """flux-2-pro and the schnell scr-b11 retry succeed; the other three
    schnell retries reach terminal failure with E9828."""

    def create_prediction(self, replicate_id, version, input_params):
        self.create_calls.append((replicate_id, version, input_params))
        prompt = input_params["prompt"]
        if "flux-2-pro" in prompt or RECOVERED_SCHNELL_BRIEF in prompt:
            return Prediction(
                id=f"retry-pred-{len(self.create_calls)}", status="succeeded",
                output="https://example.com/output.png", error=None,
                model_version="v-mock", raw={},
            )
        return Prediction(
            id=f"retry-pred-{len(self.create_calls)}", status="failed",
            output=None, error=E9828_ERROR, model_version="v-mock", raw={},
        )


def apply_retry1_state(tmp_path, run_dir, candidates):
    """Run the retry-1 pass so the fixture matches the real run's state."""
    _, outcome = run_retries(tmp_path, run_dir, candidates, Retry1Outcome())
    assert len(outcome.succeeded) == 2 and len(outcome.failed) == 3
    return outcome


def snapshot_files(paths) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


class TestSummaryRefresh:
    def test_partial_retry_pass_refreshes_run_summary_exactly(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        stale = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
        assert "retry_attempts" not in stale  # the pre-fix stale shape
        apply_retry1_state(tmp_path, run_dir, candidates)

        summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
        assert summary["planned_logical_requests"] == 60
        assert summary["first_attempt_succeeded"] == 55
        assert summary["first_attempt_failed"] == 5
        assert summary["retry_attempts"] == 5
        assert summary["retry_succeeded"] == 2
        assert summary["retry_failed"] == 3
        assert summary["recovered_by_retry"] == 2
        assert summary["unresolved_logical_requests"] == 3
        assert summary["logical_requests_with_output"] == 57
        assert summary["total_provider_attempts"] == 65
        assert summary["unresolved_by_model"] == {"schnell": 3}
        assert summary["halted_reason"] is None
        assert summary["disabled_models"] == {}
        assert summary["updated_at"]
        assert summary["first_attempt_failure_rate"] == pytest.approx(5 / 60, abs=1e-4)
        # History is not rewritten: 55 first-attempt successes, never 57.
        assert summary["first_attempt_succeeded"] != 57
        # Legacy operational keys from the original pass survive the merge.
        assert summary["succeeded"] == 55 and summary["failed"] == 5

    def test_summary_refreshed_after_full_recovery(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        apply_retry1_state(tmp_path, run_dir, candidates)
        run_retries(tmp_path, run_dir, candidates, MockAdapter(), max_retries=2)
        summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
        assert summary["recovered_by_retry"] == 5
        assert summary["unresolved_logical_requests"] == 0
        assert summary["retry_attempts"] == 8
        assert summary["retry_succeeded"] == 5
        assert summary["retry_failed"] == 3
        assert summary["logical_requests_with_output"] == 60
        assert summary["total_provider_attempts"] == 68
        assert summary["first_attempt_failed"] == 5  # evidence preserved
        assert summary["unresolved_by_model"] == {}

    def test_summary_refreshed_after_halted_pass(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        broke = MockAdapter(
            fail_with=ProviderError(
                "provider rejected request (402): insufficient credit",
                before_acceptance=True,
                status_code=402,
                provider_title="Insufficient credit",
                provider_detail="You have insufficient credit to run this model.",
            )
        )
        _, outcome = run_retries(tmp_path, run_dir, candidates, broke)
        assert outcome.halted_reason
        summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
        assert summary["halted_reason"] and "insufficient credit" in summary["halted_reason"].lower()
        assert summary["retry_attempts"] == 1
        assert summary["retry_failed"] == 1
        assert summary["first_attempt_failed"] == 5


class TestCompletenessFromLogicalState:
    def test_57_of_60_state_is_refused_for_review(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        apply_retry1_state(tmp_path, run_dir, candidates)
        problems = assess_run_completeness(run_dir)
        joined = " ".join(problems)
        assert "3 request(s) failed without a successful retry" in joined
        assert "only 57 of 60" in joined
        # And the CLI gate refuses final blind review outright.
        from model_eval import cli

        with pytest.raises(SystemExit, match="NOT valid for model selection"):
            cli.main(
                [
                    "contact-sheet",
                    "--run-id", run_dir.name,
                    "--outputs-dir", str(tmp_path / "outputs"),
                ]
            )

    def test_completeness_ignores_stale_summary_counts(self, tmp_path, candidates):
        """Even with a deliberately wrong legacy summary, completeness comes
        from the stored records."""
        run_dir = build_fixture_run(tmp_path)
        apply_retry1_state(tmp_path, run_dir, candidates)
        summary_path = run_dir / "run_summary.json"
        lying = json.loads(summary_path.read_text(encoding="utf-8"))
        lying["succeeded"] = 60
        lying["failed"] = 0
        lying["logical_requests_with_output"] = 60  # stale/false claim
        summary_path.write_text(json.dumps(lying), encoding="utf-8")
        problems = assess_run_completeness(run_dir)
        assert any("57 of 60" in p for p in problems)


class TestRetryStatus:
    def test_retry_status_is_read_only_and_accurate(self, tmp_path, candidates, capsys):
        run_dir = build_fixture_run(tmp_path)
        apply_retry1_state(tmp_path, run_dir, candidates)
        before = snapshot_files(p for p in run_dir.rglob("*") if p.is_file())

        from model_eval import cli

        rc = cli.main(
            [
                "retry-status",
                "--run-id", run_dir.name,
                "--outputs-dir", str(tmp_path / "outputs"),
            ]
        )
        assert rc == 0
        after = snapshot_files(p for p in run_dir.rglob("*") if p.is_file())
        assert after == before, "retry-status must write nothing"

        out = capsys.readouterr().out
        assert "First-attempt succeeded:    55" in out
        assert "First-attempt failed:       5" in out
        assert "Retry attempts:             5 (succeeded 2, failed 3)" in out
        assert "Unresolved logical requests: 3" in out
        assert "{'schnell': 3}" in out
        assert out.count("retries used: 1; next attempt: retry-2") == 3
        assert "Additional max reservation for one further retry pass: 0.0300 USD" in out
        assert "Ledger: budget 10.00 USD" in out

    def test_retry_status_computation(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        apply_retry1_state(tmp_path, run_dir, candidates)
        status = compute_retry_status(run_dir, candidates)
        assert len(status["unresolved"]) == 3
        for item in status["unresolved"]:
            assert item["model_key"] == "schnell"
            assert item["retries_used"] == 1
            assert item["next_attempt"] == "retry-2"
            assert item["allowlist_eligible"] is True
            assert item["next_reservation_usd"] == pytest.approx(0.01)
        assert status["next_pass_max_reservation_usd"] == pytest.approx(0.03)


class TestRetryTwo:
    def _prepared(self, tmp_path, candidates):
        run_dir = build_fixture_run(tmp_path)
        apply_retry1_state(tmp_path, run_dir, candidates)
        return run_dir

    def test_retry2_selects_exactly_the_three_unresolved_schnell_cells(self, tmp_path, candidates):
        run_dir = self._prepared(tmp_path, candidates)
        report = plan_retries(run_dir, candidates, max_retries_per_request=2)
        assert len(report.eligible) == 3
        assert report.counts_by_model() == {"schnell": 3}
        assert report.additional_max_reservation_usd == pytest.approx(0.03)
        expected = {
            f"{rid_for('schnell', brief)}--retry-2" for brief in UNRESOLVED_SCHNELL_BRIEFS
        }
        assert {c.attempt_id for c in report.eligible} == expected
        # Neither recovered cell is selected again.
        recovered_reasons = [
            i.reason for i in report.ineligible if "already recovered" in i.reason
        ]
        assert len(recovered_reasons) == 2
        selected_logical = {c.original.request_id for c in report.eligible}
        assert rid_for("flux-2-pro", "scr-b05") not in selected_logical
        assert rid_for("schnell", RECOVERED_SCHNELL_BRIEF) not in selected_logical

    def test_retry2_execution_preserves_all_prior_evidence(self, tmp_path, candidates):
        run_dir = self._prepared(tmp_path, candidates)
        originals_before = snapshot_files((run_dir / "results").glob("*.json"))
        retry1_before = snapshot_files((run_dir / "retry-results").glob("*retry-1.json"))
        ledger_before = json.loads((run_dir / "budget_ledger.json").read_text(encoding="utf-8"))

        adapter = MockAdapter()
        report, outcome = run_retries(tmp_path, run_dir, candidates, adapter, max_retries=2)
        assert len(outcome.succeeded) == 3

        # 12/13. Originals and retry-1 records byte-identical.
        assert snapshot_files((run_dir / "results").glob("*.json")) == originals_before
        assert snapshot_files((run_dir / "retry-results").glob("*retry-1.json")) == retry1_before
        # Failed retry-1 evidence intact in detail.
        for brief in UNRESOLVED_SCHNELL_BRIEFS:
            record = RetryStore(run_dir).load(f"{rid_for('schnell', brief)}--retry-1")
            assert record.status == "failed"
            assert record.error_category == "prediction_failed"
            assert E9828_ERROR in (record.error_message or "")
            assert record.provider_prediction_id
            assert record.attempt_index == 1

        # 14. Prior ledger entries unchanged; 15. distinct new keys.
        ledger_after = json.loads((run_dir / "budget_ledger.json").read_text(encoding="utf-8"))
        for key, entry in ledger_before["entries"].items():
            assert ledger_after["entries"][key] == entry
        new_keys = set(ledger_after["entries"]) - set(ledger_before["entries"])
        assert len(new_keys) == 3
        assert all(k.endswith("--retry-2") for k in new_keys)

        # 16. Identical prompt, seed and provider inputs.
        assert len(adapter.create_calls) == 3
        original_params = {
            json.dumps(ResultStore(run_dir).load(rid_for("schnell", b)).input_params, sort_keys=True)
            for b in UNRESOLVED_SCHNELL_BRIEFS
        }
        submitted = {json.dumps(c[2], sort_keys=True) for c in adapter.create_calls}
        assert submitted == original_params

        # 15 (records). Retry-2 records with their own identifiers.
        for brief in UNRESOLVED_SCHNELL_BRIEFS:
            record = RetryStore(run_dir).load(f"{rid_for('schnell', brief)}--retry-2")
            assert record.attempt_index == 2
            assert record.logical_request_id == rid_for("schnell", brief)

        # Now image-complete and reviewable.
        assert assess_run_completeness(run_dir) == []
        summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
        assert summary["logical_requests_with_output"] == 60
        assert summary["first_attempt_failed"] == 5

    def test_blind_artefacts_after_retry2_reveal_nothing(self, tmp_path, candidates):
        run_dir = self._prepared(tmp_path, candidates)
        run_retries(tmp_path, run_dir, candidates, MockAdapter(), max_retries=2)
        store = ResultStore(run_dir)
        items, _ = prepare_blind_items(store, run_dir.name)
        assert len(items) == 60
        sheet, _ = build_contact_sheet(store, run_dir.name)
        csv_path = build_scoring_sheet(store, run_dir.name)
        for text in (sheet.read_text(encoding="utf-8"), csv_path.read_text(encoding="utf-8")):
            lowered = text.lower()
            for term in ("retry", "attempt", "schnell", "flux-2-pro", "e9828"):
                assert term not in lowered

    def test_reliability_report_distinguishes_rounds(self, tmp_path, candidates):
        run_dir = self._prepared(tmp_path, candidates)
        text = build_reliability_report(run_dir).read_text(encoding="utf-8")
        # After retry-1: Schnell keeps 4 first-attempt failures; the round
        # table shows 4 attempts, 1 success, 3 failures.
        assert "| schnell | 12 | 8 | 4 |" in text
        assert "| schnell | retry-1 | 4 | 1 | 3 |" in text
        assert "| flux-2-pro | retry-1 | 1 | 1 | 0 |" in text
        assert "UNRESOLVED after 1 retry attempt(s)" in text

        run_retries(tmp_path, run_dir, candidates, MockAdapter(), max_retries=2)
        text = build_reliability_report(run_dir).read_text(encoding="utf-8")
        # A retry-2 recovery never improves the first-attempt success rate.
        assert "| schnell | 12 | 8 | 4 | 66.7% |" in text
        assert "| schnell | retry-2 | 3 | 3 | 0 |" in text
        assert "Attempts required per successful logical request:" in text
        assert "UNRESOLVED" not in text
