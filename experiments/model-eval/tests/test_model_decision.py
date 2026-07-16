"""Phase 2 model-decision verification.

Two categories:

- ``TestCanonicalDecision`` runs on EVERY checkout (fresh clone, CI): it
  verifies the committed canonical decision JSON at
  ``docs/decisions/0001-image-model.json`` and its agreement with the
  Markdown record. These tests never skip.
- ``TestLocalEvidence`` additionally cross-checks the gitignored evidence
  run when it is present locally; only these tests skip on checkouts
  without the run (set MODEL_EVAL_OUTPUTS_DIR to point elsewhere to
  simulate a clean checkout).

Zero network calls by construction — everything is local file reading."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from conftest import EXPERIMENT_ROOT
from model_eval.decision import (
    CANONICAL_DECISION_PATH,
    DECISION_CRITICAL_FIELDS,
    DecisionValidationError,
    assert_mirror_agrees,
    load_canonical_decision,
    validate_decision,
)

LOCKED_SHA256 = "ac0355030709c192f56371e1780628870dfe001a4c72ce1693961c4b9842dec7"
MARKDOWN_RECORD = CANONICAL_DECISION_PATH.with_suffix(".md")

_OUTPUTS_DIR = Path(
    os.environ.get("MODEL_EVAL_OUTPUTS_DIR", str(EXPERIMENT_ROOT / "outputs"))
)
RUN_DIR = _OUTPUTS_DIR / "runs" / "screening-20260714-001"


@pytest.fixture(scope="module")
def decision() -> dict:
    return load_canonical_decision()


class TestCanonicalDecision:
    """Always-running repository tests: a fresh clone must be able to
    machine-verify the Phase 2 decision from committed state alone."""

    def test_committed_decision_exists_parses_and_validates(self):
        assert CANONICAL_DECISION_PATH.exists(), (
            "the canonical decision JSON must be committed at "
            f"{CANONICAL_DECISION_PATH}"
        )
        decision = load_canonical_decision()  # parses + full validation
        assert decision["decision_id"] == "0001-image-model"

    def test_schema_version_supported_and_status_accepted(self, decision):
        assert decision["schema_version"] == 1
        assert decision["status"] == "accepted"

    def test_selected_models(self, decision):
        assert decision["default_model_key"] == "flux-1-1-pro"
        assert decision["default_provider_model"] == "black-forest-labs/flux-1.1-pro"
        assert decision["fast_model_key"] == "flux-1-1-pro"
        assert decision["fast_provider_model"] == "black-forest-labs/flux-1.1-pro"

    def test_demo_mode_permits_zero_provider_calls(self, decision):
        assert decision["demo_mode_provider_calls"] == 0
        assert "fixture" in decision["demo_mode_policy"].lower()

    def test_schnell_is_excluded_and_never_selectable(self, decision):
        assert "schnell" in decision["excluded_models"]
        assert decision["default_model_key"] != "schnell"
        assert decision["fast_model_key"] != "schnell"
        # The validator structurally forbids selecting any excluded model.
        tampered = dict(decision, default_model_key="schnell")
        with pytest.raises(DecisionValidationError, match="excluded"):
            validate_decision(tampered)

    def test_klein_4b_is_not_selected(self, decision):
        assert decision["default_model_key"] != "klein-4b"
        assert decision["fast_model_key"] != "klein-4b"
        assert "klein-4b" in decision["excluded_models"]

    def test_inspiration_and_refinement_remain_undecided(self, decision):
        joined = " ".join(decision["open_questions"]).lower()
        assert "inspiration" in joined
        assert "refinement" in joined
        assert "undecided" in joined

    def test_locked_score_hash_is_recorded_exactly(self, decision):
        assert decision["scoring_sha256"] == LOCKED_SHA256

    def test_configuration_defaults(self, decision):
        assert decision["configuration_defaults"] == {
            "DEFAULT_IMAGE_MODEL": "black-forest-labs/flux-1.1-pro",
            "FAST_IMAGE_MODEL": "black-forest-labs/flux-1.1-pro",
            "DEMO_MODE": "true",
        }

    def test_markdown_record_and_json_do_not_contradict(self, decision):
        text = MARKDOWN_RECORD.read_text(encoding="utf-8")
        assert "**Status:** accepted" in text
        assert decision["scoring_sha256"] in text
        assert decision["default_provider_model"] in text
        # The Markdown points at the committed canonical JSON, not only at
        # the gitignored run-local mirror.
        assert "0001-image-model.json" in text
        # Facts must match: pooled means and both roles resolved to 1.1 Pro.
        for model, results in decision["visual_results"].items():
            assert f"{results['pooled_mean']:.4f}" in text
        assert "Default MVP production model:** `black-forest-labs/flux-1.1-pro`" in text
        assert "Paid fast/development model:** `black-forest-labs/flux-1.1-pro`" in text

    def test_validator_rejects_malformed_documents(self, decision):
        with pytest.raises(DecisionValidationError, match="missing required"):
            validate_decision({"schema_version": 1})
        with pytest.raises(DecisionValidationError, match="hexadecimal"):
            validate_decision(dict(decision, scoring_sha256="NOT-A-HASH"))
        with pytest.raises(DecisionValidationError, match="schema_version"):
            validate_decision(dict(decision, schema_version=99))
        with pytest.raises(DecisionValidationError, match="status"):
            validate_decision(dict(decision, status="draft"))
        with pytest.raises(DecisionValidationError, match="ZERO provider"):
            validate_decision(dict(decision, demo_mode_provider_calls=1))
        with pytest.raises(DecisionValidationError, match="non-empty"):
            validate_decision(dict(decision, open_questions=[]))

    def test_evidence_manifest_paths_are_repo_relative(self, decision):
        for entry in decision["evidence_manifest"]["entries"]:
            path = entry["path"]
            assert not Path(path).is_absolute()
            assert "\\" not in path and not path[1:2] == ":"
        committed = [
            e for e in decision["evidence_manifest"]["entries"] if e.get("committed")
        ]
        assert committed, "at least the Markdown record must be a committed entry"
        from model_eval.decision import REPO_ROOT

        for entry in committed:
            assert (REPO_ROOT / entry["path"]).exists()


@pytest.mark.skipif(
    not RUN_DIR.exists(),
    reason="optional local-evidence checks: gitignored screening run not "
    "present on this checkout",
)
class TestLocalEvidence:
    """Optional integration checks against the gitignored evidence run."""

    def test_locked_csv_hash_recomputes_to_the_recorded_value(self):
        actual = hashlib.sha256(
            (RUN_DIR / "blind-scoped" / "scoring_sheet_locked.csv").read_bytes()
        ).hexdigest()
        assert actual == LOCKED_SHA256

    def test_local_mirror_agrees_with_canonical_decision(self):
        canonical = load_canonical_decision()
        mirror = json.loads(
            (RUN_DIR / "model_decision.json").read_text(encoding="utf-8")
        )
        assert_mirror_agrees(canonical, mirror)
        # And the guard actually bites on disagreement:
        tampered = dict(mirror, default_model_key="flux-2-pro")
        with pytest.raises(DecisionValidationError, match="disagrees"):
            assert_mirror_agrees(canonical, tampered)
        assert set(DECISION_CRITICAL_FIELDS) <= set(canonical)

    def test_manifest_hashes_match_local_evidence(self):
        decision = load_canonical_decision()
        for entry in decision["evidence_manifest"]["entries"]:
            from model_eval.decision import REPO_ROOT

            target = REPO_ROOT / entry["path"]
            if "sha256" in entry:
                assert (
                    hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha256"]
                ), f"hash mismatch for {entry['path']}"
            elif "aggregate_sha256" in entry:
                digest = hashlib.sha256()
                for path in sorted(target.glob("*")):
                    digest.update(path.read_bytes())
                assert digest.hexdigest() == entry["aggregate_sha256"], (
                    f"aggregate hash mismatch for {entry['path']}"
                )

    def test_local_ledger_totals(self):
        ledger = json.loads((RUN_DIR / "budget_ledger.json").read_text(encoding="utf-8"))
        assert ledger["totals"] == {
            "remaining_usd": 4.24, "reserved_usd": 0, "spent_usd": 5.76,
        }

    def test_local_run_history_is_intact(self):
        summary = json.loads((RUN_DIR / "run_summary.json").read_text(encoding="utf-8"))
        assert summary["first_attempt_succeeded"] == 55
        assert summary["first_attempt_failed"] == 5
        assert summary["unresolved_by_model"] == {"schnell": 2}

    def test_all_manifest_evidence_files_exist_locally(self):
        decision = load_canonical_decision()
        from model_eval.decision import REPO_ROOT

        for entry in decision["evidence_manifest"]["entries"]:
            assert (REPO_ROOT / entry["path"]).exists(), f"missing: {entry['path']}"
