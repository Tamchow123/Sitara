"""The phone-side upload endpoint (Phase 22, ADR 0026).

The grant is a bearer credential and the phase accepts that. What is NOT
accepted, and what these tests hold to account, is the credential doing
anything more than the one thing it is for:

* it adds a photograph to ONE design and there is no way to express another;
* it reads nothing — asserted structurally, against the code, not by observing
  that the customer's screen has no button;
* every unusable code answers identically, so it cannot be used to probe;
* the rights affirmation is given on the phone, by the person choosing the
  image, and an affirmation ticked earlier on the shop's iPad does not count;
* the plaintext reaches no log record and no Sentry payload;
* the design's reference cap holds under simultaneous uploads through one code.
"""

import io
import logging
import re
import threading
import uuid
from pathlib import Path

import pytest
from django.core.files.storage import default_storage
from django.db import connections
from django.utils import timezone
from PIL import Image

from sitara.designs.grant_service import create_reference_upload_grant
from sitara.designs.models import (
    Design,
    DesignInspirationUpload,
    ReferenceUploadGrant,
)

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_owned_design_id,
    csrf_client,
    unique_ip,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("inmemory_storage")]

UPLOAD_URL = "/api/v1/reference-uploads/"


def _png_bytes(*, size=(40, 60), colour=(200, 30, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def grant_for(design_id) -> str:
    """Mint through the service, returning the plaintext the phone would hold.

    The service rather than the HTTP endpoint: these tests are about what the
    PHONE can do with a code, and routing every one of them through the iPad's
    mint endpoint would couple them to that endpoint's own throttle."""
    _, plaintext = create_reference_upload_grant(Design.objects.get(pk=design_id))
    return plaintext


def post_upload(
    client,
    token_value,
    *,
    data=None,
    csrf=None,
    acknowledged="true",
    ip=None,
    omit_acknowledgement=False,
):
    csrf = csrf or bootstrap_csrf(client)
    body = {}
    if token_value is not None:
        body["grant_token"] = token_value
    if not omit_acknowledgement:
        body["rights_acknowledged"] = acknowledged
    if data is not None:
        upload = io.BytesIO(data)
        # A deliberately misleading filename and extension: neither is read.
        upload.name = "customer-photo.txt"
        body["image"] = upload
    return client.post(
        UPLOAD_URL,
        data=body,
        HTTP_X_CSRFTOKEN=csrf,
        REMOTE_ADDR=ip or unique_ip(),
    )


class TestTheOneThingItDoes:
    def test_a_phone_with_a_code_adds_a_photograph(self, settings):
        settings.MAX_INSPIRATION_IMAGES = 3
        ipad = csrf_client()
        design_id = create_owned_design_id(ipad)
        code = grant_for(design_id)
        # A brand-new client with no cookies from the shop's session: the phone
        # is a different browser on a different device, and the grant is its
        # entire authorisation.
        phone = csrf_client()

        response = post_upload(phone, code, data=_png_bytes())

        assert response.status_code == 201, response.content
        assert response.json() == {"upload": {"accepted": True}}
        assert DesignInspirationUpload.objects.filter(design_id=design_id).count() == 1

    def test_the_image_is_sanitised_exactly_as_the_ipad_s_own_upload_is(self):
        """Same service, same guarantees: a clean WebP, no original bytes kept,
        a server-generated key, and no filename anywhere near it."""
        ipad = csrf_client()
        design_id = create_owned_design_id(ipad)
        phone = csrf_client()

        assert post_upload(phone, grant_for(design_id), data=_png_bytes()).status_code == 201

        upload = DesignInspirationUpload.objects.get(design_id=design_id)
        assert upload.storage_key.startswith(f"design-uploads/{design_id}/")
        assert "customer-photo" not in upload.storage_key
        with default_storage.open(upload.storage_key, "rb") as handle:
            stored = handle.read()
        assert Image.open(io.BytesIO(stored)).format == "WEBP"
        assert stored != _png_bytes()

    def test_gps_and_camera_metadata_do_not_survive_the_handoff(self):
        """The realistic worst case for this endpoint specifically: a photograph
        straight off a phone, carrying the coordinates of the shop."""
        ipad = csrf_client()
        design_id = create_owned_design_id(ipad)
        buffer = io.BytesIO()
        image = Image.new("RGB", (48, 64), (30, 30, 30))
        exif = image.getexif()
        exif[271] = "ACME Phones"  # Make
        exif[270] = "outside 41 Green Street"  # ImageDescription
        gps = exif.get_ifd(0x8825)
        gps[1] = "N"
        gps[2] = (51.0, 30.0, 0.0)
        image.save(buffer, format="JPEG", exif=exif)

        phone = csrf_client()
        assert post_upload(phone, grant_for(design_id), data=buffer.getvalue()).status_code == 201

        upload = DesignInspirationUpload.objects.get(design_id=design_id)
        with default_storage.open(upload.storage_key, "rb") as handle:
            stored = Image.open(io.BytesIO(stored_bytes := handle.read()))
        assert dict(stored.getexif()) == {}
        assert b"ACME Phones" not in stored_bytes
        assert b"Green Street" not in stored_bytes

    def test_the_response_says_nothing_about_the_design(self):
        """A grant carries no read capability, so the reply reports only the
        consequence of the caller's own action — not the design's id, title,
        answers, versions, images, or how many references it already had."""
        ipad = csrf_client()
        design_id = create_owned_design_id(ipad, title="Ayesha, June wedding")
        phone = csrf_client()

        body = post_upload(phone, grant_for(design_id), data=_png_bytes()).json()

        assert set(body) == {"upload"}
        assert set(body["upload"]) == {"accepted"}
        serialised = str(body)
        assert design_id not in serialised
        assert "Ayesha" not in serialised

    def test_the_reply_cannot_be_used_to_count_what_was_already_there(self):
        """SEC-001 regression.

        The reply carried a remaining-slots count, which looked harmless and was
        not: the cap is on the DESIGN and ``MAX_INSPIRATION_IMAGES`` is public,
        so a phone that knows how many photographs it sent could subtract and
        recover how many references the design already had. The ordinary shop
        flow triggers it on the first upload — a stylist adds one on the iPad,
        then mints a code.

        Two designs, identical from the phone's side (one photograph sent
        through one code), differing only in what the shop put there first. The
        replies must be byte-identical."""
        shop = csrf_client()
        shop_csrf = bootstrap_csrf(shop)

        untouched = create_owned_design_id(shop, title="untouched")
        already_used = create_owned_design_id(shop, title="already used")
        # The stylist adds one from the iPad BEFORE minting the customer's code.
        ipad_image = io.BytesIO(_png_bytes(colour=(7, 7, 7)))
        ipad_image.name = "shop.png"
        assert (
            shop.post(
                f"{DESIGNS_URL}{already_used}/inspiration-uploads/",
                data={"rights_acknowledged": "true", "image": ipad_image},
                HTTP_X_CSRFTOKEN=shop_csrf,
                REMOTE_ADDR=unique_ip(),
            ).status_code
            == 201
        )

        first = post_upload(csrf_client(), grant_for(untouched), data=_png_bytes())
        second = post_upload(csrf_client(), grant_for(already_used), data=_png_bytes())

        assert first.status_code == second.status_code == 201
        assert first.content == second.content
        # And the prior state really did differ, or the comparison proves
        # nothing.
        assert DesignInspirationUpload.objects.filter(design_id=untouched).count() == 1
        assert DesignInspirationUpload.objects.filter(design_id=already_used).count() == 2

    def test_a_use_is_counted_against_the_grant(self):
        ipad = csrf_client()
        design_id = create_owned_design_id(ipad)
        code = grant_for(design_id)
        phone = csrf_client()

        assert post_upload(phone, code, data=_png_bytes(colour=(1, 1, 1))).status_code == 201
        assert post_upload(phone, code, data=_png_bytes(colour=(2, 2, 2))).status_code == 201

        grant = ReferenceUploadGrant.objects.get(design_id=design_id)
        assert grant.uses == 2
        assert grant.last_used_at is not None

    @pytest.mark.parametrize("fault", ["DatabaseError", "InterfaceError"])
    def test_a_lost_use_count_does_not_lose_the_photograph(self, fault, monkeypatch, caplog):
        """REL-001 and REL-002 regression.

        ``record_grant_use`` runs AFTER the upload has committed — the row is
        durable and the object is in storage. A transient database fault there
        must not be reported as a failed upload: the customer's natural retry
        with the same photograph would come back as ``duplicate_image``, which
        is a nonsensical answer to "it did not work", and she is standing in a
        shop while it happens. Losing an audit counter is the cheaper failure.

        Both fault types, because they are SIBLINGS in Django's hierarchy, not
        parent and child. A live database rejecting a live query raises
        ``DatabaseError``; a connection torn down under a pooler surfaces as
        ``InterfaceError``. A guard that caught only the first would leave the
        second reproducing the exact symptom."""
        import django.db

        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)
        exception_type = getattr(django.db, fault)

        def broken(*args, **kwargs):
            raise exception_type("connection reset")

        # Break the grant query ONLY for the after-commit counter write. It
        # used to be safe to patch the manager for the whole request, because
        # the counter was the only thing that queried it. The REL-003 fix added
        # a second, earlier query — the liveness re-check under the design row
        # lock — and a blanket patch would break that instead, testing the
        # opposite of what this test is about.
        import sitara.designs.grant_service as grant_service
        import sitara.designs.views as views

        real_record = grant_service.record_grant_use
        real_filter = grant_service.ReferenceUploadGrant.objects.filter

        def record_against_a_broken_database(grant, **kwargs):
            grant_service.ReferenceUploadGrant.objects.filter = broken
            try:
                return real_record(grant, **kwargs)
            finally:
                grant_service.ReferenceUploadGrant.objects.filter = real_filter

        # Patched on the VIEW: it imported the name directly, so replacing the
        # attribute on grant_service alone would leave the view calling the
        # original.
        monkeypatch.setattr(views, "record_grant_use", record_against_a_broken_database)
        with caplog.at_level(logging.WARNING):
            response = post_upload(csrf_client(), code, data=_png_bytes())

        assert response.status_code == 201, response.content
        assert DesignInspirationUpload.objects.filter(design_id=design_id).count() == 1
        # It is not silent: the lost counter is recorded, by type only.
        assert any("use not recorded" in record.getMessage() for record in caplog.records)
        assert not any("connection reset" in record.getMessage() for record in caplog.records)

    def test_the_upload_is_no_store(self):
        ipad = csrf_client()
        design_id = create_owned_design_id(ipad)
        phone = csrf_client()

        response = post_upload(phone, grant_for(design_id), data=_png_bytes())

        assert response["Cache-Control"] == "no-store"


class TestScope:
    def test_a_code_for_one_design_cannot_reach_another(self):
        """Not because a check refuses it — because there is nowhere to say it.

        The request carries no design id at all, so the strongest form of this
        assertion is that adding one changes nothing: the photograph still lands
        on the design the CODE names."""
        shop = csrf_client()
        design_a = create_owned_design_id(shop, title="A")
        design_b = create_owned_design_id(shop, title="B")
        code_for_a = grant_for(design_a)
        phone = csrf_client()

        csrf = bootstrap_csrf(phone)
        upload = io.BytesIO(_png_bytes())
        upload.name = "x.jpg"
        response = phone.post(
            UPLOAD_URL,
            data={
                "grant_token": code_for_a,
                "rights_acknowledged": "true",
                "image": upload,
                # Ignored: not a field of the serializer, and the serializer
                # rejects nothing it does not know because there is no design
                # id to reject. It simply has no effect.
                "design_id": design_b,
            },
            HTTP_X_CSRFTOKEN=csrf,
            REMOTE_ADDR=unique_ip(),
        )

        assert response.status_code == 201, response.content
        assert DesignInspirationUpload.objects.filter(design_id=design_a).count() == 1
        assert DesignInspirationUpload.objects.filter(design_id=design_b).count() == 0

    def test_a_design_id_in_the_path_is_not_a_route(self):
        """There is no per-design variant of this endpoint to find."""
        shop = csrf_client()
        design_id = create_owned_design_id(shop)

        response = csrf_client().post(f"{DESIGNS_URL}{design_id}/reference-uploads/")

        assert response.status_code == 404

    def test_two_codes_keep_their_own_designs(self):
        shop = csrf_client()
        design_a = create_owned_design_id(shop, title="A")
        design_b = create_owned_design_id(shop, title="B")
        code_a, code_b = grant_for(design_a), grant_for(design_b)

        assert (
            post_upload(csrf_client(), code_a, data=_png_bytes(colour=(1, 1, 1))).status_code == 201
        )
        assert (
            post_upload(csrf_client(), code_b, data=_png_bytes(colour=(2, 2, 2))).status_code == 201
        )

        assert DesignInspirationUpload.objects.filter(design_id=design_a).count() == 1
        assert DesignInspirationUpload.objects.filter(design_id=design_b).count() == 1


class TestThereIsNoReadPath:
    """A grant grants upload. Asserted against the code, not the interface.

    "The customer's screen has no button" is not a security property — it is a
    statement about one client. These tests are about what the SERVER will do
    for someone holding a valid code and an HTTP client."""

    def test_the_endpoint_answers_nothing_but_post(self):
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)
        phone = csrf_client()
        csrf = bootstrap_csrf(phone)

        for method in ("get", "put", "patch", "delete"):
            response = getattr(phone, method)(
                UPLOAD_URL,
                data={"grant_token": code},
                HTTP_X_CSRFTOKEN=csrf,
                REMOTE_ADDR=unique_ip(),
            )
            assert response.status_code == 405, (method, response.status_code)

    def test_a_valid_code_opens_no_other_endpoint(self):
        """Offered to every place a design is readable, in every way a token is
        normally passed. None of them accept it, so the phone stays a stranger."""
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)
        phone = csrf_client()
        bootstrap_csrf(phone)

        targets = [
            f"{DESIGNS_URL}{design_id}/",
            DESIGNS_URL,
            f"{DESIGNS_URL}{design_id}/validate/",
            # The narrow references read the iPad's own panel polls. It is the
            # closest thing in the API to "show me what this design holds", so
            # it is the one a future reader would most expect a grant to open.
            # It does not.
            f"{DESIGNS_URL}{design_id}/references/",
        ]
        for url in targets:
            for attempt in (
                {"HTTP_AUTHORIZATION": f"Bearer {code}"},
                {"HTTP_X_GRANT_TOKEN": code},
                {"QUERY_STRING": f"grant_token={code}"},
            ):
                response = phone.get(url, REMOTE_ADDR=unique_ip(), **attempt)
                # The list is an empty workspace for this stranger, a detail
                # read is the ordinary indistinguishable 404, and validate is
                # POST-only. Kept narrow deliberately: this package's fixture
                # enables the gallery, so a 503 here would be a regression, not
                # a configuration. What matters most is the last line — none of
                # them ever returns the shop's design, because a grant is not
                # an identity.
                assert response.status_code in (200, 404, 405), (url, attempt)
                assert design_id not in response.content.decode()

    def test_the_resolver_has_exactly_one_caller_in_the_application(self):
        """The structural half of the claim.

        A read path would have to start by turning a code into a grant, and
        there is one function that does that. If a second application call site
        ever appears, this fails and someone has to justify it — which is the
        point, because the non-goal is permanent (ADR 0026) and a future reader
        will not remember it."""
        api_root = Path(__file__).resolve().parents[3]
        callers = set()
        for path in api_root.rglob("*.py"):
            if "/tests/" in path.as_posix() or path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            # Skip the definition itself and its import in the caller.
            for line in text.splitlines():
                stripped = line.strip()
                if not re.search(r"\bresolve_reference_upload_grant\s*\(", stripped):
                    continue
                if stripped.startswith("def "):
                    continue
                callers.add(path.relative_to(api_root).as_posix())
        assert callers == {"sitara/designs/views.py"}, sorted(callers)


class TestEveryUnusableCodeAnswersTheSame:
    def _attempt(self, code, *, ip=None):
        return post_upload(csrf_client(), code, data=_png_bytes(), ip=ip)

    def test_expired_revoked_spent_and_unknown_are_indistinguishable(self, settings):
        settings.MAX_INSPIRATION_IMAGES = 1
        shop = csrf_client()

        expired_design = create_owned_design_id(shop, title="expired")
        expired_code = grant_for(expired_design)
        now = timezone.now()
        ReferenceUploadGrant.objects.filter(design_id=expired_design).update(
            created_at=now - timezone.timedelta(hours=2),
            expires_at=now - timezone.timedelta(hours=1),
        )

        revoked_design = create_owned_design_id(shop, title="revoked")
        revoked_code = grant_for(revoked_design)
        ReferenceUploadGrant.objects.filter(design_id=revoked_design).update(revoked_at=now)

        spent_design = create_owned_design_id(shop, title="spent")
        spent_code = grant_for(spent_design)
        assert self._attempt(spent_code).status_code == 201  # fills the one slot

        unknown_code = "not-a-real-code-at-all-but-the-right-shape"

        answers = [
            self._attempt(code) for code in (expired_code, revoked_code, spent_code, unknown_code)
        ]

        statuses = {response.status_code for response in answers}
        bodies = {response.content for response in answers}
        assert statuses == {404}
        assert len(bodies) == 1, [response.content for response in answers]
        assert answers[0].json()["error"]["code"] == "reference_upload_unavailable"
        # Nothing was written for any of the four.
        assert DesignInspirationUpload.objects.filter(design_id=expired_design).count() == 0
        assert DesignInspirationUpload.objects.filter(design_id=revoked_design).count() == 0
        assert DesignInspirationUpload.objects.filter(design_id=spent_design).count() == 1

    def test_a_revoked_code_stops_working_mid_session(self):
        """The iPad's revoke really does reach the phone — the grant is
        re-checked on every request, not trusted from when it was minted."""
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)
        phone = csrf_client()

        assert post_upload(phone, code, data=_png_bytes(colour=(1, 1, 1))).status_code == 201
        ReferenceUploadGrant.objects.filter(design_id=design_id).update(revoked_at=timezone.now())
        second = post_upload(phone, code, data=_png_bytes(colour=(2, 2, 2)))

        assert second.status_code == 404
        assert second.json()["error"]["code"] == "reference_upload_unavailable"
        assert DesignInspirationUpload.objects.filter(design_id=design_id).count() == 1

    def test_a_problem_with_the_caller_s_own_file_is_answered_honestly(self):
        """The other half of the rule. A refusal that says nothing about the
        design and that the person holding the phone can act on is NOT
        flattened into the generic 404 — being told "ask for a new code" when
        the code is fine and the photograph is a text file sends them back to
        the counter for nothing."""
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        phone = csrf_client()

        response = post_upload(phone, grant_for(design_id), data=b"this is not an image")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_image"

    def test_the_same_photograph_twice_is_a_conflict_not_a_dead_code(self):
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)
        phone = csrf_client()

        assert post_upload(phone, code, data=_png_bytes()).status_code == 201
        second = post_upload(phone, code, data=_png_bytes())

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "duplicate_image"


class TestTheAffirmationIsGivenOnThePhone:
    def test_an_upload_without_an_affirmation_is_refused(self):
        """Omitting the field is a refusal, not a default.

        Worth its own test because DRF gives ``BooleanField`` HTML-form
        semantics on a multipart body — an absent checkbox reads as False rather
        than as a missing required field. That lands on the service's own
        ``rights_not_acknowledged``, which is the right answer; what must never
        happen is an omission reading as consent."""
        shop = csrf_client()
        design_id = create_owned_design_id(shop)

        response = post_upload(
            csrf_client(), grant_for(design_id), data=_png_bytes(), omit_acknowledgement=True
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "rights_not_acknowledged"
        assert not DesignInspirationUpload.objects.filter(design_id=design_id).exists()

    def test_an_explicit_refusal_is_refused(self):
        shop = csrf_client()
        design_id = create_owned_design_id(shop)

        response = post_upload(
            csrf_client(), grant_for(design_id), data=_png_bytes(), acknowledged="false"
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "rights_not_acknowledged"
        assert not DesignInspirationUpload.objects.filter(design_id=design_id).exists()

    def test_a_tick_on_the_ipad_does_not_carry_across_the_handoff(self):
        """The affirmation is per-upload and per-person, and the whole point of
        the handoff is that a DIFFERENT person is now choosing the image. The
        stylist agreeing on the shop's screen cannot consent on the customer's
        behalf to their photograph being sent to an image provider."""
        ipad = csrf_client()
        design_id = create_owned_design_id(ipad)
        # The stylist affirms and uploads on the iPad. That upload is fine.
        ipad_csrf = bootstrap_csrf(ipad)
        ipad_image = io.BytesIO(_png_bytes(colour=(9, 9, 9)))
        ipad_image.name = "shop.png"
        assert (
            ipad.post(
                f"{DESIGNS_URL}{design_id}/inspiration-uploads/",
                data={"rights_acknowledged": "true", "image": ipad_image},
                HTTP_X_CSRFTOKEN=ipad_csrf,
                REMOTE_ADDR=unique_ip(),
            ).status_code
            == 201
        )

        # The phone, holding the code, now omits its own affirmation.
        response = post_upload(
            csrf_client(),
            grant_for(design_id),
            data=_png_bytes(colour=(3, 3, 3)),
            omit_acknowledgement=True,
        )

        assert response.status_code == 400
        # And the iPad's earlier upload is untouched — one, not two.
        assert DesignInspirationUpload.objects.filter(design_id=design_id).count() == 1

    def test_each_upload_records_its_own_affirmation(self):
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)
        phone = csrf_client()

        assert post_upload(phone, code, data=_png_bytes(colour=(1, 1, 1))).status_code == 201
        assert post_upload(phone, code, data=_png_bytes(colour=(2, 2, 2))).status_code == 201

        stamps = list(
            DesignInspirationUpload.objects.filter(design_id=design_id).values_list(
                "rights_acknowledged_at", flat=True
            )
        )
        assert len(stamps) == 2
        assert all(stamp is not None for stamp in stamps)


class TestTheSecretStaysSecret:
    def test_no_log_record_carries_the_plaintext(self, caplog):
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)
        phone = csrf_client()
        csrf = bootstrap_csrf(phone)

        with caplog.at_level(logging.DEBUG):
            assert post_upload(phone, code, data=_png_bytes(), csrf=csrf).status_code == 201
            # And a failing one, which is the likelier place for a helpful
            # developer to have logged "what code was that?".
            assert (
                post_upload(phone, "wrong-" + code, data=_png_bytes(), csrf=csrf).status_code == 404
            )

        for record in caplog.records:
            rendered = record.getMessage() + str(record.args or "")
            assert code not in rendered
            assert code[:16] not in rendered

    def test_a_sentry_event_carrying_the_body_is_scrubbed(self):
        """The token travels in the request BODY, which is exactly what the
        ``before_send`` scrubber drops. Asserted here rather than assumed,
        because "it is in the body, so it is fine" is only true while the
        scrubber keeps dropping bodies."""
        from config.sentry import scrub_event

        code = "a-plaintext-handoff-secret"
        event = scrub_event(
            {
                "request": {
                    "url": "http://shop.test/api/v1/reference-uploads/",
                    "method": "POST",
                    "data": {"grant_token": code, "rights_acknowledged": "true"},
                    "cookies": {"sitara_sessionid": "s"},
                    "query_string": f"grant_token={code}",
                },
                "exception": {
                    "values": [{"type": "GrantUnusable", "value": f"code {code} is dead"}]
                },
            }
        )

        assert code not in str(event)
        assert "data" not in event["request"]
        assert "query_string" not in event["request"]

    def test_the_token_is_write_only_and_never_echoed(self):
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)

        response = post_upload(csrf_client(), code, data=_png_bytes())

        assert code not in response.content.decode()

    def test_a_rejected_upload_does_not_echo_the_token_either(self):
        shop = csrf_client()
        create_owned_design_id(shop)
        code = "definitely-not-a-real-code"

        response = post_upload(csrf_client(), code, data=_png_bytes())

        assert response.status_code == 404
        assert code not in response.content.decode()


class TestTheUsualUnsafeRequestProtections:
    def test_csrf_is_required(self):
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)
        phone = csrf_client()
        bootstrap_csrf(phone)

        upload = io.BytesIO(_png_bytes())
        upload.name = "x.png"
        response = phone.post(
            UPLOAD_URL,
            data={"grant_token": code, "rights_acknowledged": "true", "image": upload},
            REMOTE_ADDR=unique_ip(),
        )

        assert response.status_code == 403
        assert not DesignInspirationUpload.objects.exists()

    def test_an_oversized_body_is_refused_before_anything_is_read(self, settings):
        """The wire-level gate is outside ``csrf_protect`` here too.

        The payload is deliberately NOT well-formed multipart: if the gate ran
        any later, Django's own parser would already have received the body (the
        CSRF check reads ``request.POST`` first) and blown up on it. A clean 413
        is itself the proof of ordering — and it matters more on this endpoint
        than on the iPad's, because a phone on a bad connection is the one most
        likely to send an enormous camera original."""
        settings.USER_UPLOAD_MAX_BYTES = 1_000
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        phone = csrf_client()

        response = phone.post(
            UPLOAD_URL,
            data=b"x" * 200_000,
            content_type="multipart/form-data; boundary=zzz",
            HTTP_X_CSRFTOKEN=bootstrap_csrf(phone),
            REMOTE_ADDR=unique_ip(),
        )

        assert response.status_code == 413, response.content
        assert response.json()["error"]["code"] == "upload_too_large"
        assert response["Cache-Control"] == "no-store"
        assert not DesignInspirationUpload.objects.filter(design_id=design_id).exists()

    def test_the_size_gate_needs_no_code_and_so_reveals_nothing(self, settings):
        """It runs before the token is even parsed, so an oversized body gets
        one answer whether the code is real or invented."""
        settings.USER_UPLOAD_MAX_BYTES = 1_000
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        real = grant_for(design_id)
        phone = csrf_client()
        csrf = bootstrap_csrf(phone)

        answers = {
            phone.post(
                UPLOAD_URL,
                data=b"x" * 200_000,
                content_type="multipart/form-data; boundary=zzz",
                HTTP_X_CSRFTOKEN=csrf,
                REMOTE_ADDR=unique_ip(),
            ).content
            for _ in (real, "invented")
        }

        assert len(answers) == 1

    def test_a_missing_image_is_a_validation_error(self):
        shop = csrf_client()
        design_id = create_owned_design_id(shop)

        response = post_upload(csrf_client(), grant_for(design_id), data=None)

        assert response.status_code == 400
        assert "image" in response.json()["error"]["fields"]

    def test_a_missing_token_is_a_validation_error_not_a_crash(self):
        response = post_upload(csrf_client(), None, data=_png_bytes())

        assert response.status_code == 400
        assert "grant_token" in response.json()["error"]["fields"]

    def test_an_absurdly_long_token_is_bounded_before_it_is_hashed(self):
        response = post_upload(csrf_client(), "x" * 5000, data=_png_bytes())

        assert response.status_code == 400
        assert "grant_token" in response.json()["error"]["fields"]


class TestThrottling:
    def test_guessing_is_bounded_per_address(self, settings):
        """The window that actually matters: someone with no code at all,
        working through the space from one address."""
        settings.REFERENCE_UPLOAD_GRANT_IP_LIMIT = 3
        settings.REFERENCE_UPLOAD_GRANT_LIMIT = 1000
        phone = csrf_client()
        csrf = bootstrap_csrf(phone)
        address = unique_ip()

        statuses = [
            post_upload(phone, f"guess-{n}", data=_png_bytes(), csrf=csrf, ip=address).status_code
            for n in range(4)
        ]

        assert statuses[:3] == [404, 404, 404]
        assert statuses[3] == 429

    def test_one_code_cannot_be_hammered_from_many_addresses(self, settings):
        settings.REFERENCE_UPLOAD_GRANT_LIMIT = 2
        settings.REFERENCE_UPLOAD_GRANT_IP_LIMIT = 1000
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)
        phone = csrf_client()
        csrf = bootstrap_csrf(phone)

        first = post_upload(phone, code, data=_png_bytes(colour=(1, 1, 1)), csrf=csrf)
        second = post_upload(phone, code, data=_png_bytes(colour=(2, 2, 2)), csrf=csrf)
        third = post_upload(phone, code, data=_png_bytes(colour=(3, 3, 3)), csrf=csrf)

        assert first.status_code == 201
        assert second.status_code == 201
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "reference_upload_rate_limited"
        assert int(third["Retry-After"]) > 0

    def test_a_real_code_and_an_unknown_one_throttle_identically(self, settings):
        """The per-code window runs BEFORE resolution for this reason. If it ran
        after, a real code would start answering 429 while an unknown one kept
        answering 404 at the same request count — which is a distinguisher."""
        settings.REFERENCE_UPLOAD_GRANT_LIMIT = 1
        settings.REFERENCE_UPLOAD_GRANT_IP_LIMIT = 1000
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        real = grant_for(design_id)
        fake = "an-unknown-code-of-a-similar-length"
        phone = csrf_client()
        csrf = bootstrap_csrf(phone)

        # Burn each code's single allowance.
        post_upload(phone, real, data=_png_bytes(colour=(1, 1, 1)), csrf=csrf)
        post_upload(phone, fake, data=_png_bytes(colour=(1, 1, 1)), csrf=csrf)

        real_again = post_upload(phone, real, data=_png_bytes(colour=(2, 2, 2)), csrf=csrf)
        fake_again = post_upload(phone, fake, data=_png_bytes(colour=(2, 2, 2)), csrf=csrf)

        assert real_again.status_code == fake_again.status_code == 429
        assert real_again.content == fake_again.content

    def test_the_throttle_fails_closed_on_a_cache_outage(self, monkeypatch):
        """503, never 429: an infrastructure fault is not reported to the caller
        as their own abuse. And never a silent pass — a cache outage must not
        quietly remove the only bound guessing has."""
        from sitara.accounts.rate_limits import RateLimitUnavailable

        def unreachable(*args, **kwargs):
            raise RateLimitUnavailable("cache down")

        monkeypatch.setattr("sitara.designs.grant_service.check_and_count", unreachable)
        shop = csrf_client()
        design_id = create_owned_design_id(shop)
        code = grant_for(design_id)

        response = post_upload(csrf_client(), code, data=_png_bytes())

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "reference_upload_throttle_unavailable"
        assert not DesignInspirationUpload.objects.filter(design_id=design_id).exists()

    def test_the_address_window_runs_ahead_of_the_serializer(self, settings):
        """A flood of malformed bodies is bounded too — the address counter is
        the first thing in the view.

        Named for what is actually true. It is NOT ahead of body parsing:
        ``csrf_protect`` reads ``request.POST`` before falling back to the
        header, which parses the multipart body before any check inside the view
        runs. The gate that stands in front of the parser is the
        ``Content-Length`` wrapper outside ``csrf_protect``, tested separately
        above."""
        settings.REFERENCE_UPLOAD_GRANT_IP_LIMIT = 1
        phone = csrf_client()
        csrf = bootstrap_csrf(phone)
        address = unique_ip()

        first = post_upload(phone, None, data=None, csrf=csrf, ip=address)
        second = post_upload(phone, None, data=None, csrf=csrf, ip=address)

        assert first.status_code == 400
        assert second.status_code == 429


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("inmemory_storage")
def test_simultaneous_uploads_through_one_code_cannot_exceed_the_cap(settings):
    """Two photographs sent at once from one phone (or from a phone and the
    iPad together) serialise on the Design row inside the upload service, so the
    cap holds. The grant adds no second, unlocked answer to "is there room?" —
    which is exactly why ``resolve_reference_upload_grant`` deliberately does
    not check spentness itself.

    The loser gets the generic 404, not a "limit reached" that would confirm the
    design is real."""
    settings.MAX_INSPIRATION_IMAGES = 1
    shop = csrf_client()
    design_id = create_owned_design_id(shop)
    code = grant_for(design_id)

    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def attempt(colour):
        phone = csrf_client()
        csrf = bootstrap_csrf(phone)
        try:
            barrier.wait(timeout=10)
            statuses.append(
                post_upload(phone, code, data=_png_bytes(colour=colour), csrf=csrf).status_code
            )
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=attempt, args=((1, 1, 1),)),
        threading.Thread(target=attempt, args=((2, 2, 2),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(statuses) == [201, 404], statuses
    assert DesignInspirationUpload.objects.filter(design_id=design_id).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("inmemory_storage")
def test_a_code_dies_with_its_design():
    """Nothing to clean up separately, and nothing left resolvable afterwards."""
    shop = csrf_client()
    design_id = create_owned_design_id(shop)
    code = grant_for(design_id)

    Design.objects.filter(pk=design_id).delete()

    assert not ReferenceUploadGrant.objects.filter(design_id=design_id).exists()
    response = post_upload(csrf_client(), code, data=_png_bytes())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "reference_upload_unavailable"


def test_an_unknown_design_uuid_is_not_a_code(settings):
    """A design id is public-ish (it is in the shop's own URL bar). It must not
    be usable as a handoff code by shape or by accident."""
    shop = csrf_client()
    design_id = create_owned_design_id(shop)

    response = post_upload(csrf_client(), design_id, data=_png_bytes())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "reference_upload_unavailable"
    assert not DesignInspirationUpload.objects.filter(design_id=design_id).exists()
    assert uuid.UUID(design_id)  # it really was a well-formed design id
