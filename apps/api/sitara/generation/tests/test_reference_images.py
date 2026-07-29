"""Signed reference URLs for the image provider (Phase 16B, ADR 0019).

This module is the single place inspiration bytes become reachable by a
provider, deliberately overriding ADR 0014. The tests below are therefore
mostly about what must NOT happen: an unselected asset, an asset whose rights
lapsed since selection, a raw storage key, a logged URL, or anything at all on
the demo path.
"""

import logging
from datetime import timedelta

import pytest
from django.utils import timezone

from sitara.catalogue.models import UsageRights
from sitara.catalogue.services import retire_inspiration_asset
from sitara.catalogue.tests.utils import make_eligible_asset, make_rights
from sitara.designs.models import DesignInspiration, DesignInspirationUpload
from sitara.generation.reference_images import (
    PROVIDER_MAX_REFERENCE_IMAGES,
    reference_image_urls,
)
from sitara.media.delivery import DesignImageDeliveryUnavailable

from .factory import make_active_v1, make_complete_design

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _catalogue_storage(inmemory_storage):
    """Catalogue assets need the isolated in-memory default storage — CI has
    no MinIO and no test may touch a real bucket."""


class _RecordingSigner:
    """A signer that records exactly what it was asked to sign."""

    def __init__(self, prefix="https://storage.example.test/"):
        self.calls = []
        self._prefix = prefix

    def sign_get(self, key, *, ttl_seconds, filename, disposition="inline"):
        self.calls.append(
            {
                "key": key,
                "ttl_seconds": ttl_seconds,
                "filename": filename,
                "disposition": disposition,
            }
        )
        return f"{self._prefix}{key}?sig=SIGNATURE{len(self.calls)}"


def _add_upload(design, position, *, key=None):
    return DesignInspirationUpload.objects.create(
        design=design,
        position=position,
        storage_key=key or f"design-inspiration/{design.pk}/upload-{position}.webp",
        image_width=900,
        image_height=1200,
        image_size_bytes=42_000,
        image_sha256=f"{position:064d}",
        rights_acknowledged_at=timezone.now(),
    )


def _select(design, asset, position=1):
    return DesignInspiration.objects.create(
        design=design, inspiration_asset=asset, position=position
    )


class TestNothingSelected:
    def test_a_design_with_no_references_signs_nothing(self):
        design = make_complete_design()
        signer = _RecordingSigner()
        assert reference_image_urls(design, signer=signer) == ()
        assert signer.calls == []

    def test_no_signer_is_constructed_when_there_is_nothing_to_sign(self, monkeypatch):
        # Inspiration is optional and most designs have none. Building a boto3
        # client for every generation would be wasted work — and on the
        # filesystem backend it must not fail a generation that needs no URL.
        import sitara.generation.reference_images as module

        def _fail(*args, **kwargs):
            raise AssertionError("no signer may be constructed with nothing to sign")

        monkeypatch.setattr(module, "S3DesignImageSigner", _fail)
        assert reference_image_urls(make_complete_design()) == ()


class TestSelectedReferences:
    def test_an_upload_is_signed(self):
        design = make_complete_design()
        upload = _add_upload(design, 1)
        signer = _RecordingSigner()
        urls = reference_image_urls(design, signer=signer)
        assert len(urls) == 1
        assert signer.calls[0]["key"] == upload.storage_key

    def test_an_eligible_catalogue_asset_is_signed(self):
        design = make_complete_design()
        asset = make_eligible_asset()
        _select(design, asset)
        signer = _RecordingSigner()
        urls = reference_image_urls(design, signer=signer)
        assert len(urls) == 1
        assert signer.calls[0]["key"] == asset.image_storage_key

    def test_uploads_come_before_catalogue_selections(self):
        # The user's own photographs are the references they care most about,
        # and a provider that weights earlier inputs more heavily should see
        # them first. Order is part of the contract, so pin it.
        design = make_complete_design()
        upload = _add_upload(design, 1)
        asset = make_eligible_asset()
        _select(design, asset)
        signer = _RecordingSigner()
        reference_image_urls(design, signer=signer)
        assert [call["key"] for call in signer.calls] == [
            upload.storage_key,
            asset.image_storage_key,
        ]

    def test_uploads_are_ordered_by_the_position_the_user_chose(self):
        design = make_complete_design()
        third = _add_upload(design, 3)
        first = _add_upload(design, 1)
        signer = _RecordingSigner()
        reference_image_urls(design, signer=signer)
        assert [call["key"] for call in signer.calls] == [first.storage_key, third.storage_key]

    def test_selections_are_ordered_by_the_position_the_user_chose(self):
        design = make_complete_design()
        second = make_eligible_asset(title="Second look")
        first = make_eligible_asset(title="First look")
        _select(design, second, position=2)
        _select(design, first, position=1)
        signer = _RecordingSigner()
        reference_image_urls(design, signer=signer)
        assert [call["key"] for call in signer.calls] == [
            first.image_storage_key,
            second.image_storage_key,
        ]

    def test_only_this_designs_references_are_signed(self):
        questionnaire = make_active_v1()
        design = make_complete_design(questionnaire=questionnaire)
        mine = _add_upload(design, 1)
        other = make_complete_design(questionnaire=questionnaire)
        _add_upload(other, 1)
        _select(other, make_eligible_asset(title="Someone else's pick"))
        signer = _RecordingSigner()
        reference_image_urls(design, signer=signer)
        assert [call["key"] for call in signer.calls] == [mine.storage_key]

    def test_an_unselected_eligible_asset_is_never_signed(self):
        design = make_complete_design()
        make_eligible_asset(title="Not chosen")
        signer = _RecordingSigner()
        assert reference_image_urls(design, signer=signer) == ()


class TestEligibilityIsRecheckedAtMintTime:
    """A selection made minutes ago is not evidence of rights now. The
    ``publicly_eligible()`` queryset — the single definition — is re-run here,
    immediately before the bytes become reachable."""

    def test_a_retired_asset_is_dropped(self):
        design = make_complete_design()
        asset = make_eligible_asset()
        _select(design, asset)
        retire_inspiration_asset(asset)
        signer = _RecordingSigner()
        assert reference_image_urls(design, signer=signer) == ()
        assert signer.calls == []

    def test_an_asset_whose_rights_expired_is_dropped(self):
        design = make_complete_design()
        rights = make_rights(verified=True)
        asset = make_eligible_asset(rights=rights)
        _select(design, asset)
        # Simulate the clock passing the expiry rather than the row being
        # edited: a lapse is time passing, not a staff action.
        now = timezone.now()
        UsageRights.objects.filter(pk=rights.pk).update(
            verified_at=now - timedelta(days=10), expires_at=now - timedelta(days=1)
        )
        signer = _RecordingSigner()
        assert reference_image_urls(design, signer=signer) == ()

    def test_an_asset_that_forbids_ai_input_is_dropped(self):
        design = make_complete_design()
        rights = make_rights(verified=True)
        asset = make_eligible_asset(rights=rights)
        _select(design, asset)
        UsageRights.objects.filter(pk=rights.pk).update(allows_ai_input=False)
        signer = _RecordingSigner()
        assert reference_image_urls(design, signer=signer) == ()

    def test_an_ineligible_asset_does_not_block_the_others(self):
        # One lapsed catalogue asset is a staff problem, not the user's — the
        # generation continues with what remains rather than failing outright.
        design = make_complete_design()
        upload = _add_upload(design, 1)
        good = make_eligible_asset(title="Still cleared")
        bad = make_eligible_asset(title="Since retired")
        _select(design, bad, position=1)
        _select(design, good, position=2)
        retire_inspiration_asset(bad)
        signer = _RecordingSigner()
        reference_image_urls(design, signer=signer)
        assert [call["key"] for call in signer.calls] == [
            upload.storage_key,
            good.image_storage_key,
        ]

    def test_an_upload_is_not_subject_to_the_catalogue_rights_model(self):
        # A user's own photograph has no UsageRights row and never could —
        # ADR 0018's per-upload affirmation is a different thing entirely.
        # Dropping uploads for failing a catalogue check would silently send
        # nothing at all.
        design = make_complete_design()
        upload = _add_upload(design, 1)
        signer = _RecordingSigner()
        reference_image_urls(design, signer=signer)
        assert [call["key"] for call in signer.calls] == [upload.storage_key]


class TestSigningParameters:
    def test_the_ttl_is_the_configured_short_lived_one(self, settings):
        settings.DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS = 120
        design = make_complete_design()
        _add_upload(design, 1)
        signer = _RecordingSigner()
        reference_image_urls(design, signer=signer)
        assert signer.calls[0]["ttl_seconds"] == 120

    def test_the_filename_is_server_owned(self):
        # No user-supplied filename is retained anywhere in the upload path,
        # so none can leak here — pin the server-owned constant so a future
        # change that started passing one through fails.
        design = make_complete_design()
        _add_upload(design, 1)
        signer = _RecordingSigner()
        reference_image_urls(design, signer=signer)
        assert signer.calls[0]["filename"] == "reference.webp"


class TestBounds:
    def test_a_design_at_sitaras_own_cap_is_never_truncated(self, settings):
        # The invariant that matters, stated as BEHAVIOUR rather than as a
        # comparison of two constants: fill every slot Sitara allows and every
        # one must be signed. If the provider ceiling ever dropped below
        # MAX_INSPIRATION_IMAGES, the truncation below would start silently
        # dropping references the user legitimately chose, and this fails.
        design = make_complete_design()
        for position in range(1, settings.MAX_INSPIRATION_IMAGES + 1):
            _add_upload(design, position)
        signer = _RecordingSigner()
        urls = reference_image_urls(design, signer=signer)
        assert len(urls) == settings.MAX_INSPIRATION_IMAGES

    def test_more_keys_than_the_provider_ceiling_are_truncated(self, monkeypatch):
        # UNREACHABLE through real rows today: a database CHECK caps upload and
        # selection positions at MAX_INSPIRATION_IMAGES (3), well under the
        # ceiling. This is deliberately a backstop against a FUTURE cap
        # increase, so the key list is injected rather than built from rows —
        # over-sending would be a rejected submission, and a rejected
        # submission is charged-or-ambiguous territory.
        import sitara.generation.reference_images as module

        keys = [
            f"design-inspiration/k{index}.webp"
            for index in range(PROVIDER_MAX_REFERENCE_IMAGES + 3)
        ]
        monkeypatch.setattr(module, "_selected_storage_keys", lambda design: list(keys))
        signer = _RecordingSigner()
        urls = reference_image_urls(make_complete_design(), signer=signer)
        assert len(urls) == PROVIDER_MAX_REFERENCE_IMAGES
        assert len(signer.calls) == PROVIDER_MAX_REFERENCE_IMAGES
        # Truncation keeps the user's ORDER — uploads first, then selections.
        assert [call["key"] for call in signer.calls] == keys[:PROVIDER_MAX_REFERENCE_IMAGES]


class TestFailsClosed:
    def test_the_filesystem_backend_refuses_to_sign_references(self, settings):
        # Development-only backend: it has no signing path at all, and a URL
        # that resolves nowhere would be paid for and then fail.
        settings.DESIGN_IMAGE_STORAGE_BACKEND = "filesystem"
        design = make_complete_design()
        _add_upload(design, 1)
        with pytest.raises(DesignImageDeliveryUnavailable):
            reference_image_urls(design)

    def test_the_filesystem_backend_does_not_break_a_design_with_no_references(self, settings):
        settings.DESIGN_IMAGE_STORAGE_BACKEND = "filesystem"
        assert reference_image_urls(make_complete_design()) == ()

    def test_a_signer_failure_propagates_rather_than_sending_fewer(self):
        # Silently sending two of the three references the user chose would
        # misrepresent what the concept was built from; the caller turns this
        # into a terminal generation error before any spend.
        design = make_complete_design()
        _add_upload(design, 1)

        class _Broken:
            def sign_get(self, *args, **kwargs):
                raise RuntimeError("signing backend unavailable")

        with pytest.raises(RuntimeError):
            reference_image_urls(design, signer=_Broken())


_LOGGER = "sitara.generation.reference_images"


def _module_log(caplog) -> str:
    """Only THIS module's records: the setup helpers log their own asset ids,
    and folding those in would make the assertions below meaningless."""
    return " ".join(record.getMessage() for record in caplog.records if record.name == _LOGGER)


class TestNothingSensitiveIsLogged:
    def test_neither_the_url_nor_the_storage_key_reaches_a_log_record(self, caplog):
        design = make_complete_design()
        upload = _add_upload(design, 1)
        asset = make_eligible_asset()
        _select(design, asset)
        signer = _RecordingSigner()
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            urls = reference_image_urls(design, signer=signer)
        rendered = _module_log(caplog)
        assert rendered, "the count log line is expected"
        for url in urls:
            assert url not in rendered
        assert "SIGNATURE" not in rendered
        assert upload.storage_key not in rendered
        assert asset.image_storage_key not in rendered

    def test_a_dropped_asset_is_logged_without_identifying_it(self, caplog):
        design = make_complete_design()
        asset = make_eligible_asset(title="Since retired")
        _select(design, asset)
        retire_inspiration_asset(asset)
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            reference_image_urls(design, signer=_RecordingSigner())
        rendered = _module_log(caplog)
        assert rendered, "the skip log line is expected"
        assert str(asset.pk) not in rendered
        assert asset.title not in rendered
        assert asset.image_storage_key not in rendered
