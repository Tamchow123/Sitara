"""Runner behaviour: dry runs, live gates, budget enforcement order,
references, resume, and secret redaction. Every provider interaction here is
mocked; several tests exist precisely to prove nothing can reach Replicate."""

import json

import httpx
import pytest

from conftest import (
    LIVE_ENV,
    MockAdapter,
    make_brief,
    make_bundle,
    make_candidates_config,
    make_pricing,
    make_reference_entry,
    make_refinement_brief,
    make_stage,
    tiny_png_bytes,
)
from model_eval.config import BriefsFile, ReferenceManifest
from model_eval.replicate_client import (
    ProviderError,
    ProviderGateError,
    ReplicateAdapter,
    live_gate_failures,
)
from model_eval.runner import Runner


def make_runner(tmp_path, bundle, adapter_or_factory, env=LIVE_ENV, run_id="test-run"):
    factory = (
        adapter_or_factory
        if callable(adapter_or_factory)
        else (lambda _env: adapter_or_factory)
    )
    return Runner(
        bundle,
        run_id,
        tmp_path / "outputs",
        adapter_factory=factory,
        env=env,
        log=lambda msg: None,
        poll_interval_s=0.0,
        poll_timeout_s=1.0,
    )


def simple_bundle(plain_candidate, brief=None):
    return make_bundle(
        make_stage(prompt_formats=["editorial"]),
        make_candidates_config(plain_candidate),
        BriefsFile(briefs=[brief or make_brief()]),
    )


class TestDryRun:
    def test_dry_run_makes_zero_network_calls(self, tmp_path, plain_candidate, forbidden_factory):
        runner = make_runner(tmp_path, simple_bundle(plain_candidate), forbidden_factory, env={})
        outcome = runner.execute(
            dry_run=True, confirm_live=False, budget_usd=10.0, references_dir=tmp_path
        )
        assert outcome.succeeded == [] and outcome.failed == []
        assert not runner.run_dir.exists(), "dry run must not create run artefacts"


class TestLiveGates:
    @pytest.mark.parametrize(
        "env,confirm_live,budget",
        [
            ({}, True, 10.0),                                              # nothing set
            ({"REPLICATE_API_TOKEN": "t"}, True, 10.0),                    # ALLOW missing
            ({"ALLOW_PROVIDER_CALLS": "true"}, True, 10.0),                # token missing
            (LIVE_ENV, False, 10.0),                                       # --confirm-live missing
            (LIVE_ENV, True, None),                                        # --budget-usd missing
            (LIVE_ENV, True, 0.0),                                         # non-positive budget
            ({"ALLOW_PROVIDER_CALLS": "TRUE", "REPLICATE_API_TOKEN": "t"}, True, 10.0),  # not exactly "true"
        ],
    )
    def test_any_missing_gate_refuses_and_never_builds_adapter(
        self, tmp_path, plain_candidate, forbidden_factory, env, confirm_live, budget
    ):
        runner = make_runner(tmp_path, simple_bundle(plain_candidate), forbidden_factory, env=env)
        with pytest.raises(ProviderGateError, match="unmet requirements"):
            runner.execute(
                dry_run=False,
                confirm_live=confirm_live,
                budget_usd=budget,
                references_dir=tmp_path,
            )

    def test_gate_failure_list_names_every_missing_requirement(self):
        failures = live_gate_failures({}, confirm_live=False, budget_usd=None)
        joined = " ".join(failures)
        assert "ALLOW_PROVIDER_CALLS" in joined
        assert "REPLICATE_API_TOKEN" in joined
        assert "--budget-usd" in joined
        assert "--confirm-live" in joined


class TestBudgetEnforcement:
    def test_insufficient_budget_never_invokes_provider(self, tmp_path, plain_candidate):
        # Candidate reserves 0.1 conservatively; budget only covers gates, not the call.
        adapter = MockAdapter()
        runner = make_runner(tmp_path, simple_bundle(plain_candidate), adapter)
        outcome = runner.execute(
            dry_run=False, confirm_live=True, budget_usd=0.05, references_dir=tmp_path
        )
        assert adapter.create_calls == []
        assert outcome.halted_reason and "exceed" in outcome.halted_reason

    def test_conservative_reservation_exists_before_provider_call(self, tmp_path, plain_candidate):
        runner_holder = {}

        def assert_reserved(replicate_id, version, input_params):
            ledger_file = runner_holder["runner"].run_dir / "budget_ledger.json"
            data = json.loads(ledger_file.read_text(encoding="utf-8"))
            reserved = [
                rid for rid, e in data["entries"].items() if e["status"] == "reserved"
            ]
            assert reserved, "provider was called without a persisted reservation"

        adapter = MockAdapter(on_create=assert_reserved)
        runner = make_runner(tmp_path, simple_bundle(plain_candidate), adapter)
        runner_holder["runner"] = runner
        outcome = runner.execute(
            dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
        )
        assert len(outcome.succeeded) == 1
        # And afterwards the reservation is reconciled to the expected cost.
        data = json.loads((runner.run_dir / "budget_ledger.json").read_text(encoding="utf-8"))
        entry = next(iter(data["entries"].values()))
        assert entry["status"] == "reconciled"
        assert entry["final_usd"] == pytest.approx(0.04)

    def test_rejected_before_acceptance_releases_reservation(self, tmp_path, plain_candidate):
        adapter = MockAdapter(
            fail_with=ProviderError("422 rejected", before_acceptance=True)
        )
        runner = make_runner(tmp_path, simple_bundle(plain_candidate), adapter)
        outcome = runner.execute(
            dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
        )
        assert len(outcome.failed) == 1
        data = json.loads((runner.run_dir / "budget_ledger.json").read_text(encoding="utf-8"))
        entry = next(iter(data["entries"].values()))
        assert entry["status"] == "released"

    def test_ambiguous_failure_is_conservatively_spent(self, tmp_path, plain_candidate):
        adapter = MockAdapter(
            fail_with=ProviderError("connection dropped mid-flight", before_acceptance=False)
        )
        runner = make_runner(tmp_path, simple_bundle(plain_candidate), adapter)
        runner.execute(dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path)
        data = json.loads((runner.run_dir / "budget_ledger.json").read_text(encoding="utf-8"))
        entry = next(iter(data["entries"].values()))
        assert entry["status"] == "assumed_spent"
        assert entry["final_usd"] == entry["reserved_usd"]


class TestReferences:
    def _reference_bundle(self, reffy_candidate, rights_status):
        brief = make_brief(reference_ids=["ref-a"])
        return make_bundle(
            make_stage(
                models=["reffy"],
                inspiration_modes=["reference_image"],
                prompt_formats=["editorial"],
            ),
            make_candidates_config(reffy_candidate),
            BriefsFile(briefs=[brief]),
            ReferenceManifest(
                references=[make_reference_entry("ref-a", "local/ref-a.png", rights_status)]
            ),
        )

    def test_unverified_reference_is_rejected_without_provider_call(
        self, tmp_path, reffy_candidate
    ):
        adapter = MockAdapter()
        bundle = self._reference_bundle(reffy_candidate, "pending")
        runner = make_runner(tmp_path, bundle, adapter)
        outcome = runner.execute(
            dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
        )
        assert adapter.create_calls == []
        assert len(outcome.skipped) == 1
        record = runner.store.load(outcome.skipped[0])
        assert "reference_rights_not_verified" in (record.error_message or "")

    def test_verified_reference_is_attached_as_data_uri(self, tmp_path, reffy_candidate):
        refs_dir = tmp_path / "references"
        (refs_dir / "local").mkdir(parents=True)
        (refs_dir / "local" / "ref-a.png").write_bytes(tiny_png_bytes((10, 120, 40)))
        adapter = MockAdapter()
        bundle = self._reference_bundle(reffy_candidate, "verified")
        runner = make_runner(tmp_path, bundle, adapter)
        outcome = runner.execute(
            dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=refs_dir
        )
        assert len(outcome.succeeded) == 1
        (_, _, input_params) = adapter.create_calls[0]
        images = input_params["input_images"]
        assert isinstance(images, list) and len(images) == 1
        assert images[0].startswith("data:image/png;base64,")


class TestRefinementEditFlow:
    def test_edit_uses_base_image_and_records_lineage(self, tmp_path, reffy_candidate):
        bundle = make_bundle(
            make_stage(
                models=["reffy"],
                prompt_formats=["editorial"],
                refinement={"enabled": True, "strategies": ["fresh_regeneration", "image_edit"]},
            ),
            make_candidates_config(reffy_candidate),
            BriefsFile(briefs=[make_refinement_brief()]),
        )
        adapter = MockAdapter()
        runner = make_runner(tmp_path, bundle, adapter)
        outcome = runner.execute(
            dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
        )
        assert len(outcome.succeeded) == 3  # base + fresh + edit
        edit_call = adapter.create_calls[-1]
        assert edit_call[2]["input_images"][0].startswith("data:image/")
        edit_records = [
            runner.store.load(rid) for rid in outcome.succeeded
        ]
        edit = next(r for r in edit_records if r.kind == "refinement_edit")
        base = next(r for r in edit_records if r.kind == "base")
        assert edit.base_request_id == base.request_id
        assert edit.refinement_strategy == "image_edit"
        assert "Preserve every unspecified detail" in (edit.prompt_text or "")


class TestResume:
    def test_interrupted_run_resumes_without_duplicate_requests(self, tmp_path, plain_candidate):
        bundle = simple_bundle(plain_candidate)
        first = MockAdapter()
        runner = make_runner(tmp_path, bundle, first)
        outcome1 = runner.execute(
            dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
        )
        assert len(outcome1.succeeded) == 1

        second = MockAdapter()
        runner2 = make_runner(tmp_path, bundle, second)
        outcome2 = runner2.execute(
            dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
        )
        assert second.create_calls == [], "completed requests must not be re-sent"
        assert outcome2.resumed == outcome1.succeeded
        # Spend was not double-counted either.
        data = json.loads((runner2.run_dir / "budget_ledger.json").read_text(encoding="utf-8"))
        assert data["totals"]["spent_usd"] == pytest.approx(0.04)


class TestSecretRedaction:
    def test_provider_errors_and_records_never_contain_the_token(self, tmp_path, plain_candidate):
        token = "r8_super_secret_value_9f2"

        def exploding_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"TLS failure; auth header was Bearer {token}")

        client = httpx.Client(transport=httpx.MockTransport(exploding_handler))
        real_adapter = ReplicateAdapter(token, client=client)

        env = {"ALLOW_PROVIDER_CALLS": "true", "REPLICATE_API_TOKEN": token}
        logged: list[str] = []
        bundle = simple_bundle(plain_candidate)
        runner = Runner(
            bundle,
            "redaction-run",
            tmp_path / "outputs",
            adapter_factory=lambda _env: real_adapter,
            env=env,
            log=logged.append,
            poll_interval_s=0.0,
            poll_timeout_s=1.0,
        )
        outcome = runner.execute(
            dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
        )
        assert len(outcome.failed) == 1
        record = runner.store.load(outcome.failed[0])
        assert token not in (record.error_message or "")
        assert "***REDACTED***" in (record.error_message or "")
        for line in logged:
            assert token not in line
        for artefact in runner.run_dir.rglob("*.json"):
            assert token not in artefact.read_text(encoding="utf-8")
