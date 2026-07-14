"""Genuinely blind contact sheets for human review.

Blindness guarantee: the blind sheet (and everything it references) contains
NO model keys, NO Replicate IDs, NO original request IDs and NO original
output filenames — not in captions, not in image paths, not anywhere in the
HTML source. Each successful output is copied into ``blind/`` under a
deterministic anonymised filename (``image-001.webp``), captions use an
anonymised ``item-NNN`` id plus a Candidate A/B/... code, and the ordering of
both codes and item numbers is derived from per-run hashes so it cannot be
mapped back to alphabetical model order.

The sensitive reverse mapping (candidate code -> model, item id ->
request/output) lives ONLY in candidate_key.json, which warns reviewers not
to open it until scoring is complete. A revealed (non-blind) sheet can be
generated explicitly with reveal=True for use after scoring.

Sheets for incomplete runs are only produced behind an explicit
--allow-partial flag and carry a prominent PARTIAL / NOT VALID FOR MODEL
SELECTION banner (enforced by the CLI via assess_run_completeness).
"""

from __future__ import annotations

import hashlib
import html
import shutil
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .result_store import ResultRecord, ResultStore

CompareAxis = Literal["model", "mode", "format", "refinement"]

_AXIS_ATTR: dict[CompareAxis, str] = {
    "model": "model_key",
    "mode": "inspiration_mode",
    "format": "prompt_format",
    "refinement": "kind",
}

PARTIAL_BANNER_TEXT = "PARTIAL / NOT VALID FOR MODEL SELECTION"


def candidate_codes(run_id: str, model_keys: list[str]) -> dict[str, str]:
    """Deterministic per-run anonymised codes.

    Ordering is derived from a hash of (run_id, model_key) so it is stable
    for a run but not alphabetical — reviewers cannot infer models from
    label order."""
    ranked = sorted(model_keys, key=lambda k: hashlib.sha256(f"{run_id}:{k}".encode()).hexdigest())
    letters = string.ascii_uppercase
    return {key: f"Candidate {letters[i % 26]}{i // 26 or ''}" for i, key in enumerate(ranked)}


@dataclass(frozen=True)
class BlindItem:
    record: ResultRecord
    blind_id: str          # e.g. "item-003" — safe to show reviewers
    image_filename: str    # e.g. "image-003.webp" — safe to show reviewers


def prepare_blind_items(
    store: ResultStore,
    run_id: str,
    scope: dict | None = None,
) -> tuple[list[BlindItem], Path]:
    """Copy the selected output of every LOGICAL evaluation cell into the
    blind directory under anonymised names and return the items
    (hash-ordered, so numbering leaks nothing) plus that directory. Exactly
    one image per logical cell: the original success, or — where the first
    attempt failed — its earliest successful targeted retry. Whether an
    image came from a retry is never exposed in blind artefacts (it could
    bias visual scoring); the lineage lives only in the protected mapping.

    With a validated review ``scope``, only the scope's included models'
    selected cells are used: artefacts land in ``blind-scoped/`` with their
    own protected mapping (``candidate_key_scoped.json``) that names ONLY
    included models — an excluded model appears nowhere in scoped
    artefacts, not even in the mapping."""
    if scope is not None:
        from .review_scope import scoped_logical_outputs

        records = scoped_logical_outputs(store, scope)
    else:
        from .retry import select_logical_outputs

        records = select_logical_outputs(store)
    if not records:
        raise ValueError(f"run {run_id!r} has no successful outputs")
    ordered = sorted(
        records,
        key=lambda r: hashlib.sha256(f"{run_id}:{r.request_id}".encode()).hexdigest(),
    )
    blind_dir = store.run_dir / ("blind-scoped" if scope else "blind")
    blind_dir.mkdir(parents=True, exist_ok=True)
    items: list[BlindItem] = []
    for i, record in enumerate(ordered, start=1):
        extension = Path(record.output_path or "").suffix or ".png"
        filename = f"image-{i:03d}{extension}"
        source = store.run_dir / (record.output_path or "")
        dest = blind_dir / filename
        if not dest.exists() and source.is_file():
            shutil.copy2(source, dest)
        items.append(BlindItem(record, f"item-{i:03d}", filename))

    model_keys = sorted({r.model_key for r in records})
    codes = candidate_codes(run_id, model_keys)
    store.write_json(
        "candidate_key_scoped.json" if scope else "candidate_key.json",
        {
            "warning": (
                "PROTECTED MAPPING — do not open during blind scoring. "
                "This file de-anonymises candidate codes and blind item ids."
            ),
            "codes": {codes[k]: k for k in model_keys},
            "models": {
                k: next(r.replicate_id for r in records if r.model_key == k)
                for k in model_keys
            },
            "items": {
                item.blind_id: {
                    "request_id": item.record.request_id,
                    "model_key": item.record.model_key,
                    "replicate_id": item.record.replicate_id,
                    "original_output": item.record.output_path,
                    "blind_image": item.image_filename,
                    # Retry lineage (protected — never in blind artefacts):
                    "is_retry_recovery": item.record.retry_of_request_id is not None,
                    "retry_of_request_id": item.record.retry_of_request_id,
                    "attempt_index": item.record.attempt_index,
                }
                for item in items
            },
        },
    )
    return items, blind_dir


def _cell_label(record: ResultRecord, axis: CompareAxis, codes: dict[str, str], reveal: bool) -> str:
    if axis == "model":
        base = record.replicate_id if reveal else codes[record.model_key]
    else:
        base = getattr(record, _AXIS_ATTR[axis])
    detail = f"seed {record.seed}" if record.seed is not None else ""
    if axis != "mode" and record.inspiration_mode != "text_only":
        detail += f" · {record.inspiration_mode}"
    if axis != "refinement" and record.kind != "base":
        detail += f" · {record.kind}"
    return f"{base}" + (f" <span class='detail'>({detail.strip(' ·')})</span>" if detail else "")


def _banner(partial: bool) -> str:
    if not partial:
        return ""
    return (
        f"<div class='partial-banner'>{PARTIAL_BANNER_TEXT} — this run is "
        "incomplete (see run_summary/plan); use for debugging only.</div>"
    )


_STYLE = """
 body { font-family: system-ui, sans-serif; margin: 2rem; }
 .row { display: flex; flex-wrap: wrap; gap: 1rem; }
 figure { margin: 0; max-width: 320px; }
 img { max-width: 100%; height: auto; border: 1px solid #ccc; }
 figcaption { font-size: 0.85rem; margin-top: .25rem; }
 .detail { color: #666; }
 .item { font-size: 0.75rem; color: #888; }
 .note { background: #fff6d9; padding: .5rem 1rem; border: 1px solid #e5d692; }
 .partial-banner { background: #b00020; color: #fff; font-weight: bold;
   font-size: 1.2rem; padding: .75rem 1rem; margin: 1rem 0; }
 h2 small { color: #666; font-weight: normal; }
"""


def build_contact_sheet(
    store: ResultStore,
    run_id: str,
    *,
    axis: CompareAxis = "model",
    reveal: bool = False,
    partial: bool = False,
    scope: dict | None = None,
) -> tuple[Path, Path]:
    """Render the sheet; returns (sheet_path, mapping_path).

    Blind (default): written into the blind directory referencing only
    anonymised filenames and ids. Revealed: written at the run root with
    real model identities, for use after scoring only. With a validated
    review scope, only included models' cells are rendered."""
    items, blind_dir = prepare_blind_items(store, run_id, scope=scope)
    mapping_path = store.run_dir / (
        "candidate_key_scoped.json" if scope else "candidate_key.json"
    )
    codes = candidate_codes(run_id, sorted({i.record.model_key for i in items}))

    by_brief: dict[str, list[BlindItem]] = {}
    for item in items:
        by_brief.setdefault(item.record.brief_id, []).append(item)

    rows: list[str] = []
    for brief_id in sorted(by_brief):
        group = sorted(
            by_brief[brief_id],
            key=lambda i: (
                _cell_label(i.record, axis, codes, reveal=False),
                i.blind_id,
            ),
        )
        cells = []
        for item in group:
            record = item.record
            label = _cell_label(record, axis, codes, reveal)
            if reveal:
                src = f"images/{html.escape(Path(record.output_path or '').name)}"
                sub = f"<code>{html.escape(record.request_id)}</code>"
            else:
                src = html.escape(item.image_filename)
                sub = f"<span class='item'>{html.escape(item.blind_id)}</span>"
            cells.append(
                "<figure>"
                f"<img src='{src}' alt='{html.escape(record.garment)} output' loading='lazy'>"
                f"<figcaption>{label}<br>{sub}</figcaption>"
                "</figure>"
            )
        first = group[0].record
        meta = f"{first.garment}" + (f" · {first.ceremony}" if first.ceremony else "")
        rows.append(
            f"<section><h2>{html.escape(brief_id)} <small>{html.escape(meta)}</small></h2>"
            f"<div class='row'>{''.join(cells)}</div></section>"
        )

    blind_note = (
        "" if reveal else
        "<p class='note'>Blind review sheet — model identities and request "
        "provenance are anonymised. Do not open candidate_key.json until "
        "scoring is complete.</p>"
    )
    title = f"Sitara model eval — {html.escape(run_id)} ({axis} comparison)"
    doc = f"""<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>{_STYLE}</style>
{_banner(partial)}
<h1>Contact sheet — run {html.escape(run_id)}</h1>
<p>Comparison axis: <strong>{axis}</strong></p>
{blind_note}
{''.join(rows)}
{_banner(partial)}
"""
    if reveal:
        suffix = "_scoped" if scope else ""
        sheet_path = store.run_dir / f"contact_sheet_{axis}_revealed{suffix}.html"
    else:
        sheet_path = blind_dir / f"contact_sheet_{axis}.html"
    sheet_path.write_text(doc, encoding="utf-8")
    return sheet_path, mapping_path
