"""Run the prompt-fidelity evaluation's live rendering matrix
(docs/phases/prompt-fidelity-evaluation-plan.md): one live render each for the
plan's 20-design matrix, builder frozen at whatever PROMPT_BUILDER_VERSION is
currently checked out. Phase 1 ran the whole matrix at 8.2.0; Phase 4 re-renders
only specific rows at 8.3.0 via ``--rows``.

    python manage.py run_fidelity_eval --dry-run
    python manage.py run_fidelity_eval --i-understand-this-spends-real-money
    python manage.py run_fidelity_eval --i-understand-this-spends-real-money --limit 1
    python manage.py run_fidelity_eval --i-understand-this-spends-real-money --start-at 5
    python manage.py run_fidelity_eval --i-understand-this-spends-real-money --rows 3,5,7,8,16,17,19

``--dry-run`` builds and validates the SAME rows ``--rows``/``--start-at``/
``--limit`` would select (all 20 by default) to completion (update_design_draft
+ design_completion_errors) WITHOUT enqueuing or running generation — zero
spend, no live gates required. Use it first, on the same row selection you are
about to run live, to catch any mistake in this file's own answer construction
before spending anything.

``--rows`` names an explicit, non-contiguous set of 1-based row numbers (e.g.
Phase 4's re-render list) and is mutually exclusive with ``--start-at``/
``--limit``, which only support a contiguous slice.

Each row otherwise runs through the REAL service path: update_design_draft
(the same validator the public draft API uses) -> design_completion_errors
-> enqueue_design_generation(require_availability=True, the public default)
-> run_generation_attempt with NO structured_provider/image_provider override,
so it resolves and spends against the real Anthropic and Replicate providers
exactly as the Celery task (generate_design_attempt) does. Designs are built
directly through the ORM under a script-owned anonymous DesignSession, so this
never passes through HTTP session/IP admission limits — the budget ledger and
the global daily count ceiling still apply in full, unchanged.

Sequential, one design at a time (the plan's own stopping-rule design). Stops
the WHOLE batch immediately, before any further spend, on:

- a completeness/validation failure on any row (a bug in this file's answers,
  never an expected outcome — every row is meant to validate cleanly)
- GenerationUnavailable / CountLimitReached / QueueUnavailable from enqueue
  (a gate is not actually open, or the count ceiling is already exhausted)
- an attempt whose error_code is LIVE_GENERATION_BUDGET_EXHAUSTED
- the configured daily ceiling minus the ledger's live UTC-day running total
  (ALL usage today, not just this run) coming within $1.00 — see
  _remaining_budget_estimate; this is a preflight signal that can lag the
  authoritative per-reservation check, never the hard boundary itself
- --limit attempts started (default: all remaining rows)
- CONSECUTIVE_FAILURE_LIMIT non-succeeded attempts in a row (default: 2) —
  a real per-design provider failure has no reason to repeat identically on
  the next, unrelated design, so a repeat is treated as a systemic problem,
  never per-design noise

A single ISOLATED provider-side FAILED attempt is recorded and the batch
CONTINUES to the next row — that is a legitimate Phase 1 finding, not a
harness bug. Repeated identical failures are not: see
CONSECUTIVE_FAILURE_LIMIT above.

Prints only safe provenance per row (design/version id, status, error code,
image dimensions, running cost total) and a final JSON manifest to stdout for
Phase 2 to resolve back to signed image URLs through the normal
ownership-checked endpoint — never a prompt, answer, storage key, hash or
signed URL."""

import json
import uuid as uuid_module

from django.core.management.base import BaseCommand, CommandError

from sitara.designs.models import Design, DesignSession, DesignVersion, GenerationAttempt
from sitara.designs.services import DraftUpdateError, design_completion_errors, update_design_draft
from sitara.generation import cost_control
from sitara.generation.errors import LIVE_GENERATION_BUDGET_EXHAUSTED
from sitara.generation.pipeline import (
    DesignAlreadyGenerated,
    DesignIncomplete,
    DesignNotGeneratable,
    GenerationInProgress,
    GenerationUnavailable,
    QueueUnavailable,
    build_pipeline_config,
    enqueue_design_generation,
    run_generation_attempt,
)
from sitara.questionnaire.answer_validation import QuestionnaireAnswerError
from sitara.questionnaire.models import QuestionnaireVersion

REQUIRED_QUESTIONNAIRE_VERSION = 4

# Hard cap from the plan: 50 images across the WHOLE study (Phase 1 + 4 + 5),
# not just this command. This command only ever runs Phase 1 (<=20), so the
# plan's cap is enforced here as a per-invocation ceiling; it is not a durable
# cross-run counter.
PLAN_HARD_IMAGE_CAP = 50
BUDGET_FLOOR_MICRO_USD = 1_000_000

# Two identical-looking failures in a row is a systemic-problem signal, not
# per-design stochastic noise (a real provider hiccup on one design has no
# reason to repeat identically on the next, unrelated one). Added after a
# 2026-08-02 run blindly burned through 14 consecutive
# structured_generation_failed rows before anyone noticed.
CONSECUTIVE_FAILURE_LIMIT = 2

# Matrix colour names are schema colour_choice option values from
# questionnaire/fixtures/build_v4.py's COLOUR_GROUPS. Two matrix shorthands
# are NOT literal schema ids and are mapped to the real option: "sweetheart"
# -> "sweetheart_neck", "gold" -> "antique_gold" (there is no plain "gold").
# Two matrix embellishment words are not schema options either -- "tilla" and
# "zari" are both real metallic-thread techniques with no matching option, so
# they map to the closest schema value: "tilla" (coiled wire) -> "dabka"
# (also coiled wire); "zari" (metallic-thread embroidery) -> "zardozi" (zari
# embroidery by definition). Neither substitution changes a rubric-scored
# dimension: embellishment STYLE is not graded, only presence and density.
#
# Each row is (garment, ceremony, answers). answers follows the real
# colour_choice/colour_list/single_choice/multi_choice/text shapes validated
# by questionnaire.answer_validation.validate_questionnaire_answers.
MATRIX = [
    (
        "saree",
        "nikah",
        {
            "garment_type": "saree",
            "ceremony": "nikah",
            "regional_style": "no_specific_direction",
            "silhouette": "classic_saree_drape",
            "fabric_colour": "pistachio",
            "fabrics": ["raw_silk"],
            "embellishment_styles": ["none"],
            "neckline_style": "high_neck",
            "sleeves": "full_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "hijab",
            "saree_drape": "nivi_drape",
        },
    ),
    (
        "lehenga",
        "walima",
        {
            "garment_type": "lehenga",
            "ceremony": "walima",
            "regional_style": "no_specific_direction",
            "silhouette": "a_line_lehenga",
            "fabric_colour": "scarlet",
            "embroidery_colour": "antique_gold",
            "fabrics": ["velvet"],
            "embellishment_styles": ["zardozi"],
            "embellishment_density": "heavy",
            "neckline_style": "sweetheart_neck",
            "sleeves": "cap_sleeve",
            "back_coverage": "open_back",
            "midriff": "bare_midriff",
            "head_covering": "uncovered",
            "dupatta_style": "front_drape",
        },
    ),
    (
        "gharara",
        "mehndi",
        {
            "garment_type": "gharara",
            "ceremony": "mehndi",
            "regional_style": "no_specific_direction",
            "silhouette": "classic_gharara",
            "fabric_colour": "#7b1f2b",
            "custom_colours": ["#7b1f2b"],
            "embroidery_colour": "ivory",
            "fabrics": ["georgette"],
            "embellishment_styles": ["dabka"],
            "embellishment_density": "minimal",
            "neckline_style": "band_collar",
            "sleeves": "full_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "veil_style",
            "dupatta_style": "double_dupatta",
        },
    ),
    (
        "sharara",
        "baraat",
        {
            "garment_type": "sharara",
            "ceremony": "baraat",
            "regional_style": "no_specific_direction",
            "silhouette": "classic_sharara",
            "fabric_colour": "deep_maroon",
            "embroidery_colour": "antique_gold",
            "fabrics": ["silk"],
            "embellishment_styles": ["resham_threadwork"],
            "embellishment_density": "balanced",
            "neckline_style": "v_neck",
            "sleeves": "three_quarter_sleeve",
            "back_coverage": "modest_back",
            "midriff": "semi_sheer_midriff",
            "head_covering": "dupatta_over_head",
            "dupatta_style": "head_drape",
        },
    ),
    (
        "anarkali",
        "reception",
        {
            "garment_type": "anarkali",
            "ceremony": "reception",
            "regional_style": "no_specific_direction",
            "silhouette": "floor_length_anarkali",
            "fabric_colour": "powder_blue",
            "fabrics": ["chiffon"],
            "embellishment_styles": ["none"],
            "neckline_style": "boat_neck",
            "sleeves": "elbow_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "uncovered",
            "dupatta_style": "one_shoulder",
        },
    ),
    (
        "shalwar_kameez",
        "nikah",
        {
            "garment_type": "shalwar_kameez",
            "ceremony": "nikah",
            "regional_style": "no_specific_direction",
            "silhouette": "straight_kameez",
            "fabric_colour": "sage",
            "fabrics": ["cotton_silk"],
            "embellishment_styles": ["dabka"],
            "embellishment_density": "minimal",
            "neckline_style": "classic_crew",
            "sleeves": "full_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "hijab",
            "dupatta_style": "front_drape",
        },
    ),
    (
        "saree",
        "pheras",
        {
            "garment_type": "saree",
            "ceremony": "pheras",
            "regional_style": "no_specific_direction",
            "silhouette": "lehenga_style_saree",
            "fabric_colour": "peacock",
            "embroidery_colour": "silver_grey",
            "fabrics": ["net"],
            "embellishment_styles": ["zardozi"],
            "embellishment_density": "balanced",
            "neckline_style": "deep_v_neck",
            "sleeves": "sleeveless",
            "back_coverage": "deep_cut_back",
            "midriff": "bare_midriff",
            "head_covering": "uncovered",
            "saree_drape": "bengali_drape",
        },
    ),
    (
        "lehenga",
        "anand_karaj",
        {
            "garment_type": "lehenga",
            "ceremony": "anand_karaj",
            "regional_style": "punjabi",
            "silhouette": "flared_lehenga",
            "fabric_colour": "mint",
            "embroidery_colour": "pearl",
            "fabrics": ["satin"],
            "embellishment_styles": ["chikankari"],
            "embellishment_density": "minimal",
            "neckline_style": "square_neck",
            "sleeves": "three_quarter_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "dupatta_over_head",
            "dupatta_style": "double_dupatta",
        },
    ),
    (
        "gharara",
        "walima",
        {
            "garment_type": "gharara",
            "ceremony": "walima",
            "regional_style": "no_specific_direction",
            "silhouette": "farshi_gharara",
            "fabric_colour": "oxblood",
            "embroidery_colour": "antique_gold",
            "fabrics": ["brocade"],
            "embellishment_styles": ["zardozi"],
            "embellishment_density": "heavy",
            "neckline_style": "high_neck",
            "sleeves": "full_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "hijab",
            "dupatta_style": "front_drape",
        },
    ),
    (
        "anarkali",
        "mehndi",
        {
            "garment_type": "anarkali",
            "ceremony": "mehndi",
            "regional_style": "no_specific_direction",
            "silhouette": "kalidar_anarkali",
            "fabric_colour": "marigold",
            "fabrics": ["organza"],
            "embellishment_styles": ["gota_patti"],
            "embellishment_density": "balanced",
            "neckline_style": "curved_scoop",
            "sleeves": "cap_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "uncovered",
            "dupatta_style": "trail_dupatta",
        },
    ),
    (
        "saree",
        "reception",
        {
            "garment_type": "saree",
            "ceremony": "reception",
            "regional_style": "no_specific_direction",
            "silhouette": "pre_stitched_saree",
            "fabric_colour": "ivory",
            "embroidery_colour": "champagne",
            "fabrics": ["tissue"],
            "embellishment_styles": ["none"],
            "neckline_style": "boat_neck",
            "sleeves": "sleeveless",
            "back_coverage": "open_back",
            "midriff": "bare_midriff",
            "head_covering": "uncovered",
            "saree_drape": "seedha_pallu",
        },
    ),
    (
        "sharara",
        "nikah",
        {
            "garment_type": "sharara",
            "ceremony": "nikah",
            "regional_style": "no_specific_direction",
            "silhouette": "high_waisted_sharara",
            "fabric_colour": "navy",
            "embroidery_colour": "silver_grey",
            "fabrics": ["jamawar"],
            "embellishment_styles": ["dabka"],
            "embellishment_density": "minimal",
            "neckline_style": "band_collar",
            "sleeves": "full_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "veil_style",
            "dupatta_style": "double_dupatta",
        },
    ),
    (
        "lehenga",
        "baraat",
        {
            "garment_type": "lehenga",
            "ceremony": "baraat",
            "regional_style": "no_specific_direction",
            "silhouette": "mermaid_lehenga",
            "fabric_colour": "rani_pink",
            "dupatta_colour": "match_fabric",
            "fabrics": ["velvet"],
            "embellishment_styles": ["zardozi"],
            "embellishment_density": "heavy",
            "neckline_style": "sweetheart_neck",
            "sleeves": "sleeveless",
            "back_coverage": "deep_cut_back",
            "midriff": "bare_midriff",
            "head_covering": "uncovered",
            "dupatta_style": "one_shoulder",
        },
    ),
    (
        "shalwar_kameez",
        "walima",
        {
            "garment_type": "shalwar_kameez",
            "ceremony": "walima",
            "regional_style": "no_specific_direction",
            "silhouette": "a_line_kameez",
            "fabric_colour": "lilac",
            "fabrics": ["silk"],
            "embellishment_styles": ["none"],
            "neckline_style": "classic_crew",
            "sleeves": "three_quarter_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "dupatta_over_head",
            "dupatta_style": "head_drape",
        },
    ),
    (
        "saree",
        "mehndi",
        {
            "garment_type": "saree",
            "ceremony": "mehndi",
            "regional_style": "no_specific_direction",
            "silhouette": "half_saree",
            "fabric_colour": "mehndi_green",
            "embroidery_colour": "marigold",
            "fabrics": ["georgette"],
            "embellishment_styles": ["mirror_work"],
            "embellishment_density": "balanced",
            "neckline_style": "v_neck",
            "sleeves": "elbow_sleeve",
            "back_coverage": "modest_back",
            "midriff": "bare_midriff",
            "head_covering": "uncovered",
            "saree_drape": "nivi_drape",
        },
    ),
    (
        "gharara",
        "reception",
        {
            "garment_type": "gharara",
            "ceremony": "reception",
            "regional_style": "no_specific_direction",
            "silhouette": "slim_modern_gharara",
            "fabric_colour": "plum_wine",
            "embroidery_colour": "antique_gold",
            "fabrics": ["raw_silk"],
            "embellishment_styles": ["gota_patti"],
            "embellishment_density": "balanced",
            "neckline_style": "square_neck",
            "sleeves": "full_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "hijab",
            "dupatta_style": "trail_dupatta",
        },
    ),
    (
        "anarkali",
        "pheras",
        {
            "garment_type": "anarkali",
            "ceremony": "pheras",
            "regional_style": "no_specific_direction",
            "silhouette": "front_open_anarkali",
            "fabric_colour": "emerald",
            "embroidery_colour": "antique_gold",
            "fabrics": ["brocade"],
            "embellishment_styles": ["crystals"],
            "embellishment_density": "heavy",
            "neckline_style": "high_neck",
            "sleeves": "full_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "veil_style",
            "dupatta_style": "head_drape",
        },
    ),
    (
        "lehenga",
        "nikah",
        {
            "garment_type": "lehenga",
            "ceremony": "nikah",
            "regional_style": "no_specific_direction",
            "silhouette": "straight_lehenga",
            "fabric_colour": "blush",
            "embroidery_colour": "rose",
            "fabrics": ["satin"],
            "embellishment_styles": ["pearls"],
            "embellishment_density": "minimal",
            "neckline_style": "curved_scoop",
            "sleeves": "cap_sleeve",
            "back_coverage": "modest_back",
            "midriff": "semi_sheer_midriff",
            "head_covering": "uncovered",
            "dupatta_style": "front_drape",
        },
    ),
    (
        "shalwar_kameez",
        "baraat",
        {
            "garment_type": "shalwar_kameez",
            "ceremony": "baraat",
            "regional_style": "no_specific_direction",
            "silhouette": "long_line_kameez",
            "fabric_colour": "rust",
            "fabrics": ["cotton_silk"],
            "embellishment_styles": ["none"],
            "neckline_style": "boat_neck",
            "sleeves": "full_sleeve",
            "back_coverage": "modest_back",
            "midriff": "covered_midriff",
            "head_covering": "dupatta_over_head",
            "dupatta_style": "double_dupatta",
        },
    ),
    (
        "saree",
        "walima",
        {
            "garment_type": "saree",
            "ceremony": "walima",
            "regional_style": "no_specific_direction",
            "silhouette": "classic_saree_drape",
            "fabric_colour": "amethyst",
            "embroidery_colour": "silver_grey",
            "fabrics": ["net"],
            "embellishment_styles": ["resham_threadwork"],
            "embellishment_density": "minimal",
            "neckline_style": "deep_v_neck",
            "sleeves": "three_quarter_sleeve",
            "back_coverage": "open_back",
            "midriff": "bare_midriff",
            "head_covering": "uncovered",
            "saree_drape": "lehenga_drape",
        },
    ),
]

assert len(MATRIX) == 20


def _remaining_budget_estimate() -> int:
    """Today's true remaining budget: the configured daily ceiling minus
    cost_control.day_budget_total_micro_usd(), which is a LIVE read of the
    ledger's UTC-day running total across ALL usage today, not just this
    command (its own docstring: "a cheap, non-mutating read for the
    enqueue-time preflight only — never the hard boundary"). So this can lag
    the authoritative per-reservation check by design; it is a preflight
    signal for this harness's own stopping rule, not a substitute for the
    pipeline's real BudgetExhausted enforcement."""
    return cost_control.daily_budget_micro_usd() - cost_control.day_budget_total_micro_usd()


class Command(BaseCommand):
    help = "Run Phase 1 of the prompt-fidelity evaluation (real live spend unless --dry-run)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--i-understand-this-spends-real-money",
            action="store_true",
            help="Required to run against live providers. Ignored with --dry-run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate all rows to completion; enqueue and generate nothing. Zero spend.",
        )
        parser.add_argument("--limit", type=int, default=None, help="Max rows to attempt this run.")
        parser.add_argument(
            "--start-at", type=int, default=1, help="1-based matrix row to start from (resume)."
        )
        parser.add_argument(
            "--rows",
            type=str,
            default=None,
            help=(
                "Comma-separated 1-based row numbers to run, e.g. '3,5,7,8,16,17,19' "
                "(Phase 4: only the rows a targeted fix actually addresses). Runs in the "
                "given order and mutually exclusive with --start-at/--limit."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if not dry_run and not options["i_understand_this_spends_real_money"]:
            raise CommandError(
                "pass --i-understand-this-spends-real-money to run this against live "
                "providers, or --dry-run to validate with zero spend"
            )

        rows_arg = options["rows"]
        if rows_arg is not None:
            if options["start_at"] != 1 or options["limit"] is not None:
                raise CommandError("--rows is mutually exclusive with --start-at/--limit")
            try:
                row_numbers = [int(part.strip()) for part in rows_arg.split(",") if part.strip()]
            except ValueError:
                raise CommandError("--rows must be a comma-separated list of integers") from None
            if not row_numbers:
                raise CommandError("--rows must name at least one row")
            if len(set(row_numbers)) != len(row_numbers):
                raise CommandError("--rows contains a duplicate row number")
            for number in row_numbers:
                if number < 1 or number > len(MATRIX):
                    raise CommandError(f"--rows: {number} is out of range (1-{len(MATRIX)})")
            rows = [(number, MATRIX[number - 1]) for number in row_numbers]
            start_at = row_numbers[0]
        else:
            start_at = options["start_at"]
            if start_at < 1 or start_at > len(MATRIX):
                raise CommandError(f"--start-at must be between 1 and {len(MATRIX)}")
            rows = list(enumerate(MATRIX, start=1))[start_at - 1 :]
            limit = options["limit"]
            if limit is not None:
                rows = rows[:limit]
        if len(rows) > PLAN_HARD_IMAGE_CAP:
            raise CommandError(
                f"{len(rows)} rows exceeds the plan's hard cap of {PLAN_HARD_IMAGE_CAP} images "
                "for this command; pass --limit"
            )

        active = QuestionnaireVersion.objects.filter(
            status=QuestionnaireVersion.Status.ACTIVE
        ).first()
        if active is None or active.version != REQUIRED_QUESTIONNAIRE_VERSION:
            raise CommandError(
                f"active questionnaire must be v{REQUIRED_QUESTIONNAIRE_VERSION} "
                f"(found: {active.version if active else 'none'}); "
                "run `manage.py seed_questionnaire` first"
            )

        where = (
            f"rows {','.join(str(n) for n, _ in rows)}"
            if rows_arg is not None
            else f"starting at #{start_at}"
        )
        self.stdout.write(
            f"{'DRY RUN — zero spend' if dry_run else 'LIVE — real provider spend'}: "
            f"{len(rows)} row(s), {where}, questionnaire v{active.version}"
        )

        manifest = []
        run_reserved_micro_usd = 0
        consecutive_failures = 0
        pipeline_config = build_pipeline_config()

        for row_number, (garment, ceremony, answers) in rows:
            session = DesignSession.objects.create()
            design = Design.objects.create(
                design_session=session,
                title=f"fidelity-eval-{row_number:02d}-{garment}-{ceremony}",
            )
            try:
                design = update_design_draft(
                    design,
                    questionnaire_version_id=active.id,
                    answers=answers,
                )
            except (DraftUpdateError, QuestionnaireAnswerError) as exc:
                errors = (
                    exc.errors if isinstance(exc, QuestionnaireAnswerError) else exc.field_errors
                )
                raise CommandError(
                    f"row {row_number} ({garment}/{ceremony}) failed answer validation "
                    f"— fix MATRIX before spending anything: {sorted(errors or {})}"
                ) from None

            completion_errors = design_completion_errors(design)
            if completion_errors:
                raise CommandError(
                    f"row {row_number} ({garment}/{ceremony}) is not complete "
                    f"— fix MATRIX before spending anything: {sorted(completion_errors)}"
                )

            if dry_run:
                self.stdout.write(self.style.SUCCESS(f"row {row_number:2d} OK  design={design.id}"))
                manifest.append(
                    {
                        "row": row_number,
                        "garment": garment,
                        "ceremony": ceremony,
                        "design_id": str(design.id),
                        "status": "validated_only",
                    }
                )
                continue

            try:
                attempt, _created = enqueue_design_generation(
                    design, idempotency_key=uuid_module.uuid4()
                )
            except (
                GenerationUnavailable,
                DesignIncomplete,
                GenerationInProgress,
                DesignAlreadyGenerated,
                DesignNotGeneratable,
                QueueUnavailable,
                cost_control.CountLimitReached,
                cost_control.BudgetLedgerUnavailable,
            ) as exc:
                self.stderr.write(
                    self.style.ERROR(
                        f"row {row_number} enqueue rejected: {type(exc).__name__} — stopping batch"
                    )
                )
                break

            result = run_generation_attempt(attempt.id, config=pipeline_config)
            if result is None:
                # Only possible here if the fresh advisory lock could not be
                # acquired — unreachable for this harness's strictly
                # sequential, non-concurrent use, but re-fetch rather than
                # trust the stale pre-run `attempt` object if it ever occurs.
                result = GenerationAttempt.objects.get(pk=attempt.id)
            run_reserved_micro_usd += result.cost_reserved_micro_usd

            row_record = {
                "row": row_number,
                "garment": garment,
                "ceremony": ceremony,
                "design_id": str(design.id),
                "design_version_id": str(result.design_version_id or ""),
                "status": result.status,
                "error_code": result.error_code,
            }
            if result.design_version_id:
                version = DesignVersion.objects.get(pk=result.design_version_id)
                row_record["image_dimensions"] = (
                    f"{version.image_width}x{version.image_height}"
                    if version.has_permanent_image
                    else None
                )
            manifest.append(row_record)

            remaining = _remaining_budget_estimate()
            self.stdout.write(
                f"row {row_number:2d} {result.status:14s} error={result.error_code or '-':32s} "
                f"design={design.id} version={result.design_version_id or '-'} "
                f"run_reserved=${run_reserved_micro_usd / 1_000_000:.4f} "
                f"est_remaining=${remaining / 1_000_000:.2f}"
            )

            if result.status == GenerationAttempt.Status.SUCCEEDED:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            if result.error_code == LIVE_GENERATION_BUDGET_EXHAUSTED:
                self.stderr.write(
                    self.style.ERROR(
                        "budget exhausted on this attempt — stopping batch, no further spend"
                    )
                )
                break
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                self.stderr.write(
                    self.style.ERROR(
                        f"{consecutive_failures} consecutive failures (latest: "
                        f"{result.error_code or 'unknown'}) — stopping batch, this is a systemic "
                        "problem, not per-design noise; investigate before resuming"
                    )
                )
                break
            if remaining < BUDGET_FLOOR_MICRO_USD:
                self.stderr.write(
                    self.style.WARNING(
                        "estimated remaining budget below the $1.00 floor — stopping batch "
                        "(plan stopping rule)"
                    )
                )
                break

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("MANIFEST_JSON_START"))
        self.stdout.write(json.dumps(manifest, indent=2))
        self.stdout.write(self.style.SUCCESS("MANIFEST_JSON_END"))
