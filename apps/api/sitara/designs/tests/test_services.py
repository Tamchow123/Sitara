"""Version-numbering service tests, including real concurrency."""

import threading

import pytest
from django.db import IntegrityError, connection, transaction

from sitara.designs.models import Design, DesignSession, DesignVersion
from sitara.designs.services import (
    CrossDesignLineageError,
    DesignVersionLimitReached,
    UnsupportedVersionFieldError,
    create_next_design_version,
)

pytestmark = pytest.mark.django_db


def make_design() -> Design:
    return Design.objects.create(design_session=DesignSession.objects.create())


def _refinement_stub(parent: DesignVersion) -> dict:
    # Phase 14: any version beyond 1 that is version_number==2 specifically
    # must carry complete refinement provenance from the moment its row is
    # created (see create_next_design_version_locked's docstring) — these
    # numbering-only tests supply a minimal valid stub so they keep testing
    # MAX_DESIGN_VERSIONS enforcement in isolation from refinement semantics.
    return {
        "parent_version": parent,
        "refinement_request": {"schema_version": 1, "change_type": "colour_story", "note": ""},
        "refinement_request_schema_version": 1,
        "refinement_request_sha256": "e" * 64,
    }


class TestVersionNumbering:
    def test_first_version_is_number_one(self):
        design = make_design()
        version = create_next_design_version(design)
        assert version.version_number == 1
        assert version.design_id == design.pk

    def test_second_version_is_number_two(self):
        design = make_design()
        v1 = create_next_design_version(design)
        assert create_next_design_version(design, **_refinement_stub(v1)).version_number == 2

    def test_third_version_is_refused_under_the_default_maximum(self, settings):
        assert settings.MAX_DESIGN_VERSIONS == 2
        design = make_design()
        v1 = create_next_design_version(design)
        create_next_design_version(design, **_refinement_stub(v1))
        with pytest.raises(DesignVersionLimitReached):
            create_next_design_version(design)
        assert DesignVersion.objects.filter(design=design).count() == 2

    def test_version_limit_follows_the_setting(self, settings):
        settings.MAX_DESIGN_VERSIONS = 3
        design = make_design()
        v1 = create_next_design_version(design)
        v2 = create_next_design_version(design, **_refinement_stub(v1))
        assert v2.version_number == 2
        # Only version_number==2 is database-constrained to carry a parent;
        # higher numbers are an application-level rule only (spec §8).
        v3 = create_next_design_version(design)
        assert v3.version_number == 3
        with pytest.raises(DesignVersionLimitReached):
            create_next_design_version(design)

    def test_limits_are_per_design(self):
        first = make_design()
        second = make_design()
        v1 = create_next_design_version(first)
        create_next_design_version(first, **_refinement_stub(v1))
        # A full sibling design never affects another design's budget.
        assert create_next_design_version(second).version_number == 1

    def test_direct_writes_bypassing_the_service_hit_database_constraints(self):
        design = make_design()
        create_next_design_version(design)
        with pytest.raises(IntegrityError), transaction.atomic():
            DesignVersion.objects.create(design=design, version_number=1)

    def test_cross_design_parent_version_is_rejected(self):
        # No CHECK constraint can express "parent_version belongs to the same
        # design" (it spans two rows); the service is the backstop.
        other_design = make_design()
        foreign_parent = create_next_design_version(other_design)
        design = make_design()
        create_next_design_version(design)
        with pytest.raises(CrossDesignLineageError):
            create_next_design_version(design, **_refinement_stub(foreign_parent))
        assert DesignVersion.objects.filter(design=design).count() == 1

    def test_non_instance_parent_version_is_rejected(self):
        design = make_design()
        v1 = create_next_design_version(design)
        stub = _refinement_stub(v1)
        stub["parent_version"] = v1.pk  # a bare UUID, not a DesignVersion instance
        with pytest.raises(CrossDesignLineageError):
            create_next_design_version(design, **stub)
        assert DesignVersion.objects.filter(design=design).count() == 1

    def test_unsupported_version_field_is_rejected(self):
        design = make_design()
        with pytest.raises(UnsupportedVersionFieldError):
            create_next_design_version(design, image_prompt="not the populate-after path")
        assert DesignVersion.objects.filter(design=design).count() == 0


@pytest.mark.django_db(transaction=True)
class TestVersionConcurrency:
    def test_concurrent_attempts_cannot_duplicate_version_numbers(self):
        """Two simultaneous refinement-style callers, both targeting version 2
        of an existing version 1 under MAX_DESIGN_VERSIONS=2, must produce
        exactly one version 2 and exactly one limit refusal — the row lock
        serialises them; no duplicates, no overshoot."""
        design = make_design()
        v1 = create_next_design_version(design)
        created: list[int] = []
        refused: list[str] = []
        failures: list[BaseException] = []
        barrier = threading.Barrier(2, timeout=10)

        def worker():
            try:
                barrier.wait()
                created.append(
                    create_next_design_version(design, **_refinement_stub(v1)).version_number
                )
            except DesignVersionLimitReached:
                refused.append("limit")
            except BaseException as exc:  # noqa: BLE001 - surfaced in the assert
                failures.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert failures == []
        assert created == [2]
        assert refused == ["limit"]
        numbers = list(
            DesignVersion.objects.filter(design=design).values_list("version_number", flat=True)
        )
        assert sorted(numbers) == [1, 2]
