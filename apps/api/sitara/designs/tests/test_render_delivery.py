"""Account render email delivery — the machinery, with no HTTP surface.

The endpoints are covered separately. What is proved here is the part that runs
after the request has gone: that a redelivered task does not send a second real
email, that a dead worker's send is retried at most once, that the durable
marker holds nothing private, and that nothing in the suite ever opens SMTP.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.mail import EmailMessage, get_connection
from django.core.mail.backends import locmem
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from sitara.designs import render_delivery
from sitara.designs.models import (
    Design,
    DesignAnnotationDocument,
    DesignRenderDelivery,
    DesignSession,
    DesignVersion,
)
from sitara.designs.render_delivery import deliver_render
from sitara.designs.tasks import (
    HARD_TIME_LIMIT_SECONDS,
    SOFT_TIME_LIMIT_SECONDS,
    send_design_render,
)
from sitara.media import account_delivery

from .utils import (
    create_ready_design_version,
    run_simultaneously,
    synthetic_original,
    unique_email,
)

pytestmark = pytest.mark.django_db

NOTE_TEXT = "Raise the neckline and lengthen the dupatta border."


@pytest.fixture(autouse=True)
def delivery_enabled(settings):
    """The gate is OFF by default in the shipped settings, so every test that
    expects a send must open it. Opened here and closed explicitly by the test
    that proves the gate works, rather than the reverse."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    settings.DEFAULT_FROM_EMAIL = "concepts@sitara.example"


def owned_version(*, email: str | None = None, with_image: bool = True) -> DesignVersion:
    """A ready version owned by a real account, with its image actually stored."""
    user = get_user_model().objects.create_user(
        email=email or unique_email(), password="test-password-123"
    )
    session = DesignSession.objects.create(user=user)
    design = Design.objects.create(design_session=session, title="Test design")
    version = create_ready_design_version(design.id, with_storage_objects=False)
    if with_image:
        storages["design_images"].save(version.image_storage_key, ContentFile(synthetic_original()))
    return version


def refined_version(original: DesignVersion) -> DesignVersion:
    """The one refinement a design may have, with its image actually stored.

    Built here rather than through ``create_ready_design_version`` because a
    version 2 row must satisfy ``designs_designversion_v2_requires_parent`` and
    must not reuse version 1's storage key."""
    design_id = original.design_id
    version = DesignVersion.objects.create(
        design_id=design_id,
        version_number=2,
        design_spec={"schema_version": 1},
        design_spec_schema_version=1,
        design_spec_template_version="v1",
        design_spec_provider="fixture",
        design_spec_model="fixture-model",
        design_spec_generated_at=timezone.now(),
        image_prompt="A refined prompt.",
        prompt_builder_version="3.0.0",
        parent_version=original,
        refinement_request={"schema_version": 1, "change_type": "colour_story", "note": ""},
        refinement_request_schema_version=1,
        refinement_request_sha256="d" * 64,
        image_storage_key=f"design-images/{design_id}/v2/original.webp",
        image_sha256="e" * 64,
        image_size_bytes=1000,
        image_width=original.image_width,
        image_height=original.image_height,
        thumbnail_storage_key=f"design-images/{design_id}/v2/thumbnail.webp",
        thumbnail_sha256="f" * 64,
        thumbnail_size_bytes=100,
        thumbnail_width=384,
        thumbnail_height=512,
        image_processor_version="1.0.0",
        image_ingested_at=timezone.now(),
    )
    storages["design_images"].save(version.image_storage_key, ContentFile(synthetic_original()))
    return version


def reserve(version, kind: str = DesignRenderDelivery.PLAIN, *, name: str = "") -> int:
    """Reserve a send exactly as the endpoint does, and return its epoch."""
    epoch = render_delivery.reserve_send(version, kind, requested_name=name)
    assert epoch is not None, "the reservation was refused"
    return epoch


def send_once(version, kind: str = DesignRenderDelivery.PLAIN, *, name: str = "") -> str:
    """Both steps a real send takes: the endpoint reserves, then the task runs.

    A task without a reservation now sends nothing at all — deliberately, so a
    corrupt or replayed queue message cannot mail a copy nobody asked for — which
    means a test that calls the task body alone proves nothing about a real send.
    Returns "noop" for a refused reservation so a caller can assert on either."""
    epoch = render_delivery.reserve_send(version, kind, requested_name=name)
    if epoch is None:
        return "noop"
    return deliver_render(version.pk, kind, epoch)


def anonymous_version() -> DesignVersion:
    session = DesignSession.objects.create(user=None)
    design = Design.objects.create(design_session=session, title="Anonymous design")
    version = create_ready_design_version(design.id, with_storage_objects=False)
    storages["design_images"].save(version.image_storage_key, ContentFile(synthetic_original()))
    return version


def annotate(version: DesignVersion, note: str = NOTE_TEXT) -> DesignAnnotationDocument:
    return DesignAnnotationDocument.objects.create(
        design_version=version,
        schema_version=1,
        revision=1,
        document={
            "schema_version": 1,
            "image_width": version.image_width,
            "image_height": version.image_height,
            "items": [
                {
                    "id": "3f2a1c8e-0000-4000-8000-000000000001",
                    "type": "pin",
                    "geometry": {"point": {"x": 0.5, "y": 0.25}},
                    "note": note,
                    "palette": "terracotta",
                    "created_order": 1,
                }
            ],
        },
    )


# ---------------------------------------------------------------------------
# Zero SMTP, asserted rather than assumed
# ---------------------------------------------------------------------------


def test_the_suite_resolves_the_locmem_email_backend(settings):
    """Django's setup_test_environment() swaps the configured backend for
    locmem across the whole session. That is what makes "tests make zero SMTP
    connections" structural rather than a habit — so it is asserted, not
    trusted."""
    assert settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"


def test_the_default_connection_is_a_locmem_backend_not_smtp():
    """The setting could be right while a caller passed an explicit backend.
    This checks the object a send would actually use."""
    connection = get_connection()
    assert isinstance(connection, locmem.EmailBackend)
    assert "smtp" not in type(connection).__module__


def test_the_shipped_default_gate_is_closed():
    """The autouse fixture opens the gate for these tests; the SHIPPED default
    must still be closed, or a deployment would send mail because a credential
    happened to be present.

    Read out of the settings source rather than by calling ``env_bool`` with a
    ``default=False`` of the test's own — that version of this test passed
    whatever the module actually shipped, which is no test at all."""
    import ast
    import inspect

    import config.settings as shipped

    for node in ast.walk(ast.parse(inspect.getsource(shipped))):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            getattr(target, "id", None) == "ACCOUNT_EMAIL_DELIVERY_ENABLED"
            for target in node.targets
        ):
            continue
        call = node.value
        assert isinstance(call, ast.Call), "the gate must be parsed from the environment"
        assert call.func.id == "env_bool", "the gate must use the strict boolean parser"
        default = next(kw.value for kw in call.keywords if kw.arg == "default")
        assert default.value is False, "ACCOUNT_EMAIL_DELIVERY_ENABLED must default to false"
        return
    pytest.fail("no ACCOUNT_EMAIL_DELIVERY_ENABLED assignment found in config.settings")


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_delivery_sends_one_message_to_the_owning_account():
    address = unique_email()
    version = owned_version(email=address)

    assert send_once(version) == "sent"

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [address]
    assert len(message.attachments) == 1
    filename, content, content_type = message.attachments[0]
    assert filename == "sitara-concept.png"
    assert content_type == "image/png"
    assert content[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_annotated_kind_attaches_the_annotated_filename():
    version = owned_version()
    annotate(version)

    assert send_once(version, DesignRenderDelivery.ANNOTATED) == "sent"

    assert mail.outbox[0].attachments[0][0] == "sitara-concept-annotations.png"


def test_a_completed_send_is_recorded_as_terminal():
    version = owned_version()
    send_once(version)

    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.state == DesignRenderDelivery.SENT
    assert row.attempt_count == 1
    assert row.sent_at is not None
    # The lifetime counter, which is what the cap is checked against.
    assert row.send_count == 1
    assert row.attempt_epoch == 1


def test_sending_the_plain_render_does_not_block_the_annotated_one():
    """The two surfaces send different artefacts. Keying the marker on the
    version alone would let either one permanently consume the other's only
    chance to be sent."""
    version = owned_version()
    annotate(version)

    assert send_once(version) == "sent"
    assert send_once(version, DesignRenderDelivery.ANNOTATED) == "sent"

    assert len(mail.outbox) == 2
    assert {row.kind for row in DesignRenderDelivery.objects.all()} == {"plain", "annotated"}


# ---------------------------------------------------------------------------
# Idempotency under Celery redelivery
# ---------------------------------------------------------------------------


def test_invoking_the_task_body_twice_sends_exactly_one_message():
    """acks_late plus reject_on_worker_lost means a worker killed after the
    hand-off but before its ack gets this task again. SMTP is not resumable, so
    the second invocation must produce no second email."""
    version = owned_version()

    epoch = reserve(version)
    first = deliver_render(version.pk, DesignRenderDelivery.PLAIN, epoch)
    second = deliver_render(version.pk, DesignRenderDelivery.PLAIN, epoch)

    assert (first, second) == ("sent", "noop")
    assert len(mail.outbox) == 1


def test_the_registered_task_is_idempotent_too():
    """Through the actual Celery task, not only the service function, so a
    wiring mistake in tasks.py cannot hide behind a well-behaved core."""
    version = owned_version()

    epoch = reserve(version)
    send_design_render(version.pk, DesignRenderDelivery.PLAIN, epoch)
    send_design_render(version.pk, DesignRenderDelivery.PLAIN, epoch)

    assert len(mail.outbox) == 1


def test_a_claim_inside_its_ttl_is_a_noop(settings):
    """Another worker holds this send and has not run out of time."""
    settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS = 900
    version = owned_version()
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=timezone.now(),
    )

    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN) == "noop"
    assert mail.outbox == []


def test_two_successive_stale_claims_retry_once_and_then_stop(settings):
    """The negative case, which is the one that matters.

    Asserting only "a stale claim retries" would pass against an implementation
    that increments attempt_count without ever gating on it — the unbounded
    -duplicate hazard the cap exists to close. So this drives TWO successive
    beyond-TTL redeliveries and asserts the second sends nothing."""
    settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS = 60
    version = owned_version()
    stale = timezone.now() - timedelta(seconds=3600)
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=stale,
    )

    # First beyond-TTL redelivery: the worker is presumed dead, so retry once.
    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN) == "sent"
    assert len(mail.outbox) == 1
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.attempt_count == 2

    # Make that second attempt look dead too, and redeliver again.
    DesignRenderDelivery.objects.filter(pk=row.pk).update(
        state=DesignRenderDelivery.CLAIMED, sent_at=None, send_count=0, claimed_at=stale
    )

    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN) == "noop"
    assert len(mail.outbox) == 1, "a third attempt sent a third email"
    row.refresh_from_db()
    assert row.state == DesignRenderDelivery.RETRY_EXHAUSTED
    assert row.attempt_count == 2


def test_a_sent_marker_older_than_the_ttl_still_never_sends_again(settings):
    """The case that actually needs the terminal-state check.

    Found by mutation, not by inspection: deleting the ``state in (SENT,
    RETRY_EXHAUSTED)`` branch from ``_claim`` left all 33 tests here green,
    because for a freshly-sent row the TTL check happens to return None too and
    masks it. The gap only opens once a sent row's claim goes stale — then the
    stale-claim branch would treat a COMPLETED send as a dead worker's, bump
    attempt_count to 2 and send the owner a second copy of something they
    already received, possibly months later. `sent` is "no-op, always", and this
    is the test that says so."""
    settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS = 60
    version = owned_version()
    long_ago = timezone.now() - timedelta(days=90)
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.SENT,
        attempt_count=1,
        send_count=1,
        claimed_at=long_ago,
        sent_at=long_ago,
    )

    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN) == "noop"
    assert mail.outbox == []
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.state == DesignRenderDelivery.SENT
    assert row.attempt_count == 1, "a completed send was retried as if abandoned"


def test_a_terminal_no_op_is_visible_in_the_log(settings, caplog):
    """Otherwise this outcome leaves no trace anywhere.

    The endpoint answers 202 whether a send delivered or no-opped, so without a
    log line an operator watching a run of sends that all quietly did nothing
    sees exactly what a run that all delivered looks like. ``retry_exhausted``
    was already logged; the two branches that ``return None`` were not.

    The record carries the version UUID, the kind and the state — never the
    address, which is what the surrounding tests in this file check is absent
    from every other line this module emits."""
    settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS = 60
    version = owned_version()
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.SENT,
        attempt_count=1,
        send_count=1,
        claimed_at=timezone.now(),
        sent_at=timezone.now(),
    )

    with caplog.at_level(logging.INFO, logger="sitara.designs.render_delivery"):
        assert deliver_render(version.pk, DesignRenderDelivery.PLAIN) == "noop"

    records = [r for r in caplog.records if r.message == "render_delivery.already_terminal"]
    assert len(records) == 1
    assert records[0].design_version_id == str(version.pk)
    assert records[0].delivery_state == DesignRenderDelivery.SENT
    assert "@" not in caplog.text


def test_a_terminal_retry_exhausted_marker_never_sends_again(settings):
    settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS = 60
    version = owned_version()
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.RETRY_EXHAUSTED,
        attempt_count=2,
        claimed_at=timezone.now() - timedelta(seconds=3600),
    )

    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN) == "noop"
    assert mail.outbox == []


def test_the_database_refuses_a_third_attempt():
    """The application caps attempt_count; this is the backstop that makes a
    third attempt impossible even through a stray write."""
    version = owned_version()
    row = DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=2,
        claimed_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DesignRenderDelivery.objects.filter(pk=row.pk).update(attempt_count=3)


def test_the_database_refuses_a_counted_send_without_a_timestamp():
    """The timestamp is coupled to the COUNT, not to the state, and it has to be.

    A row that has sent once and been pressed again is `pending` while still
    legitimately carrying the timestamp of the copy that did go out, so the old
    state-coupled constraint would reject every repeat send. What survives is the
    invariant that was always the point: a row claiming a delivery has a time for
    it, and a row that has never delivered has none."""
    version = owned_version()
    row = DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DesignRenderDelivery.objects.filter(pk=row.pk).update(send_count=1)


def test_the_database_refuses_a_timestamp_without_a_counted_send():
    """The other direction, which a one-sided constraint would have missed."""
    version = owned_version()
    row = DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DesignRenderDelivery.objects.filter(pk=row.pk).update(sent_at=timezone.now())


def test_a_repeat_send_keeps_the_earlier_timestamp_while_pending():
    """The exact shape the old constraint forbade, now pinned as legal."""
    version = owned_version()
    assert send_once(version) == "sent"
    reserve(version)

    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.state == DesignRenderDelivery.PENDING
    assert row.send_count == 1
    assert row.sent_at is not None, "the first delivery's time was discarded"


def test_the_database_refuses_a_pending_row_that_claims_an_attempt():
    """A reservation has attempted nothing; a claim has attempted something. The
    two cannot disagree, or `_claim` would read a reservation as mid-flight."""
    version = owned_version()
    row = DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.PENDING,
        attempt_count=0,
        claimed_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DesignRenderDelivery.objects.filter(pk=row.pk).update(attempt_count=1)


def test_the_database_refuses_a_claimed_row_that_claims_no_attempt():
    version = owned_version()
    row = DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DesignRenderDelivery.objects.filter(pk=row.pk).update(attempt_count=0)


def test_the_database_refuses_a_zero_epoch():
    """Epochs are 1-based, and `_claim` compares them for equality. A zero would
    make the default argument of a hand-crafted queue message match a real row."""
    version = owned_version()
    row = DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DesignRenderDelivery.objects.filter(pk=row.pk).update(attempt_epoch=0)


def test_one_marker_per_version_and_kind():
    version = owned_version()
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DesignRenderDelivery.objects.create(
            design_version=version,
            kind=DesignRenderDelivery.PLAIN,
            state=DesignRenderDelivery.CLAIMED,
            attempt_count=1,
            claimed_at=timezone.now(),
        )


# ---------------------------------------------------------------------------
# The marker holds nothing private
# ---------------------------------------------------------------------------


def test_the_marker_carries_no_address_or_note_shaped_field():
    """A durable row is a worse place to leak an address than a cache key: it
    survives into backups, dumps and any admin view.

    The marker is state, counters, timestamps and — since Phase 21 — exactly one
    piece of the owner's own text, the filename they chose for their copy. The
    narrowing is deliberate and is recorded in ADR 0022 rather than left as a
    claim that has quietly become false. What has NOT changed is the part this
    test exists for: no address, no note text, no rendered bytes, no storage key,
    no signed URL. The exact field set is pinned so a fourth kind of value cannot
    arrive here without someone deciding to."""
    field_names = {field.name for field in DesignRenderDelivery._meta.get_fields()}
    assert field_names == {
        "id",
        "design_version",
        "kind",
        "state",
        "attempt_count",
        "attempt_epoch",
        "send_count",
        "requested_filename",
        "claimed_at",
        "sent_at",
        "created_at",
        "updated_at",
    }
    forbidden = ("email", "address", "recipient", "note", "body", "subject", "content", "to")
    for name in field_names:
        assert not any(word in name.lower() for word in forbidden), name


def test_the_stored_filename_column_matches_the_sanitiser_bound():
    """A migration must not import a runtime constant, so the column carries a
    literal — which means the two can drift apart silently. Pinned here so
    changing either one forces the other."""
    field = DesignRenderDelivery._meta.get_field("requested_filename")
    assert field.max_length == account_delivery.MAX_FILENAME_BASE_LENGTH
    assert field.max_length == DesignRenderDelivery.REQUESTED_FILENAME_MAX_LENGTH


def test_the_marker_is_not_registered_in_admin():
    """§B asks for the stored name to be read-only in admin and absent from list
    displays. Not registering the model at all is the stronger answer: there is
    no form to make read-only and no column to omit, so the name cannot be read
    beside an account by someone browsing.

    If this table ever does need an admin, this test is the prompt to make the
    filename read-only and keep it out of list_display at the same moment."""
    from django.contrib import admin

    assert DesignRenderDelivery not in admin.site._registry


def test_no_stored_marker_contains_the_recipient_address():
    """The structural check above pins the field set; this one proves the
    values, in case a future JSON or text field slips in."""
    address = unique_email()
    version = owned_version(email=address)
    send_once(version)

    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    serialised = " ".join(str(getattr(row, f.name, "")) for f in row._meta.fields)
    assert address not in serialised
    assert address.split("@")[0] not in serialised


# ---------------------------------------------------------------------------
# Fail-closed preconditions
# ---------------------------------------------------------------------------


def test_a_closed_gate_sends_nothing(settings):
    """Re-checked inside the task, not merely at the endpoint: a queued task can
    outlive the configuration that admitted it."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    version = owned_version()

    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN) == "precondition_failed"
    assert mail.outbox == []
    assert not DesignRenderDelivery.objects.exists()


def test_an_anonymous_owner_has_no_recipient_and_nothing_is_sent():
    """No fallback, no prompt, no silent success — an anonymous workspace has no
    account address and the send simply does not happen."""
    version = anonymous_version()

    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN) == "precondition_failed"
    assert mail.outbox == []
    assert not DesignRenderDelivery.objects.exists()


def test_no_delivery_entry_point_accepts_a_recipient_address():
    """The open-relay guard is structural, not a validation rule.

    An endpoint that mails an attachment to a caller-chosen address is an open
    relay, and the cheapest way to guarantee this one cannot quietly become that
    is to give the code no way to express it: both entry points take
    identifiers, and the address is derived inside. A future parameter named
    anything address-shaped fails here before it can reach a view."""
    import inspect

    address_shaped = ("email", "address", "recipient", "to", "cc", "bcc", "reply")
    for entry_point in (deliver_render, send_design_render.run):
        parameters = inspect.signature(entry_point).parameters
        for name in parameters:
            assert not any(
                word in name.lower() for word in address_shaped
            ), f"{entry_point.__name__} accepts a caller-supplied {name!r}"


def test_the_owner_of_an_anonymous_workspace_is_nobody():
    version = anonymous_version()
    assert render_delivery.owner_of(version) is None


def test_a_version_without_a_permanent_image_sends_nothing():
    user = get_user_model().objects.create_user(email=unique_email(), password="test-password-123")
    session = DesignSession.objects.create(user=user)
    design = Design.objects.create(design_session=session, title="Pending")
    version = DesignVersion.objects.create(
        design_id=design.id,
        version_number=1,
        design_spec={"schema_version": 1},
        design_spec_schema_version=1,
        design_spec_template_version="v1",
        design_spec_provider="fixture",
        design_spec_model="fixture-model",
        design_spec_generated_at=timezone.now(),
        image_prompt="A test prompt.",
        prompt_builder_version="3.0.0",
    )

    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN) == "precondition_failed"
    assert mail.outbox == []


def test_a_missing_version_is_a_quiet_noop():
    """Purged between enqueue and execution. Nothing to send, nobody to tell."""
    import uuid

    assert deliver_render(uuid.uuid4(), DesignRenderDelivery.PLAIN) == "version_gone"
    assert mail.outbox == []


def test_an_unknown_kind_renders_nothing():
    version = owned_version()
    assert deliver_render(version.pk, "everything") == "unknown_kind"
    assert mail.outbox == []


def test_an_oversized_attachment_is_refused_rather_than_sent(settings):
    """Refused with the claim left standing, not handed to a relay that would
    reject it — or accept it and fail where the user never sees."""
    settings.ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES = 1024
    version = owned_version()

    assert send_once(version) == "failed"
    assert mail.outbox == []
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.state == DesignRenderDelivery.CLAIMED
    assert row.sent_at is None
    # A failure consumes no part of the owner's allowance.
    assert row.send_count == 0


def test_a_failed_send_is_retried_at_most_once_then_terminal(settings):
    """The bounded path out of a repeated failure: the claim stays, the stale
    -claim branch retries once, and the second failure goes terminal."""
    settings.ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES = 1024
    settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS = 60
    version = owned_version()

    assert send_once(version) == "failed"
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    epoch = row.attempt_epoch
    DesignRenderDelivery.objects.filter(pk=row.pk).update(
        claimed_at=timezone.now() - timedelta(seconds=3600)
    )
    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, epoch) == "failed"
    row.refresh_from_db()
    assert row.attempt_count == 2

    DesignRenderDelivery.objects.filter(pk=row.pk).update(
        claimed_at=timezone.now() - timedelta(seconds=3600)
    )
    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, epoch) == "noop"
    row.refresh_from_db()
    assert row.state == DesignRenderDelivery.RETRY_EXHAUSTED
    assert mail.outbox == []


# ---------------------------------------------------------------------------
# Message content and privacy
# ---------------------------------------------------------------------------


def test_the_message_carries_no_note_text_title_or_url():
    """The attachment carries the content; the message carries none of it."""
    version = owned_version()
    version.design.title = "Ayesha's mehndi lehenga"
    version.design.save(update_fields=["title"])
    annotate(version)

    send_once(version, DesignRenderDelivery.ANNOTATED)

    message = mail.outbox[0]
    haystack = f"{message.subject}\n{message.body}"
    assert NOTE_TEXT not in haystack
    assert "Ayesha" not in haystack
    assert "http://" not in haystack and "https://" not in haystack
    assert version.image_storage_key not in haystack
    assert str(version.pk) not in haystack
    assert str(version.design_id) not in haystack


def test_the_message_is_plain_text_with_a_fixed_subject_and_body():
    version = owned_version()
    send_once(version)

    message = mail.outbox[0]
    assert message.subject == account_delivery.RENDER_SUBJECT
    assert message.body == account_delivery.RENDER_BODY
    assert message.content_subtype == "plain"
    assert message.alternatives == [] if hasattr(message, "alternatives") else True


def test_the_from_address_is_the_configured_server_one(settings):
    version = owned_version()
    send_once(version)
    assert mail.outbox[0].from_email == settings.DEFAULT_FROM_EMAIL


def test_delivery_logs_carry_no_recipient_or_note_text(caplog):
    address = unique_email()
    version = owned_version(email=address)
    annotate(version)

    with caplog.at_level(logging.INFO, logger=render_delivery.__name__):
        send_once(version, DesignRenderDelivery.ANNOTATED)

    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert logged.strip(), "the delivery path logged nothing at all"
    assert address not in logged
    assert NOTE_TEXT not in logged
    assert version.image_storage_key not in logged


def test_a_failure_log_carries_only_the_exception_type(settings, caplog):
    settings.ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES = 1024
    address = unique_email()
    version = owned_version(email=address)

    with caplog.at_level(logging.WARNING, logger=render_delivery.__name__):
        send_once(version)

    records = [r for r in caplog.records if r.message == "render_delivery.send_failed"]
    assert records, "a refused attachment logged nothing"
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "AccountEmailAttachmentTooLarge" in logged
    assert address not in logged
    assert version.image_storage_key not in logged


# ---------------------------------------------------------------------------
# Bounded execution
# ---------------------------------------------------------------------------


def test_the_task_is_a_registered_celery_task_with_bounded_limits():
    """The decorator itself is asserted, not just the attributes.

    The first version of this test opened with `assert options is None or
    callable(options)`, which passes whether or not `@shared_task` is applied —
    it was decoration, not a check. Removing the decorator must fail here."""
    from celery import current_app

    assert isinstance(send_design_render, Task)
    assert current_app.tasks.get("sitara.designs.tasks.send_design_render") is not None
    assert SOFT_TIME_LIMIT_SECONDS > 0
    assert HARD_TIME_LIMIT_SECONDS > SOFT_TIME_LIMIT_SECONDS
    assert send_design_render.soft_time_limit == SOFT_TIME_LIMIT_SECONDS
    assert send_design_render.time_limit == HARD_TIME_LIMIT_SECONDS


def test_the_claim_ttl_outlasts_the_hard_time_limit(settings):
    """Otherwise a still-running task's claim could expire and a redelivery
    would send a second copy while the first is still in flight — the exact
    duplicate the marker exists to prevent."""
    assert settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS > HARD_TIME_LIMIT_SECONDS


def test_startup_refuses_a_claim_ttl_that_does_not_outlast_the_hard_limit():
    """The inequality above is enforced at STARTUP, not only in this suite.

    All four inputs are independently operator-configurable, so a test that can
    only ever see the shipped defaults is not enough: an operator raising
    EMAIL_TIMEOUT to tolerate a slow relay could push the hard limit past the
    TTL and silently break the at-most-one-duplicate bound. Re-executing the
    settings module with a hostile environment proves the check fires."""
    import os
    import runpy

    from django.core.exceptions import ImproperlyConfigured

    hostile = dict(os.environ, ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS="10")
    with (
        mock.patch.dict(os.environ, hostile, clear=True),
        pytest.raises(ImproperlyConfigured) as caught,
    ):
        runpy.run_module("config.settings", run_name="not_main")

    message = str(caught.value)
    assert "ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS" in message
    assert "10" not in message, "the rejected value must never be echoed"


def test_the_task_does_not_autoretry():
    """An automatic retry here would multiply real emails; the bounded stale
    -claim path is the only retry."""
    assert getattr(send_design_render, "autoretry_for", ()) == ()
    assert send_design_render.acks_late is True


# ---------------------------------------------------------------------------
# Real send-time failures
#
# Every failure test above trips RenderTooLarge, which deliver_render raises
# ITSELF before the mail backend is reached — so none of them exercised the
# except tuple around the actual send. Deleting OSError from that tuple used to
# fail nothing. These do reach it.
# ---------------------------------------------------------------------------


def raising_send(exc):
    """Patch the constructed message so `.send()` raises like a real relay."""
    return mock.patch.object(EmailMessage, "send", side_effect=exc)


@pytest.mark.parametrize(
    "exc",
    [
        smtplib.SMTPRecipientsRefused({"someone@example.test": (550, b"No such user")}),
        smtplib.SMTPAuthenticationError(535, b"auth failed"),
        smtplib.SMTPServerDisconnected("connection lost"),
        ConnectionRefusedError("relay down"),
        SoftTimeLimitExceeded(),
    ],
)
def test_a_send_time_failure_is_contained_and_leaves_the_claim_standing(exc):
    version = owned_version()

    with raising_send(exc):
        assert send_once(version) == "failed"

    assert mail.outbox == []
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.state == DesignRenderDelivery.CLAIMED
    assert row.sent_at is None


def test_a_rejected_recipient_never_reaches_the_logs(caplog):
    """The sharpest version of the no-address-in-logs rule.

    `SMTPRecipientsRefused` carries the rejected address inside its own args, so
    anything that logs the exception's TEXT leaks it. If this escaped
    `deliver_render`, Celery's default failure handler would log exactly that
    string and the JSON formatter would ship it verbatim."""
    address = unique_email()
    version = owned_version(email=address)
    refusal = smtplib.SMTPRecipientsRefused({address: (550, b"No such user")})

    with caplog.at_level(logging.WARNING), raising_send(refusal):
        assert send_once(version) == "failed"

    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "SMTPRecipientsRefused" in logged, "the failure was not logged at all"
    assert address not in logged
    assert address.split("@")[0] not in logged


def test_an_unforeseen_exception_type_is_contained_at_the_task_boundary():
    """The catch-all exists so nothing escapes into Celery's default logger,
    which would print the exception's text. A type nobody anticipated must still
    be contained and logged by type only."""

    class UnforeseenError(Exception):
        pass

    version = owned_version()
    with raising_send(UnforeseenError("relay said something new")):
        assert send_once(version) == "failed"

    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.state == DesignRenderDelivery.CLAIMED


def failing_record():
    """Make recording a completed send fail, leaving the message already sent.

    Patched at the function rather than at ``Model.save``, which is where this
    hooked before the count arrived: recording is now one ``UPDATE`` carrying an
    ``F()`` increment, so there is no ``save(update_fields=[...])`` call left to
    intercept. What is being pinned is the OUTCOME when recording fails — the
    claim standing, the separate log line, the uncounted send — not which ORM call
    performs it."""
    return mock.patch.object(
        render_delivery, "_record_sent", side_effect=DatabaseError("write failed")
    )


def test_a_state_save_failure_after_a_successful_send_leaves_the_claim_standing():
    """The documented at-most-one-duplicate window, made explicit.

    If the message reaches the backend but recording that fact fails, the marker
    stays `claimed` and the stale-claim path will send once more. That is the
    trade section 8.5 chose over silent loss; this test pins it so the behaviour
    cannot change silently."""
    version = owned_version()

    with failing_record():
        assert send_once(version) == "sent_unrecorded"

    assert len(mail.outbox) == 1, "the message did reach the backend"
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.state == DesignRenderDelivery.CLAIMED
    assert row.sent_at is None
    # The send went uncounted, so the owner keeps an allowance they have partly
    # used. That is the right way round: over-counting would deny someone a copy
    # they never received.
    assert row.send_count == 0


def test_an_unrecorded_send_is_reported_separately_from_a_failure(caplog):
    """An operator seeing this should expect a duplicate; an operator seeing
    `send_failed` should expect none. Folding them into one outcome would lose
    exactly the distinction that matters at 3am."""
    address = unique_email()
    version = owned_version(email=address)

    with caplog.at_level(logging.WARNING), failing_record():
        send_once(version)

    messages = [r.message for r in caplog.records]
    assert "render_delivery.sent_but_unrecorded" in messages
    assert "render_delivery.send_failed" not in messages
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert address not in logged


# ---------------------------------------------------------------------------
# Boundaries and structural guarantees
# ---------------------------------------------------------------------------


def test_an_attachment_exactly_at_the_size_limit_is_accepted(settings):
    """The check is `>`, so the limit itself is allowed. Pinned so an accidental
    flip to `>=` — which would refuse a legitimate export sitting exactly on the
    boundary — fails here."""
    version = owned_version()
    send_once(version)
    exact = len(mail.outbox[0].attachments[0][1])

    mail.outbox.clear()
    DesignRenderDelivery.objects.all().delete()
    settings.ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES = exact

    assert send_once(version) == "sent"
    assert len(mail.outbox) == 1


def test_this_module_never_reaches_past_the_choke_point():
    """`render_delivery` resolves a User ROW and hands it on; it must not import
    Django's mail API at all. The tree-wide version of this guarantee, and the
    proof that it catches every spelling, lives beside the choke point in
    `sitara/media/tests/test_account_delivery.py`."""
    source = Path(render_delivery.__file__).read_text(encoding="utf-8")
    assert "django.core.mail" not in source


def test_the_database_refuses_an_unknown_state():
    """`choices` never reaches the database, and `_claim` reasons by exclusion —
    "not terminal, therefore claimed" — so an unexpected value would fall
    through into the stale-claim path and could send."""
    version = owned_version()
    row = DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DesignRenderDelivery.objects.filter(pk=row.pk).update(state="whatever")


def test_a_version_removed_before_the_claim_is_taken_sends_nothing():
    """`_claim` re-checks the parent under the lock because the version can be
    purged between the task's own lookup and the transaction. Nothing else
    exercises that branch."""
    version = owned_version()
    detached = DesignVersion.objects.get(pk=version.pk)
    DesignVersion.objects.filter(pk=version.pk).delete()

    assert render_delivery._claim(detached, DesignRenderDelivery.PLAIN, 1) is None
    assert not DesignRenderDelivery.objects.exists()
    assert mail.outbox == []


# ---------------------------------------------------------------------------
# Genuine concurrency
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_claims_produce_one_marker_and_one_email(settings):
    """The lock itself, not the transition table the lock protects.

    Every other test here drives states sequentially, which proves the state
    machine but cannot prove `select_for_update()` serialises anything —
    Django's default wrapped-transaction isolation makes real contention
    impossible. This one runs two threads on their own connections against a
    fresh (version, kind), which is the only way a regression that narrows the
    transaction boundary would be caught."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    settings.DEFAULT_FROM_EMAIL = "concepts@sitara.example"
    version = owned_version()
    mail.outbox.clear()

    results = run_simultaneously(
        [
            lambda: send_once(version),
            lambda: send_once(version),
        ]
    )

    assert sorted(results) == ["noop", "sent"], results
    assert DesignRenderDelivery.objects.filter(design_version=version).count() == 1
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.attempt_count == 1
    assert row.state == DesignRenderDelivery.SENT
    # One email, therefore one count — whichever thread got there first.
    assert row.send_count == 1
    assert len(mail.outbox) == 1


# ---------------------------------------------------------------------------
# The lifetime send allowance (Phase 21, ADR 0022)
#
# Categorically not a rate limit. The four limits in `enforce_send_throttles`
# refill; this one does not, and it lives on the durable row precisely so a cache
# eviction cannot hand back three more sends.
# ---------------------------------------------------------------------------


def test_the_allowance_permits_three_sends_and_refuses_the_fourth(settings):
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()

    for expected in (1, 2, 3):
        assert send_once(version) == "sent"
        assert len(mail.outbox) == expected

    with pytest.raises(render_delivery.SendLimitReached) as refused:
        render_delivery.reserve_send(version, DesignRenderDelivery.PLAIN)

    assert (refused.value.used, refused.value.limit) == (3, 3)
    assert len(mail.outbox) == 3, "the refused press still sent something"


def test_the_ceiling_is_a_setting_not_a_literal(settings):
    """An operator can lower it without a code change, which is the whole reason
    it is a setting — so a test drives a value other than the default."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 1
    version = owned_version()

    assert send_once(version) == "sent"
    with pytest.raises(render_delivery.SendLimitReached):
        render_delivery.reserve_send(version, DesignRenderDelivery.PLAIN)


def test_the_two_kinds_have_independent_allowances(settings):
    """Keyed on (version, kind), so spending the plain render's allowance must
    not touch the annotated one's — they are different artefacts."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 2
    version = owned_version()
    annotate(version)

    assert send_once(version) == "sent"
    assert send_once(version) == "sent"
    with pytest.raises(render_delivery.SendLimitReached):
        render_delivery.reserve_send(version, DesignRenderDelivery.PLAIN)

    assert send_once(version, DesignRenderDelivery.ANNOTATED) == "sent"
    assert send_once(version, DesignRenderDelivery.ANNOTATED) == "sent"
    assert len(mail.outbox) == 4


def test_a_refined_version_has_its_own_allowance(settings):
    """A refinement is a separate DesignVersion, so exhausting the original's
    allowance must not deny the refinement its own. With one refinement and two
    kinds this is a ceiling of four times the setting per design, which is worth
    seeing a test state."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 1
    original = owned_version()
    refinement = refined_version(original)

    assert send_once(original) == "sent"
    with pytest.raises(render_delivery.SendLimitReached):
        render_delivery.reserve_send(original, DesignRenderDelivery.PLAIN)

    assert send_once(refinement) == "sent"
    assert len(mail.outbox) == 2


def test_a_failed_send_does_not_consume_the_allowance(settings):
    """A relay failure is not the owner's doing. The rate limits still charge for
    the attempt, so repeated failure cannot be used to grind.

    Note what the owner has to wait for. A failed send leaves its claim standing
    deliberately — that is what bounds the automatic retry to one — and a claim
    inside its TTL reserves nothing, so an immediate second press does nothing
    visible even though the allowance is untouched. Unchanged in shape from before
    this phase, when a failed send blocked the render permanently instead of for
    the TTL, and strictly better; but it is a real wait and this test names it
    rather than hiding it behind an aged timestamp."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 1
    settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS = 60
    version = owned_version()

    with raising_send(smtplib.SMTPServerDisconnected("connection lost")):
        assert send_once(version) == "failed"

    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.send_count == 0
    assert render_delivery.send_allowance(version, DesignRenderDelivery.PLAIN) == (0, 1)

    # Immediately: the failed send's claim is still live, so there is nothing to
    # reserve and no email.
    assert send_once(version) == "noop"
    assert mail.outbox == []

    # Once that claim is no longer live, the allowance the failure did not spend
    # is still there to use.
    DesignRenderDelivery.objects.filter(pk=row.pk).update(
        claimed_at=timezone.now() - timedelta(seconds=3600)
    )
    assert send_once(version) == "sent"
    assert len(mail.outbox) == 1


def test_the_allowance_survives_a_cache_flush(settings):
    """The reason it is a column and not a cache key. An eviction must not hand
    back three more sends, and this is the test that says so."""
    from django.core.cache import cache

    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 1
    version = owned_version()
    assert send_once(version) == "sent"

    cache.clear()

    with pytest.raises(render_delivery.SendLimitReached):
        render_delivery.reserve_send(version, DesignRenderDelivery.PLAIN)


def test_send_allowance_reports_what_is_used_and_what_is_allowed(settings):
    """What the UI shows before the last send is spent, so the ceiling is never a
    surprise."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()

    assert render_delivery.send_allowance(version, DesignRenderDelivery.PLAIN) == (0, 3)
    send_once(version)
    assert render_delivery.send_allowance(version, DesignRenderDelivery.PLAIN) == (1, 3)
    # An untouched kind reports zero rather than inheriting the other's count.
    assert render_delivery.send_allowance(version, DesignRenderDelivery.ANNOTATED) == (0, 3)


# ---------------------------------------------------------------------------
# Epochs: telling a deliberate second send from a redelivered task
# ---------------------------------------------------------------------------


def test_a_second_press_sends_a_second_copy(settings):
    """The behaviour this phase exists to add. Before it, `sent` was terminal for
    the lifetime of the row and a second press was silently a no-op."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()

    assert send_once(version) == "sent"
    assert send_once(version) == "sent"

    assert len(mail.outbox) == 2
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert (row.attempt_epoch, row.send_count) == (2, 2)


def test_a_redelivered_task_from_an_earlier_press_sends_nothing(settings):
    """The other half, and the one that would silently mail duplicates if epochs
    were dropped: an old task resurfacing after a newer press must no-op even
    though the row has allowance left and is not in a terminal state."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()
    first_epoch = reserve(version)
    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, first_epoch) == "sent"

    second_epoch = reserve(version)
    assert second_epoch != first_epoch

    # The broker redelivers the FIRST press's task while the second is pending.
    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, first_epoch) == "noop"
    assert len(mail.outbox) == 1

    # ...and the second press still delivers, exactly once.
    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, second_epoch) == "sent"
    assert len(mail.outbox) == 2


def test_a_task_with_no_reservation_sends_nothing():
    """A task is never the first to touch this row now — the endpoint reserves
    before it queues — so a task that finds no row is a purge or a hand-crafted
    queue message, and creating a row here would mail a copy nobody asked for."""
    version = owned_version()

    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, 1) == "noop"
    assert mail.outbox == []
    assert not DesignRenderDelivery.objects.exists()


def test_a_second_press_while_one_is_in_flight_reserves_nothing(settings):
    """A mis-click, or an impatient one. Reserving would let a single press
    consume two epochs and, with them, two of the owner's three sends."""
    settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS = 900
    version = owned_version()
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=timezone.now(),
    )

    assert render_delivery.reserve_send(version, DesignRenderDelivery.PLAIN) is None
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.attempt_epoch == 1, "an in-flight send was given a second epoch"


def test_a_second_press_does_not_orphan_a_reservation_nobody_has_claimed_yet(settings):
    """The gap that a busy-check covering only `claimed` left open.

    A reservation nobody has picked up yet is not a reservation nobody ever will:
    its task may be sitting in the broker perfectly intact. Bumping the epoch here
    would orphan it — that task would later see a newer epoch and no-op — so if
    THIS press's own enqueue then failed, the owner would hold a 202 for a message
    that will never be sent, and nothing would retry it.

    Written against the state `reserve_send` itself leaves behind rather than a
    hand-built row, which is what the earlier in-flight test did and why this case
    slipped through: a hand-built row said `claimed`, and the real one says
    `pending`."""
    settings.ACCOUNT_EMAIL_SEND_RESERVATION_GRACE_SECONDS = 60
    version = owned_version()

    first = reserve(version)

    assert render_delivery.reserve_send(version, DesignRenderDelivery.PLAIN) is None
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.state == DesignRenderDelivery.PENDING
    assert row.attempt_epoch == first, "an unclaimed reservation was orphaned"

    # ...and the task the first press queued still delivers.
    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, first) == "sent"
    assert len(mail.outbox) == 1


def test_a_reservation_nobody_claimed_can_be_superseded_once_it_is_stale(settings):
    """The other side of that window, and the reason it is short.

    If the first press's enqueue failed outright, the row is left `pending` with no
    task behind it. Blocking further presses forever would strand the send; the
    grace window is deliberately a queue-latency number rather than the claim
    TTL, so the owner's next press works soon rather than in fifteen minutes."""
    settings.ACCOUNT_EMAIL_SEND_RESERVATION_GRACE_SECONDS = 60
    version = owned_version()
    first = reserve(version)
    DesignRenderDelivery.objects.filter(design_version=version).update(
        claimed_at=timezone.now() - timedelta(seconds=120)
    )

    second = render_delivery.reserve_send(version, DesignRenderDelivery.PLAIN)

    assert second == first + 1
    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, second) == "sent"
    assert len(mail.outbox) == 1


def test_the_reservation_grace_is_shorter_than_the_claim_ttl(settings):
    """They answer different questions and must not be conflated: the TTL has to
    outlast the task's hard time limit, the grace only has to outlast queue
    latency. Shipping them equal would reintroduce a fifteen-minute wait after a
    failed enqueue."""
    assert (
        settings.ACCOUNT_EMAIL_SEND_RESERVATION_GRACE_SECONDS
        < settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS
    )


def test_a_press_absorbed_by_a_late_completion_is_logged_loudly(settings, caplog):
    """The one outcome an operator most needs to be able to see.

    A deliberate press is reserved, an older and slower send then finishes first
    and takes the last of the allowance, and this press delivers nothing while the
    owner has been told "queued". It gets its own message at warning level, so it
    cannot be mistaken for an ordinary already-exhausted retry at info."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 1
    version = owned_version()
    epoch = reserve(version)
    DesignRenderDelivery.objects.filter(design_version=version).update(
        send_count=1, sent_at=timezone.now()
    )

    with caplog.at_level(logging.INFO, logger=render_delivery.__name__):
        assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, epoch) == "noop"

    messages = [record.message for record in caplog.records]
    assert "render_delivery.reserved_press_absorbed" in messages
    assert "render_delivery.allowance_spent" not in messages
    assert mail.outbox == []


def test_an_ordinary_exhausted_retry_is_not_logged_as_an_absorbed_press(settings, caplog):
    """The other half, so the louder line stays meaningful. A redelivery of a
    press whose render was already fully spent is unremarkable and stays at
    info."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 1
    version = owned_version()
    spent_marker(version, send_count=1)

    with caplog.at_level(logging.INFO, logger=render_delivery.__name__):
        assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, 1) == "noop"

    messages = [record.message for record in caplog.records]
    assert "render_delivery.allowance_spent" in messages
    assert "render_delivery.reserved_press_absorbed" not in messages


def test_a_send_completing_after_a_newer_press_still_counts(settings):
    """The awkward interleaving, and the reason the count is written even when the
    epoch has moved on: a message the backend accepted is mail, whoever pressed
    for it. The state and timestamp belong to the CURRENT press, so those are left
    alone — writing them would clobber a live reservation and skip that press."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()
    stale_epoch = reserve(version)
    render_delivery._claim(version, DesignRenderDelivery.PLAIN, stale_epoch)

    # A newer press arrives before the first worker records its send.
    DesignRenderDelivery.objects.filter(design_version=version).update(
        state=DesignRenderDelivery.PENDING, attempt_count=0, attempt_epoch=stale_epoch + 1
    )

    assert render_delivery._record_sent(version, DesignRenderDelivery.PLAIN, stale_epoch) == (
        "sent_superseded"
    )
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.send_count == 1, "a delivered message went uncounted"
    assert row.state == DesignRenderDelivery.PENDING, "a live reservation was clobbered"
    assert row.attempt_epoch == stale_epoch + 1


def test_a_task_whose_allowance_was_spent_while_it_waited_sends_nothing(settings):
    """Re-checked inside the task, not trusted from the endpoint — exactly as the
    capability gate and the ownership derivation are. A queued task can outlive
    the allowance that admitted it."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 1
    version = owned_version()
    epoch = reserve(version)
    DesignRenderDelivery.objects.filter(design_version=version).update(
        send_count=1, sent_at=timezone.now()
    )

    assert deliver_render(version.pk, DesignRenderDelivery.PLAIN, epoch) == "noop"
    assert mail.outbox == []


# ---------------------------------------------------------------------------
# The remembered name
# ---------------------------------------------------------------------------


def test_the_chosen_name_is_used_for_the_attachment(settings):
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()

    assert send_once(version, name="Rani lehenga") == "sent"

    assert mail.outbox[0].attachments[0][0] == "Rani lehenga.png"


def test_the_chosen_name_is_remembered_for_a_later_send(settings):
    """§3: pre-fill a later send with what the owner chose last time."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()
    send_once(version, name="Rani lehenga")

    assert render_delivery.remembered_filename(version, DesignRenderDelivery.PLAIN) == (
        "Rani lehenga"
    )
    # ...and a later send with no new choice reuses it rather than reverting to
    # the server default.
    assert send_once(version) == "sent"
    assert mail.outbox[1].attachments[0][0] == "Rani lehenga.png"


def test_a_new_name_replaces_the_remembered_one(settings):
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()
    send_once(version, name="First choice")
    send_once(version, name="Second choice")

    assert mail.outbox[1].attachments[0][0] == "Second choice.png"
    assert render_delivery.remembered_filename(version, DesignRenderDelivery.PLAIN) == (
        "Second choice"
    )


def test_nothing_is_remembered_until_something_is_chosen():
    version = owned_version()
    assert render_delivery.remembered_filename(version, DesignRenderDelivery.PLAIN) == ""
    send_once(version)
    assert render_delivery.remembered_filename(version, DesignRenderDelivery.PLAIN) == ""
    assert mail.outbox[0].attachments[0][0] == "sitara-concept.png"


def test_a_dangerous_name_is_sanitised_before_it_is_stored():
    """Sanitised on the way IN, not only on the way out. A value that lives in a
    durable row outlives every code path that reads it, so the column must never
    hold something a future reader could mishandle."""
    version = owned_version()
    reserve(version, name="../../etc/passwd\r\nBcc: attacker@example.test")

    assert render_delivery.remembered_filename(version, DesignRenderDelivery.PLAIN) == ""


def test_a_stored_name_never_grows_an_extension(settings):
    """The base is stored, so round-tripping it through several sends cannot
    produce "dress.png.png.png"."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()

    send_once(version, name="dress.png")
    send_once(version)
    send_once(version)

    assert render_delivery.remembered_filename(version, DesignRenderDelivery.PLAIN) == "dress"
    assert {message.attachments[0][0] for message in mail.outbox} == {"dress.png"}


def test_the_stored_name_is_never_logged(settings, caplog):
    """The same category as an annotation note: free text about a garment someone
    intends to wear."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()

    with caplog.at_level(logging.DEBUG):
        send_once(version, name="Ayesha mehndi secret")

    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert logged.strip(), "the delivery path logged nothing at all"
    assert "Ayesha" not in logged
    assert "mehndi" not in logged
    assert "secret" not in logged


def test_a_failure_log_carries_no_chosen_name(settings, caplog):
    """Including the failure paths, which is where private values usually escape."""
    settings.ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES = 1024
    version = owned_version()

    with caplog.at_level(logging.DEBUG):
        assert send_once(version, name="Ayesha mehndi secret") == "failed"

    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "render_delivery.send_failed" in logged
    assert "Ayesha" not in logged and "secret" not in logged


def test_deleting_the_design_removes_the_stored_name():
    """No separate cleanup code: the marker CASCADEs from the version, which
    CASCADEs from the design, which is what the retention purge deletes. Pinned
    so the column cannot outlive the design it describes."""
    version = owned_version()
    reserve(version, name="Rani lehenga")
    assert DesignRenderDelivery.objects.filter(design_version=version).exists()

    Design.objects.filter(pk=version.design_id).delete()

    assert not DesignRenderDelivery.objects.exists()


def test_the_name_does_not_cross_the_queue():
    """The chosen name is read from the row inside the task, never carried in the
    message: the broker is Redis, where it would rest in the clear and survive any
    broker inspection. The task signature is the structural guarantee."""
    import inspect

    parameters = inspect.signature(send_design_render.run).parameters
    for name in parameters:
        assert "name" not in name.lower(), name
        assert "filename" not in name.lower(), name


def spent_marker(version, *, send_count: int) -> DesignRenderDelivery:
    """A marker that has already delivered ``send_count`` copies."""
    now = timezone.now()
    return DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.SENT,
        attempt_count=1,
        send_count=send_count,
        claimed_at=now,
        sent_at=now,
    )


def attempt_a_send(version) -> str:
    """One press, reporting which of the three outcomes it got."""
    try:
        return "sent" if send_once(version) == "sent" else "noop"
    except render_delivery.SendLimitReached:
        return "refused"


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_presses_for_the_last_allowance_send_one_copy(settings):
    """The row lock, on the branch that most needs it.

    Sequential tests prove the arithmetic but cannot prove `select_for_update()`
    serialises anything — Django's default wrapped-transaction isolation makes
    real contention impossible. Two threads on their own connections race for a
    single remaining send.

    Note which layer resolves it, because it is not the one you would guess. Both
    reservations can legitimately succeed: the loser reads the same "two used" the
    winner did, since neither has sent yet, and the second reservation simply
    supersedes the first press's epoch. What makes the ceiling hold is the cap
    re-check inside `_claim`, which refuses a task whose allowance was spent while
    it waited. So the assertion that matters is the count of EMAILS, not the count
    of successful reservations — asserting the latter would have passed against an
    implementation that mailed two copies."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    settings.DEFAULT_FROM_EMAIL = "concepts@sitara.example"
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()
    spent_marker(version, send_count=2)
    mail.outbox.clear()

    results = run_simultaneously([lambda: attempt_a_send(version), lambda: attempt_a_send(version)])

    assert results.count("sent") == 1, results
    assert len(mail.outbox) == 1, "the last allowance delivered two copies"
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.send_count == 3, "the allowance was exceeded"


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_presses_past_the_ceiling_are_both_refused(settings):
    """The strict half: with nothing left, contention cannot manufacture a send.

    Both threads must be REFUSED here rather than one quietly no-opping, because a
    no-op is answered as "queued" and would tell an owner their copy is on its way
    when their allowance is gone."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    settings.DEFAULT_FROM_EMAIL = "concepts@sitara.example"
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    version = owned_version()
    spent_marker(version, send_count=3)
    mail.outbox.clear()

    results = run_simultaneously([lambda: attempt_a_send(version), lambda: attempt_a_send(version)])

    assert results == ["refused", "refused"], results
    assert mail.outbox == []
