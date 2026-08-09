"""The two send endpoints.

What matters here is not that a send happens — that is the delivery machinery's
own suite — but that the HTTP surface cannot be talked into sending to the wrong
place, cannot reveal whether a design exists, and cannot be used as a firehose.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.utils import timezone

from sitara.designs.models import (
    Design,
    DesignRenderDelivery,
    DesignSession,
    DesignVersion,
)
from sitara.designs.render_delivery import RenderDeliveryThrottled, enforce_send_throttles

from .utils import (
    STRONG_PASSWORD,
    bootstrap_csrf,
    create_ready_design_version,
    csrf_client,
    login,
    register,
    send_json,
    synthetic_original,
    unique_email,
    unique_ip,
)

pytestmark = pytest.mark.django_db


class FakeRequest:
    """Only what enforce_send_throttles reads: a user pk and REMOTE_ADDR.

    A real request would drag a session and an authenticated user in and let a
    throttle test pass or fail for reasons that have nothing to do with the
    throttle."""

    def __init__(self, *, user_pk: str, ip: str):
        self.user = type("U", (), {"pk": user_pk})()
        self.META = {"REMOTE_ADDR": ip}


PLAIN = "send"
ANNOTATED = "annotations/send"


@pytest.fixture(autouse=True)
def delivery_enabled(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    settings.DEFAULT_FROM_EMAIL = "concepts@sitara.example"
    # Generous by default; a throttle test tightens the one dimension it means
    # to exercise, so no other test can fail for the wrong reason.
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 1000
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_DAY = 1000
    settings.ACCOUNT_EMAIL_SEND_IP_LIMIT_PER_HOUR = 1000
    settings.ACCOUNT_EMAIL_RECIPIENT_LIMIT_PER_DAY = 1000


@pytest.fixture(autouse=True)
def clear_throttles():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def send_url(design_id, version_id, suffix: str) -> str:
    return f"/api/v1/designs/{design_id}/versions/{version_id}/{suffix}/"


def signed_in_browser(email: str | None = None) -> tuple[object, str, DesignVersion]:
    """A logged-in client owning one ready version, with its image stored."""
    address = email or unique_email()
    browser = csrf_client()
    register(browser, address)
    # AFTER registering, never before: login rotates the session key, which
    # rotates the CSRF token with it. A token captured earlier is already dead.
    token = bootstrap_csrf(browser)
    user = get_user_model().objects.get(email=address)
    session = DesignSession.objects.create(user=user)
    design = Design.objects.create(design_session=session, title="Owned design")
    version = create_ready_design_version(design.id, with_storage_objects=False)
    storages["design_images"].save(version.image_storage_key, ContentFile(synthetic_original()))
    return browser, token, version


def anonymous_browser() -> tuple[object, str, DesignVersion]:
    browser = csrf_client()
    token = bootstrap_csrf(browser)
    session = DesignSession.objects.create(user=None)
    design = Design.objects.create(design_session=session, title="Anonymous design")
    version = create_ready_design_version(design.id, with_storage_objects=False)
    storages["design_images"].save(version.image_storage_key, ContentFile(synthetic_original()))
    # The browser's own workspace pointer must match, or ownership fails for the
    # wrong reason and the test would prove nothing about anonymity.
    store = browser.session
    store["sitara_design_session_id"] = str(session.id)
    store.save()
    return browser, token, version


def post_send(browser, token, version, suffix=PLAIN, ip=None, name=None):
    """A send, optionally naming the file.

    ``name=None`` sends ``{}`` — the shape a client that never learned about the
    field would send — rather than omitting the body, which is covered separately."""
    return send_json(
        browser,
        "post",
        send_url(version.design_id, version.pk, suffix),
        None if name is None else {"filename": name},
        token=token,
        ip=ip or unique_ip(),
    )


def get_send_state(browser, version, suffix=PLAIN):
    return browser.get(send_url(version.design_id, version.pk, suffix))


def attachment_names() -> list[str]:
    return [message.attachments[0][0] for message in mail.outbox]


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
def test_an_owner_can_queue_a_send(suffix, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    response = post_send(browser, token, version, suffix)

    assert response.status_code == 202, response.content
    assert response.json() == {"send": {"status": "queued"}}
    assert response["Cache-Control"] == "no-store"


def test_the_response_never_contains_an_address():
    """The client already knows the account address from /auth/me. Echoing it
    here would put it in a response body for no benefit."""
    address = unique_email()
    browser, token, version = signed_in_browser(address)

    response = post_send(browser, token, version)

    body = response.content.decode()
    assert address not in body
    assert address.split("@")[0] not in body
    assert "@" not in body


@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
def test_the_slash_optional_form_resolves(suffix):
    browser, token, version = signed_in_browser()
    url = send_url(version.design_id, version.pk, suffix).rstrip("/")
    response = send_json(browser, "post", url, None, token=token, ip=unique_ip())
    assert response.status_code == 202


# ---------------------------------------------------------------------------
# The recipient rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"email": "attacker@evil.test"},
        {"to": "attacker@evil.test"},
        {"recipient": "attacker@evil.test"},
        {"send": {"to": "attacker@evil.test"}},
        {"address": "attacker@evil.test"},
    ],
)
def test_a_supplied_address_is_never_honoured(body, settings):
    """The single most important rule in this section.

    Whatever the endpoint does with an unexpected body — accept and ignore it,
    or reject it — the one outcome that must never occur is a message reaching
    the supplied address. Asserted on the outbox, not on the status code, so
    this test keeps its meaning if the body handling ever changes.

    Since Phase 21 these bodies are refused outright, which would make an
    outbox-only assertion vacuously true. So the outbox is asserted EMPTY as well
    as address-free, and the deliver-to-A-anyway half of the rule is proved where
    a delivery genuinely happens — see the query-parameter test below, and
    ``test_a_named_send_still_goes_to_the_account_address``."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    address = unique_email()
    browser, token, version = signed_in_browser(address)

    send_json(
        browser,
        "post",
        send_url(version.design_id, version.pk, PLAIN),
        body,
        token=token,
        ip=unique_ip(),
    )

    assert mail.outbox == []
    for message in mail.outbox:
        assert message.to == [address]
        assert "attacker@evil.test" not in str(message.to)


@pytest.mark.parametrize("param", ["recipient", "to", "email", "cc", "bcc"])
def test_a_query_parameter_address_is_never_honoured(param, settings):
    """The same rule on the other half of the attack surface.

    Section 8.3 names the query string alongside the body, and it is the easier
    one to overlook: a body needs a parser to reach the view, whereas
    ``request.GET`` is populated for free on every request. The endpoint reads
    neither, but "reads neither" is a claim about code that a test has to
    pin — ``cc`` and ``bcc`` especially, since a caller-supplied copy recipient
    would leak a private concept just as effectively as a redirected ``to``."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    address = unique_email()
    browser, token, version = signed_in_browser(address)

    url = f"{send_url(version.design_id, version.pk, PLAIN)}?{param}=attacker@evil.test"
    send_json(browser, "post", url, None, token=token, ip=unique_ip())

    assert [m.to for m in mail.outbox] == [[address]]
    for message in mail.outbox:
        assert message.cc == []
        assert message.bcc == []
        assert "attacker@evil.test" not in str(message.message())


def test_an_anonymous_owner_is_told_to_sign_in_and_nothing_is_sent(settings):
    """No fallback, no prompt for an address, no silent success."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = anonymous_browser()

    response = post_send(browser, token, version)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_recipient_unavailable"
    assert mail.outbox == []


def test_the_send_goes_to_the_account_address_not_the_session(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    address = unique_email()
    browser, token, version = signed_in_browser(address)

    post_send(browser, token, version)

    assert [m.to for m in mail.outbox] == [[address]]


def test_a_named_send_still_goes_to_the_account_address(settings):
    """The positive half of the rule, on the request shape that now exists: an
    accepted body, an address named everywhere a caller can name one, and the
    message still goes to the account's own address.

    A test that only proves refusals would leave the case where delivery actually
    happens unexamined — which is the one that would matter."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    address = unique_email()
    browser, token, version = signed_in_browser(address)

    url = f"{send_url(version.design_id, version.pk, PLAIN)}?to=attacker@evil.test"
    response = send_json(
        browser,
        "post",
        url,
        {"filename": "Autumn lehenga"},
        token=token,
        ip=unique_ip(),
    )

    assert response.status_code == 202, response.content
    assert [m.to for m in mail.outbox] == [[address]]
    message = mail.outbox[0]
    assert message.cc == []
    assert message.bcc == []
    assert "attacker@evil.test" not in str(message.message())
    # And the name the caller DID get to choose was honoured, so this is not
    # passing because the body was ignored wholesale.
    assert message.attachments[0][0] == "Autumn lehenga.png"


# ---------------------------------------------------------------------------
# The recipient rule, re-proved now that a body exists
# ---------------------------------------------------------------------------
#
# Every test above this line was written when the endpoint parsed nothing. The
# guarantee they pin was structural then and is an explicit one now, so it is
# re-proved against the parser rather than inherited.


FORBIDDEN_FIELDS = [
    "email",
    "to",
    "recipient",
    "address",
    "cc",
    "bcc",
    "from_email",
    "reply_to",
    "from",
    "subject",
    "body",
    "attachment",
]


@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_a_forbidden_field_fails_the_whole_request_and_queues_nothing(field, suffix, settings):
    """Rejected, not ignored.

    Accepting the request and dropping the field would send the concept to the
    right address while telling the client its ``to`` was honoured — the worst of
    the available outcomes, because nobody would notice until the day a refactor
    started reading it. ``from``/``subject``/``body`` are in the table alongside
    the address fields: none of them is a destination, but all of them are header
    material, and the field this endpoint does accept is already header material."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    address = unique_email()
    browser, token, version = signed_in_browser(address)

    response = send_json(
        browser,
        "post",
        send_url(version.design_id, version.pk, suffix),
        {field: "attacker@evil.test"},
        token=token,
        ip=unique_ip(),
    )

    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == "validation_failed"
    assert response.json()["error"]["fields"] == {field: ["This field cannot be set."]}
    assert mail.outbox == []
    assert not DesignRenderDelivery.objects.filter(design_version=version).exists()


def test_a_valid_name_beside_a_forbidden_field_fails_whole(settings):
    """The partial-success case, which is the one a permissive parser produces.

    A body carrying both a usable ``filename`` and a ``to`` must not be answered
    202 with the name applied — that is an endpoint quietly deciding which half of
    a request it liked."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    response = send_json(
        browser,
        "post",
        send_url(version.design_id, version.pk, PLAIN),
        {"filename": "Autumn lehenga", "to": "attacker@evil.test"},
        token=token,
        ip=unique_ip(),
    )

    assert response.status_code == 400
    assert sorted(response.json()["error"]["fields"]) == ["to"]
    assert mail.outbox == []
    assert not DesignRenderDelivery.objects.filter(design_version=version).exists()


@pytest.mark.parametrize(
    "raw",
    [
        "Autumn\r\nBcc: attacker@evil.test",
        "Autumn\nBcc: attacker@evil.test",
        "Autumn\rlehenga",
        "Autumn\x00lehenga",
        "Autumn\x7flehenga",
    ],
)
def test_a_control_bearing_name_is_refused_and_nothing_is_sent(raw, settings):
    """A refusal, not a repair.

    The choke point would refuse these too, and a name silently stripped back to
    "AutumnBcc: attacker@evil.test" is a name nobody chose. Refusing at the edge
    is what lets the stylist see and correct it."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    response = post_send(browser, token, version, name=raw)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "filename_invalid"
    assert mail.outbox == []
    assert not DesignRenderDelivery.objects.filter(design_version=version).exists()


@pytest.mark.parametrize("raw", [12, 1.5, None, True, ["a"], {"a": "b"}])
def test_a_name_that_is_not_text_is_refused(raw, settings):
    """DRF's CharField would coerce 12 to "12" and deliver an attachment called
    that. A name arriving as a number, a null or a list is a client defect, and
    answering it with a plausible-looking file teaches the client it worked."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    response = send_json(
        browser,
        "post",
        send_url(version.design_id, version.pk, PLAIN),
        {"filename": raw},
        token=token,
        ip=unique_ip(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "filename_invalid"
    assert mail.outbox == []


@pytest.mark.parametrize("body", ["[]", '"just a string"', "12", "null", "{", "[[[["])
def test_a_body_that_is_not_a_json_object_is_a_controlled_400(body, settings):
    """Total over arbitrary JSON: a controlled envelope, never a TypeError."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    response = browser.post(
        send_url(version.design_id, version.pk, PLAIN),
        data=body,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
        REMOTE_ADDR=unique_ip(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"invalid_json", "validation_failed"}
    assert mail.outbox == []


def test_a_refused_body_is_answered_before_the_gate_is_consulted(settings):
    """So a client debugging a 400 is not told the feature is unavailable, and a
    body that will never be accepted cannot cost quota."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 1
    browser, token, version = signed_in_browser()

    response = post_send(browser, token, version, name="bad\r\nname")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "filename_invalid"

    # The quota the malformed request must not have spent.
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    assert post_send(browser, token, version).status_code == 202


def test_a_refused_name_costs_no_part_of_the_lifetime_allowance(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    browser, token, version = signed_in_browser()

    for _ in range(5):
        assert post_send(browser, token, version, name="bad\r\nname").status_code == 400

    assert get_send_state(browser, version).json()["send"]["used"] == 0
    assert post_send(browser, token, version).status_code == 202


@pytest.mark.parametrize(
    "content_type",
    [None, "application/x-www-form-urlencoded"],
)
def test_a_form_encoded_body_is_not_a_second_way_in(content_type, settings):
    """The endpoint documents a JSON object, and only ``JSONParser`` is enabled, so
    a form body cannot supply the name — or, one refactor later, anything else.

    The two encodings are refused at different depths and it is worth knowing
    which. A urlencoded body reaches DRF intact and is a clean 415. A multipart one
    does not: Django's CSRF check reads ``request.POST`` looking for
    ``csrfmiddlewaretoken`` before the view runs, and the multipart parser consumes
    the stream without caching it, so DRF finds no body at all and sees an empty
    request. That is a 202 with the field DROPPED, not honoured — which is why this
    test asserts on the attachment name rather than only on the status: the
    guarantee is "a form field never names the file", and it holds either way."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    extra = {} if content_type is None else {"content_type": content_type}
    response = browser.post(
        send_url(version.design_id, version.pk, PLAIN),
        data="filename=Autumn+lehenga" if content_type else {"filename": "Autumn lehenga"},
        HTTP_X_CSRFTOKEN=token,
        REMOTE_ADDR=unique_ip(),
        **extra,
    )

    assert response.status_code in {202, 415}
    assert attachment_names() in ([], ["sitara-concept.png"])


# ---------------------------------------------------------------------------
# The stylist names the file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [(PLAIN, "Autumn lehenga.png"), (ANNOTATED, "Autumn lehenga.png")],
)
def test_the_chosen_name_becomes_the_attachment_filename(suffix, expected, settings):
    """Both kinds. The extension is the server's to decide — the attachment is a
    PNG whatever the stylist typed."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, suffix, name="Autumn lehenga").status_code == 202

    assert attachment_names() == [expected]


def test_no_name_falls_back_to_the_default_and_not_to_the_title(settings):
    """A stylist who presses Send without typing anything gets the neutral
    default, not a filename derived from something they did not choose here."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version).status_code == 202

    assert attachment_names() == ["sitara-concept.png"]


def test_an_absent_body_is_still_accepted(settings):
    """A client cached before this field existed sent no body at all. An upgrade
    that turns those requests into 400s would look like an outage."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    response = browser.post(
        send_url(version.design_id, version.pk, PLAIN),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
        REMOTE_ADDR=unique_ip(),
    )

    assert response.status_code == 202, response.content
    assert attachment_names() == ["sitara-concept.png"]


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("  Autumn   lehenga  ", "Autumn lehenga.png"),
        ("folder/name", "foldername.png"),
        ("name.png", "name.png"),
        ("name.jpeg", "name.png"),
        ("Lehenga खूबसूरत", "Lehenga खूबसूरत.png"),
        ("A" * 100, "A" * 60 + ".png"),
    ],
)
def test_a_repairable_name_is_cleaned_rather_than_refused(typed, expected, settings):
    """Accepted and repaired, because a refusal here would cost the stylist a name
    they can perfectly well have. The rules themselves belong to the choke point's
    own suite; what this pins is that the edge does not refuse what the choke point
    repairs — two layers disagreeing about that is how a name becomes unreachable."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, name=typed).status_code == 202

    assert attachment_names() == [expected]


def test_a_name_that_sanitises_to_nothing_is_refused(settings):
    """ "...", "///" and "  " differ: the last means "I did not choose a name" and
    gets the default, while the first two are attempts at a name that cannot be
    honoured, so the stylist is told rather than handed sitara-concept.png."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, name="///").status_code == 400
    assert post_send(browser, token, version, name="../../etc/passwd").status_code == 400
    assert mail.outbox == []

    assert post_send(browser, token, version, name="   ").status_code == 202
    assert attachment_names() == ["sitara-concept.png"]


def test_an_unbounded_name_never_reaches_the_sanitiser(settings):
    """An abuse backstop, distinct from the 60-character naming rule that
    truncates: 100 characters is shortened, 5000 is refused outright."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    response = post_send(browser, token, version, name="A" * 5000)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "filename_invalid"
    assert mail.outbox == []


def test_the_name_is_remembered_for_the_next_send(settings):
    """The acceptance criterion: a stylist who named it once does not retype it."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, name="Autumn lehenga").status_code == 202
    assert get_send_state(browser, version).json()["send"]["suggested_filename"] == (
        "Autumn lehenga"
    )

    # Pressing Send again without retyping reuses it rather than falling back.
    assert post_send(browser, token, version).status_code == 202
    assert attachment_names() == ["Autumn lehenga.png", "Autumn lehenga.png"]


def test_a_new_name_replaces_the_remembered_one(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, name="First choice").status_code == 202
    assert post_send(browser, token, version, name="Second choice").status_code == 202

    assert attachment_names() == ["First choice.png", "Second choice.png"]
    assert get_send_state(browser, version).json()["send"]["suggested_filename"] == (
        "Second choice"
    )


def test_the_two_kinds_remember_their_names_separately(settings):
    """The plain render and the annotated composite are different artefacts with
    separate allowances, so one's name must not become the other's."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, PLAIN, name="Just the concept").status_code == 202
    assert post_send(browser, token, version, ANNOTATED, name="With my marks").status_code == 202

    assert attachment_names() == ["Just the concept.png", "With my marks.png"]
    assert get_send_state(browser, version, PLAIN).json()["send"]["suggested_filename"] == (
        "Just the concept"
    )
    assert get_send_state(browser, version, ANNOTATED).json()["send"]["suggested_filename"] == (
        "With my marks"
    )


@pytest.mark.parametrize(("suffix", "kind"), [(PLAIN, "plain"), (ANNOTATED, "annotated")])
def test_the_chosen_name_never_crosses_the_queue(suffix, kind, monkeypatch):
    """It is the owner's own free text and Redis is not where it belongs. The task
    reads it from the row it locks, under the epoch it was queued with.

    Both endpoints, because "the name is never in the queue message" is a privacy
    invariant rather than a per-endpoint behaviour: proving it for the plain render
    only would leave a one-sided regression — an annotated-only enqueue that passed
    the name — invisible."""
    captured = {}
    monkeypatch.setattr(
        "sitara.designs.views.send_design_render.delay",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs),
    )
    browser, token, version = signed_in_browser()

    post_send(browser, token, version, suffix, name="Autumn lehenga")

    assert captured["args"] == (str(version.pk), kind, 1)
    assert "Autumn" not in f"{captured['args']}{captured['kwargs']}"


@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
def test_no_log_line_carries_the_chosen_name(suffix, settings, caplog):
    """It is the stylist's own words about their own concept, and a log is the
    easiest place for private text to end up somewhere it is never reviewed. The
    refusal paths are exercised too, since an error message quoting the offending
    input is the usual way this leaks.

    Both endpoints, for the same reason as the queue test above — and the annotated
    one especially, since its delivery path composes a render from the owner's notes
    and so has more code in which a name could be logged."""
    import logging

    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    with caplog.at_level(logging.DEBUG):
        assert post_send(browser, token, version, suffix, name="Autumn lehenga").status_code == 202
        assert post_send(browser, token, version, suffix, name="Refused\r\nname").status_code == 400
        get_send_state(browser, version, suffix)

    logged = caplog.text
    assert "Autumn" not in logged
    assert "lehenga" not in logged
    assert "Refused" not in logged


@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
def test_the_send_response_never_echoes_the_name(suffix, settings):
    """Nothing needs it back — the client typed it. Keeping it out of the response
    keeps it out of any log or proxy that records response bodies.

    Both endpoints, on the same reasoning as the queue and log tests: the envelope
    happens to be endpoint-agnostic today, and a test that relies on that would stop
    proving anything the moment one endpoint grew a field of its own."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()

    response = post_send(browser, token, version, suffix, name="Autumn lehenga")

    assert response.json() == {"send": {"status": "queued"}}


# ---------------------------------------------------------------------------
# Send state, which is what lets the client show the ceiling coming
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
def test_the_send_state_reports_the_allowance_and_a_title_derived_name(suffix, settings):
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    browser, _token, version = signed_in_browser()

    response = get_send_state(browser, version, suffix)

    assert response.status_code == 200, response.content
    assert response.json() == {
        "send": {"used": 0, "limit": 3, "suggested_filename": "Owned design"}
    }
    assert response["Cache-Control"] == "no-store"


def test_the_send_state_counts_up_as_the_allowance_is_spent(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version).status_code == 202
    assert get_send_state(browser, version).json()["send"]["used"] == 1

    assert post_send(browser, token, version).status_code == 202
    assert post_send(browser, token, version).status_code == 202
    assert get_send_state(browser, version).json()["send"] == {
        "used": 3,
        "limit": 3,
        "suggested_filename": "Owned design",
    }
    assert post_send(browser, token, version).status_code == 409


def test_the_send_state_never_suggests_a_note(settings):
    """CLAUDE.md §7: a note is the most personal free text in the product. A
    filename travels in the message headers, so a note-derived default would put
    it somewhere the note is expressly forbidden to go."""
    from .test_render_delivery import NOTE_TEXT, annotate

    browser, _token, version = signed_in_browser()
    annotate(version)

    for suffix in (PLAIN, ANNOTATED):
        body = get_send_state(browser, version, suffix).content.decode()
        assert NOTE_TEXT not in body
        assert "neckline" not in body


def test_the_send_state_of_an_untitled_design_suggests_nothing(settings):
    """An empty suggestion is the honest answer — the client falls back to its own
    placeholder rather than being handed a name derived from nothing."""
    browser, _token, version = signed_in_browser()
    Design.objects.filter(pk=version.design_id).update(title="")

    assert get_send_state(browser, version).json()["send"]["suggested_filename"] == ""


def test_a_title_that_cannot_be_a_filename_suggests_nothing(settings):
    """The suggestion goes through the same sanitiser the attachment will, so a
    title full of separators cannot pre-fill a name the send would then refuse."""
    browser, _token, version = signed_in_browser()
    Design.objects.filter(pk=version.design_id).update(title="///")

    assert get_send_state(browser, version).json()["send"]["suggested_filename"] == ""


@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
def test_the_send_state_of_a_foreign_design_is_an_indistinguishable_404(suffix):
    """The remembered name is the owner's own free text, so this endpoint is as
    ownership-bound as the send itself."""
    _owner, _token, victim = signed_in_browser()
    stranger = csrf_client()
    register(stranger, unique_email())

    response = get_send_state(stranger, victim, suffix)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_the_send_state_of_a_nonexistent_design_is_the_same_404():
    import uuid

    browser, _token, _version = signed_in_browser()
    response = browser.get(f"/api/v1/designs/{uuid.uuid4()}/versions/{uuid.uuid4()}/send/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_the_send_state_never_contains_an_address():
    address = unique_email()
    browser, _token, version = signed_in_browser(address)

    body = get_send_state(browser, version).content.decode()

    assert address not in body
    assert "@" not in body


def test_the_send_state_is_readable_by_an_anonymous_owner(settings):
    """It creates nothing and sends nothing, so it does not need the account the
    send does. Answering 409 here would leave the client unable to show the
    allowance on a screen it is perfectly entitled to render."""
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    browser, _token, version = signed_in_browser()
    anonymous, _anon_token, anonymous_version = anonymous_browser()

    assert get_send_state(anonymous, anonymous_version).status_code == 200
    # And still cannot read someone else's.
    assert get_send_state(anonymous, version).status_code == 404


def test_the_send_state_creates_no_delivery_row(settings):
    """A safe method that wrote a row would give an unauthenticated visitor a way
    to create durable state, and would make the allowance readable only by
    starting to spend it."""
    browser, _token, version = signed_in_browser()

    assert get_send_state(browser, version).status_code == 200

    assert not DesignRenderDelivery.objects.filter(design_version=version).exists()


# ---------------------------------------------------------------------------
# Ownership and CSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
def test_a_foreign_design_is_an_indistinguishable_404(suffix, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    _owner, _token, victim_version = signed_in_browser()

    stranger = csrf_client()
    register(stranger, unique_email())
    stranger_token = bootstrap_csrf(stranger)

    response = post_send(stranger, stranger_token, victim_version, suffix)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert mail.outbox == []


def test_a_nonexistent_design_is_the_same_404():
    import uuid

    browser, token, _version = signed_in_browser()
    url = f"/api/v1/designs/{uuid.uuid4()}/versions/{uuid.uuid4()}/send/"
    response = send_json(browser, "post", url, None, token=token, ip=unique_ip())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_version_belonging_to_another_owned_design_is_a_404():
    """A valid version UUID paired with a design the caller does own must not
    leak that the version exists elsewhere."""
    browser, token, _mine = signed_in_browser()
    _other, _other_token, theirs = signed_in_browser()

    url = send_url(_mine.design_id, theirs.pk, PLAIN)
    response = send_json(browser, "post", url, None, token=token, ip=unique_ip())
    assert response.status_code == 404


@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
def test_a_missing_csrf_token_is_refused(suffix):
    browser, _token, version = signed_in_browser()
    response = browser.post(
        send_url(version.design_id, version.pk, suffix), content_type="application/json"
    )
    assert response.status_code == 403
    assert mail.outbox == []


def test_csrf_is_checked_before_the_throttle_is_charged(settings):
    """Otherwise a cross-origin page could burn a victim's quota without ever
    passing CSRF."""
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 1
    browser, token, version = signed_in_browser()

    for _ in range(3):
        browser.post(
            send_url(version.design_id, version.pk, PLAIN), content_type="application/json"
        )

    allowed = post_send(browser, token, version)
    assert allowed.status_code == 202, "CSRF failures consumed the honest caller's quota"


# ---------------------------------------------------------------------------
# Fail-closed states
# ---------------------------------------------------------------------------


def test_a_closed_gate_is_a_503_and_sends_nothing(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    browser, token, version = signed_in_browser()

    response = post_send(browser, token, version)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "email_delivery_disabled"
    assert mail.outbox == []


def test_a_version_without_an_image_is_a_409():
    browser, token, version = signed_in_browser()
    DesignVersion.objects.filter(pk=version.pk).update(
        image_storage_key="",
        image_sha256="",
        image_size_bytes=None,
        image_width=None,
        image_height=None,
        thumbnail_storage_key="",
        thumbnail_sha256="",
        thumbnail_size_bytes=None,
        thumbnail_width=None,
        thumbnail_height=None,
        image_processor_version="",
        image_ingested_at=None,
    )

    response = post_send(browser, token, version)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "design_image_not_ready"


def test_the_gate_is_checked_before_the_throttle(settings):
    """A disabled feature must not silently consume quota."""
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 1
    browser, token, version = signed_in_browser()

    for _ in range(3):
        assert post_send(browser, token, version).status_code == 503

    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
    assert post_send(browser, token, version).status_code == 202


# ---------------------------------------------------------------------------
# Abuse bounds
# ---------------------------------------------------------------------------


def test_the_per_account_hourly_ceiling_returns_429_with_retry_after(settings):
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 2
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version).status_code == 202
    assert post_send(browser, token, version).status_code == 202
    limited = post_send(browser, token, version)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "email_send_limit_reached"
    assert limited["Retry-After"].isdigit()
    assert int(limited["Retry-After"]) > 0


def test_the_per_account_daily_ceiling_refuses_on_its_own(settings):
    """The fourth dimension, isolated — it was previously never exercised.

    Every other throttle test leaves ``ACCOUNT_EMAIL_SEND_LIMIT_PER_DAY`` at a
    value it cannot reach, so deleting that entry from the ``checks`` tuple broke
    nothing. Here the hourly ceiling is raised out of the way so only the daily
    one can refuse.

    ``Retry-After`` is what pins WHICH dimension fired: the windows are 3600 and
    86400, so a value above an hour cannot have come from the hourly check."""
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 50
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_DAY = 2
    settings.ACCOUNT_EMAIL_SEND_IP_LIMIT_PER_HOUR = 50
    settings.ACCOUNT_EMAIL_RECIPIENT_LIMIT_PER_DAY = 50
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version).status_code == 202
    assert post_send(browser, token, version).status_code == 202
    limited = post_send(browser, token, version)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "email_send_limit_reached"
    assert int(limited["Retry-After"]) > 3600


def test_the_per_address_hourly_ceiling_is_independent_of_the_account(settings):
    settings.ACCOUNT_EMAIL_SEND_IP_LIMIT_PER_HOUR = 2
    shared_ip = unique_ip()

    first, first_token, first_version = signed_in_browser()
    second, second_token, second_version = signed_in_browser()

    assert post_send(first, first_token, first_version, ip=shared_ip).status_code == 202
    assert post_send(second, second_token, second_version, ip=shared_ip).status_code == 202
    limited = post_send(first, first_token, first_version, ip=shared_ip)

    assert limited.status_code == 429


def test_two_accounts_sharing_a_recipient_hit_a_recipient_scoped_limit(settings):
    """The dimension that is easy to leave out, exercised where it is reachable.

    Section 8.3 asks for "two distinct accounts resolving to the same
    recipient". That state cannot be produced through the HTTP surface today:
    ``accounts_user`` carries both a uniqueness constraint and
    ``accounts_user_email_is_canonical``, there is no email-change endpoint, and
    forcing it is refused by PostgreSQL — I tried, and the constraint held.
    Recorded rather than worked around, because the endpoint-level version would
    have meant disabling a real constraint to make a test pass.

    So the dimension is proved directly on ``enforce_send_throttles`` with two
    different user ids and one recipient. That is the mechanism the requirement
    is about; the account-to-address 1:1 is merely what keeps the endpoint from
    reaching it today, and that could change (an email-change feature, account
    deletion and re-registration, or a relaxed uniqueness rule).

    The second half is the assertion that matters: the second caller's own quota
    is untouched, which pins the refusal to the recipient dimension rather than
    to an account or address ceiling."""
    settings.ACCOUNT_EMAIL_RECIPIENT_LIMIT_PER_DAY = 2
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 50
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_DAY = 50
    settings.ACCOUNT_EMAIL_SEND_IP_LIMIT_PER_HOUR = 50

    shared_recipient = "shared@example.test"
    first = FakeRequest(user_pk="user-one", ip="203.0.113.10")
    second = FakeRequest(user_pk="user-two", ip="203.0.113.20")

    enforce_send_throttles(first, shared_recipient)
    enforce_send_throttles(first, shared_recipient)

    with pytest.raises(RenderDeliveryThrottled):
        enforce_send_throttles(second, shared_recipient)

    # The second caller is not otherwise out of quota: a different recipient
    # still passes, which is what proves the refusal above came from the
    # recipient dimension.
    enforce_send_throttles(second, "someone-else@example.test")


def test_the_recipient_never_appears_in_a_cache_key():
    """Hashed exactly as every other throttle identifier is.

    Asserted on the key the throttle actually builds rather than by listing the
    store, because the test cache is Redis and cannot be enumerated — and a test
    that quietly enumerated nothing would pass regardless."""
    from sitara.accounts.rate_limits import build_key
    from sitara.designs.render_delivery import _THROTTLE_PREFIX

    recipient = "plaintext-check@example.test"
    key = build_key("send_recipient_day", recipient, prefix=_THROTTLE_PREFIX)

    assert recipient not in key
    assert "plaintext-check" not in key
    assert "example.test" not in key
    # Same identifier, same key: the throttle would not bind otherwise.
    assert key == build_key("send_recipient_day", recipient, prefix=_THROTTLE_PREFIX)
    # A different address must not collide into the same bucket.
    assert key != build_key("send_recipient_day", "someone@example.test", prefix=_THROTTLE_PREFIX)


def test_a_throttle_store_outage_is_a_503_not_a_429(settings, monkeypatch):
    """An infrastructure fault is never reported to the caller — or to whoever
    watches the 429 rate — as their own abuse."""
    from sitara.accounts import rate_limits

    browser, token, version = signed_in_browser()

    def explode(*args, **kwargs):
        raise rate_limits.RateLimitUnavailable("cache down")

    monkeypatch.setattr("sitara.designs.render_delivery.check_and_count", explode)

    response = post_send(browser, token, version)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "email_send_unavailable"
    assert "Retry-After" not in response, "no recovery window is known, so none is promised"
    assert mail.outbox == []


def test_a_broker_outage_is_a_503_not_an_unhandled_error(settings, monkeypatch):
    """The broker is a SECOND Redis, and it can fail on its own.

    ``CELERY_BROKER_URL`` and ``REDIS_CACHE_URL`` are separate settings pointing
    at different databases by default, so the throttle cache answering happily
    says nothing about whether the queue will accept the task. A total outage
    short-circuits at the throttle and never reaches here; the exposure is a
    broker-first or broker-only fault, and before this the caller got an
    unhandled 500 having already spent quota on a send that never queued."""

    def explode(*args, **kwargs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr("sitara.designs.views.send_design_render.delay", explode)

    browser, token, version = signed_in_browser()
    response = post_send(browser, token, version)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "email_send_unavailable"
    assert "Retry-After" not in response
    assert mail.outbox == []


def test_a_second_press_delivers_a_second_copy(settings):
    """What this endpoint could not do before Phase 21.

    A repeat send used to be a silent no-op answered as 202 — the owner was told
    their copy was on its way and nothing was sent. It now delivers, up to the
    render's lifetime allowance."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, PLAIN).status_code == 202
    assert post_send(browser, token, version, PLAIN).status_code == 202

    assert len(mail.outbox) == 2


def test_the_fourth_send_is_refused_with_the_numbers_that_explain_it(settings):
    """Not a generic 429. A rate limit says "not now" and carries a Retry-After;
    this says "not again", and the message names the numbers so a ceiling the
    owner cannot see coming is not experienced as a bug."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    browser, token, version = signed_in_browser()

    for _ in range(3):
        assert post_send(browser, token, version, PLAIN).status_code == 202

    refused = post_send(browser, token, version, PLAIN)

    assert refused.status_code == 409
    body = refused.json()
    assert body["error"]["code"] == "send_limit_reached"
    # The whole message, not a substring search for "3": a message naming only
    # the ceiling ("a maximum of 3 sends") would satisfy `"3" in message` while
    # dropping the used count the acceptance criteria ask for.
    assert body["error"]["message"] == (
        "You have already emailed this concept 3 times, which is the maximum of 3."
    )
    assert "Retry-After" not in refused.headers
    assert len(mail.outbox) == 3


def test_the_refusal_names_the_used_count_and_the_ceiling_separately(settings):
    """With the two numbers different, so a message that reports one of them twice
    cannot pass. The point of naming them is that a ceiling the owner cannot see
    coming is experienced as a bug."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 2
    browser, token, version = signed_in_browser()

    for _ in range(2):
        assert post_send(browser, token, version, PLAIN).status_code == 202
    refused = post_send(browser, token, version, PLAIN)

    message = refused.json()["error"]["message"]
    assert "2 times" in message
    assert "maximum of 2" in message


def test_a_failed_enqueue_does_not_strand_a_send_that_was_already_queued(settings, monkeypatch):
    """The reliability gap a busy-check covering only `claimed` left open.

    Press 1 reserves and its task really is queued. Press 2 arrives before any
    worker has picked that task up. If press 2 were allowed to bump the epoch, the
    queued task would later find a newer epoch and no-op — and if press 2's own
    enqueue then failed, the owner would hold a 202 for a message that will never
    be sent, with nothing to retry it.

    Press 2 must therefore reserve nothing, and — the assertion that matters —
    press 1's task must still deliver afterwards."""
    settings.ACCOUNT_EMAIL_SEND_RESERVATION_GRACE_SECONDS = 60
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    queued = []
    monkeypatch.setattr(
        "sitara.designs.views.send_design_render.delay",
        lambda *args, **kwargs: queued.append(args),
    )
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, PLAIN).status_code == 202
    assert len(queued) == 1

    # Press 2, whose own enqueue would fail. It must not have reached the enqueue
    # at all, because there was nothing to reserve.
    monkeypatch.setattr(
        "sitara.designs.views.send_design_render.delay",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("broker down")),
    )
    assert post_send(browser, token, version, PLAIN).status_code == 202

    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.attempt_epoch == queued[0][2], "the queued task was orphaned"

    # The task press 1 queued still delivers.
    from sitara.designs.render_delivery import deliver_render

    assert deliver_render(*queued[0]) == "sent"
    assert len(mail.outbox) == 1


def test_a_spent_allowance_does_not_spend_quota_the_other_kind_needs(settings):
    """Pressing Send on a render that has nothing left must not lock out a
    genuinely new one.

    The per-account rate counters are shared across both kinds, so an owner who
    keeps pressing a spent render could otherwise exhaust the quota the annotated
    composite needs for a send that has never been attempted. The refusal is
    therefore decided BEFORE the throttles — the same reasoning the old
    already-sent short-circuit carried, applied to the case that now exists.

    With the rate ceiling at 2 and the allowance at 1: the first plain send
    charges the rate, the two refusals are free, and the annotated send — the one
    that matters — still gets through."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 1
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 2
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, PLAIN).status_code == 202
    assert len(mail.outbox) == 1

    assert post_send(browser, token, version, PLAIN).status_code == 409
    assert post_send(browser, token, version, PLAIN).status_code == 409
    assert len(mail.outbox) == 1

    first_annotated = post_send(browser, token, version, ANNOTATED)

    assert first_annotated.status_code == 202
    assert len(mail.outbox) == 2
    assert mail.outbox[1].attachments[0][0] != mail.outbox[0].attachments[0][0]


def test_a_permanently_failed_render_can_be_sent_again_by_a_deliberate_press(settings):
    """The case that deserves it most, and one this phase improves.

    ``RETRY_EXHAUSTED`` means the automatic retry budget for ONE press is spent —
    that owner never received their copy, which is exactly why they would keep
    pressing Send. Before Phase 21 the state was terminal for the lifetime of the
    row, so every later press was answered 202 and sent nothing, forever. A
    failure consumes no part of the allowance, so a deliberate new press now
    starts a new attempt and delivers.

    Seeded directly rather than driven through two worker deaths, which is what it
    would otherwise take to reach this state."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER = 3
    browser, token, version = signed_in_browser()
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.RETRY_EXHAUSTED,
        attempt_count=DesignRenderDelivery.MAX_SEND_ATTEMPTS,
        claimed_at=timezone.now(),
    )

    assert post_send(browser, token, version, PLAIN).status_code == 202

    assert len(mail.outbox) == 1
    row = DesignRenderDelivery.objects.get(design_version=version, kind="plain")
    assert row.send_count == 1
    assert row.attempt_epoch == 2


def test_a_press_while_one_is_in_flight_is_not_re_enqueued(settings, monkeypatch):
    """The surviving half of "the pre-check skips the queue too".

    A send already claimed by a worker within its TTL has nothing to reserve, so
    no second task is queued — a mis-click cannot consume a second epoch, or with
    it a second of the owner's three sends."""
    settings.ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS = 900
    browser, token, version = signed_in_browser()
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.CLAIMED,
        attempt_count=1,
        claimed_at=timezone.now(),
    )

    enqueued = []
    monkeypatch.setattr(
        "sitara.designs.views.send_design_render.delay",
        lambda *args, **kwargs: enqueued.append(args),
    )

    assert post_send(browser, token, version).status_code == 202
    assert enqueued == []
    assert DesignRenderDelivery.objects.get(design_version=version).attempt_epoch == 1


def test_the_throttle_runs_after_ownership(settings):
    """A throttled caller must still not be able to tell an owned design from
    one that does not exist — so a foreign design is a 404 even once the
    caller's own quota is exhausted."""
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 1
    browser, token, version = signed_in_browser()
    _other, _other_token, foreign = signed_in_browser()

    assert post_send(browser, token, version).status_code == 202
    assert post_send(browser, token, version).status_code == 429

    assert post_send(browser, token, foreign).status_code == 404


# ---------------------------------------------------------------------------
# What reaches the queue, and the remaining HTTP and ownership edges
# ---------------------------------------------------------------------------


def test_only_identifiers_are_enqueued(monkeypatch):
    """No address, no bytes, no URL and no chosen filename crosses the broker,
    where they would rest in Redis in the clear.

    The epoch is the one thing added in Phase 21, and it is an integer: it says
    WHICH press this task is for and nothing about who or what."""
    captured = {}

    def capture(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr("sitara.designs.views.send_design_render.delay", capture)

    address = unique_email()
    browser, token, version = signed_in_browser(address)
    post_send(browser, token, version)

    assert captured["args"] == (str(version.pk), "plain", 1)
    assert captured["kwargs"] == {}
    payload = f"{captured['args']}{captured['kwargs']}"
    assert address not in payload
    assert version.image_storage_key not in payload


def test_each_endpoint_enqueues_its_own_kind(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "sitara.designs.views.send_design_render.delay",
        lambda *args, **kwargs: seen.append(args[1]),
    )
    browser, token, version = signed_in_browser()

    post_send(browser, token, version, PLAIN)
    post_send(browser, token, version, ANNOTATED)

    assert seen == ["plain", "annotated"]


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
@pytest.mark.parametrize("suffix", [PLAIN, ANNOTATED])
def test_the_verbs_that_mean_nothing_here_are_refused(method, suffix):
    """GET became a real method in Phase 21 (it reports send state), so this now
    pins the verbs that genuinely remain unsupported. A send is not idempotent and
    there is nothing to update or remove: an allowance that could be DELETEd would
    not be an allowance."""
    browser, token, version = signed_in_browser()
    response = send_json(
        browser,
        method,
        send_url(version.design_id, version.pk, suffix),
        None,
        token=token,
        ip=unique_ip(),
    )
    assert response.status_code == 405
    assert mail.outbox == []


def test_an_unauthenticated_stranger_cannot_reach_another_workspace():
    """A brand-new browser has no workspace, so an owned design is a 404 rather
    than a recipient error — ownership is resolved before anything else."""
    _owner, _token, version = signed_in_browser()
    stranger = csrf_client()
    stranger_token = bootstrap_csrf(stranger)

    response = post_send(stranger, stranger_token, version)

    assert response.status_code == 404


def test_signing_in_does_not_let_a_user_send_another_users_design():
    owner_address = unique_email()
    _owner, _token, version = signed_in_browser(owner_address)

    intruder = csrf_client()
    intruder_address = unique_email()
    register(intruder, intruder_address)
    login(intruder, intruder_address, STRONG_PASSWORD)
    intruder_token = bootstrap_csrf(intruder)

    response = post_send(intruder, intruder_token, version)
    assert response.status_code == 404
    assert mail.outbox == []
