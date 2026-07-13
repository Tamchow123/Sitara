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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# Retry policy for atomic replacement. On Windows, os.replace can fail
# transiently with access-denied / sharing-violation errors while antivirus,
# indexing or backup tooling briefly holds the destination file open.
_REPLACE_RETRIES = 5
_REPLACE_BASE_DELAY_S = 0.05
_WINDOWS_TRANSIENT_WINERRORS = {5, 32, 33}  # access denied / sharing violations


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


class BudgetPersistenceError(BudgetError):
    """The ledger could not be durably replaced on disk.

    The complete intended state is preserved in the ledger's .tmp file; the
    exception message says exactly where it is and how recovery works (it is
    validated and promoted automatically on the next ledger open)."""


def _is_transient_replace_error(exc: OSError) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) in _WINDOWS_TRANSIENT_WINERRORS


def _replace_with_retries(src: Path, dst: Path) -> None:
    """os.replace with bounded exponential backoff for transient Windows
    access-denied/sharing-violation failures. Preserves ``src`` on failure."""
    last_error: OSError | None = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if not _is_transient_replace_error(exc):
                raise
            last_error = exc
            time.sleep(_REPLACE_BASE_DELAY_S * (2**attempt))
    # All retries failed. The source file holds the complete intended state —
    # deliberately NOT deleted.
    raise BudgetPersistenceError(
        f"could not replace {dst} after {_REPLACE_RETRIES} attempts "
        f"({last_error}). The complete intended ledger state is preserved at "
        f"{src}; it will be validated and recovered automatically the next "
        "time this run's ledger is opened (same run id, same budget). Do not "
        "delete the .tmp file."
    )


def atomic_write_json(path: Path, payload: Any) -> None:
    """Durably replace ``path`` with ``payload`` as JSON.

    Writes the complete document to ``<path>.tmp``, flushes and fsyncs it,
    then replaces the destination with bounded retries (see
    _replace_with_retries). If every retry fails, the VALID .tmp file is
    preserved and a BudgetPersistenceError explains how it is recovered."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:  # pragma: no cover - fsync unsupported on exotic fs
            pass
    _replace_with_retries(tmp, path)


_STATUS_RANK = {"reserved": 1, "released": 2, "reconciled": 2, "assumed_spent": 2}


def _is_forward_progress(old: Any, new: Any) -> bool:
    """True when ``new`` is a strict forward progression of ``old``: every
    old entry is present, no settled (terminal) entry changed, at least one
    reserved entry advanced or a new entry appeared."""
    old_entries = old.get("entries", {}) if isinstance(old, dict) else {}
    new_entries = new.get("entries", {}) if isinstance(new, dict) else {}
    progressed = False
    for rid, old_entry in old_entries.items():
        new_entry = new_entries.get(rid)
        if new_entry is None:
            return False  # entries must never disappear
        old_status = old_entry.get("status")
        new_status = new_entry.get("status")
        if old_status == new_status:
            if old_entry != new_entry and old_status != "reserved":
                return False  # settled entries must not mutate
            continue
        if old_status != "reserved":
            return False  # terminal-to-terminal flips are not progressions
        if _STATUS_RANK.get(new_status, 0) <= _STATUS_RANK.get(old_status, 0):
            return False
        progressed = True
    if len(new_entries) > len(old_entries):
        progressed = True
    return progressed


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
        ledger = cls(path, budget_usd, {})
        ledger._acquire_lock()
        try:
            # A crashed run may have left a complete, newer ledger state in
            # the .tmp file (the os.replace itself failed). Recover it before
            # reading, so reservations released/reconciled in the intended
            # state are not resurrected — no double-spend, no stuck reserve.
            ledger._recover_tmp_if_needed()
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
                    ledger._entries[rid] = LedgerEntry(
                        status=e["status"],
                        reserved_usd=float(e["reserved_usd"]),
                        final_usd=None if e["final_usd"] is None else float(e["final_usd"]),
                        note=e.get("note", ""),
                    )
            else:
                ledger._persist()
        except BaseException:
            ledger.close()
            raise
        return ledger

    # -- crash recovery -------------------------------------------------------

    def _tmp_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".tmp")

    def _recover_tmp_if_needed(self) -> None:
        """Validate and, when appropriate, promote a leftover .tmp file.

        Called only while holding the exclusive run lock. Promotion happens
        ONLY when the .tmp contains complete, valid JSON for the same budget
        that is a strict forward progression of the current main state (or
        the main file is missing/corrupt). Anything malformed is quarantined
        with a clear report — never promoted, never silently discarded."""
        tmp = self._tmp_path()
        if not tmp.exists():
            return
        try:
            candidate = json.loads(tmp.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            self._quarantine_tmp(tmp, f"not valid JSON ({exc})")
            return
        if (
            not isinstance(candidate, dict)
            or not isinstance(candidate.get("entries"), dict)
            or "budget_usd" not in candidate
        ):
            self._quarantine_tmp(tmp, "unexpected document shape")
            return
        try:
            tmp_budget = float(candidate["budget_usd"])
        except (TypeError, ValueError):
            self._quarantine_tmp(tmp, "non-numeric budget")
            return
        if abs(tmp_budget - self._budget_usd) > 1e-9:
            self._quarantine_tmp(
                tmp, f"budget {tmp_budget} does not match this run's {self._budget_usd}"
            )
            return

        current: dict[str, Any] | None = None
        if self._path.exists():
            try:
                current = json.loads(self._path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                current = None  # main corrupt; the valid tmp is authoritative

        if current is None:
            self._promote_tmp(tmp)
            return
        if candidate == current:
            tmp.unlink(missing_ok=True)  # stale duplicate of promoted state
            return
        if _is_forward_progress(current, candidate):
            self._promote_tmp(tmp)
        else:
            self._quarantine_tmp(
                tmp, "state is not a forward progression of the main ledger"
            )

    def _promote_tmp(self, tmp: Path) -> None:
        _replace_with_retries(tmp, self._path)
        print(
            f"[budget] recovered ledger state from {tmp.name} left by an "
            "interrupted run",
            file=sys.stderr,
        )

    @staticmethod
    def _quarantine_tmp(tmp: Path, reason: str) -> None:
        quarantined = tmp.with_suffix(tmp.suffix + ".quarantined")
        n = 1
        while quarantined.exists():
            quarantined = tmp.with_suffix(tmp.suffix + f".quarantined{n}")
            n += 1
        os.replace(tmp, quarantined)
        print(
            f"[budget] quarantined invalid ledger temp file to "
            f"{quarantined.name}: {reason}. Review it manually; it was NOT "
            "promoted into the ledger.",
            file=sys.stderr,
        )

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
        atomic_write_json(self._path, payload)
