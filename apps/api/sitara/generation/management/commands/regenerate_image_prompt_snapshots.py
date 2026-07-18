"""Deliberately regenerate the golden image-prompt snapshots — offline.

    python manage.py regenerate_image_prompt_snapshots

Builds every fixture's prompt (zero provider calls) and, ONLY if the rendered
output is unchanged or PROMPT_BUILDER_VERSION was deliberately bumped, rewrites
the committed ``.txt`` snapshots and ``manifest.json``. If the output changed
while the builder version did not, it REFUSES and instructs a version bump — the
enforcement that normal comparison-only tests rely on. Prints only safe
metadata (fixture count, builder version, combined hash), never prompt bodies.
"""

from django.core.management.base import BaseCommand, CommandError

from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION
from sitara.generation.prompt_snapshots import (
    RegenerationDecision,
    build_all_prompts,
    combined_hash,
    evaluate_regeneration,
    read_manifest,
    write_snapshots,
)


class Command(BaseCommand):
    help = "Regenerate golden image-prompt snapshots (refuses without a version bump)."

    def handle(self, *args, **options):
        prompts = build_all_prompts()
        new_hash = combined_hash(prompts)
        decision = evaluate_regeneration(read_manifest(), new_hash, PROMPT_BUILDER_VERSION)

        if decision is RegenerationDecision.REFUSED_VERSION_UNCHANGED:
            raise CommandError(
                "image-prompt output changed but PROMPT_BUILDER_VERSION is unchanged; "
                "bump PROMPT_BUILDER_VERSION deliberately before regenerating snapshots"
            )

        if decision is RegenerationDecision.UNCHANGED:
            self.stdout.write("Snapshots already current; nothing to write.")
        else:
            write_snapshots(prompts)
            self.stdout.write(
                self.style.SUCCESS(f"Wrote {len(prompts)} snapshot(s) ({decision.value}).")
            )

        self.stdout.write(f"prompt_builder_version={PROMPT_BUILDER_VERSION}")
        self.stdout.write(f"combined_sha256={new_hash}")
