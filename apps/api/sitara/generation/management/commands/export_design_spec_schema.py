"""Regenerate the committed DesignSpec JSON Schemas.

    python manage.py export_design_spec_schema

Writes every supported version's ``sitara/generation/schemas/design_spec_vN.json``
deterministically and atomically from each model's ``model_json_schema()``. CI
runs this and diffs the files, so a model change that is not regenerated fails
the build.
"""

from django.core.management.base import BaseCommand

from sitara.generation.schema_io import write_all_schemas


class Command(BaseCommand):
    help = "Regenerate every committed DesignSpec JSON Schema (design_spec_vN.json)."

    def handle(self, *args, **options):
        for path in write_all_schemas():
            self.stdout.write(self.style.SUCCESS(f"Wrote {path.name}"))
