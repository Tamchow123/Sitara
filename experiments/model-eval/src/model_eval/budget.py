"""Hard, durable budget enforcement for live runs.

This is an EXPERIMENT-LEVEL control for a single-process CLI: a JSON ledger
on disk with atomic replacement (write temp file + os.replace) and an
exclusive lock file. It is deliberately not the production budget mechanism —
the Sitara application will use an atomic Redis reserve-before-spend design
(see docs/PHASES.md Phase 16).

Rules enforced here:

1. A conservative maximum cost is reserved BEFORE any provider call.
2. A reservation that would exceed the run budget raises BudgetExceededError
   and the provider is never invoked for that request.
3. After completion the reservation is reconciled to the best available
   actual/estimated cost — never above the reserved amount, so accounted
   spend can never exceed the configured ceiling. If a caller claims a cost
   above the reservation, the ledger records the overrun claim, accounts the
   reserved amount, persists, and raises: nothing is ever silent.
4. Failures known to have happened before provider acceptance release the
   reservation; ambiguous failures conservatively convert it to spend.
5. State is persisted after every mutation, so a crashed run resumes with
   the true spent/reserved totals rather than a reset budget.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a PID (never signals/kills anything).

    Note: on Windows os.kill(pid, 0) would TERMINATE the process, so a
    query-only OpenProcess is used instead."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

EntryStatus = Literal["reserved", "reconciled", "released", "assumed_spent"]


class BudgetError(Exception):
    pass


class BudgetExceededError(BudgetError):
    pass


class BudgetLockError(BudgetError):
    pass


@dataclass
class LedgerEntry:
    status: EntryStatus
    reserved_usd: float
    final_usd: float | None = None
    note: str = ""


class BudgetLedger:
    """Durable single-process budget ledger.

    Use as a context manager so the process lock is always released:

        with BudgetLedger.open(path, budget_usd=10.0) as ledger:
            ledger.reserve("req-1", 0.08)
            ...
    """

    def __init__(self, path: Path, budget_usd: float, entries: dict[str, LedgerEntry]):
        self._path = path
        self._budget_usd = budget_usd
        self._entries = entries
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._locked = False

    # -- lifecycle ----------------------------------------------------------

    @classmethod
    def open(cls, path: Path, budget_usd: float) -> "BudgetLedger":
        if budget_usd <= 0:
            raise BudgetError("budget must be a positive USD amount")
        entries: dict[str, LedgerEntry] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            stored_budget = float(data["budget_usd"])
            if abs(stored_budget - budget_usd) > 1e-9:
                raise BudgetError(
                    f"ledger at {path} was created with budget "
                    f"{stored_budget:.4f} USD but this run requested "
                    f"{budget_usd:.4f} USD. Resume with the original budget, "
                    "or start a new run ID for a new budget."
                )
            for rid, e in data["entries"].items():
                entries[rid] = LedgerEntry(
                    status=e["status"],
                    reserved_usd=float(e["reserved_usd"]),
                    final_usd=None if e["final_usd"] is None else float(e["final_usd"]),
                    note=e.get("note", ""),
                )
        ledger = cls(path, budget_usd, entries)
        ledger._acquire_lock()
        if not path.exists():
            ledger._persist()
        return ledger

    def _acquire_lock(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder_pid = self._read_lock_pid()
                if attempt == 1 and holder_pid is not None and not _pid_alive(holder_pid):
                    # Stale lock from a crashed run: reclaim it safely.
                    try:
                        self._lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                raise BudgetLockError(
                    f"budget ledger is locked by process {holder_pid} (lock "
                    f"file {self._lock_path}). If you are certain no other "
                    "run is active, delete the lock file and retry."
                ) from None
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
            self._locked = True
            return

    def _read_lock_pid(self) -> int | None:
        try:
            return int(self._lock_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def close(self) -> None:
        if self._locked:
            try:
                self._lock_path.unlink()
            except FileNotFoundError:
                pass
            self._locked = False

    def __enter__(self) -> "BudgetLedger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- accounting ---------------------------------------------------------

    @property
    def budget_usd(self) -> float:
        return self._budget_usd

    @property
    def reserved_usd(self) -> float:
        return round(
            sum(e.reserved_usd for e in self._entries.values() if e.status == "reserved"), 9
        )

    @property
    def spent_usd(self) -> float:
        return round(
            sum(
                e.final_usd or 0.0
                for e in self._entries.values()
                if e.status in ("reconciled", "assumed_spent")
            ),
            9,
        )

    @property
    def remaining_usd(self) -> float:
        return round(self._budget_usd - self.spent_usd - self.reserved_usd, 9)

    def entry(self, request_id: str) -> LedgerEntry | None:
        return self._entries.get(request_id)

    def status_of(self, request_id: str) -> EntryStatus | None:
        e = self._entries.get(request_id)
        return None if e is None else e.status

    def has_reservation(self, request_id: str) -> bool:
        e = self._entries.get(request_id)
        return e is not None and e.status == "reserved"

    def ensure_reserved(self, request_id: str, max_cost_usd: float) -> None:
        """Reserve unless an active reservation already exists (resume path).

        A settled entry (reconciled/assumed_spent) is an error here — callers
        must check status_of first and skip budget ops for settled requests."""
        if self.has_reservation(request_id):
            return
        self.reserve(request_id, max_cost_usd)

    # -- mutations (each persists before returning) --------------------------

    def reserve(self, request_id: str, max_cost_usd: float) -> None:
        if max_cost_usd <= 0:
            raise BudgetError(f"{request_id}: reservation must be positive")
        existing = self._entries.get(request_id)
        if existing is not None and existing.status in ("reserved", "reconciled", "assumed_spent"):
            raise BudgetError(
                f"{request_id}: already {existing.status}; refusing duplicate reservation"
            )
        if self.spent_usd + self.reserved_usd + max_cost_usd > self._budget_usd + 1e-9:
            raise BudgetExceededError(
                f"{request_id}: reserving {max_cost_usd:.4f} USD would exceed "
                f"the {self._budget_usd:.4f} USD budget "
                f"(spent {self.spent_usd:.4f}, reserved {self.reserved_usd:.4f})"
            )
        self._entries[request_id] = LedgerEntry(status="reserved", reserved_usd=max_cost_usd)
        self._persist()

    def reconcile(self, request_id: str, actual_cost_usd: float) -> None:
        e = self._require(request_id, expected="reserved")
        if actual_cost_usd < 0:
            raise BudgetError(f"{request_id}: negative cost")
        if actual_cost_usd > e.reserved_usd + 1e-9:
            # Accounted spend is capped at the reservation so the ledger can
            # never claim more than the configured ceiling — but an estimate
            # bug like this must halt the run loudly, not pass silently.
            e.status = "reconciled"
            e.final_usd = e.reserved_usd
            e.note = (
                f"OVERRUN CLAIM: actual {actual_cost_usd:.6f} USD exceeded the "
                f"conservative reservation {e.reserved_usd:.6f} USD"
            )
            self._persist()
            raise BudgetError(
                f"{request_id}: actual cost {actual_cost_usd:.4f} USD exceeded "
                f"its conservative reservation {e.reserved_usd:.4f} USD — the "
                "candidate's max_cost_per_generation_usd is wrong. Halting."
            )
        e.status = "reconciled"
        e.final_usd = actual_cost_usd
        self._persist()

    def release(self, request_id: str) -> None:
        """Release a reservation for a failure that provably happened before
        the provider accepted the request (e.g. local validation error)."""
        e = self._require(request_id, expected="reserved")
        e.status = "released"
        e.final_usd = 0.0
        self._persist()

    def assume_spent(self, request_id: str, note: str = "") -> None:
        """Ambiguous failure: conservatively treat the reservation as spent."""
        e = self._require(request_id, expected="reserved")
        e.status = "assumed_spent"
        e.final_usd = e.reserved_usd
        e.note = note or "ambiguous provider failure; conservatively counted as spent"
        self._persist()

    # -- internals -----------------------------------------------------------

    def _require(self, request_id: str, expected: EntryStatus) -> LedgerEntry:
        e = self._entries.get(request_id)
        if e is None:
            raise BudgetError(f"{request_id}: no ledger entry")
        if e.status != expected:
            raise BudgetError(f"{request_id}: expected status {expected!r}, got {e.status!r}")
        return e

    def _persist(self) -> None:
        payload = {
            "budget_usd": self._budget_usd,
            "entries": {
                rid: {
                    "status": e.status,
                    "reserved_usd": e.reserved_usd,
                    "final_usd": e.final_usd,
                    "note": e.note,
                }
                for rid, e in sorted(self._entries.items())
            },
            "totals": {
                "spent_usd": self.spent_usd,
                "reserved_usd": self.reserved_usd,
                "remaining_usd": self.remaining_usd,
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)
