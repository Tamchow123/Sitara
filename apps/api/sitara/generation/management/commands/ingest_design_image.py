"""Operator-safe retry of ONLY the permanent design-image ingest (Phase 11).

    python manage.py ingest_design_image --attempt <uuid>

Runs the SAME crash-safe ingest service the pipeline uses (stage E) against
an attempt whose raw output Phase 10 already staged — for example after
``image_ingest_unverified`` retry exhaustion once storage recovered. It makes
ZERO provider calls under every path, refuses attempts without staged data or
with a mismatched DesignVersion, is idempotent, and — when the attempt had
terminally failed at the ingest stage — completes it as succeeded so the
Design becomes generated.

Prints only safe provenance: UUIDs, status, processor version and dimensions.
Never a storage key, hash, prompt, answer, signed URL or provider metadata.
"""

import uuid as uuid_module

from django.core.management.base import BaseCommand, CommandError

from sitara.designs.models import GenerationAttempt
from sitara.generation import errors
from sitara.generation.pipeline import finalise_ingest_recovery
from sitara.media.exceptions import (
    DesignImageImmutable,
    DesignImageIngestFailed,
    DesignImageIngestRetry,
)
from sitara.media.ingest import ingest_staged_design_image

# The ONLY attempt states this command acts on: a terminal ingest-stage
# failure (recovery) or an already-succeeded attempt (idempotent re-verify).
# Anything else is refused — an in-progress attempt belongs to a (possibly
# actively retrying) worker this command must never race, and an attempt that
# failed for a NON-ingest reason (design_changed, poll timeout, ambiguous
# submission …) must not have permanent image data silently committed for it,
# because no code path could ever resolve the resulting attempt/Design state.
# The code set is shared with pipeline.finalise_ingest_recovery via
# errors.INGEST_STAGE_ERROR_CODES so the two gates can never drift.
_RECOVERABLE_ERROR_CODES = errors.INGEST_STAGE_ERROR_CODES


class Command(BaseCommand):
    help = "Retry only the permanent design-image ingest for one staged attempt; provider-free."

    def add_arguments(self, parser):
        parser.add_argument("--attempt", required=True, help="GenerationAttempt UUID.")

    def handle(self, *args, **options):
        try:
            attempt_id = uuid_module.UUID(str(options["attempt"]))
        except (ValueError, TypeError):
            raise CommandError("--attempt must be a UUID") from None
        attempt = GenerationAttempt.objects.filter(pk=attempt_id).first()
        if attempt is None:
            raise CommandError("attempt not found")
        # Two admissible states only: a terminal ingest-stage failure (the
        # recovery case) or an already-succeeded attempt (the idempotent
        # re-verification case). Everything else is refused.
        recoverable_failure = (
            attempt.status == GenerationAttempt.Status.FAILED
            and attempt.error_code in _RECOVERABLE_ERROR_CODES
        )
        already_succeeded = attempt.status == GenerationAttempt.Status.SUCCEEDED
        if not recoverable_failure and not already_succeeded:
            if attempt.status != GenerationAttempt.Status.FAILED:
                raise CommandError(
                    "the attempt is not terminally failed; only a failed ingest-stage "
                    "attempt can be recovered (an in-progress attempt belongs to the worker)"
                )
            raise CommandError(
                "the attempt did not fail at the ingest stage; this command "
                "recovers only image_ingest_failed / image_ingest_unverified attempts"
            )
        if not attempt.staged_image_storage_key:
            raise CommandError("the attempt has no staged image data")
        if attempt.design_version_id is None:
            raise CommandError("the attempt has no linked design version")

        self.stdout.write("Ingest-only mode: zero provider calls will be made.")

        try:
            version = ingest_staged_design_image(attempt)
        except DesignImageIngestRetry:
            raise CommandError(
                "storage is temporarily unavailable; the ingest can be retried safely"
            ) from None
        except DesignImageImmutable:
            raise CommandError(
                "existing permanent image provenance conflicts with the staged data"
            ) from None
        except DesignImageIngestFailed as exc:
            # The service's messages are generic and safe by construction.
            raise CommandError(f"ingest failed: {exc}") from None

        # The terminal ingest-stage failure is now resolved — complete it.
        attempt = finalise_ingest_recovery(attempt.pk)
        if attempt is None:
            # Only possible if the attempt row vanished concurrently (it was
            # loaded successfully above) — surface it rather than crash.
            raise CommandError("the attempt no longer exists")

        self.stdout.write(self.style.SUCCESS(f"attempt {attempt.pk} ({attempt.status})"))
        self.stdout.write(f"design_version={version.pk}")
        self.stdout.write(f"processor_version={version.image_processor_version}")
        self.stdout.write(f"original={version.image_width}x{version.image_height}")
        self.stdout.write(f"thumbnail={version.thumbnail_width}x{version.thumbnail_height}")
        # Deliberately NOT printed: storage keys, hashes, prompts, answers,
        # signed URLs, provider or model identifiers.
