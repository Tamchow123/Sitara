"""Budget ledger: reserve-before-spend, durability, and the hard ceiling."""

import json

import pytest

from model_eval.budget import (
    BudgetError,
    BudgetExceededError,
    BudgetLedger,
    BudgetLockError,
)


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "budget_ledger.json"


class TestReservation:
    def test_reserve_within_budget(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.25)
            assert ledger.reserved_usd == 0.25
            assert ledger.remaining_usd == 0.75

    def test_reservation_exceeding_budget_is_refused(self, ledger_path):
        with BudgetLedger.open(ledger_path, 0.1) as ledger:
            with pytest.raises(BudgetExceededError):
                ledger.reserve("req-1", 0.25)
            assert ledger.reserved_usd == 0.0

    def test_cumulative_reservations_respect_ceiling(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.5)
            ledger.reserve("req-2", 0.5)
            with pytest.raises(BudgetExceededError):
                ledger.reserve("req-3", 0.01)

    def test_duplicate_reservation_refused(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.1)
            with pytest.raises(BudgetError, match="duplicate"):
                ledger.reserve("req-1", 0.1)


class TestReconciliation:
    def test_reconcile_to_actual(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.25)
            ledger.reconcile("req-1", 0.04)
            assert ledger.spent_usd == 0.04
            assert ledger.reserved_usd == 0.0
            assert ledger.remaining_usd == 0.96

    def test_reconciliation_cannot_exceed_the_ceiling(self, ledger_path):
        """A claimed actual above the reservation halts loudly and accounted
        spend stays capped at the reserved (i.e. budget-checked) amount."""
        with BudgetLedger.open(ledger_path, 0.5) as ledger:
            ledger.reserve("req-1", 0.5)
            with pytest.raises(BudgetError, match="exceeded"):
                ledger.reconcile("req-1", 9.99)
            assert ledger.spent_usd <= ledger.budget_usd
            entry = ledger.entry("req-1")
            assert entry.final_usd == 0.5
            assert "OVERRUN" in entry.note

    def test_release_before_acceptance(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.25)
            ledger.release("req-1")
            assert ledger.spent_usd == 0.0
            assert ledger.remaining_usd == 1.0

    def test_ambiguous_failure_is_assumed_spent(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.25)
            ledger.assume_spent("req-1", note="timeout mid-flight")
            assert ledger.spent_usd == 0.25
            assert ledger.entry("req-1").note == "timeout mid-flight"


class TestDurability:
    def test_state_survives_process_restart(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.3)
            ledger.reconcile("req-1", 0.3)
            ledger.reserve("req-2", 0.4)
            ledger.assume_spent("req-2")
        # "Crash" and restart: totals must not reset.
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            assert ledger.spent_usd == pytest.approx(0.7)
            with pytest.raises(BudgetExceededError):
                ledger.reserve("req-3", 0.4)

    def test_budget_mismatch_on_resume_is_refused(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0):
            pass
        with pytest.raises(BudgetError, match="original budget"):
            BudgetLedger.open(ledger_path, 5.0)

    def test_every_mutation_is_persisted_immediately(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.3)
            on_disk = json.loads(ledger_path.read_text(encoding="utf-8"))
            assert on_disk["entries"]["req-1"]["status"] == "reserved"
            ledger.reconcile("req-1", 0.1)
            on_disk = json.loads(ledger_path.read_text(encoding="utf-8"))
            assert on_disk["entries"]["req-1"]["status"] == "reconciled"
            assert on_disk["totals"]["spent_usd"] == pytest.approx(0.1)

    def test_process_lock_blocks_concurrent_opens(self, ledger_path):
        with BudgetLedger.open(ledger_path, 1.0):
            with pytest.raises(BudgetLockError, match="locked"):
                BudgetLedger.open(ledger_path, 1.0)
        # Released on close:
        with BudgetLedger.open(ledger_path, 1.0):
            pass

    def test_positive_budget_required(self, ledger_path):
        with pytest.raises(BudgetError, match="positive"):
            BudgetLedger.open(ledger_path, 0.0)
