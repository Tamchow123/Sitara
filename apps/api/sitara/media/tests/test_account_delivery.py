"""The outbound-email choke point.

Co-located with the module it tests, like every other `sitara.media` module's
tests. What lives here is the boundary itself — the gate, recipient derivation
from a user ROW, the attachment bound, the fixed message strings, and the
tree-wide guarantee that nothing else in the application sends mail. The
`DesignRenderDelivery` claim machine and end-to-end delivery are a `designs`
concern and are tested next to that code.
"""

from __future__ import annotations

import ast
import inspect
import logging
import unicodedata
from pathlib import Path

import pytest
from django.core import mail

from sitara.media import account_delivery

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class FakeUser:
    """Only what the choke point may touch. A real ``User`` would let a test
    pass for the wrong reason — by exercising some other attribute this module
    has no business reading."""

    def __init__(self, email):
        self.email = email


# ---------------------------------------------------------------------------
# The structural guarantee
# ---------------------------------------------------------------------------


def application_modules() -> list[Path]:
    api_root = Path(__file__).resolve().parents[3]
    return [
        path
        for path in api_root.rglob("*.py")
        if "/tests/" not in path.as_posix() and not path.name.startswith("test_")
    ]


MAIL_API = "django.core.mail"


def _reaches_the_mail_api(dotted: str) -> bool:
    """Does binding this dotted name put ``django.core.mail`` within reach?

    Both directions, and they are different questions. ``django.core.mail`` and
    anything under it (``django.core.mail.backends.smtp``) IS the API. A proper
    PREFIX of it (``django``, ``django.core``) is not the API but hands over a
    name from which the API is one attribute chain away.

    Deliberately not ``dotted.startswith(MAIL_API)``, which would also match a
    sibling like ``django.core.mailer``."""
    return (
        dotted == MAIL_API or dotted.startswith(MAIL_API + ".") or MAIL_API.startswith(dotted + ".")
    )


def imports_djangos_mail_api(source: str) -> bool:
    """Does this module have Django's mail API within reach?

    Parsed, not grepped. A substring search for ``EmailMessage(`` — which is
    what this test did first — misses every other way to send mail:
    ``send_mail()``, ``EmailMultiAlternatives()``, ``mail_admins()``,
    ``get_connection().send_messages()``, and an aliased import
    (``from django.core.mail import EmailMessage as _Msg``) that leaves the
    substring nowhere in the file. Asking what a module REACHES rather than how
    a call site is spelled closes all of them at once, including spellings
    Django has not invented yet.

    Two independent checks, because neither alone is enough:

    1. **What each import binds.** Applied symmetrically to ``import x`` and
       ``from x import y`` — the first version of this check applied the
       proper-prefix half to ``ast.Import`` only, so ``from django.core import
       mail``, the single most idiomatic spelling of all, sailed straight
       through it.
    2. **Any literal ``django.core.mail...`` attribute chain**, whoever bound
       the root. ``import django.db.models.deletion`` also binds the name
       ``django``, so an import check alone cannot be complete without flagging
       the fourteen migrations that legitimately do exactly that. Reading the
       chain at the use site closes that class without a single exception.

    It also avoids the false positive a substring search hits immediately:
    ``config/settings.py`` names a backend as a STRING
    (``"django.core.mail.backends.smtp.EmailBackend"``) without importing
    anything, which is configuration, not a send. A string is not an
    ``ast.Attribute``, so neither check sees it.

    What this does NOT catch, all of it one family — reaching the module
    through a runtime string rather than a name the parser can see:
    ``importlib.import_module("django.core.mail")``, ``__import__``,
    ``sys.modules["django.core.mail"]``, ``getattr`` chains, ``exec`` of a
    source string, or a name laundered through an alias of an alias. Those are
    deliberate evasions of a visible test rather than the ordinary
    second-call-site mistake this guards against — and someone willing to write
    one could equally delete this file.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative — cannot reach django from inside this app
            for alias in node.names:
                if _reaches_the_mail_api(f"{node.module}.{alias.name}"):
                    return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _reaches_the_mail_api(alias.name):
                    return True
        elif isinstance(node, ast.Attribute) and _dotted_chain(node) == MAIL_API:
            return True
    return False


def _dotted_chain(node: ast.Attribute) -> str | None:
    """``django.core.mail`` for that attribute expression, else ``None``.

    Returns the chain only up to the mail package itself, so both
    ``django.core.mail.send_mail(...)`` and a bare ``django.core.mail``
    reference match on their inner node during the walk."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def test_no_other_module_reaches_for_djangos_mail_api():
    """The choke point, enforced by the suite rather than by review.

    phases-19.md calls the single-choke-point rule the most important in its
    section: a second call site can reintroduce a caller-influenced recipient or
    a free-text body that this architecture otherwise makes impossible to
    express."""
    api_root = Path(__file__).resolve().parents[3]
    offenders = sorted(
        path.relative_to(api_root).as_posix()
        for path in application_modules()
        if imports_djangos_mail_api(path.read_text(encoding="utf-8"))
    )
    assert offenders == ["sitara/media/account_delivery.py"], offenders


@pytest.mark.parametrize(
    "evasion",
    [
        "from django.core.mail import send_mail\nsend_mail('s', 'b', 'f', ['t'])",
        "from django.core.mail import EmailMessage as _Msg\n_Msg().send()",
        "from django.core.mail import EmailMultiAlternatives\nEmailMultiAlternatives()",
        "from django.core.mail import get_connection\nget_connection().send_messages([])",
        "from django.core.mail import mail_admins\nmail_admins('s', 'b')",
        "import django.core.mail\ndjango.core.mail.send_mail('s', 'b', 'f', ['t'])",
        "import django.core.mail as m\nm.send_mail('s', 'b', 'f', ['t'])",
        "from django.core.mail.backends.smtp import EmailBackend\nEmailBackend().send_messages([])",
        # A bare root import plus a fully-qualified attribute chain.
        "import django\ndjango.core.mail.send_mail('s', 'b', 'f', ['t'])",
        "import django.core\ndjango.core.mail.get_connection().send_messages([])",
        # The `from x import y` spellings. An earlier version of this check
        # applied its proper-prefix half to `import x` only, so every one of
        # these passed — and the first is more idiomatic than anything above it.
        "from django.core import mail\nmail.send_mail('s', 'b', 'f', ['t'])",
        "from django.core import mail as m\nm.send_mail('s', 'b', 'f', ['t'])",
        "from django import core\ncore.mail.send_mail('s', 'b', 'f', ['t'])",
        # An unrelated submodule import also binds the root name, so the
        # attribute chain is what has to be read, not the import.
        "import django.db.models.deletion\ndjango.core.mail.send_mail('s', 'b', 'f', ['t'])",
    ],
)
def test_the_choke_point_check_catches_every_evasion(evasion):
    """The guard's own teeth, proved rather than asserted.

    Every one of these sends mail, and not one contains the substring
    ``EmailMessage(`` that the first version of this test searched for."""
    assert "EmailMessage(" not in evasion, "this case would not have evaded the old check"
    assert imports_djangos_mail_api(evasion)


@pytest.mark.parametrize(
    "innocent",
    [
        # Configuration, not a send. A string is not an import or an attribute
        # chain — otherwise the guard would flag `config/settings.py` and have
        # to be weakened by an exception list.
        'EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"',
        # Every `django.core.*` and `from django import ...` spelling the tree
        # actually uses. Each one binds a name that is NOT a prefix of the mail
        # package, so none of them may fire.
        "from django.core.exceptions import ImproperlyConfigured",
        "from django.core.files.storage import storages",
        "from django.core.cache import cache",
        "from django.core.management.base import BaseCommand",
        "from django import forms",
        "import django.core.validators",
        "import django.db.models.deletion",
        "import django.utils.timezone",
        # A same-prefixed sibling that is not the mail package.
        "from django.core import mailer_stub",
    ],
)
def test_the_choke_point_check_does_not_fire_on_innocent_imports(innocent):
    """The other half of a useful guard.

    A check that flagged these would need an exception list, and an exception
    list is where a real second call site eventually hides."""
    assert not imports_djangos_mail_api(innocent)


def test_the_send_entry_point_takes_a_user_row_not_an_address():
    """The load-bearing signature. An endpoint that mails an attachment to a
    caller-chosen address is an open relay, and a choke point accepting an
    arbitrary string cannot enforce otherwise — every call site would have to
    re-derive the invariant correctly."""
    parameters = inspect.signature(account_delivery.send_render_attachment).parameters
    assert "user" in parameters
    for name in parameters:
        assert name not in {
            "email",
            "address",
            "recipient",
            "to",
            "cc",
            "bcc",
            # Added with the caller-named attachment (Phase 21): the endpoints
            # now accept a request body, so the signature is the last structural
            # place the "no destination expressible" guarantee can be read off,
            # and a header dict would smuggle every one of these back in at once.
            "from_email",
            "reply_to",
            "sender",
            "headers",
            "extra_headers",
        }, name


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_a_closed_gate_refuses_before_anything_else(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    with pytest.raises(account_delivery.AccountEmailDisabled):
        account_delivery.send_render_attachment(
            user=FakeUser("someone@example.test"),
            requested_name=None,
            default_filename="x.png",
            content=PNG,
            content_type="image/png",
        )
    assert mail.outbox == []


def test_a_present_credential_does_not_open_the_gate(settings):
    """The rule that matters most in this block: configuration is not consent."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    settings.EMAIL_HOST = "smtp.example.test"
    settings.EMAIL_HOST_USER = "someone"
    settings.EMAIL_HOST_PASSWORD = "a-real-looking-secret"
    assert account_delivery.account_email_enabled() is False
    with pytest.raises(account_delivery.AccountEmailDisabled):
        account_delivery.send_render_attachment(
            user=FakeUser("someone@example.test"),
            requested_name=None,
            default_filename="x.png",
            content=PNG,
            content_type="image/png",
        )


# ---------------------------------------------------------------------------
# Recipient derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_user_without_a_usable_address_is_refused(value):
    user = None if value is None else FakeUser(value)
    with pytest.raises(account_delivery.AccountEmailRecipientUnavailable):
        account_delivery.recipient_for(user)


def test_a_surrounding_whitespace_address_is_normalised():
    assert account_delivery.recipient_for(FakeUser("  someone@example.test ")) == (
        "someone@example.test"
    )


def test_the_send_refuses_a_user_with_no_address(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    with pytest.raises(account_delivery.AccountEmailRecipientUnavailable):
        account_delivery.send_render_attachment(
            user=FakeUser(""),
            requested_name=None,
            default_filename="x.png",
            content=PNG,
            content_type="image/png",
        )
    assert mail.outbox == []


# ---------------------------------------------------------------------------
# The attachment bound
# ---------------------------------------------------------------------------


def test_an_oversized_attachment_is_refused_before_the_backend(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    settings.ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES = len(PNG) - 1
    with pytest.raises(account_delivery.AccountEmailAttachmentTooLarge):
        account_delivery.send_render_attachment(
            user=FakeUser("someone@example.test"),
            requested_name=None,
            default_filename="x.png",
            content=PNG,
            content_type="image/png",
        )
    assert mail.outbox == []


def test_an_attachment_exactly_at_the_bound_is_allowed(settings):
    """The check is ``>``. Pinned so a flip to ``>=`` — which would refuse a
    legitimate render sitting exactly on the boundary — fails here."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    settings.ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES = len(PNG)
    account_delivery.send_render_attachment(
        user=FakeUser("someone@example.test"),
        requested_name=None,
        default_filename="x.png",
        content=PNG,
        content_type="image/png",
    )
    assert len(mail.outbox) == 1


# ---------------------------------------------------------------------------
# What the message says, and does not
# ---------------------------------------------------------------------------


def test_the_message_is_plain_text_with_fixed_server_strings(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    settings.DEFAULT_FROM_EMAIL = "concepts@sitara.example"

    account_delivery.send_render_attachment(
        user=FakeUser("someone@example.test"),
        requested_name=None,
        default_filename="sitara-concept.png",
        content=PNG,
        content_type="image/png",
    )

    message = mail.outbox[0]
    assert message.subject == account_delivery.RENDER_SUBJECT
    assert message.body == account_delivery.RENDER_BODY
    assert message.content_subtype == "plain"
    assert getattr(message, "alternatives", []) == []
    assert message.to == ["someone@example.test"]
    assert message.from_email == "concepts@sitara.example"
    assert message.attachments == [("sitara-concept.png", PNG, "image/png")]


def test_the_message_strings_interpolate_nothing():
    """Fixed constants, not templates. A ``{}`` or ``%s`` here would be the
    obvious route for note text or a design title to reach a message body, where
    the relay and the receiving host both retain it."""
    for text in (account_delivery.RENDER_SUBJECT, account_delivery.RENDER_BODY):
        assert "{" not in text and "%" not in text
        assert "http://" not in text and "https://" not in text


def test_the_send_signature_offers_no_way_to_supply_message_text():
    """A caller picks a message KIND by calling this function; it cannot supply
    a subject or body. That is why the strings above can be trusted."""
    parameters = inspect.signature(account_delivery.send_render_attachment).parameters
    for name in parameters:
        assert name not in {"subject", "body", "message", "html", "template"}, name


# ---------------------------------------------------------------------------
# The one caller-named value: the attachment filename (Phase 21, ADR 0022)
# ---------------------------------------------------------------------------


DEFAULT = "sitara-concept.png"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The ordinary case.
        ("Rani lehenga", "Rani lehenga.png"),
        # HEADER INJECTION — the one that matters most. Refused outright rather
        # than repaired into the plausible-looking "aBcc: attacker@example.test".
        ("a\r\nBcc: attacker@example.test", DEFAULT),
        ("a\nb", DEFAULT),
        ("a\rb", DEFAULT),
        ("a\tb", DEFAULT),
        ("a\x00b", DEFAULT),
        ("a\x1bb", DEFAULT),
        ("a\x7fb", DEFAULT),
        ("a\x85b", DEFAULT),  # C1 NEL
        # A filename is not a path.
        ("../../etc/passwd", DEFAULT),  # ".." survives separator stripping
        ("folder/name", "foldername.png"),
        ("folder\\name", "foldername.png"),
        ("C:name", "Cname.png"),
        ("a..b", DEFAULT),
        # Leading dots and ragged whitespace.
        ("..hidden", "hidden.png"),
        (".hidden", "hidden.png"),
        ("  spaced   out  ", "spaced out.png"),
        # NBSP: collapsed as whitespace, and NOT mistaken for a C1 control.
        ("a b", "a b.png"),
        # A typed extension is dropped and ours appended — never doubled.
        ("dress.png", "dress.png"),
        ("dress.PNG", "dress.png"),
        ("dress.exe", "dress.png"),
        ("dress.jpeg", "dress.png"),
        # ...but a dotted word that is not extension-shaped keeps its text.
        ("my.dress", "my.dress.png"),
        # A bare trailing dot is not an extension (nothing follows it), so the
        # extension rule leaves it and the final tidy has to remove it. Without
        # that tidy this is "dress..png".
        ("dress.", "dress.png"),
        # Only an extension, no base. The leading dot goes, what is left is a
        # perfectly good name, and it gets OUR extension — the typed one never
        # survives even when it is the whole input.
        (".exe", "exe.png"),
        # A lone surrogate: legal in a Python str, unencodable as UTF-8, and so
        # refused here rather than left to raise inside the header encoder and
        # turn a bad name into a failed send.
        ("a\ud800b", DEFAULT),
        ("\udfff", DEFAULT),
        # Nothing usable survives.
        ("", DEFAULT),
        ("   ", DEFAULT),
        ("...", DEFAULT),
        ("/", DEFAULT),
        # Not a string at all. Total by construction, because the choke point
        # must not depend on the edge having validated first.
        (None, DEFAULT),
        (12, DEFAULT),
        ([], DEFAULT),
        ({"filename": "x"}, DEFAULT),
        (b"bytes", DEFAULT),
    ],
)
def test_the_filename_sanitiser_is_total_and_header_safe(raw, expected):
    assert account_delivery.safe_attachment_filename(raw, fallback=DEFAULT) == expected


def test_an_over_long_name_is_cut_to_the_bound():
    name = account_delivery.safe_attachment_filename("R" * 200, fallback=DEFAULT)
    base = name.removesuffix(".png")
    assert len(base) == account_delivery.MAX_FILENAME_BASE_LENGTH
    assert name == "R" * account_delivery.MAX_FILENAME_BASE_LENGTH + ".png"


def test_truncation_never_leaves_a_trailing_space_or_dot():
    """The cut lands mid-name, so tidying has to happen AFTER it, not before."""
    ragged = "R" * (account_delivery.MAX_FILENAME_BASE_LENGTH - 1) + " tail"
    assert account_delivery.safe_attachment_filename(ragged, fallback=DEFAULT) == (
        "R" * (account_delivery.MAX_FILENAME_BASE_LENGTH - 1) + ".png"
    )


def test_the_tidy_after_truncation_runs_in_the_right_order():
    """The one input that tells the two tidy steps apart.

    The cut lands so that the tail is ``". "`` — a dot followed by a space. The
    trailing whitespace has to go BEFORE the trailing dot is looked for,
    otherwise the dot is still behind a space when the dot-strip runs and
    survives into the name as "…R..png". Ordinary ragged input cannot
    distinguish the two orders, which is why this case exists."""
    stem = "R" * (account_delivery.MAX_FILENAME_BASE_LENGTH - 2)
    assert account_delivery.safe_attachment_filename(stem + ". tail", fallback=DEFAULT) == (
        stem + ".png"
    )


@pytest.mark.parametrize(
    "name",
    [
        "रानी का लहंगा",  # Devanagari
        "شادی کا جوڑا",  # Urdu
        "বিয়ের শাড়ি",  # Bengali
    ],
)
def test_a_name_in_a_south_asian_script_survives(name):
    """Refusing non-ASCII letters would mean a stylist cannot name a concept in
    their own script — precisely the flattening CLAUDE.md §2 forbids."""
    expected = unicodedata.normalize("NFC", name) + ".png"
    assert account_delivery.safe_attachment_filename(name, fallback=DEFAULT) == expected


def test_a_joiner_is_kept_because_scripts_need_it():
    """ZWNJ and ZWJ are category Cf, like the bidi controls stripped below, and
    stripping the whole category would corrupt the very names above.

    Written as escapes, not literals: an invisible character in source is a
    character nobody can review."""
    joined = "क‍ष"
    assert account_delivery.safe_attachment_filename(joined, fallback=DEFAULT) == joined + ".png"


def test_bidi_overrides_are_stripped():
    """No place in a filename, and they make one read as something it is not."""
    assert account_delivery.safe_attachment_filename("a‮b", fallback=DEFAULT) == "ab.png"


def test_two_spellings_of_one_name_behave_identically():
    """NFC first, so a decomposed name is not a different name."""
    composed = account_delivery.safe_attachment_filename("é", fallback=DEFAULT)
    decomposed = account_delivery.safe_attachment_filename("é", fallback=DEFAULT)
    assert composed == decomposed == "é.png"


def test_an_unusable_caller_default_still_yields_a_safe_name():
    """The fallback is sanitised on the same path, so a caller that mistakenly
    passed user text as its default cannot reach the header unchecked."""
    assert account_delivery.safe_attachment_filename(None, fallback="a\r\nBcc: x") == (
        account_delivery.FALLBACK_FILENAME
    )


def test_the_final_fallback_constant_is_itself_a_safe_name():
    """Otherwise the last resort would be the one unvalidated value."""
    assert (
        account_delivery.safe_attachment_filename(
            account_delivery.FALLBACK_FILENAME, fallback=account_delivery.FALLBACK_FILENAME
        )
        == account_delivery.FALLBACK_FILENAME
    )


def test_the_extension_is_ours_and_the_caller_cannot_choose_it():
    parameters = inspect.signature(account_delivery.send_render_attachment).parameters
    for name in parameters:
        assert name not in {"extension", "suffix", "content_disposition"}, name
    assert account_delivery.ATTACHMENT_EXTENSION == ".png"


def _attachment_disposition(message) -> str:
    """The raw Content-Disposition header of the one attachment part.

    Read off the generated MIME message rather than the ``attachments`` list,
    because the encoding of a non-ASCII filename is exactly what the list does
    not show — and it is the encoding, not the Python string, that a mail client
    and a relay actually see."""
    return _attachment_part(message)[1]


def _attachment_part(message) -> tuple[object, str]:
    """The one attachment part and its raw ``Content-Disposition`` header."""
    parts = [part for part in message.message().walk() if part.get_filename() is not None]
    assert len(parts) == 1, parts
    return parts[0], str(parts[0]["Content-Disposition"])


def test_a_non_ascii_filename_is_encoded_per_rfc_2231_in_the_real_header(settings):
    """Asserted against the generated header, not trusted from the library."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    account_delivery.send_render_attachment(
        user=FakeUser("someone@example.test"),
        requested_name="रानी",
        default_filename=DEFAULT,
        content=PNG,
        content_type="image/png",
    )
    part, disposition = _attachment_part(mail.outbox[0])
    assert "filename*=utf-8''" in disposition
    # The raw bytes of the name must not appear unencoded, and no header may be
    # split by anything the name contained.
    assert "रानी" not in disposition
    assert "\n" not in disposition and "\r" not in disposition
    # ...and the encoding is only worth having if it ROUND-TRIPS. Asserting the
    # header is well-formed proves nothing about whether the name inside it is
    # still the stylist's: a sanitiser that truncated it to "रा" would satisfy
    # every assertion above. Decoded by the same email machinery a mail client
    # uses, so this is what the recipient actually sees.
    assert part.get_filename() == "रानी.png"


def test_an_ascii_filename_with_spaces_is_quoted_not_split(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    account_delivery.send_render_attachment(
        user=FakeUser("someone@example.test"),
        requested_name="Rani lehenga",
        default_filename=DEFAULT,
        content=PNG,
        content_type="image/png",
    )
    disposition = _attachment_disposition(mail.outbox[0])
    assert 'filename="Rani lehenga.png"' in disposition
    assert mail.outbox[0].attachments[0][0] == "Rani lehenga.png"


def test_a_header_injection_attempt_reaches_no_header_and_no_extra_recipient(settings):
    """The whole point of refusing controls, proved end to end at the choke
    point: one recipient, the row's own, and nothing added to the headers."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    account_delivery.send_render_attachment(
        user=FakeUser("someone@example.test"),
        requested_name="concept\r\nBcc: attacker@example.test",
        default_filename=DEFAULT,
        content=PNG,
        content_type="image/png",
    )
    message = mail.outbox[0]
    assert message.to == ["someone@example.test"]
    assert message.bcc == [] and message.cc == []
    assert message.attachments[0][0] == DEFAULT
    generated = message.message().as_string()
    assert "attacker@example.test" not in generated


def test_the_filename_is_never_logged(settings, caplog):
    """It is free text about a garment someone intends to wear — the same
    category as an annotation note, and it must not reach a log or Sentry."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    with caplog.at_level(logging.DEBUG):
        account_delivery.send_render_attachment(
            user=FakeUser("someone@example.test"),
            requested_name="Rani secret lehenga",
            default_filename=DEFAULT,
            content=PNG,
            content_type="image/png",
        )
    logged = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    assert "Rani" not in logged and "lehenga" not in logged
