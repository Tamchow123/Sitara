"""Ending a walk-in session on a shared shop device (Phase 22, ADR 0027).

Two ways it ends: the stylist taps "Finish and hand back", or nobody does and
the idle timeout does it instead. Customers walk away — to take a call, to look
at a rail, to leave — and the second path exists because the first is a habit
somebody has to remember.

What each must do is the same: forget the workspace, and stop any handoff code
that is still live. What neither may do is sign the shop out or delete
anything. The account is the boutique's and the concepts are its work product;
what ends is this browser's claim on a workspace, not the workspace.
"""

import io
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from sitara.designs.grant_service import (
    GrantUnusable,
    create_reference_upload_grant,
    resolve_reference_upload_grant,
    revoke_grants_for_workspace,
)
from sitara.designs.models import Design, DesignSession, ReferenceUploadGrant
from sitara.designs.services import DESIGN_SESSION_KEY, WorkspaceCoordinationError
from sitara.designs.upload_service import InspirationUploadError, create_inspiration_upload

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_owned_design_id,
    csrf_client,
    send_json,
    signed_in_client,
    unique_ip,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("inmemory_storage")]

END_URL = f"{DESIGNS_URL}end-session/"


def end(client, token=None):
    return send_json(client, "post", END_URL, {}, token=token or bootstrap_csrf(client))


def go_idle(design_id, *, minutes: int = 120) -> None:
    """Age the workspace so the server considers the walk-in gone.

    ``last_seen_at`` is the server's own record, updated by every design
    request — which is the point of enforcing the timeout on it rather than on
    anything the browser says."""
    DesignSession.objects.filter(designs__id=design_id).update(
        last_seen_at=timezone.now() - timezone.timedelta(minutes=minutes)
    )


class TestFinishAndHandBack:
    def test_the_next_request_starts_a_fresh_workspace(self):
        client = csrf_client()
        first = create_owned_design_id(client)

        assert end(client).json() == {"ended": True}

        second = create_owned_design_id(client)
        assert second != first
        assert (
            Design.objects.get(pk=first).design_session_id
            != Design.objects.get(pk=second).design_session_id
        )

    def test_the_previous_design_becomes_unreachable_from_this_browser(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        end(client)

        response = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())

        assert response.status_code == 404

    def test_that_404_is_the_same_one_a_nonexistent_design_gets(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        end(client)

        gone = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())
        never = client.get(f"{DESIGNS_URL}{uuid.uuid4()}/", REMOTE_ADDR=unique_ip())

        assert gone.status_code == never.status_code == 404
        assert gone.json() == never.json()

    def test_the_workspace_pointer_is_gone_from_the_session(self):
        client = csrf_client()
        create_owned_design_id(client)
        assert client.session.get(DESIGN_SESSION_KEY)

        end(client)

        assert client.session.get(DESIGN_SESSION_KEY) is None

    def test_a_live_handoff_code_stops_working(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        _, plaintext = create_reference_upload_grant(Design.objects.get(pk=design_id))
        assert resolve_reference_upload_grant(plaintext)

        end(client)

        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(plaintext)

    def test_it_deletes_nothing(self):
        """The concepts are the shop's work product. Ending a session ends this
        browser's claim on a workspace, not the workspace."""
        client = csrf_client()
        design_id = create_owned_design_id(client, title="Ayesha, June wedding")

        end(client)

        design = Design.objects.get(pk=design_id)
        assert design.title == "Ayesha, June wedding"
        assert DesignSession.objects.filter(pk=design.design_session_id).exists()

    def test_it_does_not_sign_the_shop_out(self):
        """The one real simplification the shop-account decision buys. A login
        screen between every customer would cost the stylist a password each
        time and buy no privacy — the account is the boutique's either way."""
        client, token = signed_in_client()
        create_owned_design_id(client)

        end(client, token)

        me = client.get("/api/v1/auth/me/", REMOTE_ADDR=unique_ip())
        assert me.status_code == 200
        assert me.json()["user"]["email"]

    def test_ending_twice_is_not_an_error(self):
        client = csrf_client()
        create_owned_design_id(client)
        token = bootstrap_csrf(client)

        assert end(client, token).json() == {"ended": True}
        assert end(client, token).json() == {"ended": False}

    def test_ending_with_nothing_to_end_succeeds(self):
        client = csrf_client()

        response = end(client)

        assert response.status_code == 200
        assert response.json() == {"ended": False}

    def test_it_says_nothing_about_what_it_ended(self):
        """The next person may already be reading the screen."""
        client = csrf_client()
        design_id = create_owned_design_id(client, title="Ayesha, June wedding")
        create_reference_upload_grant(Design.objects.get(pk=design_id))

        body = end(client).json()

        assert set(body) == {"ended"}
        assert design_id not in str(body)
        assert "Ayesha" not in str(body)

    def test_it_requires_csrf(self):
        client = csrf_client()
        create_owned_design_id(client)
        bootstrap_csrf(client)

        response = client.post(END_URL, content_type="application/json", REMOTE_ADDR=unique_ip())

        assert response.status_code == 403
        assert client.session.get(DESIGN_SESSION_KEY)

    def test_it_does_not_reach_another_browser_s_workspace(self):
        other = csrf_client()
        other_design = create_owned_design_id(other)
        ender = csrf_client()
        create_owned_design_id(ender)

        end(ender)

        assert (
            other.get(f"{DESIGNS_URL}{other_design}/", REMOTE_ADDR=unique_ip()).status_code == 200
        )

    def test_it_only_answers_post(self):
        client = csrf_client()
        token = bootstrap_csrf(client)

        for method in ("get", "put", "patch", "delete"):
            response = getattr(client, method)(
                END_URL, HTTP_X_CSRFTOKEN=token, REMOTE_ADDR=unique_ip()
            )
            assert response.status_code == 405, method


class TestTheIdleTimeout:
    """The path that runs when nobody remembers to tap the button.

    Server-side on purpose: a backgrounded tab runs no timers and a closed lid
    runs nothing at all, so a browser prompt is not a boundary."""

    def test_an_idle_workspace_is_not_resumed(self, settings):
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client = csrf_client()
        design_id = create_owned_design_id(client)
        go_idle(design_id)

        response = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())

        assert response.status_code == 404

    def test_the_next_customer_starts_a_new_workspace(self, settings):
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client = csrf_client()
        first = create_owned_design_id(client)
        go_idle(first)

        second = create_owned_design_id(client)

        assert (
            Design.objects.get(pk=first).design_session_id
            != Design.objects.get(pk=second).design_session_id
        )

    def test_an_idle_workspace_s_handoff_code_stops_working(self, settings):
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client = csrf_client()
        design_id = create_owned_design_id(client)
        _, plaintext = create_reference_upload_grant(Design.objects.get(pk=design_id))
        go_idle(design_id)

        # Any design request is enough to notice; nothing has to be tapped.
        client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())

        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(plaintext)

    def test_the_create_path_notices_too(self, settings):
        """Both resolution paths check it, under the same lock everything else
        here is checked under — otherwise a returning customer would resume a
        workspace the next one may already have been handed."""
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client = csrf_client()
        design_id = create_owned_design_id(client)
        _, plaintext = create_reference_upload_grant(Design.objects.get(pk=design_id))
        go_idle(design_id)

        create_owned_design_id(client)

        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(plaintext)

    def test_an_active_workspace_is_left_alone(self, settings):
        """A bridal questionnaire is not quick, and there are pauses for tea and
        for fetching a sample. The timeout must not interrupt a consultation."""
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 1800
        client = csrf_client()
        design_id = create_owned_design_id(client)
        go_idle(design_id, minutes=10)

        response = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())

        assert response.status_code == 200

    def test_every_request_pushes_the_deadline_back(self, settings):
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 1800
        client = csrf_client()
        design_id = create_owned_design_id(client)
        go_idle(design_id, minutes=25)

        # A request inside the window refreshes last_seen_at...
        assert client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip()).status_code == 200
        # ...so ten more minutes is not yet a timeout.
        session = DesignSession.objects.get(designs__id=design_id)
        assert session.last_seen_at > timezone.now() - timezone.timedelta(minutes=1)

    def test_it_does_not_sign_the_shop_out_either(self, settings):
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client, _ = signed_in_client()
        design_id = create_owned_design_id(client)
        go_idle(design_id)

        client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())

        me = client.get("/api/v1/auth/me/", REMOTE_ADDR=unique_ip())
        assert me.status_code == 200
        assert me.json()["user"]["email"]

    def test_it_deletes_nothing(self, settings):
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client = csrf_client()
        design_id = create_owned_design_id(client, title="Ayesha, June wedding")
        go_idle(design_id)

        client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())

        assert Design.objects.filter(pk=design_id, title="Ayesha, June wedding").exists()

    def test_the_grant_row_survives_as_a_revoked_one(self, settings):
        """Revoked, not deleted: the row is the audit record of a code having
        existed, and a purge is a separate concern."""
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client = csrf_client()
        design_id = create_owned_design_id(client)
        create_reference_upload_grant(Design.objects.get(pk=design_id))
        go_idle(design_id)

        client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())

        grant = ReferenceUploadGrant.objects.get(design_id=design_id)
        assert grant.revoked_at is not None

    def test_it_ships_with_a_bounded_default(self):
        """Asserted against the shipped declaration rather than the running
        value, so a developer `.env` cannot turn a regression into a pass."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[3] / "config" / "settings.py").read_text(
            encoding="utf-8"
        )

        assert (
            'WALK_IN_IDLE_TIMEOUT_SECONDS = env_positive_int("WALK_IN_IDLE_TIMEOUT_SECONDS", 1800)'
            in source
        )


def _upload_file() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 60), (200, 30, 60)).save(buffer, format="PNG")
    return SimpleUploadedFile("reference.png", buffer.getvalue(), content_type="image/png")


class TestCoordinationAndFailure:
    """What has to hold when two requests collide, or one dies halfway.

    Every test here is written to fail against the first implementation of
    this slice, which claimed these properties in its docstrings without
    holding them: it dropped the pointer before revoking, took no session
    lock, and had no controlled answer when one could not be taken.
    """

    def test_a_failed_release_leaves_the_pointer_so_a_retry_can_finish(self, monkeypatch):
        """The pointer is the only handle back to a workspace.

        Dropping it before the revoke and then failing left the codes live with
        nothing that would ever retry them: the next attempt found no pointer
        and truthfully reported "nothing to end"."""
        client = csrf_client()
        design_id = create_owned_design_id(client)
        _, plaintext = create_reference_upload_grant(Design.objects.get(pk=design_id))
        pointer = client.session[DESIGN_SESSION_KEY]

        def explode(*args, **kwargs):
            raise RuntimeError("database went away mid-release")

        monkeypatch.setattr("sitara.designs.services.revoke_grants_for_workspace", explode)
        with pytest.raises(RuntimeError):
            end(client)

        # The pointer survives, so the workspace is still reachable...
        assert client.session[DESIGN_SESSION_KEY] == pointer
        # ...and the code is still live, which is exactly why that matters.
        assert resolve_reference_upload_grant(plaintext) is not None

        monkeypatch.undo()
        response = end(client)

        assert response.status_code == 200
        assert response.json()["ended"] is True
        assert DESIGN_SESSION_KEY not in client.session
        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(plaintext)

    def test_the_hand_back_locks_the_browser_session_row(self):
        """A hand-back on one tab and a first design create on another were two
        blind whole-blob session writes, and whichever landed second silently
        undid the other. Both paths coordinate on the session row now.

        The lock is asserted directly rather than by scheduling a real race,
        which cannot be made deterministic in a test."""
        import sitara.designs.services as services

        client = csrf_client()
        create_owned_design_id(client)

        locked: list[str] = []
        original = services._lock_browser_session

        def record(request):
            row, data = original(request)
            locked.append("locked")
            return row, data

        services._lock_browser_session = record
        try:
            response = end(client)
        finally:
            services._lock_browser_session = original

        assert response.status_code == 200
        assert locked == ["locked"]

    def test_a_session_row_that_cannot_be_locked_fails_visibly(self, monkeypatch):
        """A hand-back that quietly did not happen is worse than one that
        visibly failed: the stylist is about to turn the screen around."""
        client = csrf_client()
        create_owned_design_id(client)

        def unavailable(request):
            raise WorkspaceCoordinationError("the browser session could not be locked")

        monkeypatch.setattr("sitara.designs.services._lock_browser_session", unavailable)
        response = end(client)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "design_workspace_unavailable"
        # And it did not claim a hand-back it had not performed.
        assert DESIGN_SESSION_KEY in client.session


class TestTheIdleReleaseCoordinates:
    """The timeout's release is a WRITE, and has to coordinate like one.

    Dropping the pointer looks like a read-path tidy-up, but SessionMiddleware
    persists this request's whole session snapshot at response time. Unlocked,
    a concurrent — properly locked — create can have its brand-new pointer
    overwritten by our stale snapshot, stranding the design it just made. That
    is the exact hazard ``_resolve_for_create``'s lock exists to prevent, so
    the idle branch takes the same lock.
    """

    def test_the_idle_release_locks_the_browser_session_row(self, settings):
        """Asserted directly rather than by scheduling a real race, for the
        same reason the hand-back's lock is: that race cannot be made
        deterministic in a test."""
        import sitara.designs.services as services

        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client = csrf_client()
        design_id = create_owned_design_id(client)
        go_idle(design_id)

        locked: list[str] = []
        original = services._lock_browser_session

        def record(request):
            row, data = original(request)
            locked.append("locked")
            return row, data

        services._lock_browser_session = record
        try:
            response = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())
        finally:
            services._lock_browser_session = original

        assert response.status_code == 404
        assert locked == ["locked"]

    def test_an_ordinary_read_does_not_pay_for_it(self, settings):
        """Only the idle branch takes the lock. Every other read is the
        lightweight path its docstring promises, and this is what holds it to
        that — a row lock on every design GET would be a real cost on the one
        request a stylist makes most."""
        import sitara.designs.services as services

        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 3600
        client = csrf_client()
        design_id = create_owned_design_id(client)

        locked: list[str] = []
        original = services._lock_browser_session

        def record(request):
            row, data = original(request)
            locked.append("locked")
            return row, data

        services._lock_browser_session = record
        try:
            response = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())
        finally:
            services._lock_browser_session = original

        assert response.status_code == 200
        assert locked == []

    def test_a_release_that_cannot_lock_declines_to_write(self, settings, monkeypatch):
        """Declining to write beats writing unlocked.

        The workspace still must not resolve — it IS idle — but nothing is
        released, so the pointer survives for the next locked path to finish
        the job with."""
        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client = csrf_client()
        design_id = create_owned_design_id(client)
        _, plaintext = create_reference_upload_grant(Design.objects.get(pk=design_id))
        pointer = client.session[DESIGN_SESSION_KEY]
        go_idle(design_id)

        def unavailable(request):
            raise WorkspaceCoordinationError("the browser session could not be locked")

        monkeypatch.setattr("sitara.designs.services._lock_browser_session", unavailable)
        response = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())

        assert response.status_code == 404
        assert client.session[DESIGN_SESSION_KEY] == pointer
        assert resolve_reference_upload_grant(plaintext) is not None

        # And the next locked path does finish it, so declining is a deferral
        # rather than a leak.
        monkeypatch.undo()
        create_owned_design_id(client)
        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(plaintext)

    def test_it_will_not_clear_a_pointer_that_has_moved_on(self, settings):
        """Another tab already started a new workspace, under the lock.

        That pointer is not ours to clear: clearing it would strand the design
        that tab just made, which is precisely the outcome the lock was taken
        to prevent."""
        import sitara.designs.services as services

        settings.WALK_IN_IDLE_TIMEOUT_SECONDS = 60
        client = csrf_client()
        design_id = create_owned_design_id(client)
        _, plaintext = create_reference_upload_grant(Design.objects.get(pk=design_id))
        pointer = client.session[DESIGN_SESSION_KEY]
        go_idle(design_id)

        original = services._lock_browser_session

        def moved_on(request):
            row, data = original(request)
            # What the locked row looks like once another tab has committed a
            # workspace of its own.
            return row, {**data, DESIGN_SESSION_KEY: str(uuid.uuid4())}

        services._lock_browser_session = moved_on
        try:
            response = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())
        finally:
            services._lock_browser_session = original

        # The idle workspace still does not resolve for this request...
        assert response.status_code == 404
        # ...but it released nothing and wrote nothing over the other tab.
        assert client.session[DESIGN_SESSION_KEY] == pointer
        assert resolve_reference_upload_grant(plaintext) is not None


class TestTheCostOfRevoking:
    def test_revoking_a_workspace_does_not_scale_its_queries_with_it(self):
        """One locked statement plus one update, whatever the workspace holds.

        Asserted as "the count does not grow", not as a magic number: the
        release also runs on the read path's idle branch, where per-design
        round trips would scale a hot path against a number nothing bounds."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries_to_revoke(design_count: int) -> int:
            client = csrf_client()
            first = create_owned_design_id(client)
            workspace = Design.objects.get(pk=first).design_session
            for _ in range(design_count - 1):
                Design.objects.create(design_session=workspace)
            for design in Design.objects.filter(design_session=workspace):
                create_reference_upload_grant(design)
            with CaptureQueriesContext(connection) as captured:
                revoked = revoke_grants_for_workspace(workspace)
            assert revoked == design_count
            return len(captured)

        assert queries_to_revoke(2) == queries_to_revoke(8)


class TestRevocationMidUpload:
    """A code revoked while a photograph is in flight.

    ADR 0026 accepts a bearer-credential exposure specifically on the strength
    of revocation working, so "working" has to include the case where the
    revoke lands between resolving the code and committing the row."""

    def test_a_code_revoked_mid_upload_cannot_still_land_a_photograph(self):
        """Liveness used to be checked once, before the decode and the storage
        write — long enough for a hand-back or the idle timeout to land in the
        middle of a slow upload. Re-checked under the design row lock now,
        immediately before the row is written."""
        client = csrf_client()
        design_id = create_owned_design_id(client)
        design = Design.objects.get(pk=design_id)
        grant, _ = create_reference_upload_grant(design)
        # What a hand-back does, arriving while the upload is mid-flight.
        ReferenceUploadGrant.objects.filter(pk=grant.pk).update(revoked_at=timezone.now())

        with pytest.raises(InspirationUploadError) as caught:
            create_inspiration_upload(
                design,
                _upload_file(),
                rights_acknowledged=True,
                require_live_grant=grant,
            )

        assert caught.value.code == "grant_unusable"
        assert design.inspiration_uploads.count() == 0

    def test_an_upload_with_no_grant_behind_it_is_unaffected(self):
        """The iPad's own upload path passes no grant and must not acquire a
        new way to fail."""
        client = csrf_client()
        design_id = create_owned_design_id(client)
        design = Design.objects.get(pk=design_id)

        upload = create_inspiration_upload(design, _upload_file(), rights_acknowledged=True)

        assert upload.pk is not None


class TestWhatSurvivesAHandBack:
    """Who can still reach the work afterwards, stated as a decision.

    Both halves are pinned deliberately, because the answer differs by caller
    and the difference is the whole of ADR 0027 section 1."""

    def test_the_shop_can_still_reach_its_own_work_afterwards(self):
        """Recorded as the decision it is, rather than left implicit.

        A signed-in account keeps every design it has produced, because the
        shop owns them (ADR 0027 §1) — so a design URL still in this browser's
        history still resolves after a hand-back. That residual exposure is
        ACCEPTED AND BOUNDED, not removed, and this pins it so a future change
        has to face it deliberately rather than discover it."""
        client, _ = signed_in_client()
        design_id = create_owned_design_id(client)

        assert end(client).status_code == 200

        # The next thing this browser starts is a fresh workspace...
        assert DESIGN_SESSION_KEY not in client.session
        # ...but the shop's own work is still the shop's.
        assert client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip()).status_code == 200

    def test_an_anonymous_browser_loses_the_workspace_completely(self):
        """The other half of the same decision. With no account there is no
        ownership to fall back on, so a handed-back design is gone from this
        browser — one indistinguishable 404, same as one that never existed."""
        client = csrf_client()
        design_id = create_owned_design_id(client)

        assert end(client).status_code == 200

        response = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())
        unknown = client.get(f"{DESIGNS_URL}{uuid.uuid4()}/", REMOTE_ADDR=unique_ip())

        assert response.status_code == unknown.status_code == 404
        assert response.json() == unknown.json()
