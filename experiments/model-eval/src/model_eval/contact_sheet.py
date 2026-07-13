"""HTML contact sheets for blind human review.

Outputs are grouped by brief with candidate models side by side. By default
each model is labelled only with an anonymised candidate code (Candidate A,
B, ...) whose assignment is shuffled deterministically per run, so reviewers
score blind. The code-to-model mapping is written to a SEPARATE file
(candidate_key.json) that reviewers must not open until scoring is done.

The sheet can compare along one axis at a time: models (default),
inspiration modes, prompt formats, or refinement strategies.
"""

from __future__ import annotations

import hashlib
import html
import string
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


def candidate_codes(run_id: str, model_keys: list[str]) -> dict[str, str]:
    """Deterministic per-run anonymised codes.

    Ordering is derived from a hash of (run_id, model_key) so it is stable
    for a run but not alphabetical — reviewers cannot infer models from
    label order."""
    ranked = sorted(model_keys, key=lambda k: hashlib.sha256(f"{run_id}:{k}".encode()).hexdigest())
    letters = string.ascii_uppercase
    return {key: f"Candidate {letters[i % 26]}{i // 26 or ''}" for i, key in enumerate(ranked)}


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


def build_contact_sheet(
    store: ResultStore,
    run_id: str,
    *,
    axis: CompareAxis = "model",
    reveal: bool = False,
) -> tuple[Path, Path]:
    """Render the sheet; returns (sheet_path, mapping_path)."""
    records = [r for r in store.load_all() if r.status == "succeeded" and r.output_path]
    if not records:
        raise ValueError(f"run {run_id!r} has no successful outputs to display")

    model_keys = sorted({r.model_key for r in records})
    codes = candidate_codes(run_id, model_keys)

    by_brief: dict[str, list[ResultRecord]] = {}
    for r in records:
        by_brief.setdefault(r.brief_id, []).append(r)

    rows: list[str] = []
    for brief_id in sorted(by_brief):
        group = sorted(
            by_brief[brief_id],
            key=lambda r: (
                getattr(r, _AXIS_ATTR[axis]),
                r.model_key,
                r.prompt_format,
                r.inspiration_mode,
                r.kind,
                r.seed or 0,
            ),
        )
        cells = []
        for r in group:
            label = _cell_label(r, axis, codes, reveal)
            cells.append(
                "<figure>"
                f"<img src='images/{html.escape(Path(r.output_path or '').name)}' "
                f"alt='{html.escape(r.garment)} output' loading='lazy'>"
                f"<figcaption>{label}<br><code>{html.escape(r.request_id)}</code></figcaption>"
                "</figure>"
            )
        first = group[0]
        meta = f"{first.garment}" + (f" · {first.ceremony}" if first.ceremony else "")
        rows.append(
            f"<section><h2>{html.escape(brief_id)} <small>{html.escape(meta)}</small></h2>"
            f"<div class='row'>{''.join(cells)}</div></section>"
        )

    blind_note = (
        "" if reveal else
        "<p class='note'>Blind review sheet — model identities are anonymised. "
        "Do not open candidate_key.json until scoring is complete.</p>"
    )
    doc = f"""<!doctype html>
<meta charset="utf-8">
<title>Sitara model eval — {html.escape(run_id)} ({axis} comparison)</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
 .row {{ display: flex; flex-wrap: wrap; gap: 1rem; }}
 figure {{ margin: 0; max-width: 320px; }}
 img {{ max-width: 100%; height: auto; border: 1px solid #ccc; }}
 figcaption {{ font-size: 0.85rem; margin-top: .25rem; }}
 .detail {{ color: #666; }}
 code {{ font-size: 0.7rem; color: #999; word-break: break-all; }}
 .note {{ background: #fff6d9; padding: .5rem 1rem; border: 1px solid #e5d692; }}
 h2 small {{ color: #666; font-weight: normal; }}
</style>
<h1>Contact sheet — run {html.escape(run_id)}</h1>
<p>Comparison axis: <strong>{axis}</strong></p>
{blind_note}
{''.join(rows)}
"""
    sheet_path = store.run_dir / f"contact_sheet_{axis}{'_revealed' if reveal else ''}.html"
    sheet_path.write_text(doc, encoding="utf-8")

    mapping_path = store.write_json(
        "candidate_key.json",
        {
            "warning": "Do not open during blind scoring.",
            "codes": {codes[k]: k for k in model_keys},
            "models": {
                k: next(r.replicate_id for r in records if r.model_key == k)
                for k in model_keys
            },
        },
    )
    return sheet_path, mapping_path
