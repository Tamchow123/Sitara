"""Screening-decision integrity: the recorded decision must stay anchored to
the locked, hash-verified evidence.

These tests validate the LOCAL evidence run and its decision artefact; they
skip cleanly on checkouts that do not carry the (gitignored) run outputs.
Zero network calls by construction — everything is file reads."""

import hashlib
import json
from pathlib import Path

import pytest

from conftest import EXPERIMENT_ROOT

RUN_DIR = EXPERIMENT_ROOT / "outputs" / "runs" / "screening-20260714-001"
LOCKED_SHA256 = "ac0355030709c192f56371e1780628870dfe001a4c72ce1693961c4b9842dec7"

pytestmark = pytest.mark.skipif(
    not (RUN_DIR / "model_decision.json").exists(),
    reason="local screening evidence run is not present on this checkout",
)


@pytest.fixture(scope="module")
def decision() -> dict:
    return json.loads((RUN_DIR / "model_decision.json").read_text(encoding="utf-8"))


class TestDecisionIntegrity:
    def test_locked_scoring_hash_matches_evidence_and_decision(self, decision):
        actual = hashlib.sha256(
            (RUN_DIR / "blind-scoped" / "scoring_sheet_locked.csv").read_bytes()
        ).hexdigest()
        assert actual == LOCKED_SHA256
        assert decision["scoring_sha256"] == LOCKED_SHA256
        sidecar = json.loads(
            (RUN_DIR / "blind-scoped" / "scoring_sheet_locked.sha256.json").read_text(
                encoding="utf-8"
            )
        )
        assert sidecar["Hash"].lower() == LOCKED_SHA256

    def test_selected_model_keys(self, decision):
        assert decision["default_model_key"] == "flux-1-1-pro"
        assert decision["default_provider_model"] == "black-forest-labs/flux-1.1-pro"
        assert decision["fast_model_key"] == "flux-1-1-pro"
        assert decision["fast_provider_model"] == "black-forest-labs/flux-1.1-pro"
        # Configuration default, not hard-coupling.
        config = decision["configuration_defaults"]
        assert config["DEFAULT_IMAGE_MODEL"] == "black-forest-labs/flux-1.1-pro"
        assert config["FAST_IMAGE_MODEL"] == "black-forest-labs/flux-1.1-pro"

    def test_demo_mode_makes_zero_provider_calls(self, decision):
        assert decision["demo_mode_provider_calls"] == 0
        assert decision["configuration_defaults"]["DEMO_MODE"] == "true"
        assert "fixture" in decision["demo_mode_policy"].lower()

    def test_schnell_cannot_be_selected(self, decision):
        assert decision["default_model_key"] != "schnell"
        assert decision["fast_model_key"] != "schnell"
        assert "schnell" in decision["excluded_models"]
        assert decision["operational_results"]["schnell"]["status"] == (
            "operationally_disqualified"
        )
        scope = json.loads((RUN_DIR / "review_scope.json").read_text(encoding="utf-8"))
        assert "schnell" in scope["excluded_models"]
        assert "schnell" not in scope["included_models"]
        # Klein 4B is likewise not the fast model.
        assert decision["fast_model_key"] != "klein-4b"
        assert "klein-4b" in decision["excluded_models"]

    def test_decision_evidence_files_exist(self, decision):
        for rel in decision["evidence_files"]:
            assert (RUN_DIR / rel).exists(), f"missing evidence file: {rel}"
        # Open questions stay open: later stages have not been evaluated.
        joined = " ".join(decision["no_conclusion_yet"]).lower()
        assert "inspiration" in joined and "refinement" in joined
        assert len(decision["limitations"]) >= 7
        assert len(decision["next_evaluation_stages"]) == 6

    def test_original_evidence_is_unmodified(self):
        """The evidence the decision rests on matches its recorded hashes and
        settled ledger totals — nothing was reshaped to fit the decision."""

        def aggregate(pattern: str) -> str:
            digest = hashlib.sha256()
            for path in sorted(RUN_DIR.glob(pattern)):
                digest.update(path.read_bytes())
            return digest.hexdigest()[:32]

        assert aggregate("results/*.json") == "1aa60813c76a34e48beb805fa964b908"
        assert aggregate("retry-results/*.json") == "b402dfea4c18afe33a2f976ab70dfa08"
        assert aggregate("budget_ledger.json") == "3066e2509115f182f1a5875f3cc46835"
        ledger = json.loads((RUN_DIR / "budget_ledger.json").read_text(encoding="utf-8"))
        assert ledger["totals"] == {
            "remaining_usd": 4.24, "reserved_usd": 0, "spent_usd": 5.76,
        }
        summary = json.loads((RUN_DIR / "run_summary.json").read_text(encoding="utf-8"))
        assert summary["first_attempt_succeeded"] == 55
        assert summary["first_attempt_failed"] == 5
        assert summary["unresolved_by_model"] == {"schnell": 2}
