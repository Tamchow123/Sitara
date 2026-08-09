"""Annotation service behaviour that is clearer without an HTTP round trip.

The HTTP suite covers the contract as a client sees it. This module covers the
seams underneath: the defensive IntegrityError backstop (unreachable through the
API because the parent-row lock gets there first, so it needs forcing), and the
service's own guarantees about what it does and does not touch.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import IntegrityError

from sitara.designs.annotation_schema import ANNOTATION_SCHEMA_VERSION
from sitara.designs.annotation_service import (
    UNSAVED_REVISION,
    AnnotationConflict,
    AnnotationDocumentInvalid,
    AnnotationImageNotReady,
    AnnotationVersionGone,
    annotation_payload,
    delete_annotation_document,
    read_annotation_document,
    replace_annotation_document,
)
from sitara.designs.models import DesignAnnotationDocument, DesignVersion

from .utils import (
    create_owned_design_id,
    create_pending_design_version,
    create_ready_design_version,
    csrf_client,
)

pytestmark = pytest.mark.django_db

WIDTH = 1536
HEIGHT = 2048


def ready_version() -> DesignVersion:
    design_id = create_owned_design_id(csrf_client())
    return create_ready_design_version(design_id, with_storage_objects=False)


def document(note: str = "A note.") -> dict:
    return {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "image_width": WIDTH,
        "image_height": HEIGHT,
        "items": [
            {
                "id": "3f2a1c8e-0000-4000-8000-000000000001",
                "type": "pin",
                "geometry": {"point": {"x": 0.5, "y": 0.5}},
                "note": note,
                "palette": "ink",
                "created_order": 1,
            }
        ],
    }


# --- the defensive IntegrityError backstop ----------------------------------


def test_a_create_integrity_error_becomes_a_conflict_not_a_500():
    """Unreachable through the API — the parent-row lock serialises first — so
    it is forced here. A constraint escaping the service would surface as a 500
    rather than a conflict the client can resolve."""
    version = ready_version()
    with patch.object(
        DesignAnnotationDocument.objects, "create", side_effect=IntegrityError("duplicate")
    ):
        with pytest.raises(AnnotationConflict) as raised:
            replace_annotation_document(version, document(), expected_revision=UNSAVED_REVISION)
    assert raised.value.current_revision == UNSAVED_REVISION


def test_the_backstop_reports_the_revision_that_is_actually_stored():
    """After the rollback the true revision is re-read, so the client resolves
    the conflict in one reload instead of retrying into the same failure."""
    version = ready_version()
    replace_annotation_document(version, document("First."), expected_revision=UNSAVED_REVISION)
    replace_annotation_document(version, document("Second."), expected_revision=1)

    with patch.object(DesignAnnotationDocument, "save", side_effect=IntegrityError("constraint")):
        with pytest.raises(AnnotationConflict) as raised:
            replace_annotation_document(version, document("Third."), expected_revision=2)

    assert raised.value.current_revision == 2
    # And the rollback left the stored document alone.
    stored = DesignAnnotationDocument.objects.get(design_version=version)
    assert stored.revision == 2
    assert stored.document["items"][0]["note"] == "Second."


# --- the not-ready guard ----------------------------------------------------


def test_every_entry_point_refuses_a_version_with_no_permanent_image():
    version = create_pending_design_version(create_owned_design_id(csrf_client()))
    for call in (
        lambda: read_annotation_document(version),
        lambda: replace_annotation_document(version, document(), expected_revision=0),
        lambda: delete_annotation_document(version),
    ):
        with pytest.raises(AnnotationImageNotReady):
            call()


def test_a_partially_ingested_version_is_still_not_ready():
    """A storage key with no dimensions has no canonical image to bind
    coordinates to, so the guard refuses it.

    The database cannot actually hold this state — ``permanent_image_all_or_none``
    rejects it, and an ``update()`` attempting it raises. The partial object is
    therefore built in memory only: the point is that the guard does not rely on
    the constraint, so it still refuses if it is ever handed a half-populated
    version from anywhere else."""
    version = ready_version()
    version.image_width = None
    with pytest.raises(AnnotationImageNotReady):
        replace_annotation_document(version, document(), expected_revision=0)
    assert not DesignAnnotationDocument.objects.filter(design_version=version).exists()


def test_the_database_refuses_to_store_a_partially_ingested_version():
    """The reason the case above is unreachable in practice."""
    version = ready_version()
    with pytest.raises(IntegrityError):
        DesignVersion.objects.filter(pk=version.pk).update(image_width=None)


# --- the version disappearing under the request -----------------------------


def test_a_version_purged_before_the_lock_is_reported_as_gone():
    """The retention purge only reaches abandoned designs, so this window is
    narrow — but proceeding on the stale in-memory row would insert against a
    foreign key that no longer resolves, and reporting THAT as a revision
    conflict would tell the client to reload something that is gone."""
    version = ready_version()
    DesignVersion.objects.filter(pk=version.pk).delete()

    for call in (
        lambda: replace_annotation_document(version, document(), expected_revision=0),
        lambda: delete_annotation_document(version),
    ):
        with pytest.raises(AnnotationVersionGone):
            call()


# --- validation happens before the lock is taken ----------------------------


def test_an_invalid_payload_never_reaches_the_database():
    """Validated before the transaction opens, so a malformed payload cannot
    hold a row lock while it is being parsed."""
    version = ready_version()
    bad = document()
    bad["items"][0]["palette"] = "not-a-palette"
    with patch("sitara.designs.annotation_service.transaction.atomic") as atomic:
        with pytest.raises(AnnotationDocumentInvalid) as raised:
            replace_annotation_document(version, bad, expected_revision=0)
    assert raised.value.code == "annotation_invalid"
    atomic.assert_not_called()
    assert not DesignAnnotationDocument.objects.filter(design_version=version).exists()


def test_client_supplied_dimensions_are_checked_against_the_stored_image():
    """The server's own image dimensions are authoritative; a client that
    disagrees is refused rather than silently corrected."""
    version = ready_version()
    mismatched = document()
    mismatched["image_width"] = WIDTH + 1
    with pytest.raises(AnnotationDocumentInvalid) as raised:
        replace_annotation_document(version, mismatched, expected_revision=0)
    assert raised.value.code == "annotation_dimension_mismatch"


# --- what the service does not touch ---------------------------------------


def test_replacing_and_clearing_never_writes_to_the_design_version():
    version = ready_version()
    before = {
        name: getattr(version, name)
        for name in (
            "design_spec",
            "image_prompt",
            "prompt_builder_version",
            *DesignVersion.PERMANENT_IMAGE_CHAR_FIELDS,
            *DesignVersion.PERMANENT_IMAGE_NULLABLE_FIELDS,
        )
    }
    replace_annotation_document(version, document(), expected_revision=0)
    replace_annotation_document(version, document("Edited."), expected_revision=1)
    delete_annotation_document(version)

    version.refresh_from_db()
    assert {name: getattr(version, name) for name in before} == before


def test_deleting_removes_the_document_row_and_nothing_else():
    version = ready_version()
    replace_annotation_document(version, document(), expected_revision=0)
    delete_annotation_document(version)
    assert not DesignAnnotationDocument.objects.filter(design_version=version).exists()
    assert DesignVersion.objects.filter(pk=version.pk).exists()


def test_a_write_after_a_delete_is_a_first_ever_save_again():
    """Pins a contract the editor depends on knowing about.

    Once the row is gone the version is genuinely un-annotated, so the next write
    is a create and only ``expected_revision=0`` is accepted — which is why the
    editor must never leave a PUT in flight while it sends the DELETE. If the
    server handled the DELETE first, that PUT's ``expected_revision=0`` would be
    accepted here as a legitimate first save and would restore the very marks the
    user had just cleared. Sequencing the two requests client-side is what makes
    that unreachable; this test is the reason it has to."""
    version = ready_version()
    replace_annotation_document(version, document(), expected_revision=0)
    delete_annotation_document(version)

    with pytest.raises(AnnotationConflict) as raised:
        replace_annotation_document(version, document(), expected_revision=1)
    assert raised.value.current_revision == UNSAVED_REVISION

    _, revision, _ = replace_annotation_document(version, document(), expected_revision=0)
    assert revision == 1


# --- the wire payload -------------------------------------------------------


def test_the_payload_exposes_only_the_document_revision_and_timestamp():
    version = ready_version()
    stored_document, revision, updated_at = replace_annotation_document(
        version, document(), expected_revision=0
    )
    payload = annotation_payload(stored_document, revision, updated_at)
    assert set(payload) == {"annotations"}
    assert set(payload["annotations"]) == {
        "schema_version",
        "image_width",
        "image_height",
        "items",
        "revision",
        "updated_at",
    }


def test_the_timestamp_is_utc_with_a_z_suffix():
    """A trailing +00:00 would be valid ISO but inconsistent with the rest of
    the API's timestamps."""
    version = ready_version()
    payload = annotation_payload(
        *replace_annotation_document(version, document(), expected_revision=0)
    )
    assert payload["annotations"]["updated_at"].endswith("Z")


def test_an_unsaved_document_has_a_null_timestamp():
    version = ready_version()
    payload = annotation_payload(*read_annotation_document(version))
    assert payload["annotations"]["updated_at"] is None
    assert payload["annotations"]["revision"] == UNSAVED_REVISION
