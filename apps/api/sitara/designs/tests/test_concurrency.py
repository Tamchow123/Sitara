"""Genuinely simultaneous workspace tests: real threads, real commits.

Every test simulates ONE browser with multiple tabs: several Django test
clients carrying identical session/CSRF cookies fire requests through a
barrier. ``transaction=True`` gives each thread its own committed view of
PostgreSQL (required for SELECT ... FOR UPDATE to coordinate anything);
each worker closes its thread-local database connection when done."""

import threading
from http.cookies import SimpleCookie

import pytest
from django.db import connection
from django.test import Client

from sitara.designs.models import Design, DesignSession

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_design,
    csrf_client,
    design_url,
    register,
    send_json,
    unique_email,
)

pytestmark = pytest.mark.django_db(transaction=True)


def clone_browser(browser: Client) -> Client:
    """A second client carrying the SAME cookies — one browser, two tabs."""
    twin = csrf_client()
    twin.cookies = SimpleCookie()
    twin.cookies.load({name: morsel.value for name, morsel in browser.cookies.items()})
    return twin


def run_simultaneously(workers):
    """Run callables on their own threads, released together by a barrier.

    Worker exceptions surface in the assertion; every thread closes its own
    database connection safely."""
    barrier = threading.Barrier(len(workers), timeout=10)
    results = [None] * len(workers)
    failures: list[BaseException] = []

    def runner(index, work):
        try:
            barrier.wait()
            results[index] = work()
        except BaseException as exc:  # noqa: BLE001 - surfaced in the assert
            failures.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=runner, args=(index, work)) for index, work in enumerate(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert failures == []
    assert all(result is not None for result in results)
    return results


class TestConcurrentAnonymousCreation:
    def test_simultaneous_creates_share_one_workspace(self):
        browser = csrf_client()
        token = bootstrap_csrf(browser)
        # Bootstrap materialised the Django session: the browser holds one
        # session cookie BEFORE the first unsafe request.
        assert "sitara_sessionid" in browser.cookies

        def make_worker(title):
            tab = clone_browser(browser)
            return lambda: send_json(tab, "post", DESIGNS_URL, {"title": title}, token=token)

        first, second = run_simultaneously([make_worker("tab one"), make_worker("tab two")])
        assert first.status_code == 201, first.content
        assert second.status_code == 201, second.content

        # Exactly ONE workspace; both designs belong to it.
        workspace = DesignSession.objects.get()
        designs = list(Design.objects.all())
        assert len(designs) == 2
        assert all(design.design_session_id == workspace.pk for design in designs)

        # The original browser lists and retrieves BOTH designs — neither
        # was orphaned by a competing session save.
        listed = browser.get(DESIGNS_URL).json()["designs"]
        assert {entry["id"] for entry in listed} == {str(design.pk) for design in designs}
        for design in designs:
            assert browser.get(design_url(design.pk)).status_code == 200


class TestConcurrentAuthenticatedCreation:
    def test_simultaneous_creates_choose_one_current_workspace(self):
        browser = csrf_client()
        email = unique_email()
        register(browser, email)  # authenticated browser, no pointer yet
        token = bootstrap_csrf(browser)  # current post-rotation token

        def make_worker(title):
            tab = clone_browser(browser)
            return lambda: send_json(tab, "post", DESIGNS_URL, {"title": title}, token=token)

        responses = run_simultaneously([make_worker("tab one"), make_worker("tab two")])
        assert all(response.status_code == 201 for response in responses), [
            response.content for response in responses
        ]

        # One current workspace for THIS browser session, owned by the user.
        # (Different browser sessions may still legitimately create their
        # own workspaces — uniqueness is per browser, not per user.)
        workspace = DesignSession.objects.get()
        assert workspace.user is not None
        assert workspace.user.email == email

        # Both designs remain accessible.
        listed = browser.get(DESIGNS_URL).json()["designs"]
        assert len(listed) == 2
        for entry in listed:
            assert browser.get(design_url(entry["id"])).status_code == 200


class TestConcurrentPromotion:
    def test_simultaneous_requests_claim_the_workspace_exactly_once(self):
        browser = csrf_client()
        design_id = create_design(browser, title="pre-login draft").json()["id"]
        workspace = DesignSession.objects.get()
        assert workspace.user_id is None

        email = unique_email()
        register(browser, email)
        workspace.refresh_from_db()
        assert workspace.user_id is None  # promotion is lazy — not yet

        def make_worker(url):
            tab = clone_browser(browser)
            return lambda: tab.get(url)

        list_response, detail_response = run_simultaneously(
            [make_worker(DESIGNS_URL), make_worker(design_url(design_id))]
        )
        assert list_response.status_code == 200
        assert detail_response.status_code == 200
        assert [entry["id"] for entry in list_response.json()["designs"]] == [design_id]
        assert detail_response.json()["id"] == design_id

        # Claimed for the correct user exactly once; no duplicate workspace.
        workspace.refresh_from_db()
        assert workspace.user is not None
        assert workspace.user.email == email
        assert DesignSession.objects.count() == 1
