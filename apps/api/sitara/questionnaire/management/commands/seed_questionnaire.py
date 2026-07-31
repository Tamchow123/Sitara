"""Load a questionnaire fixture into the database and activate it.

    python manage.py seed_questionnaire            # newest fixture
    python manage.py seed_questionnaire --schema-version 4

Development and test infrastructure. A fresh database has no questionnaire at
all — the versioned fixtures under ``questionnaire/fixtures/`` are read by the
test suite but nothing loads them into a running stack, so `/design/new` renders
no questions until someone publishes one by hand. That is fine for a long-lived
developer database and useless for CI, which builds a new database every run and
then drives the real application through Playwright.

Deliberately NOT a new activation path: the schema is validated and the version
activated by ``activate_questionnaire_version``, the single service that is
allowed to make a version active (see its docstring and the one-active partial
unique constraint behind it). This command only reads a reviewed, committed
fixture and hands it to that service.

Idempotent: re-running with a version that is already active changes nothing and
reports so, so it is safe in a CI step that may be retried.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.services import (
    QuestionnaireActivationError,
    activate_questionnaire_version,
)

FIXTURES = Path(__file__).resolve().parents[3] / "questionnaire" / "fixtures"


class Command(BaseCommand):
    help = "Load a committed questionnaire fixture and make it the active version."

    def add_arguments(self, parser):
        parser.add_argument(
            # NOT --version: Django reserves that on every management command.
            "--schema-version",
            type=int,
            default=None,
            help="Fixture version to load; defaults to the newest committed fixture.",
        )

    def handle(self, *args, **options):
        available = sorted(
            (int(p.stem.rsplit("_v", 1)[1]), p) for p in FIXTURES.glob("questionnaire_v*.json")
        )
        if not available:
            raise CommandError("No questionnaire fixture is committed.")

        wanted = options["schema_version"]
        if wanted is None:
            version, path = available[-1]
        else:
            match = [(v, p) for v, p in available if v == wanted]
            if not match:
                raise CommandError(f"No committed fixture for version {wanted}.")
            version, path = match[0]

        existing = QuestionnaireVersion.objects.filter(version=version).first()
        if existing is not None and existing.status == QuestionnaireVersion.Status.ACTIVE:
            self.stdout.write(f"Questionnaire v{version} is already active; nothing to do.")
            return

        # A Django serialised fixture — [{"model": ..., "fields": {...}}] — not
        # a bare schema. Read the schema out of the single record rather than
        # using loaddata, which would import the fixture's own `status` and
        # `activated_at` and so bypass the activation service entirely.
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list) or len(records) != 1:
            raise CommandError(f"{path.name} is not a single-record questionnaire fixture.")
        schema = records[0].get("fields", {}).get("schema")
        if not isinstance(schema, dict):
            raise CommandError(f"{path.name} carries no schema object.")

        if existing is None:
            existing = QuestionnaireVersion.objects.create(version=version, schema=schema)
        elif existing.status == QuestionnaireVersion.Status.RETIRED:
            raise CommandError(
                f"Questionnaire v{version} exists but is retired; a retired version is "
                "never reactivated — publish a new version instead."
            )

        try:
            activate_questionnaire_version(existing)
        except QuestionnaireActivationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Questionnaire v{version} is now active."))
