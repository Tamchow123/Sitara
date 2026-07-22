"""In-memory budget ledger for provider-free generation tests.

Mirrors the atomic semantics of ``cost_control.RedisBudgetLedger`` exactly
(UTC-day total, reject-without-mutation on ceiling exceed, idempotent replay,
reconcile clamps to reserved, release deletes and refunds, never negative) but
touches no network, so it is safe under the generation tests' ``no_network``
guard. The real Redis concurrency proof uses the actual Redis ledger, not this.
"""

from __future__ import annotations

from sitara.generation import cost_control


class InMemoryBudgetLedger:
    """Deterministic in-memory replica of the Redis Lua ledger."""

    def __init__(self):
        self.totals: dict[str, int] = {}
        self.reservations: dict[str, dict] = {}
        # When set, every operation raises BudgetLedgerUnavailable — used to
        # prove a ledger outage fails closed (no provider call).
        self.fail = False

    # -- helpers -----------------------------------------------------------
    def _guard(self):
        if self.fail:
            raise cost_control.BudgetLedgerUnavailable("simulated ledger outage")

    def _day(self) -> str:
        return cost_control._utc_day()

    def _ceiling(self) -> int:
        return cost_control.daily_budget_micro_usd()

    # -- ledger API (matches RedisBudgetLedger) ----------------------------
    def reserve(self, reservation_id, amount_micro_usd, profile) -> cost_control.ReserveOutcome:
        self._guard()
        amount = int(amount_micro_usd)
        if amount < 0:
            raise cost_control.BudgetLedgerUnavailable("negative amount")
        existing = self.reservations.get(reservation_id)
        if existing is not None:
            if existing["amount"] != amount or existing["profile"] != profile.version:
                raise cost_control.BudgetLedgerInconsistent("reservation identity mismatch")
            return cost_control.ReserveOutcome("replayed", existing["amount"])
        day = self._day()
        current = self.totals.get(day, 0)
        if current + amount > self._ceiling():
            raise cost_control.BudgetExhausted("daily live-generation budget exhausted")
        self.reservations[reservation_id] = {
            "amount": amount,
            "profile": profile.version,
            "day": day,
            "state": "reserved",
            "estimated": 0,
            "unresolved": 0,
        }
        self.totals[day] = current + amount
        return cost_control.ReserveOutcome("reserved", amount)

    def reconcile_actual(self, reservation_id, actual_micro_usd, profile):
        return self._reconcile(reservation_id, "reconcile", int(actual_micro_usd), profile)

    def retain(self, reservation_id, profile):
        return self._reconcile(reservation_id, "retain", 0, profile)

    def release(self, reservation_id, profile):
        return self._reconcile(reservation_id, "release", 0, profile)

    def _reconcile(self, reservation_id, mode, actual, profile) -> cost_control.ReconcileOutcome:
        self._guard()
        res = self.reservations.get(reservation_id)
        if res is None:
            return cost_control.ReconcileOutcome("missing", 0, 0, 0)
        if res["profile"] != profile.version:
            raise cost_control.BudgetLedgerInconsistent("reservation identity mismatch")
        if res["state"] != "reserved":
            return cost_control.ReconcileOutcome("already", 0, 0, 0)
        reserved = res["amount"]
        day = res["day"]
        if mode == "release":
            cur = self.totals.get(day, 0)
            dec = min(reserved, cur)
            self.totals[day] = cur - dec
            del self.reservations[reservation_id]
            return cost_control.ReconcileOutcome("released", 0, 0, dec)
        if mode == "retain":
            res["state"] = "reconciled"
            res["estimated"] = reserved
            res["unresolved"] = reserved
            return cost_control.ReconcileOutcome("retained", reserved, reserved, 0)
        actual = max(0, min(actual, reserved))
        refund = reserved - actual
        dec = 0
        if refund > 0:
            cur = self.totals.get(day, 0)
            dec = min(refund, cur)
            self.totals[day] = cur - dec
        res["state"] = "reconciled"
        res["estimated"] = actual
        res["unresolved"] = 0
        return cost_control.ReconcileOutcome("reconciled", actual, 0, dec)

    # -- test introspection ------------------------------------------------
    def total_for_today(self) -> int:
        return self.totals.get(self._day(), 0)

    def reservation_count(self) -> int:
        return len(self.reservations)
