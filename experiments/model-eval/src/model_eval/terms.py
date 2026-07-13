"""Render the versioned provider-terms snapshot from the candidates config.

The snapshot records only what official sources said on the verification
date, distinguishes model licences from platform terms, and flags unresolved
items for human review. It never draws legal conclusions. Terms are
time-sensitive: re-verify official pages immediately before a live run and
before the Phase 2 decision record is accepted.
"""

from __future__ import annotations

from pathlib import Path

from .config import CandidatesConfig


def render_terms_snapshot(config: CandidatesConfig) -> str:
    lines: list[str] = [
        "# Provider terms snapshot — Sitara model evaluation",
        "",
        "> Facts recorded from official provider sources on the dates shown.",
        "> This document makes **no legal conclusions**. Items listed as",
        "> unresolved require human review. Terms and pricing are",
        "> time-sensitive — re-verify official pages immediately before any",
        "> live run or production decision.",
        "",
        "## Replicate platform terms",
        "",
        f"- Summary: {config.platform_terms.summary}",
        f"- Commercial use: {config.platform_terms.commercial_use}",
        f"- Input retention: {config.platform_terms.input_retention}",
        f"- Training on customer data: {config.platform_terms.training_use}",
        f"- Verified on: {config.platform_terms.verified_on}",
        "- Sources:",
    ]
    lines += [f"  - {s}" for s in config.platform_terms.sources]
    if config.platform_terms.unresolved:
        lines.append("- **Unresolved (human review required):**")
        lines += [f"  - {u}" for u in config.platform_terms.unresolved]
    lines.append("")

    for c in config.candidates:
        lines += [
            f"## {c.name} (`{c.replicate_id}`)",
            "",
            f"- Model licence: {c.terms.model_licence}",
            f"- Commercial use: {c.terms.commercial_use}",
            f"- Input retention: {c.terms.input_retention}",
            f"- Output ownership: {c.terms.output_ownership}",
            f"- Training on submitted data: {c.terms.training_use}",
            f"- Pricing checked on: {c.pricing.checked_on} "
            f"({c.pricing.source_url})",
            f"- Terms verified on: {c.terms.verified_on}",
            "- Sources:",
        ]
        lines += [f"  - {s}" for s in c.terms.sources]
        if c.terms.unresolved:
            lines.append("- **Unresolved (human review required):**")
            lines += [f"  - {u}" for u in c.terms.unresolved]
        lines.append("")

    if config.requires_manual_verification:
        lines.insert(
            2,
            "> **WARNING: this snapshot was generated from PLACEHOLDER data "
            "that has not been verified against live provider pages.**\n",
        )
    return "\n".join(lines)


def write_terms_snapshot(config: CandidatesConfig, dest: Path) -> Path:
    dest.write_text(render_terms_snapshot(config), encoding="utf-8")
    return dest
