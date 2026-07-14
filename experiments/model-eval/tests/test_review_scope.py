"""Balanced scoped review: Schnell's disqualification must not unbalance,
leak into, or silently vanish from the blind evaluation.

Fixture state mirrors screening-20260714-001 after retry-2: 58/60 logical
cells with output; schnell has 2 unresolved cells (E9828 on original,
retry-1 and retry-2). All provider interactions are mocked."""

import hashlib
import json
from pathlib import Path

import pytest

from conftest import MockAdapter
from model_eval import cli
from model_eval.config import load_candidates
from model_eval.contact_sheet import build_contact_sheet, prepare_blind_items
from model_eval.replicate_client import Prediction
from model_eval.result_store import ResultStore, assess_run_completeness
from model_eval.review_scope import (
    ReviewScopeError,
    build_review_scope,
    build_review_scope_report,
    load_and_validate_review_scope,
    scoped_logical_outputs,
    write_review_scope,
)
from model_eval.scoring import build_scoring_sheet
from test_retry import (
    E9828_ERROR,
    MODELS,
    RECOVERED_SCHNELL_BRIEF,
    UNRESOLVED_SCHNELL_BRIEFS,
    apply_retry1_state,
    build_fixture_run,
    rid_for,
    run_retries,
    snapshot_files,
)

INCLUDED = ["klein-4b", "flux-1-1-pro", "flux-2-pro", "flux-2-max"]
EXCLUSION_REASON = (
    "Operationally disqualified after 4/12 first-attempt failures and two "
    "unresolved logical cells following retry-2."
)
RETRY2_RECOVERED_BRIEF = "scr-b04"
FINAL_UNRESOLVED_BRIEFS = ["scr-b02", "scr-b09"]


class Retry2Outcome(MockAdapter):
    """scr-b04 recovers on retry-2; scr-b02 and scr-b09 fail again."""

    def create_prediction(self, replicate_id, version, input_params):
        self.create_calls.append((replicate_id, version, input_params))
        if RETRY2_RECOVERED_BRIEF in input_params["prompt"]:
            return Prediction(
                id=f"retry2-pred-{len(self.create_calls)}", status="succeeded",
                output="https://example.com/output.png", error=None,
                model_version="v-mock", raw={},
            )
        return Prediction(
            id=f"retry2-pred-{len(self.create_calls)}", status="failed",
            output=None, error=E9828_ERROR, model_version="v-mock", raw={},
        )


@pytest.fixture(scope="module")
def candidates():
    from conftest import EXPERIMENT_ROOT

    return load_candidates(EXPERIMENT_ROOT / "configs" / "model_candidates.yaml")


def build_post_retry2_run(tmp_path, candidates) -> Path:
    """58/60 state: schnell b02/b09 unresolved after retry-2."""
    run_dir = build_fixture_run(tmp_path)
    apply_retry1_state(tmp_path, run_dir, candidates)
    _, outcome = run_retries(
        tmp_path, run_dir, candidates, Retry2Outcome(), max_retries=2
    )
    assert len(outcome.succeeded) == 1 and len(outcome.failed) == 2
    summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["logical_requests_with_output"] == 58
    assert summary["unresolved_by_model"] == {"schnell": 2}
    return run_dir


class TestScopeValidation:
    def test_unscoped_58_of_60_run_remains_unreviewable(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        problems = assess_run_completeness(run_dir)
        assert any("failed without a successful retry" in p for p in problems)
        with pytest.raises(SystemExit, match="NOT valid for model selection"):
            cli.main(
                ["contact-sheet", "--run-id", run_dir.name,
                 "--outputs-dir", str(tmp_path / "outputs")]
            )

    def test_scope_including_schnell_is_rejected(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        with pytest.raises(ReviewScopeError, match="unresolved logical cell"):
            build_review_scope(run_dir, INCLUDED + ["schnell"], [], "")

    def test_four_model_scope_excluding_schnell_is_accepted(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        scope = build_review_scope(run_dir, INCLUDED, ["schnell"], EXCLUSION_REASON)
        assert scope["included_models"] == sorted(INCLUDED)
        assert scope["excluded_models"] == ["schnell"]
        assert scope["exclusion_reason"] == EXCLUSION_REASON
        assert scope["planned_cells_per_included_model"] == {m: 12 for m in INCLUDED}
        assert len(scope["selected_logical_cells"]) == 48
        assert len(scope["excluded_logical_cells"]) == 12
        assert sorted(scope["unresolved_excluded_cells"]) == sorted(
            rid_for("schnell", b) for b in FINAL_UNRESOLVED_BRIEFS
        )
        assert scope["source_run_summary_hash"]

    def test_selected_matrix_is_balanced_and_deduplicated(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        scope = build_review_scope(run_dir, INCLUDED, ["schnell"], EXCLUSION_REASON)
        records = scoped_logical_outputs(ResultStore(run_dir), scope)
        assert len(records) == 48
        by_model = {}
        for r in records:
            by_model.setdefault(r.model_key, []).append(r)
        assert {m: len(v) for m, v in sorted(by_model.items())} == {m: 12 for m in sorted(INCLUDED)}
        logical_ids = [r.logical_request_id or r.request_id for r in records]
        assert len(logical_ids) == len(set(logical_ids))
        # Earliest successful retry stands in where the original failed.
        recovered = next(
            r for r in records
            if (r.logical_request_id or "") == rid_for("flux-2-pro", "scr-b05")
        )
        assert recovered.attempt_index == 1
        # No schnell cell — successful or unresolved — appears.
        assert "schnell" not in by_model

    def test_unbalanced_included_matrix_is_rejected(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        # Remove one klein-4b cell's record: klein no longer covers the brief set.
        victim = run_dir / "results" / f"{rid_for('klein-4b', 'scr-b03')}.json"
        victim.unlink()
        with pytest.raises(ReviewScopeError, match="unbalanced|does not cover"):
            build_review_scope(run_dir, INCLUDED, ["schnell"], EXCLUSION_REASON)

    def test_exclusion_without_reason_is_rejected(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        with pytest.raises(ReviewScopeError, match="exclusion-reason"):
            build_review_scope(run_dir, INCLUDED, ["schnell"], "   ")

    def test_unaccounted_models_are_rejected(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        with pytest.raises(ReviewScopeError, match="unaccounted"):
            build_review_scope(run_dir, ["flux-2-pro"], [], "")

    def test_stale_scope_is_refused(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        scope = build_review_scope(run_dir, INCLUDED, ["schnell"], EXCLUSION_REASON)
        scope["selected_logical_cells"] = scope["selected_logical_cells"][:-1]
        path = write_review_scope(run_dir, scope)
        with pytest.raises(ReviewScopeError, match="stale"):
            load_and_validate_review_scope(run_dir, path)


class TestScopedBlindArtefacts:
    def _scoped(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        scope = build_review_scope(run_dir, INCLUDED, ["schnell"], EXCLUSION_REASON)
        write_review_scope(run_dir, scope)
        return run_dir, scope

    def test_scoped_artefacts_are_balanced_and_blind(self, tmp_path, candidates):
        run_dir, scope = self._scoped(tmp_path, candidates)
        store = ResultStore(run_dir)
        items, blind_dir = prepare_blind_items(store, run_dir.name, scope=scope)
        assert len(items) == 48
        assert blind_dir.name == "blind-scoped"
        assert len(list(blind_dir.glob("image-*"))) == 48

        sheet, mapping = build_contact_sheet(store, run_dir.name, scope=scope)
        csv_path = build_scoring_sheet(store, run_dir.name, scope=scope)
        html = sheet.read_text(encoding="utf-8")
        csv_text = csv_path.read_text(encoding="utf-8")

        # Exactly four anonymised candidates, 12 rows each in the CSV.
        codes = {line.split(",")[5] for line in csv_text.splitlines()[1:] if line}
        assert len(codes) == 4
        assert all(code.startswith("Candidate ") for code in codes)
        assert len(csv_text.splitlines()) == 1 + 48

        # Full leak scan: no model identity (included or excluded), no retry
        # or exclusion information, no request/prediction ids, no original
        # filenames.
        leak_terms = ["retry", "attempt", "exclu", "disqualif", "e9828"]
        leak_terms += list(MODELS)
        leak_terms += [replicate_id for replicate_id, _, _ in MODELS.values()]
        all_records = store.load_all()
        leak_terms += [r.request_id for r in all_records]
        leak_terms += [
            Path(r.output_path).name for r in all_records if r.output_path
        ]
        leak_terms += [r.provider_prediction_id for r in all_records if r.provider_prediction_id]
        for text, name in ((html, "HTML"), (csv_text, "CSV")):
            lowered = text.lower()
            for term in leak_terms:
                assert term.lower() not in lowered, f"scoped {name} leaked {term!r}"
        for image in blind_dir.glob("image-*"):
            for model in MODELS:
                assert model not in image.name

        # Mapping: exactly the four included identities, never schnell.
        mapping_data = json.loads(mapping.read_text(encoding="utf-8"))
        assert sorted(mapping_data["models"]) == sorted(INCLUDED)
        assert "schnell" not in json.dumps(mapping_data)
        assert len(mapping_data["items"]) == 48

    def test_scoped_cli_generates_despite_incomplete_run(self, tmp_path, candidates):
        run_dir, scope = self._scoped(tmp_path, candidates)
        rc = cli.main(
            ["contact-sheet", "--run-id", run_dir.name,
             "--review-scope", "review_scope.json",
             "--outputs-dir", str(tmp_path / "outputs")]
        )
        assert rc == 0
        rc = cli.main(
            ["scoring-sheet", "--run-id", run_dir.name,
             "--review-scope", "review_scope.json",
             "--outputs-dir", str(tmp_path / "outputs")]
        )
        assert rc == 0
        html = (run_dir / "blind-scoped" / "contact_sheet_model.html").read_text(encoding="utf-8")
        assert "PARTIAL" not in html  # a valid scope is not a partial hack
        assert "schnell" not in html.lower()

    def test_scope_creation_preserves_all_evidence(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        originals_before = snapshot_files((run_dir / "results").glob("*.json"))
        retries_before = snapshot_files((run_dir / "retry-results").glob("*.json"))
        ledger_before = (run_dir / "budget_ledger.json").read_bytes()

        rc = cli.main(
            ["create-review-scope", "--run-id", run_dir.name,
             "--include-model", "klein-4b", "--include-model", "flux-1-1-pro",
             "--include-model", "flux-2-pro", "--include-model", "flux-2-max",
             "--exclude-model", "schnell",
             "--exclusion-reason", EXCLUSION_REASON,
             "--outputs-dir", str(tmp_path / "outputs")]
        )
        assert rc == 0
        assert snapshot_files((run_dir / "results").glob("*.json")) == originals_before
        assert snapshot_files((run_dir / "retry-results").glob("*.json")) == retries_before
        assert (run_dir / "budget_ledger.json").read_bytes() == ledger_before
        assert (run_dir / "review_scope.json").exists()
        assert (run_dir / "review_scope_report.md").exists()


class TestDispositionReport:
    def test_report_preserves_schnell_reliability_facts(self, tmp_path, candidates):
        run_dir = build_post_retry2_run(tmp_path, candidates)
        scope = build_review_scope(run_dir, INCLUDED, ["schnell"], EXCLUSION_REASON)
        report = build_review_scope_report(run_dir, scope)
        text = report.read_text(encoding="utf-8")
        assert "Excluded: schnell — operationally disqualified" in text
        assert EXCLUSION_REASON in text
        assert "Retry limit applied: 2" in text
        assert "- planned logical cells: 12" in text
        assert "- first-attempt successes: 8" in text
        assert "- first-attempt failures: 4" in text
        assert "- first-attempt success rate: 66.7%" in text
        assert "- retry-1: 4 attempts, 1 successes, 3 failures" in text
        assert "- retry-2: 3 attempts, 1 successes, 2 failures" in text
        assert "- logical cells eventually recovered: 2" in text
        assert "- logical cells still unresolved: 2" in text
        assert "`scr-b02`" in text and "`scr-b09`" in text
        assert "- logical cells with output: 10/12" in text
        assert "- total provider attempts: 19" in text
        assert "- total successful provider attempts: 10" in text
        assert "- total failed provider attempts: 9" in text
        assert "No visual-quality conclusion" in text
        assert "Do NOT open this during blind visual scoring" in text
