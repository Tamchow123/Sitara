"""Generate a DesignSpec for one design — offline fixture OR gated live mode.

    # Offline (zero network calls):
    python manage.py generate_spec --design <uuid> --fixture valid

    # Live (paid; requires all gates AND explicit --confirm-live):
    python manage.py generate_spec --design <uuid> --confirm-live [--show-spec]

A plain invocation with the live gates open but WITHOUT --confirm-live makes
ZERO provider calls. The command never prints an API key, a raw prompt or a
raw provider response.
"""

import json
import uuid as uuid_module

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sitara.ai_gateway.policy import (
    PaidGenerationDisabled,
    structured_design_generation_is_available,
)
from sitara.ai_gateway.structured_design import StructuredDesignProviderError
from sitara.designs.models import Design
from sitara.generation.context import DesignNotReady
from sitara.generation.fixture_provider import FixtureStructuredDesignProvider
from sitara.generation.input_safety import UnsafeUserTextError
from sitara.generation.services import (
    DesignChangedDuringGeneration,
    GenerationFailed,
    GenerationLocked,
    GenerationRefused,
    ProviderIdentityChanged,
    generate_design_spec_for_design,
)


class Command(BaseCommand):
    help = "Generate a validated DesignSpec (offline fixture or gated live Anthropic)."

    def add_arguments(self, parser):
        parser.add_argument("--design", required=True, help="Design UUID.")
        parser.add_argument(
            "--fixture",
            default=None,
            help="Offline fixture mode: use the recorded fixture provider (zero network).",
        )
        parser.add_argument(
            "--confirm-live",
            action="store_true",
            help="Explicitly authorise a paid live Anthropic request (requires open gates).",
        )
        parser.add_argument(
            "--show-spec",
            action="store_true",
            help="Print only the validated persisted DesignSpec for local review.",
        )

    def handle(self, *args, **options):
        try:
            design_id = uuid_module.UUID(str(options["design"]))
        except (ValueError, TypeError):
            raise CommandError("--design must be a UUID") from None
        design = Design.objects.filter(pk=design_id).first()
        if design is None:
            raise CommandError("design not found")

        if options["fixture"] and options["confirm_live"]:
            raise CommandError("choose either --fixture or --confirm-live, not both")

        if options["fixture"]:
            provider = FixtureStructuredDesignProvider(fixture_name=options["fixture"])
            self.stdout.write("Offline fixture mode: no network calls will be made.")
        elif options["confirm_live"]:
            # The environment gate is the ONE central definition; --confirm-live
            # is only an additional explicit opt-in on top of it.
            if not structured_design_generation_is_available():
                raise CommandError(
                    "live generation requires DEMO_MODE=false, ALLOW_PAID_AI_CALLS=true, "
                    "a non-empty ANTHROPIC_API_KEY and a valid ANTHROPIC_MODEL"
                )
            provider = None  # the service selects the gated live provider
            self.stdout.write("Live mode: at most 2 Anthropic requests may occur.")
            self.stdout.write(f"Model: {settings.ANTHROPIC_MODEL}")
            self.stdout.write(f"Max output tokens: {settings.DESIGN_SPEC_MAX_OUTPUT_TOKENS}")
        else:
            self.stdout.write(
                "No mode selected — use --fixture NAME (offline) or --confirm-live (paid). "
                "Zero provider calls were made."
            )
            return

        try:
            version = generate_design_spec_for_design(design, provider=provider)
        except (
            DesignNotReady,
            GenerationLocked,
            GenerationRefused,
            GenerationFailed,
            DesignChangedDuringGeneration,
            ProviderIdentityChanged,
            UnsafeUserTextError,
            PaidGenerationDisabled,
            StructuredDesignProviderError,
        ) as exc:
            # Safe: report only the exception type, never a prompt, answer,
            # output, key or provider error body.
            raise CommandError(f"generation did not complete: {type(exc).__name__}") from None

        self.stdout.write(
            self.style.SUCCESS(f"DesignVersion {version.id} created (v{version.version_number})")
        )
        self.stdout.write(
            f"schema_version={version.design_spec_schema_version} "
            f"template_version={version.design_spec_template_version}"
        )
        self.stdout.write(
            f"provider={version.design_spec_provider} "
            f"attempts={getattr(version, 'spec_generation_attempts', '?')}"
        )
        self.stdout.write(
            f"input_tokens={version.design_spec_input_tokens} "
            f"output_tokens={version.design_spec_output_tokens}"
        )
        if options["show_spec"]:
            self.stdout.write(json.dumps(version.design_spec, indent=2, ensure_ascii=False))
