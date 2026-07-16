"""Version-numbering service tests, including real concurrency."""

import threading

import pytest
from django.db import IntegrityError, connection, transaction

from sitara.designs.models import Design, DesignSession, DesignVersion
from sitara.designs.services import DesignVersionLimitReached, create_next_design_version

pytestmark = pytest.mark.django_db


def make_design() -> Design:
    return Design.objects.create(design_session=DesignSession.objects.create())


class TestVersionNumbering:
    def test_first_version_is_number_one(self):
        design = make_design()
        version = create_next_design_version(design)
        assert version.version_number == 1
        assert version.design_id == design.pk

    def test_second_version_is_number_two(self):
        design = make_design()
        create_next_design_version(design)
        assert create_next_design_version(design).version_number == 2

    def test_third_version_is_refused_under_the_default_maximum(self, settings):
        assert settings.MAX_DESIGN_VERSIONS == 2
        design = make_design()
        create_next_design_version(design)
        create_next_design_version(design)
        with pytest.raises(DesignVersionLimitReached):
            create_next_design_version(design)
        assert DesignVersion.objects.filter(design=design).count() == 2

    def test_version_limit_follows_the_setting(self, settings):
        settings.MAX_DESIGN_VERSIONS = 3
        design = make_design()
        for expected in (1, 2, 3):
            assert create_next_design_version(design).version_number == expected
        with pytest.raises(DesignVersionLimitReached):
            create_next_design_version(design)

    def test_limits_are_per_design(self):
        first = make_design()
        second = make_design()
        create_next_design_version(first)
        create_next_design_version(first)
        # A full sibling design never affects another design's budget.
        assert create_next_design_version(second).version_number == 1

    def test_direct_writes_bypassing_the_service_hit_database_constraints(self):
        design = make_design()
        create_next_design_version(design)
        with pytest.raises(IntegrityError), transaction.atomic():
            DesignVersion.objects.create(design=design, version_number=1)


@pytest.mark.django_db(transaction=True)
class TestVersionConcurrency:
    def test_concurrent_attempts_cannot_duplicate_version_numbers(self):
        """Three simultaneous callers against MAX_DESIGN_VERSIONS=2 must
        produce exactly versions {1, 2} and exactly one limit refusal —
        the row lock serialises them; no duplicates, no overshoot."""
        design = make_design()
        created: list[int] = []
        refused: list[str] = []
        failures: list[BaseException] = []
        barrier = threading.Barrier(3, timeout=10)

        def worker():
            try:
                barrier.wait()
                created.append(create_next_design_version(design).version_number)
            except DesignVersionLimitReached:
                refused.append("limit")
            except BaseException as exc:  # noqa: BLE001 - surfaced in the assert
                failures.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert failures == []
        assert sorted(created) == [1, 2]
        assert refused == ["limit"]
        numbers = list(
            DesignVersion.objects.filter(design=design).values_list("version_number", flat=True)
        )
        assert sorted(numbers) == [1, 2]
