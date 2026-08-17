"""A guard for a CLASS of defect: prose that still claims the old refinement budget.

ADR 0029 raised ``MAX_REFINEMENTS`` from 1 to 3 and chained the rounds. The code
change was small; the stale CLAIMS about the old number were not, and they took
three separate sweeps to find because each sweep was narrower than the problem —
the last one was a model docstring reading "(initial concept + one refinement)",
which is the model's own statement of its scope.

A budget claim written as prose cannot be type-checked and no import graph
reaches it, so the only thing that catches it is a test that reads the prose.
This is the same reasoning behind the existing ``ast``-based guards in this
package (the ``_note_keyword`` call-site scan, the ``except``-tuple completeness
scan): pin the class, not the instance.

Scoped to ``apps/api`` so it needs nothing outside this Docker build context.

PEER GUARD: ``apps/web/src/app/refinement-budget-copy.test.ts``, because the
surface that matters most — the customer-facing explanation of what a concept
is — lives there. The two pattern lists are deliberately NOT shared. Sharing
them would mean a file readable from both Docker build contexts, which is the
very thing the scoping above avoids, and they are not the same list: this one
carries docstring phrasings ("its one refinement") no marketing page will ever
contain, and the peer carries customer-facing ones ("refine it once", "used
your one") no docstring will. What they must share is the PROPOSITION — a claim
about how many refinements a design gets. Add a new phrasing to whichever side
can actually contain it, and to both when both can.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

_API_ROOT = Path(__file__).resolve().parents[3]
_SCANNED = ("sitara", "config")
_SUFFIXES = (".py", ".md")

# Each pattern is a claim about the SIZE of the budget that was true at
# MAX_REFINEMENTS == 1 and is false now. Deliberately NOT matching "one
# refinement attempt's output" or "a second refinement carries on from the first
# one's output" — those describe a single instance, not the limit.
_STALE_BUDGET_CLAIMS = (
    (re.compile(r"\brefined once\b", re.I), "the budget is MAX_REFINEMENTS, not once"),
    (re.compile(r"\bmay be refined only\b", re.I), "reads as a cap of one"),
    (re.compile(r"\bexactly one refinement\b", re.I), "the budget is plural"),
    (re.compile(r"\bsingle refinement\b", re.I), "the budget is plural"),
    (re.compile(r"\bonly one refinement\b", re.I), "the budget is plural"),
    (re.compile(r"\bthe one refinement\b", re.I), "the budget is plural"),
    (re.compile(r"\bits one refinement\b", re.I), "the budget is plural"),
    (re.compile(r"\+ one refinement\b", re.I), "a design holds one row per refinement"),
    (re.compile(r"\brefined at most once\b", re.I), "the budget is MAX_REFINEMENTS"),
)

# Exact repo-relative paths whose mention is a CITATION of ADR 0015 by its real
# title ("single-round constrained refinement") or this guard's own prose.
# Exact paths, so a new file cannot inherit an exemption by accident.
_ALLOWED = frozenset(
    {
        "sitara/generation/tests/test_refinement_budget_copy.py",
    }
)


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for top in _SCANNED:
        root = _API_ROOT / top
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in _SUFFIXES:
                continue
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            files.append(path)
    return files


def test_the_scan_actually_reaches_the_source_tree():
    """A broken walk must fail loudly rather than pass vacuously.

    This is the failure mode the three manual sweeps had: each one ran, reported
    nothing, and was wrong because its scope was too narrow."""
    files = _scanned_files()
    assert len(files) > 100, f"only {len(files)} files scanned — the walk is broken"
    names = {p.name for p in files}
    # Two files that certainly exist and certainly discuss refinement.
    assert "refinement_service.py" in names
    assert "models.py" in names


def test_no_superseded_claim_about_how_many_refinements_a_design_gets():
    offences: list[str] = []
    for path in _scanned_files():
        rel = path.relative_to(_API_ROOT).as_posix()
        if rel in _ALLOWED:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:  # pragma: no cover - no such file today
            continue
        for number, line in enumerate(lines, start=1):
            for pattern, why in _STALE_BUDGET_CLAIMS:
                if pattern.search(line):
                    offences.append(
                        f"{rel}:{number} — {pattern.pattern} ({why})\n    {line.strip()}"
                    )

    assert not offences, (
        f"Superseded refinement-budget claims found. MAX_REFINEMENTS is "
        f"{settings.MAX_REFINEMENTS} and the rounds chain (ADR 0029), so these read as "
        "promises the product does not keep:\n\n" + "\n\n".join(offences)
    )


@pytest.mark.parametrize(
    "line",
    [
        "A design may be refined once.",
        "the design's single refinement",
        "One generated concept iteration (initial concept + one refinement).",
    ],
)
def test_the_guard_has_teeth(line):
    """The patterns must actually fire on the exact prose that shipped.

    Without this the guard could be silently inert — a green test proving
    nothing, which is precisely what the concepts-page test was."""
    assert any(pattern.search(line) for pattern, _ in _STALE_BUDGET_CLAIMS), line
