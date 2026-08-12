"""The account concept gallery ships switched off (Phase 22, ADR 0027).

Not deleted — switched off. Everything ADR 0024 built is still here and comes
back with one flag. What changed is who is holding the device: on a shop-floor
iPad one account serves many walk-in customers in a day, so a list of every
concept the shop has produced is, on that screen, a list of other customers'
concepts shown to whoever is sitting there now.

Three things these tests hold to account:

* the refusal is a CONTROLLED one, not a 404 pretending the surface never
  existed, because an operator can switch it back on;
* it refuses before it reads anything, so a disabled deployment cannot be used
  to probe and does no work;
* it gates READING the list only. Creating a design still works, because the
  walk-in flow starts there and gating it would end the product rather than the
  gallery.
"""

import uuid

import pytest

from sitara.designs.models import Design

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


@pytest.fixture(autouse=True)
def gallery_off(settings):
    """This suite is the one that owns the disabled path, so it overrides the
    package fixture that turns the gallery on for everybody else."""
    settings.ACCOUNT_GALLERY_ENABLED = False


class TestTheRefusal:
    def test_a_signed_in_owner_is_refused_with_a_controlled_code(self):
        client, _ = signed_in_client()
        create_owned_design_id(client)

        response = client.get(DESIGNS_URL, REMOTE_ADDR=unique_ip())

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "gallery_disabled"
        assert response["Cache-Control"] == "no-store"

    def test_it_is_not_a_404(self):
        """A 404 would say the surface was never there. It was, and an operator
        can bring it back — saying otherwise would send someone looking for a
        bug instead of a setting."""
        client, _ = signed_in_client()

        response = client.get(DESIGNS_URL, REMOTE_ADDR=unique_ip())

        assert response.status_code != 404

    def test_the_message_names_no_setting_and_no_design(self):
        client, _ = signed_in_client()
        create_owned_design_id(client, title="Ayesha, June wedding")

        body = client.get(DESIGNS_URL, REMOTE_ADDR=unique_ip()).json()

        message = body["error"]["message"]
        assert "ACCOUNT_GALLERY_ENABLED" not in message
        assert "Ayesha" not in str(body)

    def test_an_anonymous_caller_gets_the_same_answer_as_an_owner(self):
        """The gate runs before any identity or ownership work, so it cannot be
        used to learn whether the caller has anything to list."""
        owner, _ = signed_in_client()
        create_owned_design_id(owner)
        stranger = csrf_client()

        owned = owner.get(DESIGNS_URL, REMOTE_ADDR=unique_ip())
        anonymous = stranger.get(DESIGNS_URL, REMOTE_ADDR=unique_ip())

        assert owned.status_code == anonymous.status_code == 503
        assert owned.content == anonymous.content

    def test_a_nonsense_page_is_refused_by_the_gate_rather_than_validated(self):
        """The gate is BEFORE the page parameters are read: a disabled
        deployment must not still answer questions about what it would have
        accepted."""
        client, _ = signed_in_client()

        response = client.get(f"{DESIGNS_URL}?limit=banana", REMOTE_ADDR=unique_ip())

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "gallery_disabled"

    def test_it_does_not_create_a_workspace(self):
        client = csrf_client()

        client.get(DESIGNS_URL, REMOTE_ADDR=unique_ip())

        assert not Design.objects.exists()


class TestWhatStaysOpen:
    """The walk-in flow runs entirely on surfaces the gate does not touch."""

    def test_a_design_can_still_be_created(self):
        client = csrf_client()

        design_id = create_owned_design_id(client)

        assert Design.objects.filter(pk=design_id).exists()

    def test_a_design_can_still_be_read_by_its_owner(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = client.get(f"{DESIGNS_URL}{design_id}/", REMOTE_ADDR=unique_ip())

        assert response.status_code == 200
        assert response.json()["id"] == design_id

    def test_a_draft_can_still_be_saved(self):
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = create_owned_design_id(client)

        response = send_json(
            client, "patch", f"{DESIGNS_URL}{design_id}/", {"title": "New title"}, token=token
        )

        assert response.status_code == 200

    def test_a_foreign_design_is_still_an_indistinguishable_404(self):
        """The gate must not change what a stranger learns about a design that
        is not theirs."""
        owner = csrf_client()
        owned = create_owned_design_id(owner)
        stranger = csrf_client()

        foreign = stranger.get(f"{DESIGNS_URL}{owned}/", REMOTE_ADDR=unique_ip())
        missing = stranger.get(f"{DESIGNS_URL}{uuid.uuid4()}/", REMOTE_ADDR=unique_ip())

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()


class TestTheFlagIsTheOnlyThingThatOpensIt:
    def test_being_signed_in_is_not_enough(self):
        client, _ = signed_in_client()
        create_owned_design_id(client)

        assert client.get(DESIGNS_URL, REMOTE_ADDR=unique_ip()).status_code == 503

    def test_turning_it_on_restores_the_gallery_unchanged(self, settings):
        """Gated, not deleted: everything ADR 0024 built is still there."""
        client, _ = signed_in_client()
        design_id = create_owned_design_id(client, title="A concept")

        settings.ACCOUNT_GALLERY_ENABLED = True
        response = client.get(DESIGNS_URL, REMOTE_ADDR=unique_ip())

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["designs"][0]["id"] == design_id

    def test_it_ships_defaulting_to_off(self):
        """Asserted against the SHIPPED declaration, not the running value.

        Reading `django.conf.settings` here would only tell us what this test
        run's environment happens to say, and a developer `.env` that switched
        the gallery on would turn a real regression into a passing test — the
        same trap CLAUDE.md §20 already warns about for the live-generation
        flags. What must not change without a decision is the default in the
        source, so that is what this reads."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[3] / "config" / "settings.py").read_text(
            encoding="utf-8"
        )

        assert 'ACCOUNT_GALLERY_ENABLED = env_bool("ACCOUNT_GALLERY_ENABLED", default=False)' in (
            source
        )


class TestWhatTheFrontendIsTold:
    def test_the_public_config_advertises_the_flag(self, settings):
        settings.ACCOUNT_GALLERY_ENABLED = False
        client = csrf_client()

        body = client.get("/api/v1/config/public", REMOTE_ADDR=unique_ip()).json()

        assert body["account_gallery_enabled"] is False

    def test_it_follows_the_setting_rather_than_being_hard_coded(self, settings):
        settings.ACCOUNT_GALLERY_ENABLED = True
        client = csrf_client()

        body = client.get("/api/v1/config/public", REMOTE_ADDR=unique_ip()).json()

        assert body["account_gallery_enabled"] is True

    def test_the_advertisement_is_not_the_boundary(self, settings):
        """§10: a route guard is not authorisation. A client that ignores the
        advertisement, or lies about it, still cannot read the list."""
        settings.ACCOUNT_GALLERY_ENABLED = False
        client, _ = signed_in_client()
        create_owned_design_id(client)

        assert client.get(DESIGNS_URL, REMOTE_ADDR=unique_ip()).status_code == 503
