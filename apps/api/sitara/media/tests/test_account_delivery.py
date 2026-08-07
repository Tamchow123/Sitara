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


def imports_djangos_mail_api(source: str) -> bool:
    """Does this module import anything from ``django.core.mail``?

    Parsed, not grepped. A substring search for ``EmailMessage(`` — which is
    what this test did first — misses every other way to send mail:
    ``send_mail()``, ``EmailMultiAlternatives()``, ``mail_admins()``,
    ``get_connection().send_messages()``, and an aliased import
    (``from django.core.mail import EmailMessage as _Msg``) that leaves the
    substring nowhere in the file. Asking what a module IMPORTS rather than how
    a call site is spelled closes all of them at once, including spellings
    Django has not invented yet.

    It also avoids the false positive that a substring search hits immediately:
    ``config/settings.py`` names a backend as a STRING
    (``"django.core.mail.backends.smtp.EmailBackend"``) without importing
    anything, which is configuration, not a send.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("django.core.mail"):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name.startswith("django.core.mail") for alias in node.names):
                return True
    return False


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
    ],
)
def test_the_choke_point_check_catches_every_evasion(evasion):
    """The guard's own teeth, proved rather than asserted.

    Every one of these sends mail, and not one contains the substring
    ``EmailMessage(`` that the first version of this test searched for."""
    assert "EmailMessage(" not in evasion, "this case would not have evaded the old check"
    assert imports_djangos_mail_api(evasion)


def test_the_choke_point_check_does_not_fire_on_configuration():
    """A backend named as a string is configuration, not a send — otherwise the
    guard would flag `config/settings.py` and have to be weakened by exception."""
    assert not imports_djangos_mail_api(
        'EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"'
    )


def test_the_send_entry_point_takes_a_user_row_not_an_address():
    """The load-bearing signature. An endpoint that mails an attachment to a
    caller-chosen address is an open relay, and a choke point accepting an
    arbitrary string cannot enforce otherwise — every call site would have to
    re-derive the invariant correctly."""
    parameters = inspect.signature(account_delivery.send_render_attachment).parameters
    assert "user" in parameters
    for name in parameters:
        assert name not in {"email", "address", "recipient", "to", "cc", "bcc"}, name


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_a_closed_gate_refuses_before_anything_else(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    with pytest.raises(account_delivery.AccountEmailDisabled):
        account_delivery.send_render_attachment(
            user=FakeUser("someone@example.test"),
            filename="x.png",
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
            filename="x.png",
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
            user=FakeUser(""), filename="x.png", content=PNG, content_type="image/png"
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
            filename="x.png",
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
        filename="x.png",
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
        filename="sitara-concept.png",
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
