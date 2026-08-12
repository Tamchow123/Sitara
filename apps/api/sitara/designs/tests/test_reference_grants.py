"""Reference upload grants: the bearer credential and its bounds (ADR 0026).

The grant is deliberately a bearer credential — whoever can see the iPad's
screen can photograph the QR code and use it — and the phase accepts that
exposure rather than pretending to remove it. What these tests hold to account
is everything that BOUNDS it: scope, lifetime, revocability, the digest-only
row, and the single indistinguishable answer that stops the code being used to
probe for designs.
"""

import hashlib

import pytest
from django.utils import timezone

from sitara.designs.grant_service import (
    GrantUnusable,
    resolve_reference_upload_grant,
    revoke_reference_upload_grants,
)
from sitara.designs.models import Design, ReferenceUploadGrant

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_owned_design_id,
    csrf_client,
    send_json,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("inmemory_storage")]


def grants_url(design_id) -> str:
    return f"{DESIGNS_URL}{design_id}/reference-grants/"


def expire(design_id) -> None:
    """Age a grant past its expiry.

    Both timestamps move, not just ``expires_at``: the row carries a CHECK that
    an expiry follows its creation, and shoving only the expiry into the past
    would violate it — correctly. A grant that expired before it existed is not
    a state the product can reach, so a test must not manufacture one to prove
    a point about a state it can.
    """
    now = timezone.now()
    ReferenceUploadGrant.objects.filter(design_id=design_id).update(
        created_at=now - timezone.timedelta(hours=2),
        expires_at=now - timezone.timedelta(hours=1),
    )


def mint(client, design_id, token=None):
    token = token or bootstrap_csrf(client)
    return send_json(client, "post", grants_url(design_id), {}, token=token)


class TestMinting:
    def test_the_owner_gets_a_secret_exactly_once(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = mint(client, design_id)

        assert response.status_code == 201, response.content
        grant = response.json()["grant"]
        assert set(grant) == {"id", "token", "expires_at", "slots_remaining"}
        assert len(grant["token"]) >= 40
        assert grant["slots_remaining"] == 3
        assert response["Cache-Control"] == "no-store"

    def test_the_row_stores_a_digest_and_never_the_plaintext(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)

        plaintext = mint(client, design_id).json()["grant"]["token"]

        row = ReferenceUploadGrant.objects.get(design_id=design_id)
        # Asserted by SHAPE and by the plaintext's absence, both: a digest that
        # merely looks right could still be an encoding of the secret.
        assert len(row.token_digest) == 64
        assert all(character in "0123456789abcdef" for character in row.token_digest)
        assert row.token_digest == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        assert plaintext not in row.token_digest
        for column in ReferenceUploadGrant._meta.get_fields():
            value = getattr(row, column.name, None)
            assert plaintext != value

    def test_each_grant_is_unique(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        first = mint(client, design_id).json()["grant"]["token"]
        second = mint(client, design_id).json()["grant"]["token"]
        assert first != second

    def test_minting_revokes_the_previous_code(self):
        """Re-opening the panel must not leave an older photographed code live.

        Two working codes for one design is two bearer credentials in the wild
        when the stylist believes there is one."""
        client = csrf_client()
        design_id = create_owned_design_id(client)
        first = mint(client, design_id).json()["grant"]["token"]

        mint(client, design_id)

        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(first)

    def test_an_expiry_before_its_creation_is_a_database_error(self):
        """The constraint that makes the expiry meaningful.

        Written because the natural way to test expiry — shove ``expires_at``
        into the past — is exactly what this refuses, and a reader hitting that
        should find the reason here rather than in a failing assertion."""
        from django.db import IntegrityError, transaction

        client = csrf_client()
        design_id = create_owned_design_id(client)
        mint(client, design_id)
        row = ReferenceUploadGrant.objects.get(design_id=design_id)

        with pytest.raises(IntegrityError), transaction.atomic():
            ReferenceUploadGrant.objects.filter(pk=row.pk).update(
                expires_at=row.created_at - timezone.timedelta(seconds=1)
            )

    def test_the_expiry_is_the_configured_window(self, settings):
        settings.REFERENCE_UPLOAD_GRANT_TTL_SECONDS = 900
        client = csrf_client()
        design_id = create_owned_design_id(client)

        mint(client, design_id)

        row = ReferenceUploadGrant.objects.get(design_id=design_id)
        window = (row.expires_at - row.created_at).total_seconds()
        assert 890 <= window <= 910

    def test_a_design_with_no_free_slots_is_refused_rather_than_given_a_dead_code(self, settings):
        settings.MAX_INSPIRATION_IMAGES = 0
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = mint(client, design_id)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "inspiration_limit_reached"
        assert not ReferenceUploadGrant.objects.exists()

    def test_minting_requires_csrf(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        response = client.post(grants_url(design_id))
        assert response.status_code == 403
        assert not ReferenceUploadGrant.objects.exists()

    def test_a_foreign_design_is_the_same_404_as_a_nonexistent_one(self):
        import uuid as uuid_module

        owner = csrf_client()
        design_id = create_owned_design_id(owner)
        stranger = csrf_client()
        stoken = bootstrap_csrf(stranger)

        foreign = send_json(stranger, "post", grants_url(design_id), {}, token=stoken)
        missing = send_json(stranger, "post", grants_url(uuid_module.uuid4()), {}, token=stoken)

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()
        assert not ReferenceUploadGrant.objects.exists()

    def test_there_is_no_way_to_read_a_grant_back(self):
        """A minted secret is returned once and is then unrecoverable.

        Asserted against the ROUTE rather than against the client's UI: the
        collection answers POST and DELETE and nothing else, so there is no GET
        to list grants and no per-grant detail route to fetch one."""
        client = csrf_client()
        design_id = create_owned_design_id(client)
        grant = mint(client, design_id).json()["grant"]

        listing = client.get(grants_url(design_id))
        assert listing.status_code == 405

        detail = client.get(f"{grants_url(design_id)}{grant['id']}/")
        assert detail.status_code == 404


class TestTheSecretIsNeverLogged:
    def test_no_log_record_carries_the_plaintext(self, caplog):
        client = csrf_client()
        design_id = create_owned_design_id(client)

        with caplog.at_level("DEBUG"):
            plaintext = mint(client, design_id).json()["grant"]["token"]
            # The whole lifecycle, not only the mint: resolution and revocation
            # are the two places a "helpful" debug line would most likely be
            # added later.
            resolve_reference_upload_grant(plaintext)
            revoke_reference_upload_grants(Design.objects.get(pk=design_id))
            with pytest.raises(GrantUnusable):
                resolve_reference_upload_grant(plaintext)

        assert plaintext not in caplog.text
        digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        assert digest not in caplog.text
        # The lifecycle IS logged — safe identifiers only, so an operator can
        # still see that a handoff happened.
        assert "reference upload grant issued" in caplog.text
        assert str(design_id) in caplog.text

    def test_a_failed_resolution_logs_nothing_about_the_attempt(self, caplog):
        with caplog.at_level("DEBUG"):
            with pytest.raises(GrantUnusable):
                resolve_reference_upload_grant("a-code-somebody-guessed")
        assert "a-code-somebody-guessed" not in caplog.text


class TestResolution:
    def _live_grant(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        return design_id, mint(client, design_id).json()["grant"]["token"]

    def test_a_live_grant_resolves_to_its_own_design(self):
        design_id, plaintext = self._live_grant()
        grant = resolve_reference_upload_grant(plaintext)
        assert str(grant.design_id) == design_id

    @pytest.mark.parametrize("bad", ["", "   ", "not-a-real-code", "x" * 600])
    def test_an_unknown_code_is_refused(self, bad):
        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(bad)

    @pytest.mark.parametrize("bad", [None, 12, [], {}, True])
    def test_a_non_string_is_refused_rather_than_raising(self, bad):
        # Totality: a malformed body must become a controlled refusal, never a
        # TypeError that escapes as an HTML 500.
        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(bad)

    def test_an_expired_grant_is_refused(self):
        design_id, plaintext = self._live_grant()
        expire(design_id)
        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(plaintext)

    def test_a_revoked_grant_is_refused(self):
        design_id, plaintext = self._live_grant()
        revoke_reference_upload_grants(Design.objects.get(pk=design_id))
        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(plaintext)

    def test_expired_revoked_and_unknown_are_one_indistinguishable_outcome(self):
        """No attribute, message or type separates the three.

        This is CLAUDE.md section 15's enumeration rule applied to a new
        identifier: a caller must not learn that a design exists but its code
        lapsed. The endpoint-level version of this assertion lives with the
        upload endpoint; this is the service-level one."""
        design_a, expired = self._live_grant()
        expire(design_a)
        design_b, revoked = self._live_grant()
        revoke_reference_upload_grants(Design.objects.get(pk=design_b))

        raised = []
        for candidate in (expired, revoked, "never-existed"):
            with pytest.raises(GrantUnusable) as excinfo:
                resolve_reference_upload_grant(candidate)
            raised.append(excinfo.value)

        assert {type(error) for error in raised} == {GrantUnusable}
        assert {str(error) for error in raised} == {""}
        assert {tuple(error.args) for error in raised} == {()}
        assert all(not vars(error) for error in raised)


class TestRevocation:
    def test_the_owner_can_stop_accepting_photographs(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        plaintext = mint(client, design_id).json()["grant"]["token"]
        token = bootstrap_csrf(client)

        response = send_json(client, "delete", grants_url(design_id), {}, token=token)

        assert response.status_code == 200
        assert response.json() == {"revoked": 1}
        with pytest.raises(GrantUnusable):
            resolve_reference_upload_grant(plaintext)

    def test_revoking_nothing_succeeds(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        token = bootstrap_csrf(client)
        response = send_json(client, "delete", grants_url(design_id), {}, token=token)
        assert response.status_code == 200
        assert response.json() == {"revoked": 0}

    def test_revoking_is_idempotent(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        mint(client, design_id)
        token = bootstrap_csrf(client)

        first = send_json(client, "delete", grants_url(design_id), {}, token=token)
        second = send_json(client, "delete", grants_url(design_id), {}, token=token)

        assert first.json() == {"revoked": 1}
        assert second.json() == {"revoked": 0}

    def test_revocation_requires_csrf(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        mint(client, design_id)
        response = client.delete(grants_url(design_id))
        assert response.status_code == 403
        assert ReferenceUploadGrant.objects.filter(revoked_at__isnull=True).count() == 1

    def test_a_stranger_cannot_revoke_and_learns_nothing(self):
        owner = csrf_client()
        design_id = create_owned_design_id(owner)
        mint(owner, design_id)
        stranger = csrf_client()
        stoken = bootstrap_csrf(stranger)

        response = send_json(stranger, "delete", grants_url(design_id), {}, token=stoken)

        assert response.status_code == 404
        assert ReferenceUploadGrant.objects.filter(revoked_at__isnull=True).count() == 1

    def test_one_design_s_revocation_does_not_touch_another_s(self):
        client = csrf_client()
        first = create_owned_design_id(client, title="one")
        second = create_owned_design_id(client, title="two")
        mint(client, first)
        live = mint(client, second).json()["grant"]["token"]
        token = bootstrap_csrf(client)

        send_json(client, "delete", grants_url(first), {}, token=token)

        assert str(resolve_reference_upload_grant(live).design_id) == second


class TestMintThrottling:
    """Ownership and CSRF decide WHO may mint; neither bounds how often.

    Every mint writes a durable row that only the retention purge removes, so a
    loop against one's own design would otherwise cost an attacker nothing and
    grow a table without bound."""

    def test_minting_is_refused_after_the_session_limit(self, settings):
        settings.REFERENCE_UPLOAD_GRANT_MINT_LIMIT = 2
        settings.REFERENCE_UPLOAD_GRANT_MINT_IP_LIMIT = 1000
        client = csrf_client()
        design_id = create_owned_design_id(client)
        token = bootstrap_csrf(client)

        assert mint(client, design_id, token).status_code == 201
        assert mint(client, design_id, token).status_code == 201
        third = mint(client, design_id, token)

        assert third.status_code == 429
        assert third.json()["error"]["code"] == "grant_rate_limited"
        assert int(third["Retry-After"]) > 0
        # Refused before anything was written.
        assert ReferenceUploadGrant.objects.count() == 2

    def test_the_throttle_fails_closed_on_a_cache_outage(self, settings, monkeypatch):
        """An infrastructure fault refuses the request — as a 503, never as a
        429, so a cache outage is not reported to the caller (or to whoever
        watches the 429 rate) as their own abuse."""
        from sitara.accounts.rate_limits import RateLimitUnavailable

        def unreachable(*args, **kwargs):
            raise RateLimitUnavailable("cache down")

        monkeypatch.setattr("sitara.designs.grant_service.check_and_count", unreachable)
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = mint(client, design_id)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "grant_throttle_unavailable"
        assert not ReferenceUploadGrant.objects.exists()

    def test_the_throttle_runs_after_ownership_so_it_reveals_nothing(self, settings):
        """A throttled caller must still be unable to tell an owned design from
        one that does not exist — so the ownership 404 comes first."""
        import uuid as uuid_module

        settings.REFERENCE_UPLOAD_GRANT_MINT_LIMIT = 1
        stranger = csrf_client()
        stoken = bootstrap_csrf(stranger)
        owner = csrf_client()
        owned = create_owned_design_id(owner)

        foreign = send_json(stranger, "post", grants_url(owned), {}, token=stoken)
        missing = send_json(stranger, "post", grants_url(uuid_module.uuid4()), {}, token=stoken)

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()


class TestLifecycle:
    def test_only_one_grant_can_be_live_at_a_time(self):
        """The database's own word on it, independent of any lock.

        Written against the constraint rather than the service so a future
        caller that forgets the row lock still cannot produce two live codes."""
        from django.db import IntegrityError, transaction

        client = csrf_client()
        design_id = create_owned_design_id(client)
        mint(client, design_id)

        with pytest.raises(IntegrityError), transaction.atomic():
            ReferenceUploadGrant.objects.create(
                design_id=design_id,
                token_digest="f" * 64,
                created_at=timezone.now(),
                expires_at=timezone.now() + timezone.timedelta(minutes=15),
            )

    def test_a_grant_is_deleted_with_its_design(self):
        """No orphan rows, and nothing for the retention purge to special-case:
        the FK cascades, so a purged design takes its grants with it."""
        client = csrf_client()
        design_id = create_owned_design_id(client)
        mint(client, design_id)

        Design.objects.filter(pk=design_id).delete()

        assert not ReferenceUploadGrant.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_mints_leave_exactly_one_live_grant():
    """The race the row lock exists for, exercised rather than assumed.

    A double-tapped "Generate code" button on a laggy shop iPad is the realistic
    trigger. Without the lock both requests see nothing to revoke and both
    insert, leaving the customer's already-photographed code alive after the
    stylist believed they had replaced it — which would break the exact control
    ADR 0026 accepts the bearer exposure on.
    """
    import threading

    from django.db import connection

    from sitara.designs.grant_service import create_reference_upload_grant
    from sitara.designs.models import DesignSession

    session = DesignSession.objects.create()
    design = Design.objects.create(design_session=session)

    barrier = threading.Barrier(2, timeout=10)
    failures = []
    minted = []

    def worker():
        try:
            barrier.wait()
            _, plaintext = create_reference_upload_grant(design)
            minted.append(plaintext)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            failures.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert failures == []
    assert len(minted) == 2
    live = ReferenceUploadGrant.objects.filter(design=design, revoked_at__isnull=True)
    assert live.count() == 1
    # And it is the LAST one minted that survives, not an arbitrary winner: the
    # loser's row exists but is revoked, so the code it handed out is dead.
    survivor = live.get()
    dead = [
        plaintext
        for plaintext in minted
        if hashlib.sha256(plaintext.encode("utf-8")).hexdigest() != survivor.token_digest
    ]
    assert len(dead) == 1
    with pytest.raises(GrantUnusable):
        resolve_reference_upload_grant(dead[0])
