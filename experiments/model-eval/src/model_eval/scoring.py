"""Blind human scoring templates.

The CSV identifies each output only by its anonymised ``blind_item_id`` and
anonymised image filename (see contact_sheet.prepare_blind_items) plus the
Candidate A/B/... code — never by request id, model key, Replicate ID or
original filename. The reverse mapping lives only in the protected
candidate_key.json.

Scores are 1-5; hard-failure columns are yes/no. There is deliberately no
automated aesthetic scoring and no LLM judge: human review is authoritative.

Note the bridal-distinctiveness dimension: a structurally coherent,
beautiful outfit that reads as ordinary formalwear rather than bridalwear is
NOT sufficient for Sitara — it scores low on
``bridal_occasion_distinctiveness`` and, in the extreme, fails
``hf_reads_as_non_bridal_everydaywear``.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .contact_sheet import PARTIAL_BANNER_TEXT, prepare_blind_items
from .result_store import ResultStore

RUBRIC_FIELDS = [
    "garment_accuracy",
    "cultural_coherence",
    "fabric_realism",
    "embroidery_quality",
    "dupatta_styling",
    "anatomy",
    "prompt_adherence",
    "modesty_coverage_adherence",
    "bridal_occasion_distinctiveness",
    "reference_image_influence",
    "refinement_consistency",
    "overall_visual_quality",
]

HARD_FAILURE_FIELDS = [
    "hf_gharara_sharara_confused",
    "hf_saree_misrepresented",
    "hf_tradition_conflated",
    "hf_sleeve_neckline_coverage_ignored",
    "hf_dupatta_count_or_placement_wrong",
    "hf_unwanted_sexualisation",
    "hf_implausible_garment_construction",
    "hf_text_logos_or_designer_marks",
    "hf_reference_copied_too_literally",
    "hf_major_unrelated_refinement_drift",
    "hf_reads_as_non_bridal_everydaywear",
]

# Blind context only: no request ids, no model keys, no original filenames.
CONTEXT_FIELDS = [
    "run_id",
    "blind_item_id",
    "brief_id",
    "garment",
    "ceremony",
    "candidate_code",
    "prompt_format",
    "inspiration_mode",
    "kind",
    "refinement_strategy",
    "seed",
    "image_file",
]


def build_scoring_sheet(store: ResultStore, run_id: str, *, partial: bool = False) -> Path:
    from .contact_sheet import candidate_codes

    items, blind_dir = prepare_blind_items(store, run_id)
    codes = candidate_codes(run_id, sorted({i.record.model_key for i in items}))

    path = blind_dir / "scoring_sheet.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if partial:
            writer.writerow(
                [f"{PARTIAL_BANNER_TEXT} — incomplete run; debugging only"]
            )
        writer.writerow(
            CONTEXT_FIELDS
            + [f"{f} (1-5)" for f in RUBRIC_FIELDS]
            + [f"{f} (yes/no)" for f in HARD_FAILURE_FIELDS]
            + ["reviewer_notes"]
        )
        for item in sorted(items, key=lambda i: i.blind_id):
            record = item.record
            writer.writerow(
                [
                    record.run_id,
                    item.blind_id,
                    record.brief_id,
                    record.garment,
                    record.ceremony or "",
                    codes[record.model_key],
                    record.prompt_format,
                    record.inspiration_mode,
                    record.kind,
                    record.refinement_strategy or "",
                    "" if record.seed is None else record.seed,
                    item.image_filename,
                ]
                + [""] * (len(RUBRIC_FIELDS) + len(HARD_FAILURE_FIELDS))
                + [""]
            )
    return path
