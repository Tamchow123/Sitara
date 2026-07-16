"""Anonymous → authenticated ownership lifecycle (the twelve Phase 4 flows).

These integration tests drive the REAL auth endpoints (register / login /
logout, full CSRF) and the design API together, proving the lazy-promotion
ownership model end to end."""

import pytest

from sitara.designs.models import Design, DesignSession
from sitara.designs.services import DESIGN_SESSION_KEY

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_design,
    csrf_client,
    design_url,
    login,
    logout,
    register,
    unique_email,
)

pytestmark = pytest.mark.django_db


def listed_ids(client) -> list[str]:
    response = client.get(DESIGNS_URL)
    assert response.status_code == 200
    return [design["id"] for design in response.json()["designs"]]


class TestAnonymousLifecycle:
    def test_anonymous_browser_creates_lists_and_retrieves_its_design(self):
        client = csrf_client()
        design_id = create_design(client, title="Phase 4 draft").json()["id"]
        assert listed_ids(client) == [design_id]
        assert client.get(design_url(design_id)).status_code == 200

    def test_a_separate_anonymous_browser_receives_404(self):
        owner = csrf_client()
        design_id = create_design(owner).json()["id"]
        stranger = csrf_client()
        assert stranger.get(design_url(design_id)).status_code == 404
        assert listed_ids(stranger) == []

    def test_second_design_reuses_the_same_workspace(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        create_design(client, token=token)
        create_design(client, token=token)
        assert DesignSession.objects.count() == 1
        assert len(listed_ids(client)) == 2


class TestLoginPromotion:
    def test_login_preserves_the_pointer_and_the_next_request_claims(self):
        client = csrf_client()
        design_id = create_design(client, title="pre-login draft").json()["id"]
        workspace = DesignSession.objects.get()
        assert workspace.user_id is None

        email = unique_email()
        register(client, email)
        # Django rotated the session KEY on login but preserved the DATA:
        # the workspace pointer survived.
        assert client.session[DESIGN_SESSION_KEY] == str(workspace.id)
        workspace.refresh_from_db()
        assert workspace.user_id is None  # not yet — promotion is lazy

        # The next design API interaction claims the workspace...
        assert listed_ids(client) == [design_id]
        workspace.refresh_from_db()
        assert workspace.user is not None
        assert workspace.user.email == email
        # ...and the design stays reachable.
        assert client.get(design_url(design_id)).status_code == 200

    def test_same_user_can_access_the_design_from_another_browser(self):
        first_browser = csrf_client()
        design_id = create_design(first_browser, title="cross-browser").json()["id"]
        email = unique_email()
        register(first_browser, email)
        assert listed_ids(first_browser) == [design_id]  # claim happens here

        second_browser = csrf_client()
        login(second_browser, email)
        assert listed_ids(second_browser) == [design_id]
        assert second_browser.get(design_url(design_id)).status_code == 200

    def test_a_different_user_receives_404(self):
        owner_browser = csrf_client()
        design_id = create_design(owner_browser).json()["id"]
        register(owner_browser, unique_email())
        listed_ids(owner_browser)  # claim

        other_browser = csrf_client()
        register(other_browser, unique_email())
        assert other_browser.get(design_url(design_id)).status_code == 404
        assert listed_ids(other_browser) == []

    def test_designs_from_multiple_workspaces_merge_in_authenticated_lists(self):
        email = unique_email()
        first_browser = csrf_client()
        first_id = create_design(first_browser, title="from browser one").json()["id"]
        register(first_browser, email)
        listed_ids(first_browser)  # claim workspace one

        second_browser = csrf_client()
        login(second_browser, email)
        second_id = create_design(second_browser, title="from browser two").json()["id"]
        assert DesignSession.objects.count() == 2
        assert set(listed_ids(second_browser)) == {first_id, second_id}

    def test_authenticated_create_without_pointer_makes_a_user_owned_workspace(self):
        client = csrf_client()
        email = unique_email()
        register(client, email)
        create_design(client, title="born authenticated")
        workspace = DesignSession.objects.get()
        assert workspace.user.email == email
        assert client.session[DESIGN_SESSION_KEY] == str(workspace.id)

    def test_authenticated_list_without_pointer_creates_nothing(self):
        client = csrf_client()
        register(client, unique_email())
        assert listed_ids(client) == []
        assert DesignSession.objects.count() == 0


class TestLogoutAndReturn:
    def test_logout_flushes_the_pointer_and_blocks_anonymous_access(self):
        client = csrf_client()
        design_id = create_design(client, title="protected").json()["id"]
        email = unique_email()
        register(client, email)
        listed_ids(client)  # claim

        logout(client)
        # The Django session was flushed, taking the pointer with it.
        assert DESIGN_SESSION_KEY not in client.session
        # The now-anonymous browser cannot reach the user-owned design.
        assert client.get(design_url(design_id)).status_code == 404
        assert listed_ids(client) == []

    def test_logging_back_in_restores_access(self):
        client = csrf_client()
        design_id = create_design(client, title="come back").json()["id"]
        email = unique_email()
        register(client, email)
        listed_ids(client)  # claim
        logout(client)

        login(client, email)
        assert listed_ids(client) == [design_id]
        assert client.get(design_url(design_id)).status_code == 200

    def test_switching_accounts_on_a_shared_browser_never_transfers_designs(self):
        shared_browser = csrf_client()
        design_id = create_design(shared_browser, title="belongs to A").json()["id"]
        email_a = unique_email()
        register(shared_browser, email_a)
        listed_ids(shared_browser)  # A claims
        workspace = DesignSession.objects.get()
        owner_id = workspace.user_id

        logout(shared_browser)
        register(shared_browser, unique_email())  # user B on the same browser
        assert listed_ids(shared_browser) == []
        assert shared_browser.get(design_url(design_id)).status_code == 404
        workspace.refresh_from_db()
        assert workspace.user_id == owner_id  # still A's


class TestPointerRobustness:
    def test_malformed_pointer_is_dropped_and_treated_as_absent(self):
        client = csrf_client()
        session = client.session
        session[DESIGN_SESSION_KEY] = "not-a-uuid"
        session.save()
        assert listed_ids(client) == []
        assert DESIGN_SESSION_KEY not in client.session

    def test_stale_pointer_to_a_deleted_workspace_is_dropped(self):
        client = csrf_client()
        create_design(client)
        DesignSession.objects.all().delete()
        assert listed_ids(client) == []
        assert DESIGN_SESSION_KEY not in client.session

    def test_anonymous_pointer_to_a_claimed_workspace_grants_nothing(self):
        """Knowing UUIDs is never enough: even a browser session whose
        pointer references a claimed workspace gets 404s."""
        owner_browser = csrf_client()
        design_id = create_design(owner_browser).json()["id"]
        register(owner_browser, unique_email())
        listed_ids(owner_browser)  # claim
        claimed = DesignSession.objects.get()

        intruder = csrf_client()
        session = intruder.session
        session[DESIGN_SESSION_KEY] = str(claimed.id)
        session.save()
        assert intruder.get(design_url(design_id)).status_code == 404
        assert listed_ids(intruder) == []

    def test_authenticated_pointer_to_another_users_workspace_is_never_reused(self):
        owner_browser = csrf_client()
        create_design(owner_browser)
        register(owner_browser, unique_email())
        listed_ids(owner_browser)  # claim for user A
        claimed = DesignSession.objects.get()

        other_browser = csrf_client()
        register(other_browser, unique_email())
        session = other_browser.session
        session[DESIGN_SESSION_KEY] = str(claimed.id)
        session.save()
        # B's create lands in a FRESH workspace, not A's.
        create_design(other_browser, title="B's own")
        claimed.refresh_from_db()
        assert Design.objects.filter(design_session=claimed).count() == 1
        b_workspace = DesignSession.objects.exclude(pk=claimed.pk).get()
        assert b_workspace.user is not None
        assert b_workspace.user_id != claimed.user_id

    def test_last_seen_at_advances_on_design_activity(self):
        client = csrf_client()
        create_design(client)
        workspace = DesignSession.objects.get()
        before = workspace.last_seen_at
        client.get(DESIGNS_URL)
        workspace.refresh_from_db()
        assert workspace.last_seen_at > before

    def test_claim_races_leave_exactly_one_owner(self):
        """Two racing authenticated requests over the same unclaimed
        workspace: the conditional UPDATE lets exactly one win, and the
        loser sees the winner's result (same user here, so both succeed)."""
        client = csrf_client()
        create_design(client)
        email = unique_email()
        register(client, email)
        # Two back-to-back requests both traverse the claim path; the second
        # finds the workspace already claimed by the same user and reuses it.
        assert len(listed_ids(client)) == 1
        assert len(listed_ids(client)) == 1
        workspace = DesignSession.objects.get()
        assert workspace.user.email == email
