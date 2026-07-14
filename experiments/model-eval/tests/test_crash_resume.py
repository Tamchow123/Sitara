"""Crash-safe resume: a run may die at any point; resuming must never
resubmit a prediction whose id was PERSISTED, never double-reserve, and
never lose or double-count spend. (Around the acceptance boundary itself —
between Replicate accepting a request and the id write — duplicate
prevention is best-effort, not exactly-once; that window has no local
evidence to test against.) Crashes are simulated with raw RuntimeErrors
(which the runner deliberately does not catch) at four points:

1. after reservation, before submission
2. after provider acceptance (prediction id persisted)
3. during polling
4. after output download, before final result persistence
"""

import json
from pathlib import Path

import pytest

from conftest import (
    LIVE_ENV,
    MockAdapter,
    make_brief,
    make_bundle,
    make_candidates_config,
    make_stage,
    tiny_png_bytes,
)
from model_eval.config import BriefsFile
from model_eval.replicate_client import Prediction, ProviderError
from model_eval.result_store import ResultStore
from model_eval.runner import Runner


def _prediction(pid: str, status: str) -> Prediction:
    return Prediction(
        id=pid,
        status=status,
        output="https://example.com/output.png",
        error=None,
        model_version="v-mock",
        raw={},
    )


class ResumeOnlyAdapter:
    """Fails the test if anything is submitted; only polling and downloads
    are allowed — exactly what a correct resume needs."""

    def __init__(self):
        self.get_calls: list[str] = []
        self.download_calls: list[str] = []

    def create_prediction(self, replicate_id, version, input_params):  # pragma: no cover
        raise AssertionError("resume must poll the accepted prediction, not resubmit")

    def get_prediction(self, prediction_id: str) -> Prediction:
        self.get_calls.append(prediction_id)
        return _prediction(prediction_id, "succeeded")

    def download(self, url: str, dest: Path, *, max_bytes: int, allowed_mime_prefixes=("image/",)):
        if dest.exists():
            raise ProviderError(f"refusing to overwrite {dest}", before_acceptance=False)
        self.download_calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = tiny_png_bytes()
        dest.write_bytes(payload)
        return "image/png", len(payload)

    def close(self):
        pass


def crashy_bundle(plain_candidate):
    return make_bundle(
        make_stage(prompt_formats=["editorial"]),
        make_candidates_config(plain_candidate),
        BriefsFile(briefs=[make_brief()]),
    )


def build_runner(tmp_path, bundle, adapter, run_id="crash-run"):
    return Runner(
        bundle,
        run_id,
        tmp_path / "outputs",
        adapter_factory=lambda _env: adapter,
        env=LIVE_ENV,
        log=lambda msg: None,
        poll_interval_s=0.0,
        poll_timeout_s=1.0,
    )


def run(runner, tmp_path):
    return runner.execute(
        dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
    )


def ledger_entries(runner):
    data = json.loads((runner.run_dir / "budget_ledger.json").read_text(encoding="utf-8"))
    return data["entries"], data["totals"]


def assert_single_clean_success(runner, outcome, expected_spend=0.04):
    assert len(outcome.succeeded) == 1
    entries, totals = ledger_entries(runner)
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry["status"] == "reconciled"
    assert totals["spent_usd"] == pytest.approx(expected_spend)
    record = runner.store.load(outcome.succeeded[0])
    assert record.status == "succeeded"
    assert record.output_sha256


def _out_of_credit_error() -> ProviderError:
    return ProviderError(
        "provider rejected request (402): insufficient credit",
        before_acceptance=True,
        status_code=402,
        provider_title="Insufficient credit",
        provider_detail="You have insufficient credit to run this model.",
    )


class SucceedThenOutOfCredit(MockAdapter):
    """First request succeeds; every later one is a 402."""

    def create_prediction(self, replicate_id, version, input_params):
        if self.create_calls:
            self.create_calls.append((replicate_id, version, input_params))
            raise _out_of_credit_error()
        return super().create_prediction(replicate_id, version, input_params)


def three_brief_bundle(candidate):
    from model_eval.config import BriefsFile as BF

    return make_bundle(
        make_stage(prompt_formats=["editorial"]),
        make_candidates_config(candidate),
        BF(briefs=[make_brief(f"brief-{i}") for i in (1, 2, 3)]),
    )


class TestRetryablePreAcceptanceHalts:
    """The 402/401 halt message promises 'rerun with the same run id' — these
    tests make that promise true, and prove its safety limits."""

    def test_full_402_halt_and_resume_cycle(self, tmp_path, plain_candidate):
        # Phase 1: request 1 succeeds, request 2 gets a 402, request 3 is
        # never attempted.
        first = SucceedThenOutOfCredit()
        crashed = build_runner(tmp_path, three_brief_bundle(plain_candidate), first, run_id="retry-run")
        outcome1 = run(crashed, tmp_path)
        assert len(outcome1.succeeded) == 1
        assert len(outcome1.failed) == 1
        assert outcome1.halted_reason and "insufficient credit" in outcome1.halted_reason.lower()
        assert len(first.create_calls) == 2, "request 3 must never be attempted"
        succeeded_rid = outcome1.succeeded[0]
        failed_rid = outcome1.failed[0]
        assert "brief-3" not in succeeded_rid + failed_rid

        # Phase 2: credit conceptually restored — rerun with the SAME run id.
        good = MockAdapter()
        resumed = build_runner(tmp_path, three_brief_bundle(plain_candidate), good, run_id="retry-run")
        outcome2 = run(resumed, tmp_path)

        # Request 1 was not re-sent; requests 2 and 3 ran and succeeded.
        assert len(good.create_calls) == 2
        assert outcome2.resumed == [succeeded_rid]
        assert failed_rid in outcome2.succeeded
        assert outcome2.failed == [] and outcome2.previously_failed == []
        assert outcome2.halted_reason is None

        # Final state: three successes, no failures, spend counted once.
        records = resumed.store.load_all()
        assert sorted(r.status for r in records) == ["succeeded"] * 3
        entries, totals = ledger_entries(resumed)
        assert len(entries) == 3
        assert all(e["status"] == "reconciled" for e in entries.values())
        assert totals["spent_usd"] == pytest.approx(3 * 0.04)
        assert totals["reserved_usd"] == 0
        # The run summary reflects the final state (no superseded failure).
        summary = json.loads(
            (resumed.run_dir / "run_summary.json").read_text(encoding="utf-8")
        )
        assert summary["failed"] == 0 and summary["halted_reason"] is None
        from model_eval.result_store import assess_run_completeness

        assert assess_run_completeness(resumed.run_dir) == []

    def test_repeated_402_replaces_the_failure_and_halts_again(self, tmp_path, plain_candidate):
        first = SucceedThenOutOfCredit()
        run(build_runner(tmp_path, three_brief_bundle(plain_candidate), first, run_id="retry-run"), tmp_path)

        still_broke = MockAdapter(fail_with=_out_of_credit_error())
        resumed = build_runner(tmp_path, three_brief_bundle(plain_candidate), still_broke, run_id="retry-run")
        outcome = run(resumed, tmp_path)
        assert len(still_broke.create_calls) == 1, "halts again after one call"
        assert outcome.halted_reason
        assert len(outcome.failed) == 1
        entries, totals = ledger_entries(resumed)
        failed_entry = entries[outcome.failed[0]]
        assert failed_entry["status"] == "released", "the new reservation was released again"
        assert totals["spent_usd"] == pytest.approx(0.04)  # only request 1, once
        record = resumed.store.load(outcome.failed[0])
        assert record.error_category == "provider_insufficient_credit"

    def test_ambiguous_failure_is_still_not_retried(self, tmp_path, plain_candidate):
        ambiguous = MockAdapter(
            fail_with=ProviderError("connection dropped mid-flight", before_acceptance=False)
        )
        run(build_runner(tmp_path, crashy_bundle(plain_candidate), ambiguous, run_id="amb-run"), tmp_path)

        good = MockAdapter()
        resumed = build_runner(tmp_path, crashy_bundle(plain_candidate), good, run_id="amb-run")
        outcome = run(resumed, tmp_path)
        assert good.create_calls == [], "ambiguous failures must stay final"
        assert len(outcome.previously_failed) == 1
        _, totals = ledger_entries(resumed)
        assert totals["spent_usd"] == pytest.approx(0.1)  # conservatively assumed spent, once

    def test_retryable_category_with_accepted_prediction_id_is_never_resubmitted(
        self, tmp_path, plain_candidate
    ):
        first = MockAdapter(fail_with=_out_of_credit_error())
        crashed = build_runner(tmp_path, crashy_bundle(plain_candidate), first, run_id="zombie-run")
        outcome1 = run(crashed, tmp_path)
        rid = outcome1.failed[0]
        # Fabricate the unsafe condition: a retryable category whose record
        # somehow carries an accepted prediction id.
        record = crashed.store.load(rid)
        tainted = record.model_copy(update={"provider_prediction_id": "pred-zombie"})
        crashed.store.record_path(rid).write_text(tainted.model_dump_json(indent=2), encoding="utf-8")

        good = MockAdapter()
        resumed = build_runner(tmp_path, crashy_bundle(plain_candidate), good, run_id="zombie-run")
        outcome2 = run(resumed, tmp_path)
        assert good.create_calls == [], "an accepted prediction id forbids resubmission"
        assert outcome2.previously_failed == [rid]

    def test_submitted_attempt_record_forbids_retry(self, tmp_path, plain_candidate):
        first = MockAdapter(fail_with=_out_of_credit_error())
        crashed = build_runner(tmp_path, crashy_bundle(plain_candidate), first, run_id="attempt-run")
        outcome1 = run(crashed, tmp_path)
        rid = outcome1.failed[0]
        crashed.store.save_attempt(
            rid, {"request_id": rid, "state": "submitted", "prediction_id": "pred-left"}
        )

        good = MockAdapter()
        resumed = build_runner(tmp_path, crashy_bundle(plain_candidate), good, run_id="attempt-run")
        outcome2 = run(resumed, tmp_path)
        assert good.create_calls == []
        assert outcome2.previously_failed == [rid]


class TestCrashAfterReservationBeforeSubmission:
    def test_resume_submits_exactly_once(self, tmp_path, plain_candidate):
        class CrashBeforeSubmit(MockAdapter):
            def create_prediction(self, *args, **kwargs):
                raise RuntimeError("simulated crash before submission")

        bundle = crashy_bundle(plain_candidate)
        crashed = build_runner(tmp_path, bundle, CrashBeforeSubmit())
        with pytest.raises(RuntimeError, match="before submission"):
            run(crashed, tmp_path)

        # State after the crash: reservation persisted, attempt persisted
        # pre-submission, no prediction id, no result record.
        entries, totals = ledger_entries(crashed)
        assert next(iter(entries.values()))["status"] == "reserved"
        rid = next(iter(entries.keys()))
        attempt = crashed.store.load_attempt(rid)
        assert attempt is not None and attempt["state"] == "reserved"
        assert "prediction_id" not in attempt

        good = MockAdapter()
        resumed = build_runner(tmp_path, crashy_bundle(plain_candidate), good)
        outcome = run(resumed, tmp_path)
        assert len(good.create_calls) == 1, "resume must submit exactly once"
        assert_single_clean_success(resumed, outcome)


class TestCrashAfterProviderAcceptance:
    def test_resume_polls_the_accepted_prediction(self, tmp_path, plain_candidate):
        class AcceptThenCrash(MockAdapter):
            def create_prediction(self, replicate_id, version, input_params):
                self.create_calls.append((replicate_id, version, input_params))
                return _prediction("pred-accepted-42", "processing")

            def get_prediction(self, prediction_id):
                raise RuntimeError("simulated crash immediately after acceptance")

        bundle = crashy_bundle(plain_candidate)
        crashed = build_runner(tmp_path, bundle, AcceptThenCrash())
        with pytest.raises(RuntimeError, match="after acceptance"):
            run(crashed, tmp_path)

        entries, _ = ledger_entries(crashed)
        rid = next(iter(entries.keys()))
        attempt = crashed.store.load_attempt(rid)
        assert attempt["state"] == "submitted"
        assert attempt["prediction_id"] == "pred-accepted-42"

        resume_adapter = ResumeOnlyAdapter()
        resumed = build_runner(tmp_path, crashy_bundle(plain_candidate), resume_adapter)
        outcome = run(resumed, tmp_path)
        assert resume_adapter.get_calls[0] == "pred-accepted-42"
        assert_single_clean_success(resumed, outcome)


class TestCrashDuringPolling:
    def test_resume_polls_instead_of_resubmitting(self, tmp_path, plain_candidate):
        class CrashDuringPolling(MockAdapter):
            def __init__(self):
                super().__init__()
                self.polls = 0

            def create_prediction(self, replicate_id, version, input_params):
                self.create_calls.append((replicate_id, version, input_params))
                return _prediction("pred-polling-7", "processing")

            def get_prediction(self, prediction_id):
                self.polls += 1
                if self.polls == 1:
                    return _prediction(prediction_id, "processing")
                raise RuntimeError("simulated crash mid-polling")

        crashed = build_runner(tmp_path, crashy_bundle(plain_candidate), CrashDuringPolling())
        with pytest.raises(RuntimeError, match="mid-polling"):
            run(crashed, tmp_path)

        resume_adapter = ResumeOnlyAdapter()
        resumed = build_runner(tmp_path, crashy_bundle(plain_candidate), resume_adapter)
        outcome = run(resumed, tmp_path)
        assert resume_adapter.get_calls == ["pred-polling-7"]
        assert_single_clean_success(resumed, outcome)


class TestCrashAfterDownloadBeforePersistence:
    def test_resume_reuses_download_and_does_not_double_charge(
        self, tmp_path, plain_candidate, monkeypatch
    ):
        bundle = crashy_bundle(plain_candidate)
        adapter = MockAdapter()
        crashed = build_runner(tmp_path, bundle, adapter)

        real_save = ResultStore.save

        def crash_on_success_save(self, record, **kwargs):
            if record.status == "succeeded":
                raise RuntimeError("simulated crash before result persistence")
            return real_save(self, record, **kwargs)

        monkeypatch.setattr(ResultStore, "save", crash_on_success_save)
        with pytest.raises(RuntimeError, match="before result persistence"):
            run(crashed, tmp_path)
        monkeypatch.setattr(ResultStore, "save", real_save)

        # The crash happened after download AND after reconciliation.
        entries, totals = ledger_entries(crashed)
        rid = next(iter(entries.keys()))
        assert entries[rid]["status"] == "reconciled"
        spent_before_resume = totals["spent_usd"]
        images = list((crashed.run_dir / "images").glob("*"))
        assert len(images) == 1, "output was downloaded before the crash"

        resume_adapter = ResumeOnlyAdapter()
        resumed = build_runner(tmp_path, crashy_bundle(plain_candidate), resume_adapter)
        outcome = run(resumed, tmp_path)
        assert resume_adapter.download_calls == [], "existing download must be reused"
        assert_single_clean_success(resumed, outcome)
        _, totals_after = ledger_entries(resumed)
        assert totals_after["spent_usd"] == pytest.approx(spent_before_resume), (
            "settled ledger entries must not be charged again on resume"
        )
