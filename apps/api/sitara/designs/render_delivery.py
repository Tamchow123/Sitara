"""Claiming and delivering one owner's copy of their own render (Phase 19, §8).

This module owns the *durable claim* — the part that makes a redelivered Celery
task not send a second real email. It does not construct mail: every message in
this application is built by :mod:`sitara.media.account_delivery`, the single
choke point, which takes the owning ``User`` row and derives the address itself.
That split is deliberate. §8.1 calls the recipient rule the most important in the
phase, and it is enforced structurally: this module resolves a *row*, never an
address string, and hands the row on.

The HTTP surface lives in :mod:`sitara.designs.views`; nothing here reads a
request.

**Nothing private is logged.** Log lines carry a safe operation name, the
``DesignVersion`` UUID, the kind and an exception *type* — never the address,
note text, attachment bytes or message body, and never exception text, which for
a rejected recipient embeds the address itself.
"""

from __future__ import annotations

import logging
import smtplib

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from sitara.media.account_delivery import (
    AccountEmailAttachmentTooLarge,
    AccountEmailDisabled,
    AccountEmailRecipientUnavailable,
    recipient_for,
    require_account_email_enabled,
    send_render_attachment,
)
from sitara.media.annotation_render import (
    ANNOTATED_FILENAME,
    PLAIN_FILENAME,
    RENDER_CONTENT_TYPE,
    compose_annotated_png,
)
from sitara.media.exceptions import DesignAnnotationRenderError

from .annotation_service import read_annotation_document
from .models import DesignRenderDelivery, DesignVersion

logger = logging.getLogger(__name__)


class RenderNotReady(Exception):
    """The version has no permanent image yet, so there is nothing to send."""


def owner_of(version: DesignVersion):
    """The account that owns this version, or ``None`` for an anonymous one.

    The only path is ``DesignVersion -> Design -> DesignSession -> user``, the
    same derivation ownership filtering uses. Returns the ROW; turning it into an
    address is the choke point's business, not this module's."""
    session = version.design.design_session
    return session.user if session.user_id else None


def require_render_ready(version: DesignVersion) -> None:
    if not version.image_storage_key:
        raise RenderNotReady("this version has no permanent image yet")


def _claim(version: DesignVersion, kind: str) -> DesignRenderDelivery | None:
    """Take the durable claim on this send, or return None to no-op.

    Committed BEFORE the message is handed over, which is the whole point: a
    redelivered task must be able to observe that someone already holds or
    completed this send. The parent ``DesignVersion`` row is locked rather than
    the delivery row, because ``select_for_update()`` cannot lock a row that does
    not exist yet and the first claim has none — two workers would otherwise both
    find nothing and both insert. Same shape, and the same ``.first()`` idiom and
    ``IntegrityError`` backstop, as
    :func:`sitara.designs.annotation_service.replace_annotation_document`.
    """
    now = timezone.now()
    ttl = settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS
    with transaction.atomic():
        locked_version = DesignVersion.objects.select_for_update().filter(pk=version.pk).first()
        if locked_version is None:
            # Purged between the task's own lookup and this lock.
            return None

        row = (
            DesignRenderDelivery.objects.select_for_update()
            .filter(design_version=version, kind=kind)
            .first()
        )
        if row is None:
            try:
                return DesignRenderDelivery.objects.create(
                    design_version=version,
                    kind=kind,
                    state=DesignRenderDelivery.CLAIMED,
                    attempt_count=1,
                    claimed_at=now,
                )
            except IntegrityError:
                # The parent lock should make a duplicate insert unreachable;
                # the unique constraint is the backstop, and losing that race
                # means another worker holds the claim — a no-op here, not an
                # error to escape into Celery's default handler.
                return None

        if row.state in (DesignRenderDelivery.SENT, DesignRenderDelivery.RETRY_EXHAUSTED):
            # Terminal, always — including long after the claim has gone stale.
            # Without this the stale branch below would treat a COMPLETED send as
            # an abandoned one and mail the owner a second copy months later.
            return None

        if (now - row.claimed_at).total_seconds() < ttl:
            # Another worker holds this send and has not run out of time.
            return None

        # The claim is stale, so its worker is presumed dead. Retry once — never
        # twice. The cap is checked BEFORE incrementing, so a row already at the
        # ceiling becomes terminal instead of producing a third attempt.
        if row.attempt_count >= DesignRenderDelivery.MAX_SEND_ATTEMPTS:
            row.state = DesignRenderDelivery.RETRY_EXHAUSTED
            row.save(update_fields=["state", "updated_at"])
            logger.warning(
                "render_delivery.retry_exhausted",
                extra={"design_version_id": str(version.pk), "kind": kind},
            )
            return None

        row.attempt_count += 1
        row.claimed_at = now
        row.save(update_fields=["attempt_count", "claimed_at", "updated_at"])
        return row


def _compose(version: DesignVersion, kind: str) -> tuple[bytes, str]:
    """The rendered attachment and its server-owned filename."""
    annotated = kind == DesignRenderDelivery.ANNOTATED
    # Read even for the plain render: read_annotation_document returns a
    # synthetic empty document for an unannotated version, and
    # compose_annotated_png ignores the items entirely when annotated=False, so
    # one code path costs one discarded query and removes a branch.
    document, _revision, _updated_at = read_annotation_document(version)
    render = compose_annotated_png(
        storage_key=version.image_storage_key, document=document, annotated=annotated
    )
    return render.content, ANNOTATED_FILENAME if annotated else PLAIN_FILENAME


def _log_failure(version_id, kind: str, exc: BaseException) -> None:
    logger.warning(
        "render_delivery.send_failed",
        extra={
            "design_version_id": str(version_id),
            "kind": kind,
            "exception_type": type(exc).__name__,
        },
    )


def deliver_render(design_version_id, kind: str) -> str:
    """The Celery task body: compose one render and mail it to its owner.

    Takes UUIDs, not a payload — no address, no bytes and no URL crosses the
    queue, where they would rest in Redis in the clear and survive any broker
    inspection. Everything is re-derived here from database state.

    Returns a short outcome string for the task log. Every no-op path returns
    rather than raising, because a redelivery observing "already sent" is the
    system working correctly, not a failure to alert on."""
    if kind not in {DesignRenderDelivery.PLAIN, DesignRenderDelivery.ANNOTATED}:
        # Not reachable from the endpoints, which pass a literal; a corrupt or
        # hand-crafted queue message must still not render something arbitrary.
        logger.warning("render_delivery.unknown_kind", extra={"kind": str(kind)[:32]})
        return "unknown_kind"

    version = (
        DesignVersion.objects.filter(pk=design_version_id)
        .select_related("design__design_session__user")
        .first()
    )
    if version is None:
        # Purged or deleted between enqueue and execution. Nothing to send and
        # nobody to tell.
        return "version_gone"

    try:
        # Re-checked here, not trusted from the endpoint: a queued task can
        # outlive the configuration and the ownership that admitted it.
        require_account_email_enabled()
        require_render_ready(version)
        owner = owner_of(version)
        recipient_for(owner)  # raises if this workspace has no account address
    except (AccountEmailDisabled, RenderNotReady, AccountEmailRecipientUnavailable) as exc:
        _log_failure(version.pk, kind, exc)
        return "precondition_failed"

    claim = _claim(version, kind)
    if claim is None:
        return "noop"

    try:
        content, filename = _compose(version, kind)
        send_render_attachment(
            user=owner, filename=filename, content=content, content_type=RENDER_CONTENT_TYPE
        )
    except (
        DesignAnnotationRenderError,
        AccountEmailAttachmentTooLarge,
        AccountEmailDisabled,
        AccountEmailRecipientUnavailable,
        smtplib.SMTPException,
        SoftTimeLimitExceeded,
        OSError,
        ValueError,
    ) as exc:
        # smtplib.SMTPException is named explicitly even though it subclasses
        # OSError: this codebase writes its exception tuples out, and the
        # ancestry is an implementation detail nobody should have to recall.
        # SoftTimeLimitExceeded is caught rather than left to propagate because
        # the render budget is sized to absorb a loaded worker, which makes a
        # soft timeout the most foreseeable real failure here — it should be as
        # visible in this module's own logs as every other failure, not a
        # differently shaped Celery error.
        #
        # The claim deliberately stays in place. Releasing it would let an
        # immediate redelivery retry without bound; leaving it means the
        # stale-claim path retries exactly once, then goes terminal.
        _log_failure(version.pk, kind, exc)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - deliberate task-boundary containment
        # The Celery task boundary, and the one place broad containment is
        # justified here. An exception escaping this function is logged by
        # Celery's own default handler, which embeds the exception's string
        # form; smtplib.SMTPRecipientsRefused and friends put the rejected
        # ADDRESS in that string, and config/logging.py's JsonFormatter copies
        # record.getMessage() verbatim. An unforeseen exception type escaping
        # here would therefore write the owner's address into exactly the
        # structured log stream this module exists to keep address-free.
        # Contained, logged by type only, and reported as a failure the bounded
        # retry already handles.
        _log_failure(version.pk, kind, exc)
        return "failed"

    # Marked sent only AFTER the backend accepted the message. The other order
    # would record a send that never happened, turning a crash into silent loss;
    # this order turns the same crash into at most one duplicate to the owner's
    # own address, which §8.5 accepts deliberately.
    try:
        claim.state = DesignRenderDelivery.SENT
        claim.sent_at = timezone.now()
        claim.save(update_fields=["state", "sent_at", "updated_at"])
    except Exception as exc:  # noqa: BLE001 - see the task-boundary note above
        # The narrow window §8.5 is about, and the one place a duplicate becomes
        # LIKELY rather than merely possible: the owner has the message, but the
        # marker still says `claimed`, so the stale-claim path will send once
        # more after the TTL. Reported under its own outcome and its own log
        # line rather than folded into "failed", because an operator seeing this
        # should expect a duplicate — it is not the same event as a send that
        # never happened.
        logger.warning(
            "render_delivery.sent_but_unrecorded",
            extra={
                "design_version_id": str(version.pk),
                "kind": kind,
                "exception_type": type(exc).__name__,
            },
        )
        return "sent_unrecorded"

    logger.info(
        "render_delivery.sent",
        extra={"design_version_id": str(version.pk), "kind": kind},
    )
    return "sent"
