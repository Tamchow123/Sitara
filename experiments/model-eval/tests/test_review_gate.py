"""Incomplete runs must never quietly become evaluation artefacts.

contact-sheet and scoring-sheet refuse incomplete runs by default; with
--allow-partial they produce artefacts prominently marked PARTIAL / NOT
VALID FOR MODEL SELECTION — and even those must stay blind."""

import json

import pytest

from conftest import (
    LIVE_ENV,
    MockAdapter,
    make_brief,
    make_bundle,
    make_candidates_config,
    make_stage,
)
from model_eval import cli
from model_eval.config import BriefsFile
from model_eval.contact_sheet import PARTIAL_BANNER_TEXT
from model_eval.replicate_client import ProviderError
from model_eval.result_store import assess_run_completeness
from model_eval.runner import Runner


class SucceedThenOutOfCredit(MockAdapter):
    """First request succeeds; every later one is a 402."""

    def create_prediction(self, replicate_id, version, input_params):
        if self.create_calls:
            self.create_calls.append((replicate_id, version, input_params))
            raise ProviderError(
                "provider rejected request (402): insufficient credit",
                before_acceptance=True,
                status_code=402,
                provider_title="Insufficient credit",
                provider_detail="You have insufficient credit to run this model.",
            )
        return super().create_prediction(replicate_id, version, input_params)


def run_scenario(tmp_path, candidate, adapter, brief_count=1, run_id="gate-run"):
    bundle = make_bundle(
        make_stage(models=[candidate.key], prompt_formats=["editorial"]),
        make_candidates_config(candidate),
        BriefsFile(briefs=[make_brief(f"brief-{i}") for i in range(1, brief_count + 1)]),
    )
    runner = Runner(
        bundle,
        run_id,
        tmp_path / "outputs",
        adapter_factory=lambda _env: adapter,
        env=LIVE_ENV,
        log=lambda msg: None,
        poll_interval_s=0.0,
        poll_timeout_s=1.0,
    )
    outcome = runner.execute(
        dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
    )
    return runner, outcome


class TestCompletenessAssessment:
    def test_complete_run_has_no_problems(self, tmp_path, plain_candidate):
        runner, _ = run_scenario(tmp_path, plain_candidate, MockAdapter())
        assert assess_run_completeness(runner.run_dir) == []

    def test_failed_requests_make_a_run_incomplete(self, tmp_path, plain_candidate):
        adapter = MockAdapter(
            fail_with=ProviderError("boom", before_acceptance=True, status_code=None)
        )
        runner, _ = run_scenario(tmp_path, plain_candidate, adapter)
        problems = " ".join(assess_run_completeness(runner.run_dir))
        assert "failed" in problems
        assert "0 of 1" in problems

    def test_halted_run_is_incomplete(self, tmp_path, plain_candidate):
        runner, outcome = run_scenario(
            tmp_path, plain_candidate, SucceedThenOutOfCredit(), brief_count=3
        )
        assert outcome.halted_reason
        problems = " ".join(assess_run_completeness(runner.run_dir))
        assert "halted early" in problems

    def test_interrupted_run_is_incomplete(self, tmp_path, plain_candidate):
        class Crash(MockAdapter):
            def create_prediction(self, *args, **kwargs):
                raise RuntimeError("simulated crash")

        with pytest.raises(RuntimeError):
            run_scenario(tmp_path, plain_candidate, Crash())
        run_dir = tmp_path / "outputs" / "runs" / "gate-run"
        problems = " ".join(assess_run_completeness(run_dir))
        assert "interrupted" in problems

    def test_unresolved_reservation_is_reported(self, tmp_path, plain_candidate):
        runner, _ = run_scenario(tmp_path, plain_candidate, MockAdapter())
        ledger_path = runner.run_dir / "budget_ledger.json"
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        data["entries"]["stuck"] = {
            "status": "reserved", "reserved_usd": 0.08, "final_usd": None, "note": ""
        }
        ledger_path.write_text(json.dumps(data), encoding="utf-8")
        problems = " ".join(assess_run_completeness(runner.run_dir))
        assert "reservation" in problems


class TestCliGate:
    def _cli(self, tmp_path, *extra):
        return [
            *extra,
            "--run-id", "gate-run",
            "--outputs-dir", str(tmp_path / "outputs"),
        ]

    def test_complete_run_is_reviewable_without_flags(self, tmp_path, plain_candidate):
        runner, _ = run_scenario(tmp_path, plain_candidate, MockAdapter())
        assert cli.main(self._cli(tmp_path, "contact-sheet")) == 0
        assert cli.main(self._cli(tmp_path, "scoring-sheet")) == 0
        sheet = runner.run_dir / "blind" / "contact_sheet_model.html"
        assert PARTIAL_BANNER_TEXT not in sheet.read_text(encoding="utf-8")

    def test_incomplete_run_is_refused_by_default(self, tmp_path, plain_candidate):
        run_scenario(tmp_path, plain_candidate, SucceedThenOutOfCredit(), brief_count=3)
        for command in ("contact-sheet", "scoring-sheet"):
            with pytest.raises(SystemExit, match="NOT valid for model selection"):
                cli.main(self._cli(tmp_path, command))

    def test_allow_partial_produces_marked_blind_artefacts(self, tmp_path, plain_candidate):
        runner, _ = run_scenario(
            tmp_path, plain_candidate, SucceedThenOutOfCredit(), brief_count=3
        )
        assert cli.main(self._cli(tmp_path, "contact-sheet", "--allow-partial")) == 0
        assert cli.main(self._cli(tmp_path, "scoring-sheet", "--allow-partial")) == 0
        sheet = (runner.run_dir / "blind" / "contact_sheet_model.html").read_text(encoding="utf-8")
        csv_text = (runner.run_dir / "blind" / "scoring_sheet.csv").read_text(encoding="utf-8")
        assert PARTIAL_BANNER_TEXT in sheet
        assert PARTIAL_BANNER_TEXT in csv_text.splitlines()[0]
        # Even partial artefacts stay blind.
        for leak in ("plain", "test-owner/plain"):
            assert leak not in sheet
        for record in runner.store.load_all():
            assert record.request_id not in sheet
            assert record.request_id not in csv_text
