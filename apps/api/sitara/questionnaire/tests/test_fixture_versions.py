"""Published-version immutability and the v1 → v2 lifecycle.

Published questionnaire schemas are immutable (ADR 0005): a behaviour change
ships as a NEW ``QuestionnaireVersion``, never as an edit to an already-published
fixture. ``questionnaire_v1`` is the published, active seed; ``questionnaire_v2``
is a DRAFT that adds only ``none_hides_embellishment_density`` and is activated
solely through the ``activate_questionnaire_version`` service/admin workflow.

A deterministic fingerprint over v1's canonical schema (sorted keys, no
formatting whitespace, no timestamps) fails loudly if the published v1 schema is
ever edited, forcing an explicit, reviewed versioning decision.
"""

import hashlib
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from sitara.designs.models import Design, DesignSession
from sitara.designs.services import update_design_draft
from sitara.questionnaire.answer_validation import QuestionnaireAnswerError
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.services import activate_questionnaire_version

pytestmark = pytest.mark.django_db

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_V1_PATH = _FIXTURES / "questionnaire_v1.json"
_V2_PATH = _FIXTURES / "questionnaire_v2.json"

# The canonical fingerprint of the PUBLISHED v1 schema. If this changes, the
# published v1 schema was edited — which is forbidden. Ship the change as a new
# version and update this constant only as a deliberate, reviewed decision.
V1_SCHEMA_FINGERPRINT = "6c010ffa3f816a0ce0fa5f9d16a7407124535e9aee315f09f44ff5dfec580a0d"

_HIDE_RULE_ID = "none_hides_embellishment_density"


def _schema_from_fixture(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)[0]["fields"]["schema"]


def _canonical(schema: dict) -> str:
    # Canonical JSON: sorted keys, no insignificant whitespace, full Unicode.
    return json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(schema: dict) -> str:
    return hashlib.sha256(_canonical(schema).encode("utf-8")).hexdigest()


def _rule_ids(schema: dict) -> set[str]:
    return {rule["id"] for rule in schema.get("rules", [])}


class TestPublishedV1Immutability:
    def test_v1_canonical_fingerprint_is_unchanged(self):
        # Guards the published v1 schema against any silent edit — timestamps and
        # formatting are excluded, only the canonical schema content is hashed.
        assert _fingerprint(_schema_from_fixture(_V1_PATH)) == V1_SCHEMA_FINGERPRINT

    def test_loading_v1_preserves_the_fingerprint(self):
        call_command("loaddata", "questionnaire_v1", verbosity=0)
        loaded = QuestionnaireVersion.objects.get(version=1)
        assert _fingerprint(loaded.schema) == V1_SCHEMA_FINGERPRINT

    def test_v1_does_not_contain_the_new_hide_rule(self):
        assert _HIDE_RULE_ID not in _rule_ids(_schema_from_fixture(_V1_PATH))


class TestVersionTwoFixture:
    def test_v2_is_a_distinct_draft_with_only_the_new_rule(self):
        v1_schema = _schema_from_fixture(_V1_PATH)
        v2_fixture = json.loads(_V2_PATH.read_text(encoding="utf-8"))[0]
        v2_schema = v2_fixture["fields"]["schema"]

        assert v2_fixture["pk"] != "3f7a2b9e-4c1d-4e8a-9b6f-1a2b3c4d5e6f"
        assert v2_fixture["fields"]["version"] == 2
        assert v2_fixture["fields"]["status"] == "draft"
        assert v2_fixture["fields"]["activated_at"] is None
        # v2 == v1 plus EXACTLY the one new rule.
        assert _rule_ids(v2_schema) - _rule_ids(v1_schema) == {_HIDE_RULE_ID}
        assert _rule_ids(v1_schema) - _rule_ids(v2_schema) == set()

    def test_loading_both_creates_two_distinct_versions(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v2", verbosity=0)
        assert QuestionnaireVersion.objects.count() == 2
        v1 = QuestionnaireVersion.objects.get(version=1)
        v2 = QuestionnaireVersion.objects.get(version=2)
        assert v1.pk != v2.pk
        assert v1.status == QuestionnaireVersion.Status.ACTIVE
        assert v2.status == QuestionnaireVersion.Status.DRAFT

    def test_loading_v2_does_not_activate_or_retire_v1(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v2", verbosity=0)
        assert QuestionnaireVersion.objects.get(version=1).status == "active"
        assert QuestionnaireVersion.objects.get(version=2).status == "draft"
        # v2 carries the new rule; v1 still does not.
        assert _HIDE_RULE_ID in _rule_ids(QuestionnaireVersion.objects.get(version=2).schema)
        assert _HIDE_RULE_ID not in _rule_ids(QuestionnaireVersion.objects.get(version=1).schema)


class TestActivationLifecycle:
    def test_activating_v2_atomically_retires_v1(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v2", verbosity=0)
        v2 = QuestionnaireVersion.objects.get(version=2)

        activate_questionnaire_version(v2)

        assert QuestionnaireVersion.objects.get(version=1).status == "retired"
        assert QuestionnaireVersion.objects.get(version=2).status == "active"
        assert (
            QuestionnaireVersion.objects.filter(status=QuestionnaireVersion.Status.ACTIVE).count()
            == 1
        )

    def test_v1_fingerprint_survives_v2_activation(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v2", verbosity=0)
        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=2))
        # Retiring v1 must not rewrite its schema.
        assert _fingerprint(QuestionnaireVersion.objects.get(version=1).schema) == (
            V1_SCHEMA_FINGERPRINT
        )


def _design_pinned_to(version: QuestionnaireVersion) -> Design:
    session = DesignSession.objects.create()
    return Design.objects.create(design_session=session, questionnaire_version=version)


class TestHistoricalSemantics:
    _NONE_PLUS_DENSITY = {"embellishment_styles": ["none"], "embellishment_density": "heavy"}

    def test_design_on_v1_keeps_historical_semantics(self):
        # v1 has no hide rule: density stays visible, so "none" + a density is
        # accepted under the pinned historical schema.
        call_command("loaddata", "questionnaire_v1", verbosity=0)
        design = _design_pinned_to(QuestionnaireVersion.objects.get(version=1))
        updated = update_design_draft(design, answers=self._NONE_PLUS_DENSITY)
        assert updated.answers["embellishment_styles"] == ["none"]
        assert updated.answers["embellishment_density"] == "heavy"

    def test_design_on_activated_v2_rejects_hidden_density(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v2", verbosity=0)
        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=2))
        design = _design_pinned_to(QuestionnaireVersion.objects.get(version=2))
        with pytest.raises(QuestionnaireAnswerError) as excinfo:
            update_design_draft(design, answers=self._NONE_PLUS_DENSITY)
        assert "embellishment_density" in excinfo.value.errors

    def test_design_on_retired_v1_remains_resumable(self):
        call_command("loaddata", "questionnaire_v1", "questionnaire_v2", verbosity=0)
        design = _design_pinned_to(QuestionnaireVersion.objects.get(version=1))
        update_design_draft(design, answers=self._NONE_PLUS_DENSITY)

        # v2 is activated; v1 is now retired — but the pinned design still
        # validates and edits against its own historical v1 schema.
        activate_questionnaire_version(QuestionnaireVersion.objects.get(version=2))
        design.refresh_from_db()
        assert design.questionnaire_version.status == "retired"
        resumed = update_design_draft(design, answers={"embellishment_styles": ["none"]})
        assert resumed.answers["embellishment_styles"] == ["none"]
