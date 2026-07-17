"""Regenerate the committed DesignSpec JSON Schema.

    python manage.py export_design_spec_schema

Writes ``sitara/generation/schemas/design_spec_v1.json`` deterministically and
atomically from ``DesignSpec.model_json_schema()``. CI runs this and diffs the
file, so a model change that is not regenerated fails the build.
"""

from django.core.management.base import BaseCommand

from sitara.generation.schema_io import SCHEMA_PATH, write_schema


class Command(BaseCommand):
    help = "Regenerate the committed DesignSpec JSON Schema (design_spec_v1.json)."

    def handle(self, *args, **options):
        write_schema()
        self.stdout.write(self.style.SUCCESS(f"Wrote {SCHEMA_PATH.name}"))
