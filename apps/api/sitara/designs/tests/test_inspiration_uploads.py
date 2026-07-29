"""User inspiration uploads (Phase 16B, T8).

Covers the whole endpoint contract: CSRF, private ownership (a foreign design
and a nonexistent one are one indistinguishable 404), the sanitisation matrix,
the rights affirmation, the shared preset+upload cap under concurrency, storage
cleanup on failure, and the guarantee that no storage key, hash or byte size
ever leaves the server.
"""

import io
import threading
import uuid

import pytest
from django.core.files.storage import default_storage
from django.db import DatabaseError, connections
from PIL import Image

from sitara.designs.models import Design, DesignInspirationUpload
from sitara.designs.upload_service import (
    InspirationUploadError,
    create_inspiration_upload,
    delete_inspiration_upload,
)

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_owned_design_id,
    csrf_client,
    unique_ip,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("inmemory_storage")]


def _png_bytes(*, size=(40, 60), colour=(200, 30, 60), mode="RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes(*, size=(40, 60), colour=(10, 120, 90)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def _animated_webp_bytes() -> bytes:
    buffer = io.BytesIO()
    frames = [Image.new("RGB", (32, 32), (i * 40, 10, 10)) for i in range(1, 4)]
    frames[0].save(buffer, format="WEBP", save_all=True, append_images=frames[1:], duration=40)
    return buffer.getvalue()


def uploads_url(design_id) -> str:
    return f"{DESIGNS_URL}{design_id}/inspiration-uploads/"


def upload_url(design_id, upload_id) -> str:
    return f"{DESIGNS_URL}{design_id}/inspiration-uploads/{upload_id}/"


def image_url(design_id, upload_id) -> str:
    return f"{DESIGNS_URL}{design_id}/inspiration-uploads/{upload_id}/image/"


def post_upload(client, design_id, *, data=None, token=None, acknowledged="true", ip=None):
    token = token or bootstrap_csrf(client)
    body = {"rights_acknowledged": acknowledged}
    if data is not None:
        upload = io.BytesIO(data)
        # A deliberately misleading filename and extension: neither is read.
        upload.name = "not-really.txt"
        body["image"] = upload
    return client.post(
        uploads_url(design_id),
        data=body,
        HTTP_X_CSRFTOKEN=token,
        REMOTE_ADDR=ip or unique_ip(),
    )


class TestUploadSuccess:
    def test_a_png_is_sanitised_into_a_webp_and_returned(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = post_upload(client, design_id, data=_png_bytes())

        assert response.status_code == 201, response.content
        payload = response.json()["upload"]
        assert payload["position"] == 1
        assert payload["width"] == 40 and payload["height"] == 60
        assert payload["rights_acknowledged_at"]
        # Nothing private is ever surfaced.
        assert set(payload) == {
            "id",
            "position",
            "width",
            "height",
            "rights_acknowledged_at",
            "created_at",
        }

        upload = DesignInspirationUpload.objects.get(pk=payload["id"])
        assert upload.storage_key.startswith(f"design-uploads/{design_id}/")
        # The stored object is a real, clean WebP — never the original bytes.
        with default_storage.open(upload.storage_key, "rb") as handle:
            stored = handle.read()
        assert Image.open(io.BytesIO(stored)).format == "WEBP"
        assert stored != _png_bytes()

    def test_exif_and_metadata_do_not_survive(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        # A JPEG carrying EXIF: an image description, the camera make and an
        # orientation flag — the kinds of tag that can carry identifying data.
        buffer = io.BytesIO()
        image = Image.new("RGB", (48, 64), (30, 30, 30))
        exif = image.getexif()
        exif[0x010E] = "a private description"
        exif[0x010F] = "SomeCamera"
        exif[0x0112] = 1
        image.save(buffer, format="JPEG", exif=exif.tobytes())

        response = post_upload(client, design_id, data=buffer.getvalue())

        assert response.status_code == 201, response.content
        upload = DesignInspirationUpload.objects.get(pk=response.json()["upload"]["id"])
        with default_storage.open(upload.storage_key, "rb") as handle:
            stored = handle.read()
        decoded = Image.open(io.BytesIO(stored))
        assert not decoded.getexif()
        assert b"a private description" not in stored

    def test_the_upload_appears_on_the_design_detail(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        post_upload(client, design_id, data=_png_bytes())

        detail = client.get(f"{DESIGNS_URL}{design_id}/").json()

        assert len(detail["inspiration_uploads"]) == 1
        assert "storage_key" not in detail["inspiration_uploads"][0]

    def test_positions_increment_per_design(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        first = post_upload(client, design_id, data=_png_bytes(colour=(1, 2, 3)))
        second = post_upload(client, design_id, data=_png_bytes(colour=(9, 9, 9)))
        assert [first.json()["upload"]["position"], second.json()["upload"]["position"]] == [1, 2]

    def test_the_owner_can_stream_the_sanitised_image(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        upload_id = post_upload(client, design_id, data=_png_bytes()).json()["upload"]["id"]

        response = client.get(image_url(design_id, upload_id))

        assert response.status_code == 200
        assert response["Content-Type"] == "image/webp"
        assert response["Cache-Control"] == "no-store"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert Image.open(io.BytesIO(response.content)).format == "WEBP"


class TestUploadRejection:
    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"not an image at all",
            # A valid PNG header with a truncated body.
            _png_bytes()[:20],
        ],
    )
    def test_non_images_are_rejected(self, data):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        response = post_upload(client, design_id, data=data)
        assert response.status_code == 400, response.content
        assert not DesignInspirationUpload.objects.exists()

    def test_an_animated_webp_is_rejected(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        response = post_upload(client, design_id, data=_animated_webp_bytes())
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_image"
        assert not DesignInspirationUpload.objects.exists()

    def test_an_oversized_upload_is_rejected_before_decoding(self, settings):
        settings.USER_UPLOAD_MAX_BYTES = 50
        client = csrf_client()
        design_id = create_owned_design_id(client)
        response = post_upload(client, design_id, data=_jpeg_bytes(size=(400, 600)))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_image"

    def test_a_decompression_bomb_is_rejected_by_its_declared_dimensions(self, settings):
        settings.USER_UPLOAD_MAX_IMAGE_PIXELS = 100
        client = csrf_client()
        design_id = create_owned_design_id(client)
        response = post_upload(client, design_id, data=_png_bytes(size=(200, 200)))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_image"

    def test_the_rights_affirmation_is_required(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        response = post_upload(client, design_id, data=_png_bytes(), acknowledged="false")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "rights_not_acknowledged"
        assert not DesignInspirationUpload.objects.exists()

    def test_a_missing_file_is_a_validation_error(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        response = post_upload(client, design_id, data=None)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_failed"

    def test_the_same_image_twice_is_rejected(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        assert post_upload(client, design_id, data=_png_bytes()).status_code == 201
        second = post_upload(client, design_id, data=_png_bytes())
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "duplicate_image"
        assert DesignInspirationUpload.objects.count() == 1

    def test_a_rejected_second_upload_leaves_no_orphaned_object(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        post_upload(client, design_id, data=_png_bytes())
        keys_before = set(_all_storage_keys())
        post_upload(client, design_id, data=_png_bytes())
        assert set(_all_storage_keys()) == keys_before


def _all_storage_keys() -> list[str]:
    directories, files = default_storage.listdir("")
    keys: list[str] = []
    pending = [(directory, directory) for directory in directories]
    keys.extend(files)
    while pending:
        prefix, path = pending.pop()
        sub_directories, sub_files = default_storage.listdir(path)
        keys.extend(f"{prefix}/{name}" for name in sub_files)
        pending.extend((f"{prefix}/{name}", f"{path}/{name}") for name in sub_directories)
    return keys


class TestPrivacyAndCsrf:
    def test_another_sessions_design_is_an_indistinguishable_404(self):
        owner = csrf_client()
        design_id = create_owned_design_id(owner)
        stranger = csrf_client()

        response = post_upload(stranger, design_id, data=_png_bytes())

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        assert not DesignInspirationUpload.objects.exists()

    def test_a_nonexistent_design_answers_the_same_way(self):
        client = csrf_client()
        response = post_upload(client, "11111111-1111-4111-8111-111111111111", data=_png_bytes())
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_another_sessions_upload_cannot_be_read_or_deleted(self):
        owner = csrf_client()
        design_id = create_owned_design_id(owner)
        upload_id = post_upload(owner, design_id, data=_png_bytes()).json()["upload"]["id"]
        stranger = csrf_client()

        assert stranger.get(image_url(design_id, upload_id)).status_code == 404
        deleted = stranger.delete(
            upload_url(design_id, upload_id),
            HTTP_X_CSRFTOKEN=bootstrap_csrf(stranger),
            REMOTE_ADDR=unique_ip(),
        )
        assert deleted.status_code == 404
        assert DesignInspirationUpload.objects.count() == 1

    def test_an_upload_from_another_design_is_404_even_for_its_owner(self):
        client = csrf_client()
        first = create_owned_design_id(client)
        second = create_owned_design_id(client)
        upload_id = post_upload(client, first, data=_png_bytes()).json()["upload"]["id"]

        assert client.get(image_url(second, upload_id)).status_code == 404

    def test_an_upload_without_a_csrf_token_is_rejected(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        upload = io.BytesIO(_png_bytes())
        upload.name = "x.png"

        response = client.post(
            uploads_url(design_id),
            data={"image": upload, "rights_acknowledged": "true"},
            REMOTE_ADDR=unique_ip(),
        )

        assert response.status_code == 403
        assert not DesignInspirationUpload.objects.exists()

    def test_a_delete_without_a_csrf_token_is_rejected(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        upload_id = post_upload(client, design_id, data=_png_bytes()).json()["upload"]["id"]

        response = client.delete(upload_url(design_id, upload_id), REMOTE_ADDR=unique_ip())

        assert response.status_code == 403
        assert DesignInspirationUpload.objects.count() == 1


class TestDeletion:
    def test_the_owner_can_remove_an_upload_and_its_object(self):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        upload_id = post_upload(client, design_id, data=_png_bytes()).json()["upload"]["id"]
        storage_key = DesignInspirationUpload.objects.get(pk=upload_id).storage_key

        response = client.delete(
            upload_url(design_id, upload_id),
            HTTP_X_CSRFTOKEN=bootstrap_csrf(client),
            REMOTE_ADDR=unique_ip(),
        )

        assert response.status_code == 204
        assert not DesignInspirationUpload.objects.exists()
        assert not default_storage.exists(storage_key)

    def test_a_storage_failure_keeps_the_row(self, monkeypatch):
        client = csrf_client()
        design_id = create_owned_design_id(client)
        upload_id = post_upload(client, design_id, data=_png_bytes()).json()["upload"]["id"]

        def explode(*args, **kwargs):
            raise OSError("storage down")

        monkeypatch.setattr(default_storage, "delete", explode)
        response = client.delete(
            upload_url(design_id, upload_id),
            HTTP_X_CSRFTOKEN=bootstrap_csrf(client),
            REMOTE_ADDR=unique_ip(),
        )

        assert response.status_code == 503
        # The row survives so the bytes stay reachable by a retry or the sweeper.
        assert DesignInspirationUpload.objects.filter(pk=upload_id).exists()

    def test_a_row_delete_failure_is_a_controlled_json_error(self, monkeypatch):
        # REL-004: the object is already gone by then, so a transient database
        # failure must still answer with the JSON envelope a client can retry
        # on — never an unhandled HTML 500.
        client = csrf_client()
        design_id = create_owned_design_id(client)
        upload_id = post_upload(client, design_id, data=_png_bytes()).json()["upload"]["id"]

        class _ExplodingManager:
            def filter(self, *args, **kwargs):
                raise DatabaseError("deadlock detected")

        class _ExplodingModel:
            objects = _ExplodingManager()

        # Bound in the SERVICE module only, so the view's own ownership lookup
        # still resolves the upload and the failure lands where it is meant to.
        monkeypatch.setattr(
            "sitara.designs.upload_service.DesignInspirationUpload", _ExplodingModel
        )
        response = client.delete(
            upload_url(design_id, upload_id),
            HTTP_X_CSRFTOKEN=bootstrap_csrf(client),
            REMOTE_ADDR=unique_ip(),
        )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"


class TestSharedInspirationBudget:
    def test_uploads_are_capped_at_the_shared_limit(self, settings):
        settings.MAX_INSPIRATION_IMAGES = 2
        client = csrf_client()
        design_id = create_owned_design_id(client)
        assert post_upload(client, design_id, data=_png_bytes(colour=(1, 1, 1))).status_code == 201
        assert post_upload(client, design_id, data=_png_bytes(colour=(2, 2, 2))).status_code == 201

        third = post_upload(client, design_id, data=_png_bytes(colour=(3, 3, 3)))

        assert third.status_code == 409
        assert third.json()["error"]["code"] == "inspiration_limit_reached"
        assert DesignInspirationUpload.objects.count() == 2

    def test_uploads_consume_the_preset_budget(self, settings):
        from sitara.designs.services import DraftUpdateError, update_design_draft

        settings.MAX_INSPIRATION_IMAGES = 1
        client = csrf_client()
        design_id = create_owned_design_id(client)
        post_upload(client, design_id, data=_png_bytes())
        design = Design.objects.get(pk=design_id)

        with pytest.raises(DraftUpdateError) as excinfo:
            update_design_draft(
                design, inspiration_asset_ids=["11111111-1111-4111-8111-111111111111"]
            )

        assert "inspiration_asset_ids" in excinfo.value.field_errors

    def test_clearing_presets_is_never_blocked_by_the_upload_count(self, settings):
        # REL-006: if the cap were ever lowered below an existing upload count,
        # the remaining budget goes negative — and the one action that helps
        # (selecting nothing) must not be the thing it refuses.
        from sitara.designs.services import update_design_draft

        client = csrf_client()
        design_id = create_owned_design_id(client)
        post_upload(client, design_id, data=_png_bytes(colour=(4, 4, 4)))
        post_upload(client, design_id, data=_png_bytes(colour=(5, 5, 5)))
        settings.MAX_INSPIRATION_IMAGES = 1
        design = Design.objects.get(pk=design_id)

        update_design_draft(design, inspiration_asset_ids=[])

        assert design.inspiration_selections.count() == 0

    def test_a_storage_write_failure_creates_no_row(self, monkeypatch):
        client = csrf_client()
        design_id = create_owned_design_id(client)

        def explode(*args, **kwargs):
            raise OSError("bucket unreachable")

        monkeypatch.setattr(default_storage, "save", explode)
        response = post_upload(client, design_id, data=_png_bytes())

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"
        assert not DesignInspirationUpload.objects.exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("inmemory_storage")
def test_concurrent_uploads_cannot_exceed_the_limit(settings):
    """Two simultaneous uploads for one design serialise on the design row, so
    the second sees the first's row and is refused — the cap is a guarantee, not
    a race."""
    settings.MAX_INSPIRATION_IMAGES = 1
    client = csrf_client()
    design_id = create_owned_design_id(client)
    design = Design.objects.get(pk=design_id)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def attempt(colour):
        try:
            barrier.wait(timeout=10)
            upload = io.BytesIO(_png_bytes(colour=colour))
            upload.name = "x"
            upload.size = len(upload.getvalue())
            create_inspiration_upload(design, upload, rights_acknowledged=True)
            outcomes.append("created")
        except InspirationUploadError as exc:
            outcomes.append(exc.code)
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=attempt, args=((1, 1, 1),)),
        threading.Thread(target=attempt, args=((2, 2, 2),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert sorted(outcomes) == ["created", "inspiration_limit_reached"]
    assert DesignInspirationUpload.objects.filter(design=design).count() == 1


class TestPositionReuse:
    def test_a_freed_slot_can_be_filled_again(self, settings):
        """REL-001 regression: positions are REUSED, not monotonic.

        Deleting anything other than the highest-positioned upload and adding a
        replacement is an ordinary "swap this photo" flow. A monotonic
        MAX(position)+1 would exceed the model's position bound and fail
        identically on every retry, permanently stranding a free slot."""
        settings.MAX_INSPIRATION_IMAGES = 3
        client = csrf_client()
        design_id = create_owned_design_id(client)
        ids = [
            post_upload(client, design_id, data=_png_bytes(colour=(n, n, n))).json()["upload"]["id"]
            for n in (1, 2, 3)
        ]
        # Remove the FIRST one, leaving positions 2 and 3 in use.
        assert (
            client.delete(
                upload_url(design_id, ids[0]),
                HTTP_X_CSRFTOKEN=bootstrap_csrf(client),
                REMOTE_ADDR=unique_ip(),
            ).status_code
            == 204
        )

        replacement = post_upload(client, design_id, data=_png_bytes(colour=(9, 8, 7)))

        assert replacement.status_code == 201, replacement.content
        assert replacement.json()["upload"]["position"] == 1
        assert DesignInspirationUpload.objects.filter(design_id=design_id).count() == 3


class TestAbuseBounds:
    def test_an_oversized_body_is_refused_before_the_parser_reads_it(self, settings):
        """REL-002 regression: Content-Length is judged before anything reads the
        body.

        The payload here is deliberately NOT well-formed multipart: if the gate
        ran any later than it does, Django's own parser would have already
        received the body and blown up on it (the CSRF check reads
        ``request.POST`` before falling back to the header), so a clean 413 is
        itself the proof of ordering."""
        settings.USER_UPLOAD_MAX_BYTES = 1_000
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = client.post(
            uploads_url(design_id),
            data=b"x" * 200_000,
            content_type="multipart/form-data; boundary=zzz",
            HTTP_X_CSRFTOKEN=bootstrap_csrf(client),
            REMOTE_ADDR=unique_ip(),
        )

        assert response.status_code == 413, response.content
        assert response.json()["error"]["code"] == "upload_too_large"
        assert response["Cache-Control"] == "no-store"
        assert not DesignInspirationUpload.objects.exists()

    def test_the_size_gate_answers_identically_for_a_design_that_does_not_exist(self, settings):
        # The gate runs before ownership can be resolved, so it must be
        # DB-independent: an oversized body gets one answer whatever design UUID
        # it names, and cannot be used to probe for one.
        settings.USER_UPLOAD_MAX_BYTES = 1_000
        client = csrf_client()
        owned = create_owned_design_id(client)
        token = bootstrap_csrf(client)
        body = b"x" * 200_000

        answers = {
            client.post(
                uploads_url(design_id),
                data=body,
                content_type="multipart/form-data; boundary=zzz",
                HTTP_X_CSRFTOKEN=token,
                REMOTE_ADDR=unique_ip(),
            ).status_code
            for design_id in (owned, uuid.uuid4())
        }

        assert answers == {413}

    def test_a_body_that_understates_its_length_is_read_only_that_far(self, settings):
        """The header gate refuses an OVERSTATED length; this pins the other
        direction.

        Django wraps the input in a ``LimitedStream`` bounded by
        ``Content-Length``, treating an absent or unparsable one as zero — so a
        length-less (chunked) or understating body cannot make the parser
        receive, or spool to disk, more than it declared. That is what makes the
        header check sufficient rather than merely advisory, and it is a Django
        internal, so it is asserted rather than assumed."""
        settings.USER_UPLOAD_MAX_BYTES = 1_000_000
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = client.post(
            uploads_url(design_id),
            data=b"x" * 5_000_000,
            content_type="multipart/form-data; boundary=zzz",
            HTTP_X_CSRFTOKEN=bootstrap_csrf(client),
            REMOTE_ADDR=unique_ip(),
            # Declares nothing, so nothing may be read — the 5 MB never
            # reaches the parser, let alone a temporary file.
            CONTENT_LENGTH="",
        )

        # Refused for having no image at all, which is the proof: the bytes
        # were never received.
        assert response.status_code == 400, response.content
        assert response.json()["error"]["code"] == "validation_failed"
        assert not DesignInspirationUpload.objects.exists()

    def test_a_missing_content_length_still_reaches_the_in_process_gate(self, settings):
        # The wire-level gate deliberately trusts nothing: without a usable
        # Content-Length the request proceeds and the byte gate on the bytes
        # actually read is what bounds it.
        settings.USER_UPLOAD_MAX_BYTES = 50
        client = csrf_client()
        design_id = create_owned_design_id(client)
        response = post_upload(client, design_id, data=_jpeg_bytes(size=(400, 600)))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_image"

    def test_the_endpoint_is_rate_limited_per_session(self, settings):
        """SEC-002 regression: the stored-upload cap bounds storage, not rate."""
        settings.USER_UPLOAD_SESSION_LIMIT = 2
        client = csrf_client()
        design_id = create_owned_design_id(client)
        token = bootstrap_csrf(client)
        ip = unique_ip()

        first = post_upload(
            client, design_id, data=_png_bytes(colour=(1, 1, 1)), token=token, ip=ip
        )
        second = post_upload(
            client, design_id, data=_png_bytes(colour=(2, 2, 2)), token=token, ip=ip
        )
        third = post_upload(
            client, design_id, data=_png_bytes(colour=(3, 3, 3)), token=token, ip=ip
        )

        assert [first.status_code, second.status_code] == [201, 201]
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "upload_rate_limited"
        assert third["Retry-After"]

    def test_the_endpoint_is_rate_limited_per_ip_across_sessions(self, settings):
        # The IP window is the one an attacker cannot escape by rotating
        # sessions, so it is asserted in its own right — not inferred from the
        # session window sharing an implementation with it.
        settings.USER_UPLOAD_SESSION_LIMIT = 100
        settings.USER_UPLOAD_IP_LIMIT = 2
        ip = unique_ip()
        statuses = []
        for colour in ((1, 1, 1), (2, 2, 2), (3, 3, 3)):
            # A FRESH session each time: only the address is shared.
            client = csrf_client()
            design_id = create_owned_design_id(client)
            statuses.append(
                post_upload(client, design_id, data=_png_bytes(colour=colour), ip=ip).status_code
            )

        assert statuses == [201, 201, 429]

    def test_a_cache_outage_refuses_the_upload_as_unavailable(self, monkeypatch):
        # SEC-003: fail CLOSED either way, but a broken cache is an
        # infrastructure fault (503) — never reported to the caller, or to
        # whoever watches the 429 rate, as their own abuse.
        client = csrf_client()
        design_id = create_owned_design_id(client)

        def explode(*args, **kwargs):
            raise OSError("cache down")

        monkeypatch.setattr("sitara.accounts.rate_limits.cache.add", explode)
        response = post_upload(client, design_id, data=_png_bytes())

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "upload_throttle_unavailable"
        assert not DesignInspirationUpload.objects.exists()

    def test_a_throttled_request_still_cannot_probe_a_foreign_design(self, settings):
        # Ownership is resolved BEFORE the throttle, so a rate-limited attacker
        # still learns nothing about whether a design exists.
        settings.USER_UPLOAD_SESSION_LIMIT = 1
        owner = csrf_client()
        design_id = create_owned_design_id(owner)
        stranger = csrf_client()
        token = bootstrap_csrf(stranger)
        ip = unique_ip()

        for _ in range(3):
            response = post_upload(stranger, design_id, data=_png_bytes(), token=token, ip=ip)
            assert response.status_code == 404


class TestDecodeBoundaryIsTotal:
    @pytest.mark.parametrize(
        "corrupt",
        [
            # A PNG whose IDAT payload is destroyed.
            _png_bytes()[:60] + b"\x00" * 40 + _png_bytes()[100:],
            # A JPEG truncated mid-scan.
            _jpeg_bytes(size=(80, 80))[:120],
            # A WebP header with a destroyed VP8 chunk.
            b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\xff" * 24,
            # A PNG signature followed by noise.
            b"\x89PNG\r\n\x1a\n" + b"\x01\x02\x03" * 50,
        ],
    )
    def test_a_fuzzed_image_is_always_a_json_error_never_a_500(self, corrupt):
        """SEC-001 regression: the decode boundary is TOTAL.

        This is the only place in the codebase that fully decodes anonymous,
        attacker-controlled bytes; a codec error outside the enumerated Pillow
        set must still be a controlled JSON 4xx."""
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = post_upload(client, design_id, data=corrupt)

        assert response.status_code == 400, response.content
        assert response["Content-Type"].startswith("application/json")
        assert response.json()["error"]["code"] == "invalid_image"
        assert not DesignInspirationUpload.objects.exists()

    def test_an_unexpected_decoder_error_becomes_a_controlled_rejection(self, monkeypatch):
        # Force a Pillow error OUTSIDE the primitives' enumerated set.
        def explode(*args, **kwargs):
            raise RuntimeError("codec exploded")

        monkeypatch.setattr("sitara.designs.upload_processing.load_and_orient", explode)
        client = csrf_client()
        design_id = create_owned_design_id(client)

        response = post_upload(client, design_id, data=_png_bytes())

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_image"
        # The exception text never reaches the user.
        assert "codec exploded" not in response.content.decode()


class TestServiceContract:
    def test_delete_is_scoped_to_the_owning_design(self):
        client = csrf_client()
        first_id = create_owned_design_id(client)
        second_id = create_owned_design_id(client)
        upload_id = post_upload(client, first_id, data=_png_bytes()).json()["upload"]["id"]
        upload = DesignInspirationUpload.objects.get(pk=upload_id)

        # A mismatched design deletes nothing, even though the object is gone.
        delete_inspiration_upload(Design.objects.get(pk=second_id), upload)

        assert DesignInspirationUpload.objects.filter(pk=upload_id).exists()
