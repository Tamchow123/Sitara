"""update_design_draft: atomicity and concurrency (PostgreSQL).

Phase 22 (ADR 0025) removed the ordered inspiration replacement from this
service along with the catalogue it selected from, so what is exercised here is
the remaining transactional surface: answers and title under the Design row
lock. The row lock itself did not go anywhere — the other writer that depends on
it, ``upload_service``, has its own concurrency test.
"""

import threading

import pytest
from django.db import connection

from sitara.designs.models import Design, DesignSession
from sitara.designs.services import (
    QuestionnaireAnswerError,
    update_design_draft,
)

from .utils import make_active_questionnaire

pytestmark = pytest.mark.django_db


def make_design(version=None) -> Design:
    session = DesignSession.objects.create()
    return Design.objects.create(design_session=session, questionnaire_version=version)


class TestAtomicity:
    def test_invalid_answers_do_not_persist(self, inmemory_storage):
        version = make_active_questionnaire()
        design = make_design(version)
        with pytest.raises(QuestionnaireAnswerError):
            update_design_draft(design, answers={"garment_type": "not_a_real_option"})
        design.refresh_from_db()
        assert design.answers == {}

    def test_a_rejected_answer_update_leaves_an_earlier_one_intact(self, inmemory_storage):
        version = make_active_questionnaire()
        design = make_design(version)
        update_design_draft(design, answers={"garment_type": "lehenga"})
        with pytest.raises(QuestionnaireAnswerError):
            update_design_draft(design, answers={"garment_type": "not_a_real_option"})
        design.refresh_from_db()
        assert design.answers == {"garment_type": "lehenga"}


@pytest.mark.django_db(transaction=True)
def test_concurrent_draft_updates_serialise_on_the_design_row():
    """Two simultaneous answer updates on one design must leave exactly one
    coherent set of answers rather than a blend of both, because
    update_design_draft locks the Design row for the whole transaction."""
    version = make_active_questionnaire()
    session = DesignSession.objects.create()
    design = Design.objects.create(design_session=session, questionnaire_version=version)

    barrier = threading.Barrier(2, timeout=10)
    failures = []

    def worker(garment):
        try:
            barrier.wait()
            update_design_draft(design, answers={"garment_type": garment})
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            failures.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, args=("lehenga",)),
        threading.Thread(target=worker, args=("saree",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert failures == []
    design.refresh_from_db()
    # One writer's value survives whole — never a merge of the two.
    assert design.answers in ({"garment_type": "lehenga"}, {"garment_type": "saree"})


class TestTitleThroughService:
    def test_title_update(self):
        design = make_design()
        update_design_draft(design, title="new title")
        design.refresh_from_db()
        assert design.title == "new title"
