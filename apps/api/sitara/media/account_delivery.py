"""The only place Sitara constructs an outbound email (Phase 19, section 8).

The same single-choke-point discipline `ai_gateway` gives paid providers and
`media/delivery.py` gives signed image URLs. The capability gate, recipient
derivation, attachment bounds and message construction all live here; views and
Celery tasks call in and never assemble mail themselves. `EmailMessage` is
constructed in exactly one place in the codebase — this module — and a test
asserts that by searching the tree, so a second call site fails the suite rather
than review.

**Every entry point takes the user ROW, never an address string.** That is the
load-bearing detail rather than a stylistic one. §8.1 calls the recipient rule
the single most important rule in the phase — an endpoint that mails an
attachment to a caller-chosen address is an open relay — and a choke point
accepting an arbitrary string cannot enforce it: every present and future call
site would have to re-implement the invariant correctly. Taking the row means
the address is derived here, once, from a database column, and a caller has no
way to express any other destination.

The message body is not a parameter either. A caller chooses a message *kind* by
calling the matching function; the subject and body are fixed strings owned by
this module. Free-text parameters would be the obvious route for private note
content to leave the system inside a message body, where the relay and the
receiving host both retain it.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)

# Fixed server strings for the one message kind this module currently sends.
# No design title, no note text, no brief, no signed URL, no tracking pixel,
# plain text rather than HTML — the attachment carries the content and the
# message carries none of it.
RENDER_SUBJECT = "Your Sitara concept"
RENDER_BODY = (
    "A private copy of your Sitara bridalwear concept is attached.\n"
    "\n"
    "You are receiving this because you asked for a copy from your Sitara "
    "workspace. Sitara concepts are for visualisation only.\n"
)


class AccountEmailDisabled(Exception):
    """``ACCOUNT_EMAIL_DELIVERY_ENABLED`` is off.

    Its own operator decision, exactly like ``LIVE_GENERATION_ENABLED``: a
    present SMTP credential never enables sending by itself. -> 503
    email_delivery_disabled."""


class AccountEmailRecipientUnavailable(Exception):
    """The user row has no address to deliver to.

    Raised by this module rather than the caller, so the "no address, no send"
    rule is enforced at the choke point instead of at each call site. There is
    deliberately no fallback: no prompt, no stored alternative, no silent
    success. -> 409 email_recipient_unavailable."""


class AccountEmailAttachmentTooLarge(Exception):
    """The attachment exceeded ``ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES``.

    Refused here rather than handed to a relay that would reject it — or accept
    it and fail somewhere the user never sees."""


def account_email_enabled() -> bool:
    return bool(settings.ACCOUNT_EMAIL_DELIVERY_ENABLED)


def require_account_email_enabled() -> None:
    if not account_email_enabled():
        raise AccountEmailDisabled("account email delivery is disabled")


def recipient_for(user) -> str:
    """The account's own address, from the row.

    Module-level rather than inlined so a caller that needs to know *whether* a
    recipient exists (an endpoint deciding between 409 and 202) asks the same
    question the sender will, instead of reimplementing it."""
    email = (getattr(user, "email", "") or "").strip() if user is not None else ""
    if not email:
        raise AccountEmailRecipientUnavailable("this account has no email address")
    return email


def send_render_attachment(*, user, filename: str, content: bytes, content_type: str) -> None:
    """Mail one rendered concept to ``user``'s own account address.

    ``filename`` and ``content_type`` are server-owned constants from
    :mod:`sitara.media.annotation_render`, never derived from a design title or
    any other user input, so the attachment headers carry nothing private and
    offer no injection surface.

    Raises rather than returning a status: every failure here is one a caller
    must map to an exact response code or a durable retry decision, and a
    boolean would lose that distinction."""
    require_account_email_enabled()
    recipient = recipient_for(user)

    if len(content) > settings.ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES:
        raise AccountEmailAttachmentTooLarge("the rendered attachment is too large to send")

    message = EmailMessage(
        subject=RENDER_SUBJECT,
        body=RENDER_BODY,
        from_email=settings.DEFAULT_FROM_EMAIL or None,
        to=[recipient],
    )
    message.attach(filename, content, content_type)
    message.send(fail_silently=False)
