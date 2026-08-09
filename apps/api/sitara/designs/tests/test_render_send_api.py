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


def post_send(browser, token, version, suffix=PLAIN, ip=None):
    return send_json(
        browser,
        "post",
        send_url(version.design_id, version.pk, suffix),
        None,
        token=token,
        ip=ip or unique_ip(),
    )


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
    this test keeps its meaning if the body handling ever changes."""
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


def test_an_already_sent_render_does_not_spend_quota_the_other_kind_needs(settings):
    """Repeated sends of something already delivered must not lock out a
    genuinely new one.

    ``_claim`` makes a repeat send a no-op, but the endpoint used to charge all
    four counters before enqueuing the task that would discover that — and the
    per-account counters are shared across both kinds. So an owner unsure
    whether the first send worked could, by clicking again, exhaust the quota
    the annotated composite needs for a send that has never been attempted.

    With the ceiling at 2: the first plain send charges it, the two repeats are
    free, and the annotated send — the one that matters — still gets through.
    Without the pre-check the third plain post is already refused and the
    annotated one never had a chance."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 2
    browser, token, version = signed_in_browser()

    assert post_send(browser, token, version, PLAIN).status_code == 202
    assert len(mail.outbox) == 1

    assert post_send(browser, token, version, PLAIN).status_code == 202
    assert post_send(browser, token, version, PLAIN).status_code == 202
    assert len(mail.outbox) == 1, "a repeat send must not deliver a second copy"

    first_annotated = post_send(browser, token, version, ANNOTATED)

    assert first_annotated.status_code == 202
    assert len(mail.outbox) == 2
    assert mail.outbox[1].attachments[0][0] != mail.outbox[0].attachments[0][0]


def test_a_permanently_failed_render_does_not_spend_quota_either(settings):
    """The same protection for the case that deserves it more.

    ``RETRY_EXHAUSTED`` was initially left out of the pre-check, on the reasoning
    that it means the send FAILED so answering 202 would be misleading. That
    reasoning did not hold: the 202 is byte-identical whether the pre-check fires
    or not, because ``_claim`` no-ops on both terminal states anyway. So the
    exclusion protected nothing and left the worse case exposed — this owner
    never received their copy, which is exactly why they would keep pressing
    Send.

    Seeded directly rather than driven through two worker deaths, which is what
    it would otherwise take to reach this state."""
    settings.ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR = 2
    browser, token, version = signed_in_browser()
    DesignRenderDelivery.objects.create(
        design_version=version,
        kind=DesignRenderDelivery.PLAIN,
        state=DesignRenderDelivery.RETRY_EXHAUSTED,
        attempt_count=DesignRenderDelivery.MAX_SEND_ATTEMPTS,
        claimed_at=timezone.now(),
    )

    for _ in range(4):
        assert post_send(browser, token, version, PLAIN).status_code == 202
    assert mail.outbox == [], "a permanently failed delivery must not send"

    # The quota those four no-ops would otherwise have spent is still there for
    # the annotated composite, which has never been attempted.
    settings.CELERY_TASK_ALWAYS_EAGER = True
    assert post_send(browser, token, version, ANNOTATED).status_code == 202
    assert len(mail.outbox) == 1


def test_an_already_sent_render_is_not_re_enqueued(settings, monkeypatch):
    """The pre-check skips the queue too, not just the counters."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    browser, token, version = signed_in_browser()
    assert post_send(browser, token, version).status_code == 202

    enqueued = []
    monkeypatch.setattr(
        "sitara.designs.views.send_design_render.delay",
        lambda *args, **kwargs: enqueued.append(args),
    )

    assert post_send(browser, token, version).status_code == 202
    assert enqueued == []


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
    """No address, no bytes and no URL crosses the broker, where they would rest
    in Redis in the clear."""
    captured = {}

    def capture(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr("sitara.designs.views.send_design_render.delay", capture)

    address = unique_email()
    browser, token, version = signed_in_browser(address)
    post_send(browser, token, version)

    assert captured["args"] == (str(version.pk), "plain")
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


def test_a_get_is_not_allowed():
    browser, _token, version = signed_in_browser()
    response = browser.get(send_url(version.design_id, version.pk, PLAIN))
    assert response.status_code == 405


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
