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

**The attachment filename is the one thing a caller may now name** (Phase 21,
ADR 0022), and it is the single exception to everything above. The two halves of
the reason the filename used to be server-owned need different answers, and only
one of them can be kept:

*No injection surface* is **restored by validation**. A filename reaches a
message header, where a bare ``\\r`` or ``\\n`` is header injection.
:func:`safe_attachment_filename` is total over any input — including a
non-string — and repairs or discards rather than trusting a caller to have
validated, for the same reason :func:`recipient_for` takes a row.

*The headers carry nothing private* is **given up, not mitigated**. Whatever a
stylist types travels in the message headers, appears in their mail client, and
is retained by the relay and the receiving host. The user is told so before they
type it. Nothing here reduces that exposure and nothing in this codebase may
describe it as reduced.

It follows that the filename is **never logged**, on any path: it is free text
about a garment someone intends to wear, the same category as an annotation note.
"""

from __future__ import annotations

import logging
import re
import unicodedata

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


#: The extension every attachment gets, chosen here and never by a caller.
ATTACHMENT_EXTENSION = ".png"

#: The name used when nothing usable survives sanitisation and the caller's own
#: default is unusable too. An unusable name is a naming failure, not a send
#: failure — the owner still gets their concept.
FALLBACK_FILENAME = "sitara-concept.png"

#: Long enough for a real description, short enough that a header stays a header.
MAX_FILENAME_BASE_LENGTH = 60

# A trailing dot-suffix of 1–4 ASCII alphanumerics is treated as an extension the
# user typed and dropped, so "dress.png" does not become "dress.png.png" and
# "dress.exe" survives only as "dress.png". Deliberately NOT "anything after the
# last dot": that would turn "my.dress" into "my.png" and silently eat a word.
_TYPED_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,4}$")

# Bidi controls only — NOT the whole Cf category. U+200C/U+200D (ZWNJ/ZWJ) are
# category Cf and are load-bearing in Devanagari, Urdu and Bengali; stripping
# them would corrupt exactly the names this function exists to allow. The
# overrides and isolates below have no place in a filename and can make one read
# as something it is not.
_BIDI_CONTROLS = dict.fromkeys(
    [0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069]
)

_SEPARATORS = str.maketrans({"/": None, "\\": None, ":": None})


def _is_unencodable(char: str) -> bool:
    """A character that must never reach a header, for either of two reasons.

    C0, DEL and C1 controls, where ``\\r`` and ``\\n`` are the two that matter:
    Python's own header quoting escapes ``"`` and ``\\`` but NOT control
    characters, so nothing downstream would catch a CRLF this function let
    through.

    Lone surrogates, which are a different failure entirely: a JSON body may
    carry ``"\\ud800"`` with no pair, Python holds it happily in a ``str``, and
    nothing rejects it until the UTF-8 encode inside the RFC 2231 header path
    raises. That would turn a bad NAME into a failed SEND — and this function
    promises to be total, which has to mean total for the caller's outcome and
    not merely free of exceptions here."""
    point = ord(char)
    return point < 0x20 or 0x7F <= point <= 0x9F or 0xD800 <= point <= 0xDFFF


def _sanitise_base(raw) -> str:
    """The usable base name in ``raw``, or ``""`` if nothing survives.

    Total over any input by construction: a non-string (a number, ``None``, a
    list that reached here past a serializer) has no base name and returns
    ``""`` rather than raising. The edge rejects those with a 400 so the stylist
    learns their name was not used; this layer must not depend on that having
    happened."""
    if not isinstance(raw, str):
        return ""

    # NFC first, so two spellings of one name cannot behave differently.
    value = unicodedata.normalize("NFC", raw)

    # Control characters are REFUSED, not stripped. Stripping "a\r\nBcc: x"
    # would silently produce the plausible-looking "aBcc: x"; refusing means the
    # send falls back to a name nobody chose, which is the honest outcome.
    if any(_is_unencodable(char) for char in value):
        return ""
    value = value.translate(_BIDI_CONTROLS)

    # Whitespace is settled before anything positional, so a leading run of dots
    # is found whether or not it was indented.
    value = " ".join(value.split())

    # The order of these three is load-bearing, and phases-21.md's own rules
    # collide unless it is this one: it lists "reject any .." beside an example
    # requiring "..hidden" to become "hidden". Leading dots are a hidden-file
    # spelling and are simply dropped; a ".." anywhere ELSE is path-shaped and
    # refused, which also catches "../../etc/passwd" rather than quietly
    # delivering it mangled to "etcpasswd"; only then are separators removed,
    # because removing them first would manufacture ".." runs out of "a/./.b".
    value = value.lstrip(".")
    if ".." in value:
        return ""
    value = value.translate(_SEPARATORS)

    value = _TYPED_EXTENSION.sub("", value)
    # Truncation can leave a trailing space or dot behind, so tidy after cutting
    # rather than before.
    return value.strip()[:MAX_FILENAME_BASE_LENGTH].strip().rstrip(".").strip()


def safe_attachment_filename(raw, *, fallback: str = FALLBACK_FILENAME) -> str:
    """A header-safe ``.png`` attachment name, for any input whatsoever.

    ``fallback`` is the caller's server-owned constant (the plain or annotated
    render's name) and is sanitised on exactly the same path. A caller that
    mistakenly passed user text as the fallback would therefore still get a safe
    header, and a caller cannot reach the final constant by exhausting the
    others.

    The extension is appended here and never taken from the caller, so the
    content type and the name cannot disagree."""
    base = _sanitise_base(raw) or _sanitise_base(fallback)
    if not base:
        return FALLBACK_FILENAME
    return base + ATTACHMENT_EXTENSION


def recipient_for(user) -> str:
    """The account's own address, from the row.

    Module-level rather than inlined so a caller that needs to know *whether* a
    recipient exists (an endpoint deciding between 409 and 202) asks the same
    question the sender will, instead of reimplementing it."""
    email = (getattr(user, "email", "") or "").strip() if user is not None else ""
    if not email:
        raise AccountEmailRecipientUnavailable("this account has no email address")
    return email


def send_render_attachment(
    *,
    user,
    requested_name,
    default_filename: str,
    content: bytes,
    content_type: str,
) -> None:
    """Mail one rendered concept to ``user``'s own account address.

    ``requested_name`` is the only caller-influenced value in the message, and it
    is the stylist's own free text (ADR 0022). It is sanitised here rather than
    trusted — see :func:`safe_attachment_filename` and the module docstring for
    what that does and does not buy. ``default_filename`` is the caller's
    server-owned constant, used when the requested name yields nothing usable.

    ``content_type`` stays a server-owned constant from
    :mod:`sitara.media.annotation_render`; a caller has never been able to choose
    it and still cannot, so the declared type always matches the bytes.

    Nothing about the recipient changes: it is derived from the row, here, and no
    argument can express a destination.

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
    message.attach(
        safe_attachment_filename(requested_name, fallback=default_filename),
        content,
        content_type,
    )
    message.send(fail_silently=False)
