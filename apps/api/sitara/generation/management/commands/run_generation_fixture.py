"""Run the full generation pipeline OFFLINE for one design (Phase 10).

    python manage.py run_generation_fixture --design <uuid> [--idempotency-key <uuid>]

Uses the REAL enqueue and resumable state-machine services, but injects
zero-network fixture providers (a deterministic StructuredDesign provider, a
fake image provider returning scripted prediction states, a local synthetic
WebP downloader) and stages the result through the SAME validation and private
storage path as live rendering. It makes ZERO network calls to Anthropic or
Replicate, is idempotent for the supplied key, and prints only safe provenance
— never a prompt, questionnaire answer, storage key or private provider
metadata.
"""

import uuid as uuid_module

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from sitara.designs.models import Design, GenerationAttempt
from sitara.generation.fixture_provider import FixtureStructuredDesignProvider
from sitara.generation.image_fixtures import FakeImageProvider, synthetic_webp_downloader
from sitara.generation.pipeline import (
    DesignAlreadyGenerated,
    DesignIncomplete,
    DesignNotGeneratable,
    GenerationInProgress,
    PipelineConfig,
    enqueue_design_generation,
    run_generation_attempt,
)

# Deterministic offline seed (never a real generated seed).
_FIXTURE_SEED = 7


class Command(BaseCommand):
    help = "Run the generation pipeline offline with fixtures (zero network calls)."

    def add_arguments(self, parser):
        parser.add_argument("--design", required=True, help="Design UUID.")
        parser.add_argument(
            "--idempotency-key",
            default=None,
            help="Optional idempotency key UUID (repeating it is a no-op).",
        )

    def handle(self, *args, **options):
        try:
            design_id = uuid_module.UUID(str(options["design"]))
        except (ValueError, TypeError):
            raise CommandError("--design must be a UUID") from None
        design = Design.objects.filter(pk=design_id).first()
        if design is None:
            raise CommandError("design not found")

        if options["idempotency_key"]:
            try:
                key = uuid_module.UUID(str(options["idempotency_key"]))
            except (ValueError, TypeError):
                raise CommandError("--idempotency-key must be a UUID") from None
        else:
            key = uuid_module.uuid4()

        self.stdout.write("Offline fixture mode: zero network calls will be made.")

        try:
            # Real enqueue path, but availability-gate bypassed (fixtures make no
            # paid call) and no Celery task submitted — we run the state machine
            # inline below with injected fixtures.
            attempt, created = enqueue_design_generation(
                design,
                idempotency_key=key,
                enqueue_task=lambda _attempt: None,
                require_availability=False,
            )
        except DesignIncomplete as exc:
            raise CommandError(f"design is incomplete: {sorted(exc.field_errors)}") from None
        except (GenerationInProgress, DesignAlreadyGenerated, DesignNotGeneratable) as exc:
            raise CommandError(f"cannot generate: {type(exc).__name__}") from None

        run_generation_attempt(
            attempt.id,
            structured_provider=FixtureStructuredDesignProvider(),
            image_provider=FakeImageProvider(),
            image_downloader=synthetic_webp_downloader,
            storage=default_storage,
            seed_factory=lambda: _FIXTURE_SEED,
            config=PipelineConfig(poll_interval_seconds=0.0, poll_max_attempts=5),
        )

        result = GenerationAttempt.objects.get(pk=attempt.id)
        self.stdout.write(self.style.SUCCESS(f"attempt {result.id} ({result.status})"))
        self.stdout.write(f"created_new_attempt={created}")
        self.stdout.write(f"design_version={result.design_version_id}")
        self.stdout.write(f"final_status={result.status}")
        self.stdout.write(f"error_code={result.error_code or '-'}")
        self.stdout.write(f"staged_size_bytes={result.staged_image_size_bytes}")
        self.stdout.write(f"staged_sha256={result.staged_image_sha256 or '-'}")
        # Deliberately NOT printed: prompt, answers, storage key, provider,
        # model, prediction id, seed or parameters.
