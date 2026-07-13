"""Human scoring templates.

Generates a CSV with one row per successful output, using anonymised
candidate codes so scoring stays blind. Scores are 1-5; hard-failure columns
are yes/no. There is deliberately no automated aesthetic scoring and no LLM
judge: human review is authoritative for this evaluation.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .contact_sheet import candidate_codes
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
]

CONTEXT_FIELDS = [
    "run_id",
    "request_id",
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


def build_scoring_sheet(store: ResultStore, run_id: str) -> Path:
    records = [r for r in store.load_all() if r.status == "succeeded" and r.output_path]
    if not records:
        raise ValueError(f"run {run_id!r} has no successful outputs to score")

    codes = candidate_codes(run_id, sorted({r.model_key for r in records}))

    path = store.run_dir / "scoring_sheet.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            CONTEXT_FIELDS
            + [f"{f} (1-5)" for f in RUBRIC_FIELDS]
            + [f"{f} (yes/no)" for f in HARD_FAILURE_FIELDS]
            + ["reviewer_notes"]
        )
        for r in sorted(records, key=lambda r: r.request_id):
            writer.writerow(
                [
                    r.run_id,
                    r.request_id,
                    r.brief_id,
                    r.garment,
                    r.ceremony or "",
                    codes[r.model_key],
                    r.prompt_format,
                    r.inspiration_mode,
                    r.kind,
                    r.refinement_strategy or "",
                    "" if r.seed is None else r.seed,
                    Path(r.output_path or "").name,
                ]
                + [""] * (len(RUBRIC_FIELDS) + len(HARD_FAILURE_FIELDS))
                + [""]
            )
    return path
