"""Budget ledger: reserve-before-spend, durability, the hard ceiling, and
Windows-hardened persistence with crash recovery."""

import json
import os

import pytest

import model_eval.budget as budget_module
from model_eval.budget import (
    BudgetError,
    BudgetExceededError,
    BudgetLedger,
    BudgetLockError,
    BudgetPersistenceError,
)


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(budget_module, "_REPLACE_BASE_DELAY_S", 0.0)


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

    def test_stale_lock_from_dead_process_is_reclaimed(self, ledger_path):
        lock = ledger_path.with_suffix(ledger_path.suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("99999999", encoding="utf-8")  # PID that cannot be alive
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.1)
        assert not lock.exists() or lock.read_text(encoding="utf-8") != "99999999"

    def test_unreadable_lock_is_not_reclaimed(self, ledger_path):
        lock = ledger_path.with_suffix(ledger_path.suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("not-a-pid", encoding="utf-8")
        with pytest.raises(BudgetLockError, match="locked"):
            BudgetLedger.open(ledger_path, 1.0)

    def test_positive_budget_required(self, ledger_path):
        with pytest.raises(BudgetError, match="positive"):
            BudgetLedger.open(ledger_path, 0.0)


class TestWindowsHardenedPersistence:
    """os.replace can fail transiently on Windows (antivirus/indexers hold
    the destination). The ledger retries with backoff, preserves the valid
    .tmp on total failure, and recovers it safely on the next open."""

    def _flaky_replace(self, monkeypatch, fail_times: int):
        real_replace = os.replace
        state = {"fails": 0}

        def replace(src, dst, *a, **kw):
            if str(dst).endswith("budget_ledger.json") and state["fails"] < fail_times:
                state["fails"] += 1
                raise PermissionError(5, "Access is denied", str(dst))
            return real_replace(src, dst, *a, **kw)

        monkeypatch.setattr(budget_module.os, "replace", replace)
        return state

    def test_transient_permission_error_is_retried_and_succeeds(
        self, ledger_path, monkeypatch
    ):
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            state = self._flaky_replace(monkeypatch, fail_times=2)
            ledger.reserve("req-1", 0.08)
            assert state["fails"] == 2
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert data["entries"]["req-1"]["status"] == "reserved"
        assert not ledger_path.with_suffix(".json.tmp").exists()

    def test_exhausted_retries_preserve_the_valid_tmp_file(self, ledger_path, monkeypatch):
        tmp = ledger_path.with_suffix(".json.tmp")
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            ledger.reserve("req-1", 0.08)
            self._flaky_replace(monkeypatch, fail_times=99)
            with pytest.raises(BudgetPersistenceError, match="preserved"):
                ledger.release("req-1")
        # The intended (released) state survives in the tmp file...
        assert tmp.exists()
        intended = json.loads(tmp.read_text(encoding="utf-8"))
        assert intended["entries"]["req-1"]["status"] == "released"
        # ...while the main file still shows the stale reservation.
        stale = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert stale["entries"]["req-1"]["status"] == "reserved"


class TestTmpRecovery:
    def _write_incident_state(self, ledger_path):
        """Reproduce the screening-20260713-001 condition: main holds a stale
        $0.08 reservation; the tmp holds its newer released state."""
        entry = {"reserved_usd": 0.08, "final_usd": None, "note": ""}
        main = {
            "budget_usd": 10.0,
            "entries": {"req-stuck": {**entry, "status": "reserved"}},
            "totals": {"spent_usd": 0.0, "reserved_usd": 0.08, "remaining_usd": 9.92},
        }
        newer = {
            "budget_usd": 10.0,
            "entries": {"req-stuck": {**entry, "status": "released", "final_usd": 0.0}},
            "totals": {"spent_usd": 0.0, "reserved_usd": 0.0, "remaining_usd": 10.0},
        }
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(main), encoding="utf-8")
        ledger_path.with_suffix(".json.tmp").write_text(json.dumps(newer), encoding="utf-8")

    def test_valid_newer_tmp_state_is_recovered(self, ledger_path):
        self._write_incident_state(ledger_path)
        with BudgetLedger.open(ledger_path, 10.0) as ledger:
            assert ledger.status_of("req-stuck") == "released"
            assert ledger.reserved_usd == 0.0
            assert ledger.spent_usd == 0.0
        assert not ledger_path.with_suffix(".json.tmp").exists()

    def test_no_double_spend_or_double_reservation_after_recovery(self, ledger_path):
        self._write_incident_state(ledger_path)
        with BudgetLedger.open(ledger_path, 10.0) as ledger:
            # The released request may be deliberately retried: exactly one
            # active reservation results, and totals stay consistent.
            ledger.ensure_reserved("req-stuck", 0.08)
            assert ledger.reserved_usd == pytest.approx(0.08)
            assert ledger.spent_usd == 0.0
            ledger.ensure_reserved("req-stuck", 0.08)  # idempotent
            assert ledger.reserved_usd == pytest.approx(0.08)
            assert ledger.remaining_usd == pytest.approx(9.92)

    def test_malformed_tmp_is_quarantined_not_promoted(self, ledger_path, capsys):
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(
            json.dumps({"budget_usd": 1.0, "entries": {}, "totals": {}}), encoding="utf-8"
        )
        tmp = ledger_path.with_suffix(".json.tmp")
        tmp.write_text("{ this is not json", encoding="utf-8")
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            assert ledger.spent_usd == 0.0
        assert not tmp.exists()
        quarantined = list(ledger_path.parent.glob("*.quarantined*"))
        assert len(quarantined) == 1
        assert "quarantined" in capsys.readouterr().err

    def test_regressive_tmp_state_is_not_promoted(self, ledger_path):
        """A tmp that would resurrect a settled entry must never win."""
        entry = {"reserved_usd": 0.08, "note": ""}
        main = {
            "budget_usd": 1.0,
            "entries": {"req-1": {**entry, "status": "reconciled", "final_usd": 0.04}},
            "totals": {},
        }
        regressive = {
            "budget_usd": 1.0,
            "entries": {"req-1": {**entry, "status": "reserved", "final_usd": None}},
            "totals": {},
        }
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(main), encoding="utf-8")
        ledger_path.with_suffix(".json.tmp").write_text(
            json.dumps(regressive), encoding="utf-8"
        )
        with BudgetLedger.open(ledger_path, 1.0) as ledger:
            assert ledger.status_of("req-1") == "reconciled"
            assert ledger.spent_usd == pytest.approx(0.04)
        assert list(ledger_path.parent.glob("*.quarantined*"))

    def test_tmp_with_different_budget_is_quarantined(self, ledger_path):
        self._write_incident_state(ledger_path)
        tmp = ledger_path.with_suffix(".json.tmp")
        payload = json.loads(tmp.read_text(encoding="utf-8"))
        payload["budget_usd"] = 99.0
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        with BudgetLedger.open(ledger_path, 10.0) as ledger:
            assert ledger.status_of("req-stuck") == "reserved"  # main untouched
        assert list(ledger_path.parent.glob("*.quarantined*"))
