"""Claiming and delivering one owner's copy of their own render (Phase 19, §8).

This module owns the *durable claim* — the part that makes a redelivered Celery
task not send a second real email. It does not construct mail: every message in
this application is built by :mod:`sitara.media.account_delivery`, the single
choke point, which takes the owning ``User`` row and derives the address itself.
That split is deliberate. §8.1 calls the recipient rule the most important in the
phase, and it is enforced structurally: this module resolves a *row*, never an
address string, and hands the row on.

The HTTP surface lives in :mod:`sitara.designs.views`. Everything here resolves
from rows and UUIDs and is safe to call off a request — with one deliberate
exception, :func:`enforce_send_throttles`, which needs the session user and the
client IP. It is co-located rather than hidden in the view because it guards
this module's own delivery flow, following ``designs.upload_service``'s
precedent of a request-reading throttle beside otherwise request-free services.

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
from django.db.models import F
from django.utils import timezone

from sitara.accounts.rate_limits import RateLimitUnavailable, check_and_count, client_ip
from sitara.media.account_delivery import (
    AccountEmailAttachmentTooLarge,
    AccountEmailDisabled,
    AccountEmailRecipientUnavailable,
    recipient_for,
    require_account_email_enabled,
    safe_stored_filename,
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


_THROTTLE_PREFIX = "sendrl"  # its own key namespace, distinct from auth/upload


class RenderNotReady(Exception):
    """The version has no permanent image yet, so there is nothing to send."""


class RenderDeliveryThrottled(Exception):
    """A per-account, per-address or per-recipient ceiling was reached.

    ``retry_after`` is the window length — a conservative, non-revealing hint,
    never a precise countdown. -> 429."""

    def __init__(self, retry_after: int):
        self.retry_after = int(retry_after)
        super().__init__("account email send limit reached")


class SendLimitReached(Exception):
    """This render has used its whole lifetime allowance of sends.

    Deliberately NOT a subclass of :class:`RenderDeliveryThrottled` and
    deliberately not a 429: a rate limit says "not now", and this says "not
    again". A ``Retry-After`` would be a lie, because no amount of waiting
    returns an allowance that is spent for good. -> 409 send_limit_reached, and
    the response says how many were used so the ceiling is never a surprise."""

    def __init__(self, used: int, limit: int):
        self.used = int(used)
        self.limit = int(limit)
        super().__init__("this render has been sent the maximum number of times")


class RenderDeliveryThrottleUnavailable(Exception):
    """The throttle cache cannot be reached, so the request is refused unthrottled.

    Deliberately distinct from :class:`RenderDeliveryThrottled`: an
    infrastructure fault is never reported to the caller — or to whoever watches
    the 429 rate — as their own abuse. -> 503 email_send_unavailable, and no
    ``Retry-After``, because no recovery window is known. Follows
    ``accounts.rate_limits.RateLimitUnavailable`` and
    ``generation.admission.AdmissionControlUnavailable``."""


def enforce_send_throttles(request, recipient: str) -> None:
    """Bound how often sends happen, from three independent directions.

    Run AFTER CSRF and ownership, never before: a cross-origin page must not be
    able to burn a victim's quota, and a throttled caller must still not be able
    to tell an owned design from one that does not exist.

    The third dimension is keyed on the RECIPIENT and is the one that is easy to
    leave out. Per-actor ceilings bound what one account or address can do; they
    do not bound what one mailbox RECEIVES. The account address is unverified
    (§8.5) and both registration and demo generation are free, so rotating
    accounts or source IPs would otherwise deliver attachment-bearing mail
    repeatedly to an address the sender merely typed at registration and does
    not control. It is sized generously — an abuse backstop, not a usage limit.

    The address is hashed exactly as every other identifier is, so no recipient
    ever appears in a cache key in the clear.

    Each check short-circuits the next, as the login view's pair does, so one
    refused request costs one increment rather than four."""
    checks = (
        (
            "send_user_hour",
            str(request.user.pk),
            settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR,
            3600,
        ),
        (
            "send_user_day",
            str(request.user.pk),
            settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_DAY,
            86_400,
        ),
        (
            "send_ip_hour",
            client_ip(request),
            settings.ACCOUNT_EMAIL_SEND_IP_LIMIT_PER_HOUR,
            3600,
        ),
        (
            "send_recipient_day",
            recipient,
            settings.ACCOUNT_EMAIL_RECIPIENT_LIMIT_PER_DAY,
            86_400,
        ),
    )
    for scope, identifier, limit, window in checks:
        try:
            retry_after = check_and_count(scope, identifier, limit, window, prefix=_THROTTLE_PREFIX)
        except RateLimitUnavailable as exc:
            raise RenderDeliveryThrottleUnavailable from exc
        if retry_after is not None:
            raise RenderDeliveryThrottled(retry_after)


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


def send_allowance(version: DesignVersion, kind: str) -> tuple[int, int]:
    """``(used, limit)`` for this exact ``(version, kind)``.

    An unlocked read, deliberately, and used for two different jobs: telling the
    owner how many sends they have left before they spend the last one, and
    letting the endpoint refuse a send that is already over the ceiling without
    first charging four rate counters for work that will not happen. The same
    reasoning the old ``delivery_is_terminal`` hint carried — that repeatedly
    pressing Send on a finished render must not exhaust the shared per-account
    quota a genuinely new render needs.

    It is a hint, not the authority. :func:`reserve_send` re-checks under the row
    lock, which is what makes two concurrent fourth attempts impossible rather
    than merely unlikely."""
    limit = int(settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER)
    row = (
        DesignRenderDelivery.objects.filter(design_version=version, kind=kind)
        .values_list("send_count", flat=True)
        .first()
    )
    return (int(row) if row is not None else 0), limit


def remembered_filename(version: DesignVersion, kind: str) -> str:
    """The name this owner last chose for this render, or ``""``.

    Never logged and never returned by anything but the owner's own pre-fill
    endpoint — see :class:`~sitara.designs.models.DesignRenderDelivery` for the
    full set of protections this column carries."""
    stored = (
        DesignRenderDelivery.objects.filter(design_version=version, kind=kind)
        .values_list("requested_filename", flat=True)
        .first()
    )
    return stored or ""


def reserve_send(version: DesignVersion, kind: str, *, requested_name: str = "") -> int | None:
    """Reserve this owner's next send and return the epoch to queue it under.

    Returns ``None`` when a send for this render is already in flight, which the
    endpoint answers exactly as it answers a fresh reservation. That is not
    laziness: the previous behaviour was to enqueue a second task that the claim
    then refused, so the caller could never distinguish the two anyway, and
    reserving nothing is strictly better than queueing work known to no-op.

    Raises :class:`SendLimitReached` when the lifetime allowance is spent. This
    check is the authoritative one — it runs under ``select_for_update`` on the
    row inside the same transaction that records the reservation, so two
    concurrent fourth attempts cannot both read "two used" and both proceed.

    The reservation deliberately does NOT take the claim. A worker does that, and
    it has to be a separate step: if the endpoint marked the row ``claimed``, the
    task it just queued would see a claim younger than the TTL, conclude another
    worker held it, and no-op — the send would never happen at all.

    The parent ``DesignVersion`` row is locked as well as the delivery row,
    because ``select_for_update()`` cannot lock a row that does not exist yet and
    the first reservation has none; two callers would otherwise both find nothing
    and both insert. Same shape, and the same ``.first()`` idiom and
    ``IntegrityError`` backstop, as
    :func:`sitara.designs.annotation_service.replace_annotation_document`."""
    now = timezone.now()
    limit = int(settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER)
    ttl = settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS
    grace = settings.ACCOUNT_EMAIL_SEND_RESERVATION_GRACE_SECONDS
    name = safe_stored_filename(requested_name)

    with transaction.atomic():
        locked_version = DesignVersion.objects.select_for_update().filter(pk=version.pk).first()
        if locked_version is None:
            # Purged between the endpoint's own lookup and this lock.
            return None

        row = (
            DesignRenderDelivery.objects.select_for_update()
            .filter(design_version=version, kind=kind)
            .first()
        )
        if row is None:
            try:
                created = DesignRenderDelivery.objects.create(
                    design_version=version,
                    kind=kind,
                    state=DesignRenderDelivery.PENDING,
                    attempt_count=0,
                    attempt_epoch=1,
                    send_count=0,
                    requested_filename=name,
                    claimed_at=now,
                )
            except IntegrityError:
                # The parent lock should make a duplicate insert unreachable; the
                # unique constraint is the backstop, and losing that race means
                # another caller holds the reservation — nothing to queue here.
                return None
            return created.attempt_epoch

        if row.send_count >= limit:
            raise SendLimitReached(row.send_count, limit)

        age = (now - row.claimed_at).total_seconds()
        if row.state == DesignRenderDelivery.CLAIMED and age < ttl:
            # A worker holds this send and has not run out of time. A second
            # press is a mis-click or an impatient one; either way there is
            # nothing to reserve, and reserving would let one press consume two
            # epochs.
            return None

        if row.state == DesignRenderDelivery.PENDING and age < grace:
            # A reservation nobody has picked up YET — which is not the same as
            # nobody ever will. Its task may be sitting in the broker perfectly
            # intact, and bumping the epoch here would orphan it: that task would
            # later find a newer epoch on the row and no-op, so if THIS press's
            # own enqueue then failed, the owner would hold a 202 for a message
            # that will never be sent, with nothing to retry it.
            #
            # A shorter window than the claim TTL on purpose. The TTL answers "how
            # long may a worker hold a send before we presume it dead" and must
            # exceed the task's hard time limit; this answers "how long may a
            # queued task go unclaimed", which is queue latency and is measured in
            # seconds. So a genuinely failed enqueue is retryable soon, while an
            # impatient double click is still absorbed.
            return None

        # A fresh press. The attempt counter resets because the retry-once budget
        # belongs to a press, not to the row: a previous press that exhausted its
        # retries failed WITHOUT consuming any of the owner's allowance, so it
        # must not leave them unable to try again. Every press still costs the
        # per-hour, per-IP and per-recipient rate quota, which is what stops
        # repeated failure being used to grind.
        row.attempt_epoch += 1
        row.state = DesignRenderDelivery.PENDING
        row.attempt_count = 0
        row.claimed_at = now
        if name:
            # A blank name means "no new choice", not "forget the old one".
            row.requested_filename = name
        row.save(
            update_fields=[
                "attempt_epoch",
                "state",
                "attempt_count",
                "claimed_at",
                "requested_filename",
                "updated_at",
            ]
        )
        return row.attempt_epoch


def _claim(version: DesignVersion, kind: str, epoch: int) -> DesignRenderDelivery | None:
    """Take the durable claim on this send, or return None to no-op.

    Committed BEFORE the message is handed over, which is the whole point: a
    redelivered task must be able to observe that someone already holds or
    completed this send.

    Everything here is scoped to ``epoch``. That is what lets one render be sent
    more than once without a redelivery becoming a duplicate: a task acts only on
    the press it was queued for, and a task carrying a superseded epoch no-ops no
    matter what state the row is in."""
    now = timezone.now()
    ttl = settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS
    limit = int(settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER)
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
            # No reservation. A task is never the first to touch this row now —
            # the endpoint reserves before it queues — so this means the row was
            # purged, and creating one here would mail a copy nobody asked for.
            return None

        if row.attempt_epoch != epoch:
            # A later press superseded this one, or this is a redelivery of a
            # task from a previous press. Either way the current epoch's own task
            # owns the row.
            return None

        # Re-checked here rather than trusted from the endpoint, exactly as the
        # capability gate and the ownership derivation are: a queued task can
        # outlive the allowance that admitted it — most plainly when a
        # long-running worker from an earlier press completes and takes the count
        # to the ceiling while this press is still pending.
        #
        # This is NOT redundant with `reserve_send`'s check, and deleting it as
        # "defensive" would let the ceiling be exceeded. Two concurrent presses for
        # a single remaining send can BOTH reserve legitimately: each reads the
        # same "two used" because neither has sent yet. It is this check, under the
        # row lock and after the winner's count is committed, that refuses the
        # loser. Mutation-verified — without it, that race mails two copies.
        if row.send_count >= limit:
            if row.state == DesignRenderDelivery.PENDING:
                # The unhappy version of this branch, and worth its own line at a
                # louder level: a DELIBERATE press was reserved, and then an
                # older, slower send finished first and took the last of the
                # allowance. The owner is told "queued" and receives nothing new —
                # the only copy that arrives is the one from the press they had
                # already given up on. Inherent to counting real sends while
                # gating on the same counter, and TTL-bounded, but an operator
                # reading `allowance_spent` at info level would not be able to
                # tell it from an ordinary already-exhausted retry.
                logger.warning(
                    "render_delivery.reserved_press_absorbed",
                    extra={"design_version_id": str(version.pk), "kind": kind},
                )
            else:
                logger.info(
                    "render_delivery.allowance_spent",
                    extra={"design_version_id": str(version.pk), "kind": kind},
                )
            return None

        if row.state in (DesignRenderDelivery.SENT, DesignRenderDelivery.RETRY_EXHAUSTED):
            # Terminal for THIS epoch, always — including long after the claim
            # has gone stale. Without this the stale branch below would treat a
            # completed send as an abandoned one and mail the owner a second copy
            # months later.
            #
            # Logged, because otherwise this outcome is invisible: a redelivery
            # that no-ops looks from outside exactly like one that delivered.
            logger.info(
                "render_delivery.already_terminal",
                extra={
                    "design_version_id": str(version.pk),
                    "kind": kind,
                    "delivery_state": row.state,
                },
            )
            return None

        if row.state == DesignRenderDelivery.PENDING:
            # The ordinary path: the endpoint reserved, and this is the task it
            # queued.
            row.state = DesignRenderDelivery.CLAIMED
            row.attempt_count = 1
            row.claimed_at = now
            row.save(update_fields=["state", "attempt_count", "claimed_at", "updated_at"])
            return row

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
    """The rendered attachment and the server-owned name it falls back to."""
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


def _record_sent(version: DesignVersion, kind: str, epoch: int) -> str:
    """Count the message that just went out, and close this epoch.

    The count is incremented **whether or not the epoch still matches**, and the
    asymmetry is deliberate. ``send_count`` exists to bound how much mail one
    render can produce, and a message the backend accepted is mail regardless of
    whether a later press has since superseded the epoch that produced it. The
    state and timestamp, by contrast, describe the CURRENT press, so writing them
    for a superseded epoch would clobber a live reservation and cause the newer
    press to be skipped.

    ``F()`` rather than a read-modify-write, so the increment survives whatever
    else touched the row between the claim and here."""
    with transaction.atomic():
        row = (
            DesignRenderDelivery.objects.select_for_update()
            .filter(design_version=version, kind=kind)
            .first()
        )
        if row is None:
            # Purged mid-send. The owner has their copy; there is no row left to
            # record it on and nothing to alert on.
            return "sent_unrecorded"

        now = timezone.now()
        if row.attempt_epoch != epoch:
            DesignRenderDelivery.objects.filter(pk=row.pk).update(
                send_count=F("send_count") + 1, sent_at=now, updated_at=now
            )
            return "sent_superseded"

        DesignRenderDelivery.objects.filter(pk=row.pk).update(
            send_count=F("send_count") + 1,
            state=DesignRenderDelivery.SENT,
            sent_at=now,
            updated_at=now,
        )
        return "sent"


def deliver_render(design_version_id, kind: str, attempt_epoch: int = 1) -> str:
    """The Celery task body: compose one render and mail it to its owner.

    Takes UUIDs and an integer, not a payload — no address, no bytes, no URL and
    no filename crosses the queue, where they would rest in Redis in the clear
    and survive any broker inspection. Everything is re-derived here from
    database state, the chosen name included.

    ``attempt_epoch`` identifies WHICH press this task is for. Without it a
    redelivery and a deliberate second send are indistinguishable, and the row
    would have to choose between refusing legitimate sends and mailing duplicates.

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

    claim = _claim(version, kind, attempt_epoch)
    if claim is None:
        return "noop"

    try:
        content, default_filename = _compose(version, kind)
        send_render_attachment(
            user=owner,
            # Read from the row the claim just locked, never from the queue
            # message. The name is the owner's own free text, and Redis is not
            # where it belongs.
            requested_name=claim.requested_filename,
            default_filename=default_filename,
            content=content,
            content_type=RENDER_CONTENT_TYPE,
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
        outcome = _record_sent(version, kind, attempt_epoch)
    except Exception as exc:  # noqa: BLE001 - see the task-boundary note above
        # The narrow window §8.5 is about, and the one place a duplicate becomes
        # LIKELY rather than merely possible: the owner has the message, but the
        # marker still says `claimed`, so the stale-claim path will send once
        # more after the TTL. Reported under its own outcome and its own log
        # line rather than folded into "failed", because an operator seeing this
        # should expect a duplicate — it is not the same event as a send that
        # never happened.
        #
        # It also means the send went uncounted, so the owner keeps an allowance
        # they have partly used. That is the right way round: over-counting would
        # deny someone a copy they never received.
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
        extra={"design_version_id": str(version.pk), "kind": kind, "outcome": outcome},
    )
    return outcome
