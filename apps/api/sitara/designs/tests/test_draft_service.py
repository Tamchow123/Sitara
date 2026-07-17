"""update_design_draft: atomicity, ordering and concurrency (PostgreSQL)."""

import threading

import pytest
from django.db import connection

from sitara.catalogue.tests.utils import make_eligible_asset
from sitara.designs.models import Design, DesignInspiration, DesignSession
from sitara.designs.services import (
    DraftUpdateError,
    QuestionnaireAnswerError,
    update_design_draft,
)

from .utils import make_active_questionnaire

pytestmark = pytest.mark.django_db


def make_design(version=None) -> Design:
    session = DesignSession.objects.create()
    return Design.objects.create(design_session=session, questionnaire_version=version)


class TestAtomicity:
    def test_answers_and_selections_roll_back_together_on_selection_failure(self, inmemory_storage):
        import uuid

        version = make_active_questionnaire()
        design = make_design(version)
        good = make_eligible_asset()

        # A valid answer update PLUS an ineligible (nonexistent) inspiration:
        # the whole call must roll back, leaving answers unchanged.
        with pytest.raises(DraftUpdateError):
            update_design_draft(
                design,
                answers={"garment_type": "lehenga"},
                inspiration_asset_ids=[str(good.id), str(uuid.uuid4())],
            )
        design.refresh_from_db()
        assert design.answers == {}
        assert DesignInspiration.objects.filter(design=design).count() == 0

    def test_invalid_answers_do_not_persist(self, inmemory_storage):
        version = make_active_questionnaire()
        design = make_design(version)
        with pytest.raises(QuestionnaireAnswerError):
            update_design_draft(design, answers={"garment_type": "not_a_real_option"})
        design.refresh_from_db()
        assert design.answers == {}

    def test_selection_order_becomes_positions(self, inmemory_storage):
        version = make_active_questionnaire()
        design = make_design(version)
        assets = [make_eligible_asset() for _ in range(3)]
        update_design_draft(
            design,
            inspiration_asset_ids=[str(assets[1].id), str(assets[2].id), str(assets[0].id)],
        )
        rows = list(DesignInspiration.objects.filter(design=design).order_by("position"))
        assert [r.inspiration_asset_id for r in rows] == [
            assets[1].id,
            assets[2].id,
            assets[0].id,
        ]
        assert [r.position for r in rows] == [1, 2, 3]


@pytest.mark.django_db(transaction=True)
def test_concurrent_draft_updates_serialise_on_the_design_row(settings):
    """Two simultaneous full-replacement updates on one design must leave a
    consistent selection set (never duplicate positions, never > max), because
    update_design_draft locks the Design row."""
    import copy

    storages = copy.deepcopy(settings.STORAGES)
    storages["default"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = storages

    version = make_active_questionnaire()
    session = DesignSession.objects.create()
    design = Design.objects.create(design_session=session, questionnaire_version=version)
    assets_a = [make_eligible_asset() for _ in range(3)]
    assets_b = [make_eligible_asset() for _ in range(3)]

    barrier = threading.Barrier(2, timeout=10)
    failures = []

    def worker(asset_group):
        try:
            barrier.wait()
            update_design_draft(design, inspiration_asset_ids=[str(a.id) for a in asset_group])
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            failures.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, args=(assets_a,)),
        threading.Thread(target=worker, args=(assets_b,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert failures == []
    rows = list(DesignInspiration.objects.filter(design=design).order_by("position"))
    # Exactly one winner's set survives: 3 rows, positions 1..3, one group.
    assert len(rows) == 3
    assert sorted(r.position for r in rows) == [1, 2, 3]
    surviving = {r.inspiration_asset_id for r in rows}
    assert surviving in ({a.id for a in assets_a}, {a.id for a in assets_b})


class TestTitleThroughService:
    def test_title_update(self):
        design = make_design()
        update_design_draft(design, title="new title")
        design.refresh_from_db()
        assert design.title == "new title"
