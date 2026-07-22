"""Live-generation budget ledger — unit, config, estimation and a REAL Redis
concurrency proof (Phase 16, Part A). No provider is ever constructed here.

The concurrency test deliberately uses a real Redis logical database (a mocked
lock or sequential loop would not prove the Lua atomicity), so this module
overrides the generation package's ``no_network`` guard and its autouse
in-memory-ledger fixture.
"""

from __future__ import annotations

import concurrent.futures

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings import env_nonnegative_int
from sitara.generation import cost_control
from sitara.generation.tests.cost_fakes import InMemoryBudgetLedger

# Real Redis logical DB reserved for this test module (separate from Celery/
# cache/the app's budget DB). Reachable as ``redis`` inside the compose network.
_REDIS_URL = "redis://redis:6379/15"


@pytest.fixture(autouse=True)
def no_network():
    # This module connects to real Redis for the concurrency proof; the parent
    # conftest guard is intentionally disabled here.
    yield


@pytest.fixture(autouse=True)
def in_memory_budget_ledger():
    # Each test manages its own ledger instance; ensure no global leaks across.
    cost_control.reset_ledger()
    yield
    cost_control.reset_ledger()


def _profile(version="p1", *, in_rate=0, out_rate=0, image=0):
    return cost_control.PricingProfile(
        version=version,
        anthropic_input_micro_usd_per_mtok=in_rate,
        anthropic_output_micro_usd_per_mtok=out_rate,
        replicate_max_image_micro_usd=image,
    )


# ---------------------------------------------------------------------------
# Config fails closed (spec test 2)
# ---------------------------------------------------------------------------
class TestConfigFailsClosed:
    def test_non_integer_setting_refuses_startup(self, monkeypatch):
        monkeypatch.setenv("X_BUDGET", "not-a-number")
        with pytest.raises(ImproperlyConfigured):
            env_nonnegative_int("X_BUDGET", 0)

    def test_negative_setting_refuses_startup(self, monkeypatch):
        # A leading '-' is not all-digits, so it is rejected without echo.
        monkeypatch.setenv("X_BUDGET", "-5")
        with pytest.raises(ImproperlyConfigured):
            env_nonnegative_int("X_BUDGET", 0)

    def test_zero_budget_is_not_live_valid(self, settings):
        settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 0
        settings.LIVE_GENERATION_PRICING_PROFILE = "p1"
        assert cost_control.live_cost_config_is_valid() is False

    def test_blank_profile_is_not_live_valid(self, settings):
        settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 1_000
        settings.LIVE_GENERATION_PRICING_PROFILE = ""
        assert cost_control.live_cost_config_is_valid() is False

    def test_positive_budget_and_profile_is_live_valid(self, settings):
        settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 1_000
        settings.LIVE_GENERATION_PRICING_PROFILE = "p1"
        assert cost_control.live_cost_config_is_valid() is True


# ---------------------------------------------------------------------------
# Estimation arithmetic never under-reserves (spec test 1)
# ---------------------------------------------------------------------------
class TestConservativeEstimates:
    def test_ceil_div_rounds_up(self):
        assert cost_control._ceil_div(1, 1_000_000) == 1
        assert cost_control._ceil_div(1_000_001, 1_000_000) == 2
        assert cost_control._ceil_div(2_000_000, 1_000_000) == 2

    def test_token_cost_never_rounds_below_true_cost(self):
        # 1 token at 3 micro-USD/Mtok is a true 0.000003 micro-USD; the estimate
        # rounds UP to 1, never 0.
        assert cost_control._token_cost_micro_usd(1, 3) == 1

    def test_anthropic_max_uses_configured_input_bound_and_output_tokens(self, settings):
        settings.ANTHROPIC_MAX_INPUT_TOKENS = 8192
        profile = _profile(in_rate=3_000_000, out_rate=15_000_000)  # 3 and 15 USD/Mtok
        # input: ceil(8192 * 3_000_000 / 1_000_000) = 24576
        # output: ceil(1000 * 15_000_000 / 1_000_000) = 15000
        assert cost_control.anthropic_call_max_micro_usd(profile, 1000) == 24576 + 15000

    def test_anthropic_actual_rounds_up(self):
        profile = _profile(in_rate=3_000_000, out_rate=15_000_000)
        assert cost_control.anthropic_actual_micro_usd(profile, 1, 1) == 3 + 15

    def test_replicate_max_is_configured_value(self):
        assert cost_control.replicate_call_max_micro_usd(_profile(image=40_000)) == 40_000

    def test_reservation_id_is_deterministic_and_stage_bound(self):
        profile = _profile("v9")
        rid1 = cost_control.reservation_id_for("abc", cost_control.STAGE_IMAGE_SUBMISSION, profile)
        rid2 = cost_control.reservation_id_for("abc", cost_control.STAGE_IMAGE_SUBMISSION, profile)
        rid3 = cost_control.reservation_id_for(
            "abc", cost_control.STAGE_STRUCTURED_INITIAL, profile
        )
        assert rid1 == rid2 != rid3
        assert rid1 == "abc:image_submission:v9"

    def test_unknown_stage_is_rejected(self):
        with pytest.raises(cost_control.BudgetLedgerUnavailable):
            cost_control.reservation_id_for("abc", "not-a-stage", _profile())


# ---------------------------------------------------------------------------
# In-memory ledger logic (mirrors the Lua). Spec tests 4, 5, 6.
# ---------------------------------------------------------------------------
class TestLedgerSemantics:
    def _ledger(self, settings, ceiling):
        settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = ceiling
        return InMemoryBudgetLedger()

    def test_reservation_replay_is_idempotent(self, settings):
        ledger = self._ledger(settings, 1_000)
        profile = _profile()
        first = ledger.reserve("r1", 100, profile)
        second = ledger.reserve("r1", 100, profile)
        assert first.newly_reserved is True
        assert second.newly_reserved is False  # replayed
        assert ledger.total_for_today() == 100  # not doubled

    def test_reservation_rejected_without_mutation_at_ceiling(self, settings):
        ledger = self._ledger(settings, 100)
        profile = _profile()
        ledger.reserve("r1", 100, profile)
        with pytest.raises(cost_control.BudgetExhausted):
            ledger.reserve("r2", 1, profile)
        assert ledger.total_for_today() == 100  # unchanged by the rejected call

    def test_inconsistent_replay_fails_closed(self, settings):
        ledger = self._ledger(settings, 1_000)
        ledger.reserve("r1", 100, _profile("a"))
        with pytest.raises(cost_control.BudgetLedgerInconsistent):
            ledger.reserve("r1", 100, _profile("b"))  # different profile
        with pytest.raises(cost_control.BudgetLedgerInconsistent):
            ledger.reserve("r1", 999, _profile("a"))  # different amount

    def test_reconcile_is_idempotent(self, settings):
        ledger = self._ledger(settings, 1_000)
        profile = _profile()
        ledger.reserve("r1", 100, profile)
        first = ledger.reconcile_actual("r1", 40, profile)
        second = ledger.reconcile_actual("r1", 40, profile)
        assert first.status == "reconciled"
        assert first.estimated_micro_usd == 40
        assert second.status == "already"
        assert ledger.total_for_today() == 40  # refunded 60 once, not twice

    def test_reconcile_clamps_actual_to_reserved(self, settings):
        ledger = self._ledger(settings, 1_000)
        profile = _profile()
        ledger.reserve("r1", 100, profile)
        outcome = ledger.reconcile_actual("r1", 999, profile)  # actual exceeds reserved
        assert outcome.estimated_micro_usd == 100  # clamped, never increased
        assert ledger.total_for_today() == 100

    def test_retain_keeps_full_reservation_as_unresolved(self, settings):
        ledger = self._ledger(settings, 1_000)
        profile = _profile()
        ledger.reserve("r1", 100, profile)
        outcome = ledger.retain("r1", profile)
        assert outcome.estimated_micro_usd == 100
        assert outcome.unresolved_micro_usd == 100
        assert ledger.total_for_today() == 100  # retained, not refunded

    def test_release_refunds_and_cannot_go_negative(self, settings):
        ledger = self._ledger(settings, 1_000)
        profile = _profile()
        ledger.reserve("r1", 100, profile)
        assert ledger.total_for_today() == 100
        ledger.release("r1", profile)
        assert ledger.total_for_today() == 0
        # A duplicate release is a harmless no-op and never drives it negative.
        again = ledger.release("r1", profile)
        assert again.status == "missing"
        assert ledger.total_for_today() == 0

    def test_release_allows_clean_re_reservation(self, settings):
        ledger = self._ledger(settings, 100)
        profile = _profile()
        ledger.reserve("r1", 100, profile)
        ledger.release("r1", profile)
        # A bounded retry of the same deterministic stage re-reserves fresh.
        again = ledger.reserve("r1", 100, profile)
        assert again.newly_reserved is True
        assert ledger.total_for_today() == 100


# ---------------------------------------------------------------------------
# Real Redis atomicity + concurrency proof (spec: N parallel, N-1 admit)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRealRedisLedger:
    def _fresh_ledger(self):
        ledger = cost_control.RedisBudgetLedger(_REDIS_URL, 5)
        client = ledger._connect()
        client.flushdb()
        return ledger, client

    def test_budget_keys_use_utc_day_window_and_bounded_expiry(self, settings):
        settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 1_000
        ledger, client = self._fresh_ledger()
        try:
            profile = _profile("real")
            ledger.reserve("r1", 100, profile)
            day = cost_control._utc_day()
            total_key = f"{cost_control._TOTAL_PREFIX}{day}"
            assert client.get(total_key) == "100"
            ttl = client.ttl(total_key)
            # Bounded: positive, and never longer than a day plus the grace.
            assert 0 < ttl <= 24 * 60 * 60 + cost_control._EXPIRY_GRACE_SECONDS + 5
        finally:
            client.flushdb()

    def test_reconcile_after_reserve_returns_unused_to_ceiling(self, settings):
        settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 1_000
        ledger, client = self._fresh_ledger()
        try:
            profile = _profile("real")
            ledger.reserve("r1", 100, profile)
            ledger.reconcile_actual("r1", 30, profile)
            day = cost_control._utc_day()
            assert client.get(f"{cost_control._TOTAL_PREFIX}{day}") == "30"
        finally:
            client.flushdb()

    def test_n_parallel_reservations_admit_exactly_n_minus_one(self, settings):
        n = 8
        amount = 250
        settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = (n - 1) * amount
        ledger, client = self._fresh_ledger()
        try:
            profile = _profile("real")
            # Pre-connect/register scripts once before the threads race.
            ledger._connect()

            def attempt(i):
                try:
                    ledger.reserve(f"r{i}", amount, profile)
                    return True
                except cost_control.BudgetExhausted:
                    return False

            with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
                results = list(pool.map(attempt, range(n)))

            assert sum(results) == n - 1  # exactly N-1 admitted
            day = cost_control._utc_day()
            assert client.get(f"{cost_control._TOTAL_PREFIX}{day}") == str((n - 1) * amount)
        finally:
            client.flushdb()
