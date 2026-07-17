"""Phase 7 model and database-constraint tests (PostgreSQL-backed).

Constraint tests write through the ORM directly — deliberately bypassing the
services — to prove the DATABASE is the final backstop for the questionnaire
link, the inspiration PROTECT relationships and the selection uniqueness /
position invariants."""

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from sitara.catalogue.tests.utils import make_eligible_asset
from sitara.designs.models import Design, DesignInspiration, DesignSession
from sitara.questionnaire.models import QuestionnaireVersion

from .utils import CONTRACT_SCHEMA

pytestmark = pytest.mark.django_db


def make_design(**kwargs) -> Design:
    session = kwargs.pop("design_session", None) or DesignSession.objects.create()
    return Design.objects.create(design_session=session, **kwargs)


def make_version(*, version: int = 1, status: str = "active") -> QuestionnaireVersion:
    return QuestionnaireVersion.objects.create(
        version=version, status=status, schema=CONTRACT_SCHEMA
    )


class TestQuestionnaireLink:
    def test_questionnaire_version_is_nullable_and_defaults_null(self):
        design = make_design()
        design.refresh_from_db()
        assert design.questionnaire_version_id is None

    def test_existing_title_only_designs_remain_valid(self):
        # A Phase-4-style title-only design still saves and reads back cleanly.
        design = make_design(title="legacy concept")
        design.refresh_from_db()
        assert design.title == "legacy concept"
        assert design.questionnaire_version is None

    def test_linking_a_version_is_protected_from_deletion(self):
        version = make_version()
        make_design(questionnaire_version=version)
        with pytest.raises(ProtectedError), transaction.atomic():
            version.delete()


class TestInspirationSelections:
    def test_asset_is_protected_from_deletion_while_selected(self, inmemory_storage):
        design = make_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        with pytest.raises(ProtectedError), transaction.atomic():
            asset.delete()

    def test_unique_asset_per_design(self, inmemory_storage):
        design = make_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=2)

    def test_unique_position_per_design(self, inmemory_storage):
        design = make_design()
        first = make_eligible_asset()
        second = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=first, position=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            DesignInspiration.objects.create(design=design, inspiration_asset=second, position=1)

    def test_position_below_one_is_blocked_by_the_database(self, inmemory_storage):
        design = make_design()
        asset = make_eligible_asset()
        with pytest.raises(IntegrityError), transaction.atomic():
            DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=0)

    def test_position_above_maximum_is_blocked_by_the_database(self, inmemory_storage):
        design = make_design()
        asset = make_eligible_asset()
        with pytest.raises(IntegrityError), transaction.atomic():
            DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=4)

    def test_same_asset_may_be_selected_by_different_designs(self, inmemory_storage):
        asset = make_eligible_asset()
        first = make_design()
        second = make_design()
        DesignInspiration.objects.create(design=first, inspiration_asset=asset, position=1)
        DesignInspiration.objects.create(design=second, inspiration_asset=asset, position=1)
        assert DesignInspiration.objects.filter(inspiration_asset=asset).count() == 2

    def test_selections_ordered_by_position(self, inmemory_storage):
        design = make_design()
        assets = [make_eligible_asset() for _ in range(3)]
        # Insert out of order; ordering must still come back by position.
        DesignInspiration.objects.create(design=design, inspiration_asset=assets[2], position=3)
        DesignInspiration.objects.create(design=design, inspiration_asset=assets[0], position=1)
        DesignInspiration.objects.create(design=design, inspiration_asset=assets[1], position=2)
        positions = [s.position for s in design.inspiration_selections.all()]
        assert positions == [1, 2, 3]

    def test_deleting_a_design_cascades_selections(self, inmemory_storage):
        design = make_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        design.delete()
        assert DesignInspiration.objects.count() == 0
