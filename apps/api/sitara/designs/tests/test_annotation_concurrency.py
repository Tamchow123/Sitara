"""Genuinely simultaneous annotation writes: real threads, real commits.

``transaction=True`` is the whole point. The default transaction-wrapped
``TestCase`` shares one connection, so ``SELECT ... FOR UPDATE`` never contends
and a race test written against it would pass no matter what the service does.
Here each thread gets its own committed view of PostgreSQL, so the lock in
``annotation_service`` is actually exercised.

The interesting case is the FIRST save. Before it there is no annotation row, so
there is nothing for ``select_for_update()`` to lock — which is exactly why the
service locks the parent ``DesignVersion`` instead. Without that, two tabs would
both find nothing stored, both insert, and one would either lose its work
silently or escape as a 500 from the one-to-one constraint.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from sitara.designs.annotation_service import (
    delete_annotation_document,
    replace_annotation_document,
)
from sitara.designs.models import DesignAnnotationDocument, DesignVersion

from .test_annotation_api import annotations_url, item, owned_version, write_body
from .utils import bootstrap_csrf, clone_browser, csrf_client, run_simultaneously, send_json

pytestmark = pytest.mark.django_db(transaction=True)


def annotated_setup():
    """One browser owning one ready version, plus a current CSRF token.

    Built on ``owned_version`` rather than repeating it, so the two suites cannot
    drift apart over what "a version ready to annotate" means."""
    browser = csrf_client()
    token = bootstrap_csrf(browser)
    _, design_id, version = owned_version(browser)
    return browser, token, design_id, version


def tab_writing(browser, token, url, note, *, expected_revision):
    """A worker that PUTs from its own tab of the SAME browser session."""
    tab = clone_browser(browser)
    body = write_body([item(note=note)], expected_revision=expected_revision)
    return lambda: send_json(tab, "put", url, body, token=token)


def winners_and_losers(responses):
    return (
        [r for r in responses if r.status_code == 200],
        [r for r in responses if r.status_code == 409],
    )


class TestTheLockIsActuallyTaken:
    """The first-save outcome tests below cannot see the lock, so this one does.

    For the INSERT-vs-INSERT race the parent-row lock and the one-to-one unique
    constraint produce byte-identical results: with the lock, the loser re-reads
    the committed row and conflicts cleanly; without it, Postgres blocks the
    second insert on the unique index and the IntegrityError backstop converts it
    to the same 409 with the same revision. Both are safe — that is the point of
    having two — but it means the outcome assertions alone would stay green if the
    lock were deleted.

    Measured, not assumed. Replacing ``_lock_version(version)`` with ``pass`` in
    ``replace_annotation_document`` and running this module gives exactly two
    failures: ``test_replacing_locks_the_parent_version_row`` here, and
    ``TestSimultaneousUpdate.test_only_one_same_revision_update_is_admitted``
    (the UPDATE race does lose an update without the lock). All four
    ``TestSimultaneousFirstSave`` tests still pass — which is precisely why this
    class exists."""

    def test_replacing_locks_the_parent_version_row(self):
        _, _, _, version = annotated_setup()
        with CaptureQueriesContext(connection) as captured:
            replace_annotation_document(
                version,
                {
                    "schema_version": 1,
                    "image_width": version.image_width,
                    "image_height": version.image_height,
                    "items": [],
                },
                expected_revision=0,
            )
        locking = [
            query["sql"]
            for query in captured.captured_queries
            if "FOR UPDATE" in query["sql"].upper() and DesignVersion._meta.db_table in query["sql"]
        ]
        assert locking, [query["sql"] for query in captured.captured_queries]

    def test_deleting_locks_the_parent_version_row(self):
        """DELETE takes the same lock, so a clear racing a first save cannot
        interleave with it."""
        _, _, _, version = annotated_setup()
        with CaptureQueriesContext(connection) as captured:
            delete_annotation_document(version)
        assert any(
            "FOR UPDATE" in query["sql"].upper() and DesignVersion._meta.db_table in query["sql"]
            for query in captured.captured_queries
        ), [query["sql"] for query in captured.captured_queries]


class TestSimultaneousFirstSave:
    """Two tabs, no prior document, both claiming ``expected_revision: 0``."""

    def test_exactly_one_first_save_wins_and_the_other_conflicts(self):
        browser, token, design_id, version = annotated_setup()
        url = annotations_url(design_id, version.id)

        responses = run_simultaneously(
            [
                tab_writing(browser, token, url, "Tab one's note.", expected_revision=0),
                tab_writing(browser, token, url, "Tab two's note.", expected_revision=0),
            ]
        )

        # Never a 500: the constraint is reached through a controlled path.
        assert all(response.status_code in {200, 409} for response in responses), [
            (response.status_code, response.content) for response in responses
        ]
        winners, losers = winners_and_losers(responses)
        assert len(winners) == 1, [r.content for r in responses]
        assert len(losers) == 1
        assert winners[0].json()["annotations"]["revision"] == 1
        assert losers[0].json()["error"]["code"] == "annotation_conflict"

        # One row, revision 1 — not two rows, and not a lost update.
        document = DesignAnnotationDocument.objects.get(design_version=version)
        assert document.revision == 1
        assert len(document.document["items"]) == 1

    def test_the_loser_is_told_the_revision_that_actually_won(self):
        """A conflict reporting a stale 0 would send the client straight back
        into the identical conflict, so the reported revision must be the
        winner's."""
        browser, token, design_id, version = annotated_setup()
        url = annotations_url(design_id, version.id)

        responses = run_simultaneously(
            [
                tab_writing(browser, token, url, "First.", expected_revision=0),
                tab_writing(browser, token, url, "Second.", expected_revision=0),
            ]
        )
        _, losers = winners_and_losers(responses)

        assert losers[0].json()["revision"] == 1
        # And that IS the stored revision, so a reload resolves it in one step.
        assert (
            DesignAnnotationDocument.objects.get(design_version=version).revision
            == losers[0].json()["revision"]
        )

    def test_the_conflict_still_never_echoes_the_winning_document(self):
        browser, token, design_id, version = annotated_setup()
        url = annotations_url(design_id, version.id)
        secret = "The winning private note."

        responses = run_simultaneously(
            [
                tab_writing(browser, token, url, secret, expected_revision=0),
                tab_writing(browser, token, url, "The losing note.", expected_revision=0),
            ]
        )
        _, losers = winners_and_losers(responses)

        body = losers[0].json()
        assert set(body.keys()) == {"error", "revision"}
        assert secret not in losers[0].content.decode()

    def test_three_tabs_still_admit_exactly_one(self):
        browser, token, design_id, version = annotated_setup()
        url = annotations_url(design_id, version.id)

        responses = run_simultaneously(
            [
                tab_writing(browser, token, url, f"Tab {index}.", expected_revision=0)
                for index in range(3)
            ]
        )

        winners, losers = winners_and_losers(responses)
        assert len(winners) == 1
        assert len(losers) == 2
        assert DesignAnnotationDocument.objects.filter(design_version=version).count() == 1


class TestSimultaneousUpdate:
    """Two tabs updating an EXISTING document from the same revision."""

    def test_only_one_same_revision_update_is_admitted(self):
        browser, token, design_id, version = annotated_setup()
        url = annotations_url(design_id, version.id)
        first = send_json(browser, "put", url, write_body(expected_revision=0), token=token)
        assert first.status_code == 200, first.content

        responses = run_simultaneously(
            [
                tab_writing(browser, token, url, "Tab one edits.", expected_revision=1),
                tab_writing(browser, token, url, "Tab two edits.", expected_revision=1),
            ]
        )

        winners, losers = winners_and_losers(responses)
        assert len(winners) == 1, [r.content for r in responses]
        assert winners[0].json()["annotations"]["revision"] == 2
        assert losers[0].json()["error"]["code"] == "annotation_conflict"
        assert losers[0].json()["revision"] == 2

        # The winner's content is what is stored — no interleaved half-write.
        stored = DesignAnnotationDocument.objects.get(design_version=version)
        assert stored.revision == 2
        assert (
            stored.document["items"][0]["note"]
            == winners[0].json()["annotations"]["items"][0]["note"]
        )

    def test_a_simultaneous_write_and_clear_leave_a_consistent_state(self):
        """Whichever order they serialise in, the result is coherent: either the
        write landed and was then cleared, or the clear ran and the write
        conflicted. Never a half-written row, never a 500."""
        browser, token, design_id, version = annotated_setup()
        url = annotations_url(design_id, version.id)
        send_json(browser, "put", url, write_body(expected_revision=0), token=token)

        clearing_tab = clone_browser(browser)
        responses = run_simultaneously(
            [
                tab_writing(browser, token, url, "An edit.", expected_revision=1),
                lambda: clearing_tab.delete(url, HTTP_X_CSRFTOKEN=token),
            ]
        )
        write_response, delete_response = responses

        assert delete_response.status_code == 204, delete_response.content
        assert write_response.status_code in {200, 409}, write_response.content
        stored = DesignAnnotationDocument.objects.filter(design_version=version).first()
        if stored is not None:
            # The write serialised after the clear is impossible (it carries
            # revision 1, which no longer exists), so a surviving row can only
            # be a write that landed BEFORE the clear — which the clear then
            # removed. Either way the row must be internally consistent.
            assert stored.revision >= 1
            assert stored.schema_version == 1

    def test_the_generated_image_survives_every_racing_write(self):
        """Annotations are feedback beside the image, never a change to it."""
        browser, token, design_id, version = annotated_setup()
        url = annotations_url(design_id, version.id)
        before = (
            version.image_storage_key,
            version.image_sha256,
            version.image_processor_version,
            version.image_width,
            version.image_height,
        )

        run_simultaneously(
            [
                tab_writing(browser, token, url, f"Tab {index}.", expected_revision=0)
                for index in range(3)
            ]
        )

        version.refresh_from_db()
        assert (
            version.image_storage_key,
            version.image_sha256,
            version.image_processor_version,
            version.image_width,
            version.image_height,
        ) == before
