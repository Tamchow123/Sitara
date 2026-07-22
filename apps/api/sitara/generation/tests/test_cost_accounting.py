"""Provider-boundary cost accounting integration (Phase 16, Part A).

Runs the REAL asynchronous pipeline with injected fake providers and the
autouse in-memory budget ledger — no Anthropic/Replicate client is ever built.
Proves the reserve/reconcile/retain/release semantics at both provider
boundaries, demo bypass, fail-closed on ledger outage, and that no private cost
field leaks into a public payload.
"""

from __future__ import annotations

import logging

import pytest
from django.core.management import call_command
from django.db import DatabaseError
from django.db.models import F

from sitara.ai_gateway.image_generation import (
    PREDICTION_FAILED,
    PREDICTION_SUCCEEDED,
    ImageProviderError,
)
from sitara.ai_gateway.structured_design import StructuredDesignProviderError
from sitara.designs.models import Design, GenerationAttempt
from sitara.generation import cost_accounting, cost_control, errors
from sitara.generation.context import build_generation_context
from sitara.generation.fixture_provider import FixtureStructuredDesignProvider
from sitara.generation.image_fixtures import (
    FakeImageProvider,
    InMemoryStorage,
    synthetic_webp_downloader,
)
from sitara.generation.pipeline import PipelineConfig, run_generation_attempt
from sitara.generation.tests import fakes

from .factory import make_complete_design

pytestmark = pytest.mark.django_db

_Status = GenerationAttempt.Status
_FAST = PipelineConfig(poll_interval_seconds=0.0, poll_max_attempts=10)


@pytest.fixture
def priced(settings):
    """A live pricing profile with known integer rates so reserved/estimated
    micro-USD are exactly predictable."""
    settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 1_000_000_000
    settings.LIVE_GENERATION_PRICING_PROFILE = "test-profile-1"
    settings.ANTHROPIC_INPUT_MICRO_USD_PER_MTOK = 1_000_000  # 1 USD / Mtok
    settings.ANTHROPIC_OUTPUT_MICRO_USD_PER_MTOK = 1_000_000
    settings.ANTHROPIC_MAX_INPUT_TOKENS = 1000
    settings.DESIGN_SPEC_MAX_OUTPUT_TOKENS = 4096
    settings.REPLICATE_MAX_IMAGE_MICRO_USD = 40_000
    return cost_control.active_pricing_profile()


def _source_selections(design):
    return build_generation_context(design).source_selections


def _queued(design) -> GenerationAttempt:
    design.status = Design.Status.GENERATING
    design.save(update_fields=["status"])
    return GenerationAttempt.objects.create(design=design, status=_Status.QUEUED)


def _run(attempt, *, structured=None, image=None, downloader=synthetic_webp_downloader):
    return run_generation_attempt(
        attempt.id,
        structured_provider=structured or FixtureStructuredDesignProvider(),
        image_provider=image or FakeImageProvider(),
        image_downloader=downloader,
        storage=InMemoryStorage(),
        seed_factory=lambda: 0,
        config=_FAST,
    )


def _anthropic_max(profile):
    return cost_control.anthropic_call_max_micro_usd(profile, 4096)


class TestTextBoundary:
    def test_measured_usage_reconciles_down(self, priced, in_memory_budget_ledger):
        design = make_complete_design()
        attempt = _queued(design)
        ss = _source_selections(design)
        # valid_result carries usage 1234 in / 567 out.
        _run(attempt, structured=fakes.SequenceProvider([fakes.valid_result(ss)]))
        attempt.refresh_from_db()
        actual = cost_control.anthropic_actual_micro_usd(priced, 1234, 567)
        assert attempt.cost_reserved_micro_usd >= _anthropic_max(priced)
        # The text portion reconciled to the measured actual (image adds its own).
        assert attempt.accounted_input_tokens == 1234
        assert attempt.accounted_output_tokens == 567
        assert attempt.cost_unresolved_micro_usd == 0
        assert attempt.cost_estimated_micro_usd >= actual
        assert attempt.cost_accounting_complete is True

    def test_missing_usage_retains_conservative_reservation(self, priced, in_memory_budget_ledger):
        # FixtureStructuredDesignProvider returns usage None -> the text stage
        # retains its full conservative reservation. The image stage reconciles to
        # its fixed max (a positive, valid price is now required for every stage).
        design = make_complete_design()
        attempt = _queued(design)
        _run(attempt)
        attempt.refresh_from_db()
        text_max = _anthropic_max(priced)
        image_max = cost_control.replicate_call_max_micro_usd(priced)
        assert attempt.cost_unresolved_micro_usd == text_max  # text retained
        assert attempt.cost_estimated_micro_usd == text_max + image_max

    def test_partial_usage_retains_full_reservation_not_a_partial_reconcile(
        self, priced, in_memory_budget_ledger
    ):
        # A usage report missing ONE dimension must NOT reconcile the missing side
        # to zero (which would refund that portion and undercount spend) — it
        # retains the full conservative reservation instead.
        design = make_complete_design()
        attempt = _queued(design)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider(
            [fakes.result_with_usage(ss, input_tokens=1000, output_tokens=None)]
        )
        _run(attempt, structured=provider)
        attempt.refresh_from_db()
        text_max = _anthropic_max(priced)
        assert attempt.cost_unresolved_micro_usd == text_max  # text retained, not partial
        # A partial reconcile would have folded only the input portion (< text_max).
        assert cost_control.anthropic_actual_micro_usd(priced, 1000, 0) < text_max

    def test_ambiguous_provider_error_retains_full_reservation(
        self, priced, in_memory_budget_ledger
    ):
        design = make_complete_design()
        attempt = _queued(design)
        provider = fakes.RaisingProvider(
            StructuredDesignProviderError("timeout", ambiguous_acceptance=True)
        )
        _run(attempt, structured=provider)
        attempt.refresh_from_db()
        text_max = _anthropic_max(priced)
        assert attempt.status == _Status.FAILED
        assert attempt.error_code == errors.STRUCTURED_SUBMISSION_AMBIGUOUS
        assert attempt.cost_unresolved_micro_usd == text_max
        assert in_memory_budget_ledger.total_for_today() == text_max  # retained

    def test_definite_pre_spend_failure_releases_reservation(self, priced, in_memory_budget_ledger):
        design = make_complete_design()
        attempt = _queued(design)
        provider = fakes.RaisingProvider(
            StructuredDesignProviderError("authentication", ambiguous_acceptance=False)
        )
        _run(attempt, structured=provider)
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED
        assert attempt.cost_estimated_micro_usd == 0
        assert attempt.cost_unresolved_micro_usd == 0
        assert in_memory_budget_ledger.total_for_today() == 0  # released

    def test_validation_retry_uses_a_distinct_reservation(self, priced, in_memory_budget_ledger):
        design = make_complete_design()
        attempt = _queued(design)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.malformed_result(), fakes.valid_result(ss)])
        _run(attempt, structured=provider)
        profile = priced
        initial = cost_control.reservation_id_for(
            attempt.id, cost_control.STAGE_STRUCTURED_INITIAL, profile
        )
        retry = cost_control.reservation_id_for(
            attempt.id, cost_control.STAGE_STRUCTURED_RETRY, profile
        )
        assert initial != retry
        assert initial in in_memory_budget_ledger.reservations
        assert retry in in_memory_budget_ledger.reservations


class TestImageBoundary:
    def test_accepted_prediction_accounts_cost_even_if_polling_fails(
        self, priced, in_memory_budget_ledger
    ):
        design = make_complete_design()
        attempt = _queued(design)
        image = FakeImageProvider(poll_actions=[PREDICTION_FAILED])
        _run(attempt, image=image)
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED
        assert attempt.error_code == errors.IMAGE_PREDICTION_FAILED
        text_max = _anthropic_max(priced)
        # The accepted prediction's image cost is retained despite the later poll
        # failure; the text stage (usage None) retained its reservation too.
        assert attempt.cost_estimated_micro_usd == text_max + 40_000
        assert in_memory_budget_ledger.total_for_today() == text_max + 40_000

    def test_pre_acceptance_image_failure_releases_then_retry_re_reserves(
        self, priced, in_memory_budget_ledger
    ):
        design = make_complete_design()
        attempt = _queued(design)
        # Create fails pre-acceptance -> release + bounded retry. The retry
        # (same attempt id, same stage) must re-reserve cleanly.
        image = FakeImageProvider(
            create_error=ImageProviderError("create_pre_acceptance", ambiguous_acceptance=False)
        )
        from sitara.generation.pipeline import GenerationRetry

        # GenerationRetry propagates out of run_generation_attempt.
        with pytest.raises(GenerationRetry):
            _run(attempt, image=image)
        # The image reservation was RELEASED (pre-acceptance failure); only the
        # text stage's retained reservation (usage None) remains counted.
        assert in_memory_budget_ledger.total_for_today() == _anthropic_max(priced)


class TestDemoBypass:
    def test_demo_attempt_creates_no_budget_keys_and_zero_cost(
        self, priced, in_memory_budget_ledger, inmemory_storage
    ):
        call_command("install_demo_asset_pack", "--dev-synthetic")
        design = make_complete_design()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        attempt = GenerationAttempt.objects.create(
            design=design, status=_Status.QUEUED, is_demo=True
        )
        result = run_generation_attempt(attempt.id, config=_FAST)
        assert result.status == _Status.SUCCEEDED
        assert result.is_demo is True
        # No reservation was ever made and no live cost recorded.
        assert in_memory_budget_ledger.reservation_count() == 0
        assert result.cost_reserved_micro_usd == 0
        assert result.cost_estimated_micro_usd == 0
        assert result.cost_unresolved_micro_usd == 0


class TestLedgerOutageFailsClosed:
    def test_ledger_outage_prevents_provider_invocation(self, priced, in_memory_budget_ledger):
        in_memory_budget_ledger.fail = True
        design = make_complete_design()
        attempt = _queued(design)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        _run(attempt, structured=provider)
        attempt.refresh_from_db()
        # The provider was NEVER called and the attempt failed closed.
        assert provider.calls == 0
        assert attempt.status == _Status.FAILED
        assert attempt.error_code == errors.INTERNAL_GENERATION_ERROR


_FORBIDDEN = (
    "cost_reserved",
    "cost_estimated",
    "cost_unresolved",
    "cost_pricing_profile",
    "cost_accounting_complete",
    "accounted_input_tokens",
    "accounted_output_tokens",
    "micro_usd",
)


class TestNoPublicCostLeak:
    def test_job_payload_exposes_no_cost_field(self, priced, in_memory_budget_ledger):
        from sitara.designs.jobs import public_job_payload

        design = make_complete_design()
        attempt = _queued(design)
        _run(
            attempt,
            structured=fakes.SequenceProvider([fakes.valid_result(_source_selections(design))]),
        )
        attempt.refresh_from_db()
        assert attempt.status == _Status.SUCCEEDED

        flat = str(public_job_payload(attempt)).lower()
        for term in _FORBIDDEN:
            assert term not in flat, f"{term!r} leaked into the job payload"

    def test_openapi_serializers_declare_no_cost_field(self):
        # The documentation-only serializers define the public wire contract; a
        # private cost field must never appear in either.
        from sitara.designs.openapi import DesignResultSerializer, GenerationJobSerializer

        for serializer_cls in (GenerationJobSerializer, DesignResultSerializer):
            names = set(serializer_cls().fields.keys())
            for term in _FORBIDDEN:
                assert not any(
                    term in name for name in names
                ), f"{term!r} present in {serializer_cls.__name__}"


class TestBridgeSelfDefendsDemo:
    """ARCH-001: the demo-bypass invariant is enforced INSIDE the bridge, not
    only at call sites. Calling the cost_accounting functions directly with a
    demo attempt (bypassing any pipeline `if cost_on:` guard) touches no ledger
    and no cost column."""

    def test_all_functions_noop_for_demo_attempt(self, priced, in_memory_budget_ledger):
        design = make_complete_design()
        design.status = Design.Status.GENERATING
        design.save(update_fields=["status"])
        demo = GenerationAttempt.objects.create(design=design, status=_Status.QUEUED, is_demo=True)
        stage = cost_control.STAGE_IMAGE_SUBMISSION
        outcome = cost_accounting.reserve(demo, stage, 500, priced)
        assert outcome.newly_reserved is False
        cost_accounting.reconcile_actual(demo, stage, priced, input_tokens=10, output_tokens=5)
        cost_accounting.reconcile_fixed(demo, stage, priced, 500)
        cost_accounting.retain(demo, stage, priced)
        cost_accounting.release(demo, stage, priced)
        cost_accounting.mark_complete(demo)

        assert in_memory_budget_ledger.reservation_count() == 0
        assert in_memory_budget_ledger.total_for_today() == 0
        demo.refresh_from_db()
        assert demo.cost_reserved_micro_usd == 0
        assert demo.cost_estimated_micro_usd == 0
        assert demo.cost_unresolved_micro_usd == 0
        assert demo.cost_accounting_complete is False


class TestAuditWriteIsBestEffort:
    """REL-001: a failure of the non-authoritative cost-audit DB write must never
    break the pipeline or lose the authoritative accepted-prediction evidence."""

    def test_update_swallows_constraint_violating_db_error(self, priced, in_memory_budget_ledger):
        design = make_complete_design()
        attempt = _queued(design)
        # reserved is 0; a fold that pushes estimated to 100 would violate the
        # designs_attempt_cost_estimated_within_reserved CHECK constraint.
        cost_accounting._update(
            attempt, cost_estimated_micro_usd=F("cost_estimated_micro_usd") + 100
        )
        attempt.refresh_from_db()
        # No exception propagated and the bad write was rolled back in its savepoint.
        assert attempt.cost_estimated_micro_usd == 0

    def test_accepted_prediction_id_survives_a_failing_cost_fold(
        self, priced, in_memory_budget_ledger, monkeypatch
    ):
        design = make_complete_design()
        attempt = _queued(design)

        def boom(*args, **kwargs):
            raise DatabaseError("simulated audit-fold failure")

        # Force the post-acceptance cost fold to raise (worse than reality, where
        # the fold's own DB write is guarded) to prove the reorder protects the id.
        monkeypatch.setattr(cost_accounting, "reconcile_fixed", boom)
        image = FakeImageProvider(poll_actions=[PREDICTION_SUCCEEDED])
        # The raise is absorbed by the pipeline's terminal boundary (the attempt
        # fails), but the authoritative prediction id was persisted BEFORE the
        # fold, so the billed submission is never orphaned.
        _run(attempt, image=image)
        attempt.refresh_from_db()
        assert attempt.image_prediction_id != ""
        assert attempt.image_submission_in_flight is False


class TestInconsistentReservationLoggedDistinctly:
    """REL-002: a reservation-identity mismatch is logged distinctly (error), not
    collapsed into the generic ledger-unavailable warning, and never propagates."""

    def test_reconcile_logs_error_on_inconsistent(
        self, priced, in_memory_budget_ledger, monkeypatch, caplog
    ):
        design = make_complete_design()
        attempt = _queued(design)

        def raise_inconsistent(*args, **kwargs):
            raise cost_control.BudgetLedgerInconsistent("identity mismatch")

        monkeypatch.setattr(cost_control, "reconcile_actual", raise_inconsistent)
        with caplog.at_level(logging.ERROR, logger="sitara.generation.cost_accounting"):
            cost_accounting.reconcile_actual(
                attempt, cost_control.STAGE_STRUCTURED_INITIAL, priced, input_tokens=10
            )
        assert any(
            "identity mismatch" in rec.message and rec.levelno == logging.ERROR
            for rec in caplog.records
        )


class TestTerminalRetainsUnresolvedSubmission:
    """FUNC-001: a submission marker still set when an attempt terminalises
    (crash-window redelivery guard, stuck-job reaper, or ambiguous acceptance)
    means the provider MAY have accepted and billed the request. ``_finalise_failure``
    must retain the reservation so the attempt's audit columns record the
    unresolved (assume-spent) cost, instead of looking like a clean pre-spend
    release that hides possible spend."""

    def test_stuck_reaper_retains_inflight_image_reservation(self, priced, in_memory_budget_ledger):
        from datetime import timedelta

        from django.utils import timezone

        from sitara.generation import pipeline

        design = make_complete_design()
        attempt = _queued(design)
        amount = cost_control.replicate_call_max_micro_usd(priced)
        # A prior run reserved the image stage and set the marker, then stalled.
        cost_accounting.reserve(attempt, cost_control.STAGE_IMAGE_SUBMISSION, amount, priced)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=_Status.RUNNING_IMAGE, image_submission_in_flight=True
        )
        attempt.refresh_from_db()
        assert attempt.cost_reserved_micro_usd == amount
        assert attempt.cost_unresolved_micro_usd == 0  # not yet resolved
        result = pipeline.reconcile_if_stuck(attempt.id, timezone.now() + timedelta(seconds=1))
        assert result == "reconciled"
        attempt.refresh_from_db()
        assert attempt.error_code == errors.GENERATION_STUCK
        # The in-flight image reservation is now recorded as unresolved spend.
        assert attempt.cost_estimated_micro_usd == amount
        assert attempt.cost_unresolved_micro_usd == amount
        assert in_memory_budget_ledger.total_for_today() == amount  # still counted

    def test_ambiguous_terminal_retains_inflight_text_reservation(
        self, priced, in_memory_budget_ledger
    ):
        from sitara.generation import pipeline

        design = make_complete_design()
        attempt = _queued(design)
        amount = _anthropic_max(priced)
        cost_accounting.reserve(attempt, cost_control.STAGE_STRUCTURED_INITIAL, amount, priced)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=_Status.RUNNING_TEXT, text_submission_in_flight=True
        )
        attempt.refresh_from_db()
        pipeline._finalise_failure(attempt, errors.STRUCTURED_SUBMISSION_AMBIGUOUS)
        attempt.refresh_from_db()
        assert attempt.error_code == errors.STRUCTURED_SUBMISSION_AMBIGUOUS
        assert attempt.cost_estimated_micro_usd == amount
        assert attempt.cost_unresolved_micro_usd == amount

    def test_terminal_retain_is_idempotent_on_redelivery(self, priced, in_memory_budget_ledger):
        from sitara.generation import pipeline

        design = make_complete_design()
        attempt = _queued(design)
        amount = _anthropic_max(priced)
        cost_accounting.reserve(attempt, cost_control.STAGE_STRUCTURED_INITIAL, amount, priced)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=_Status.RUNNING_TEXT, text_submission_in_flight=True
        )
        attempt.refresh_from_db()
        pipeline._finalise_failure(attempt, errors.STRUCTURED_SUBMISSION_AMBIGUOUS)
        # A second finalisation (e.g. redelivery) is a no-op — already terminal,
        # so the unresolved audit total is folded exactly once, never doubled.
        again = GenerationAttempt.objects.get(pk=attempt.pk)
        pipeline._finalise_failure(again, errors.STRUCTURED_SUBMISSION_AMBIGUOUS)
        again.refresh_from_db()
        assert again.cost_unresolved_micro_usd == amount

    def test_clean_pre_spend_terminal_does_not_retain(self, priced, in_memory_budget_ledger):
        # No submission marker set: a genuine pre-spend failure. Nothing is
        # retained, so the audit columns stay zero (distinguishable from spend).
        from sitara.generation import pipeline

        design = make_complete_design()
        attempt = _queued(design)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(status=_Status.RUNNING_TEXT)
        attempt.refresh_from_db()
        pipeline._finalise_failure(attempt, errors.DESIGN_INCOMPLETE)
        attempt.refresh_from_db()
        assert attempt.cost_estimated_micro_usd == 0
        assert attempt.cost_unresolved_micro_usd == 0

    def test_demo_attempt_never_retains(self, priced, in_memory_budget_ledger):
        # A demo attempt cannot spend; even with a marker set, terminalisation
        # never touches the ledger or the cost columns.
        from sitara.generation import pipeline

        design = make_complete_design()
        attempt = _queued(design)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=_Status.RUNNING_IMAGE, image_submission_in_flight=True, is_demo=True
        )
        attempt.refresh_from_db()
        pipeline._finalise_failure(attempt, errors.IMAGE_SUBMISSION_AMBIGUOUS)
        attempt.refresh_from_db()
        assert attempt.cost_unresolved_micro_usd == 0
        assert in_memory_budget_ledger.total_for_today() == 0

    def test_terminal_retain_uses_frozen_profile_version_after_rotation(
        self, priced, in_memory_budget_ledger, settings
    ):
        # A reservation made under one profile version must still be found at
        # terminalisation after the operator ROTATES the active pricing profile —
        # the terminal retain uses the version frozen on the attempt, not the
        # currently active one, so the reservation is never silently missed.
        from sitara.generation import pipeline

        design = make_complete_design()
        attempt = _queued(design)
        amount = cost_control.replicate_call_max_micro_usd(priced)
        cost_accounting.reserve(attempt, cost_control.STAGE_IMAGE_SUBMISSION, amount, priced)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=_Status.RUNNING_IMAGE, image_submission_in_flight=True
        )
        settings.LIVE_GENERATION_PRICING_PROFILE = "rotated-v2"  # profile rotated
        attempt.refresh_from_db()
        assert attempt.cost_pricing_profile_version == priced.version  # frozen at reserve
        pipeline._finalise_failure(attempt, errors.IMAGE_SUBMISSION_AMBIGUOUS)
        attempt.refresh_from_db()
        assert attempt.cost_unresolved_micro_usd == amount  # found via frozen version
        assert attempt.cost_accounting_complete is True

    def test_completion_stays_false_when_terminal_retain_is_swallowed(
        self, priced, in_memory_budget_ledger, monkeypatch
    ):
        # A Redis outage during the terminal retain must not block terminalisation,
        # but the attempt must NOT be falsely marked accounting-complete while a
        # reservation may still be 'reserved'.
        from sitara.generation import pipeline

        design = make_complete_design()
        attempt = _queued(design)
        amount = cost_control.replicate_call_max_micro_usd(priced)
        cost_accounting.reserve(attempt, cost_control.STAGE_IMAGE_SUBMISSION, amount, priced)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(
            status=_Status.RUNNING_IMAGE, image_submission_in_flight=True
        )
        attempt.refresh_from_db()

        def _outage(*args, **kwargs):
            raise cost_control.BudgetLedgerUnavailable("ledger down")

        monkeypatch.setattr(cost_control, "retain", _outage)
        pipeline._finalise_failure(attempt, errors.IMAGE_SUBMISSION_AMBIGUOUS)
        attempt.refresh_from_db()
        assert attempt.status == _Status.FAILED  # terminalisation still completed
        assert attempt.cost_accounting_complete is False  # not falsely settled


class TestProviderBoundaryAndSettlement:
    """P1/P2: the provider-boundary reservation fails closed under an invalid
    config; an actual-over-reserved measurement is a recorded incident, not a
    silent clamp; and completion is claimed only when every ledger op settled —
    on the ordinary success path too, not just the terminal-retain path."""

    def test_invalid_config_at_worker_time_fails_closed_before_provider(
        self, priced, in_memory_budget_ledger, settings
    ):
        design = make_complete_design()
        attempt = _queued(design)
        settings.ANTHROPIC_INPUT_MICRO_USD_PER_MTOK = 0  # config invalidated after enqueue
        provider = FixtureStructuredDesignProvider()
        result = _run(attempt, structured=provider)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.INTERNAL_GENERATION_ERROR
        assert provider._calls == 0  # never reached the paid provider
        assert in_memory_budget_ledger.total_for_today() == 0  # nothing reserved

    def test_actual_over_reserved_records_overage_and_incident(
        self, priced, in_memory_budget_ledger, caplog
    ):
        design = make_complete_design()
        attempt = _queued(design)
        cost_accounting.reserve(attempt, cost_control.STAGE_STRUCTURED_INITIAL, 100, priced)
        with caplog.at_level(logging.ERROR, logger="sitara.generation.cost_accounting"):
            cost_accounting.reconcile_actual(
                attempt,
                cost_control.STAGE_STRUCTURED_INITIAL,
                priced,
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            )
        attempt.refresh_from_db()
        assert attempt.cost_estimated_micro_usd == 100  # clamped estimate
        # The overage (measured actual beyond the reservation) is recorded as a
        # distinct incident metric, not silently discarded.
        assert attempt.cost_overage_micro_usd == 2_000_000 - 100
        assert any("reservation exceeded" in rec.message for rec in caplog.records)

    def test_completion_false_when_success_path_reconcile_is_swallowed(
        self, priced, in_memory_budget_ledger, monkeypatch
    ):
        design = make_complete_design()
        attempt = _queued(design)

        def _outage(*args, **kwargs):
            raise cost_control.BudgetLedgerUnavailable("ledger down")

        # Both the text reconcile and the image reconcile_fixed route through
        # cost_control.reconcile_actual; a swallowed outage on the SUCCESS path
        # must leave completion False.
        monkeypatch.setattr(cost_control, "reconcile_actual", _outage)
        _run(
            attempt,
            structured=fakes.SequenceProvider([fakes.valid_result(_source_selections(design))]),
        )
        attempt.refresh_from_db()
        assert attempt.status == _Status.SUCCEEDED  # generation still succeeds
        assert attempt.cost_accounting_settled is False
        assert attempt.cost_accounting_complete is False  # not falsely settled

    def test_completion_true_on_clean_success_path(self, priced, in_memory_budget_ledger):
        design = make_complete_design()
        attempt = _queued(design)
        _run(
            attempt,
            structured=fakes.SequenceProvider([fakes.valid_result(_source_selections(design))]),
        )
        attempt.refresh_from_db()
        assert attempt.status == _Status.SUCCEEDED
        assert attempt.cost_accounting_settled is True
        assert attempt.cost_accounting_complete is True
