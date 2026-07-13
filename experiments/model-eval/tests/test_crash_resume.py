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
