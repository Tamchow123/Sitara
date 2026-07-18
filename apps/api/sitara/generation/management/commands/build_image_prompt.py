"""Build and persist a DesignVersion's image prompt — offline, zero network.

    python manage.py build_image_prompt --design-version <uuid> [--show-prompt]

Performs NO provider calls. The prompt is produced entirely by the deterministic
local builder from the already-validated DesignSpec. Reports the DesignVersion
UUID, the prompt-builder version and the prompt character count; ``--show-prompt``
additionally prints ONLY the persisted prompt. The command never prints user
answers, questionnaire content, Anthropic context, API keys or storage metadata,
and is idempotent for an already-matching prompt.
"""

import uuid as uuid_module

from django.core.management.base import BaseCommand, CommandError

from sitara.designs.models import DesignVersion
from sitara.generation.prompt_builder import ImagePromptBuildError
from sitara.generation.prompt_service import (
    ImagePromptImmutable,
    build_and_store_image_prompt,
)


class Command(BaseCommand):
    help = "Build and persist a DesignVersion's deterministic image prompt (offline)."

    def add_arguments(self, parser):
        parser.add_argument("--design-version", required=True, help="DesignVersion UUID.")
        parser.add_argument(
            "--show-prompt",
            action="store_true",
            help="Print only the persisted image prompt for local review.",
        )

    def handle(self, *args, **options):
        try:
            version_id = uuid_module.UUID(str(options["design_version"]))
        except (ValueError, TypeError):
            raise CommandError("--design-version must be a UUID") from None

        version = DesignVersion.objects.filter(pk=version_id).first()
        if version is None:
            raise CommandError("design version not found")

        try:
            updated = build_and_store_image_prompt(version)
        except (ImagePromptBuildError, ImagePromptImmutable) as exc:
            # Safe: report only the exception type, never the prompt or spec.
            raise CommandError(f"could not build image prompt: {type(exc).__name__}") from None

        self.stdout.write(self.style.SUCCESS(f"DesignVersion {updated.id}"))
        self.stdout.write(f"prompt_builder_version={updated.prompt_builder_version}")
        self.stdout.write(f"prompt_chars={len(updated.image_prompt)}")
        if options["show_prompt"]:
            self.stdout.write(updated.image_prompt)
