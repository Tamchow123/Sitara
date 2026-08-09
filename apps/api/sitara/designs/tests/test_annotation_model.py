"""DesignAnnotationDocument model guarantees (Phase 19 Part A, ADR 0020).

Database-level properties only: one document per version, cascade with the
version and the design, positive revision and schema version, independence
between an original and its refinement, and no ownership data duplicated onto
the row. HTTP behaviour is in ``test_annotation_api.py``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from sitara.designs.annotation_schema import empty_annotation_document
from sitara.designs.models import (
    Design,
    DesignAnnotationDocument,
    DesignSession,
    DesignVersion,
)

from .utils import create_ready_design_version

pytestmark = pytest.mark.django_db

WIDTH = 1536
HEIGHT = 2048


def make_design() -> Design:
    """A minimal owned design. The annotation model cares only that a version
    hangs off a design in a workspace — no questionnaire is needed."""
    return Design.objects.create(design_session=DesignSession.objects.create())


def make_version(design: Design, *, version_number: int = 1, **extra) -> DesignVersion:
    return create_ready_design_version(
        design.id, version_number=version_number, with_storage_objects=False, **extra
    )


def make_document(version: DesignVersion, *, revision: int = 1) -> DesignAnnotationDocument:
    return DesignAnnotationDocument.objects.create(
        design_version=version,
        schema_version=1,
        document=empty_annotation_document(
            image_width=version.image_width, image_height=version.image_height
        ),
        revision=revision,
    )


# --- one document per version ----------------------------------------------


def test_one_document_per_design_version():
    """A version has at most one annotation overlay; a second insert is a
    database error, not a silently competing second document."""
    version = make_version(make_design())
    make_document(version)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_document(version)


def test_document_is_reachable_from_its_version():
    version = make_version(make_design())
    document = make_document(version)
    version.refresh_from_db()
    assert version.annotation_document == document


def test_a_version_without_a_document_reports_none():
    """The absence of a document is an ordinary, expected state — a version
    nobody has annotated yet."""
    version = make_version(make_design())
    assert not DesignAnnotationDocument.objects.filter(design_version=version).exists()


# --- cascade ----------------------------------------------------------------


def test_deleting_the_version_deletes_the_document():
    version = make_version(make_design())
    document = make_document(version)
    version.delete()
    assert not DesignAnnotationDocument.objects.filter(pk=document.pk).exists()


def test_deleting_the_design_deletes_the_document():
    """Ownership is derived through the design, so a design delete must take
    the annotations with it — there is no annotation that outlives its design."""
    design = make_design()
    document = make_document(make_version(design))
    design.delete()
    assert not DesignAnnotationDocument.objects.filter(pk=document.pk).exists()


def test_deleting_the_workspace_deletes_the_document():
    design = make_design()
    session = design.design_session
    document = make_document(make_version(design))
    session.delete()
    assert not DesignAnnotationDocument.objects.filter(pk=document.pk).exists()


def test_retention_purge_leaves_no_orphan_annotation_rows(inmemory_storage):
    """The purge deletes storage objects first and then the Design row, letting
    the cascade remove children. Annotations are a new child, so prove the
    cascade actually reaches them rather than assuming it."""
    from sitara.generation import maintenance

    design = make_design()
    design.status = Design.Status.GENERATED
    design.save(update_fields=["status"])
    version = create_ready_design_version(design.id)
    document = make_document(version)
    Design.objects.filter(pk=design.pk).update(created_at=timezone.now() - timedelta(days=40))

    result = maintenance.purge_expired_designs()

    assert result["purged"] == 1
    assert not Design.objects.filter(pk=design.pk).exists()
    assert not DesignAnnotationDocument.objects.filter(pk=document.pk).exists()
    # No orphan survives anywhere in the table, not just this row.
    assert DesignAnnotationDocument.objects.count() == 0


def test_a_failed_purge_retains_the_design_and_its_annotations_together(
    inmemory_storage, monkeypatch
):
    """The cascade's failure-safety depends on the design delete being the LAST
    statement inside one atomic block: an earlier object-deletion failure must
    roll the whole set back, leaving the design AND its annotations for a retry.

    Without this, a future optimisation that bulk-deleted annotation rows before
    the object loop would orphan the deletion — annotations gone while the design
    survives — and the happy-path test above would still pass."""
    from sitara.generation import maintenance

    design = make_design()
    design.status = Design.Status.GENERATED
    design.save(update_fields=["status"])
    version = create_ready_design_version(design.id)
    document = make_document(version)
    Design.objects.filter(pk=design.pk).update(created_at=timezone.now() - timedelta(days=40))

    def boom(_key):
        raise OSError("storage unavailable")

    monkeypatch.setattr(maintenance.design_image_storage(), "delete", boom)
    result = maintenance.purge_expired_designs()

    assert result["retained"] == 1
    assert result["purged"] == 0
    # Both survive, together — never one without the other.
    assert Design.objects.filter(pk=design.pk).exists()
    assert DesignAnnotationDocument.objects.filter(pk=document.pk).exists()


# --- constraints ------------------------------------------------------------


def test_revision_must_be_positive():
    """A stored document has been written at least once. Revision 0 is reserved
    for the API's synthetic never-saved response and must never be persisted,
    or the client's "have I saved?" signal would become ambiguous."""
    version = make_version(make_design())
    with pytest.raises(IntegrityError), transaction.atomic():
        make_document(version, revision=0)


@pytest.mark.parametrize("schema_version", [0, 2, 99])
def test_schema_version_is_pinned_to_the_supported_version(schema_version):
    """Pinned rather than merely positive, matching every other versioned-JSON
    provenance column on DesignVersion. Introducing schema 2 must require a
    reviewed migration, not a stray write pairing a v2 marker with a v1
    document."""
    version = make_version(make_design())
    with pytest.raises(IntegrityError), transaction.atomic():
        DesignAnnotationDocument.objects.create(
            design_version=version,
            schema_version=schema_version,
            document=empty_annotation_document(image_width=WIDTH, image_height=HEIGHT),
            revision=1,
        )


def test_revision_can_be_incremented():
    version = make_version(make_design())
    document = make_document(version)
    document.revision = 7
    document.save(update_fields=["revision"])
    document.refresh_from_db()
    assert document.revision == 7


# --- refinement independence ------------------------------------------------


def test_a_refined_version_has_its_own_independent_document():
    """Annotations belong to the exact image they were drawn over. A refinement
    is a different image, so it starts with no marks — they are never copied
    from parent to child, and clearing one never touches the other."""
    design = make_design()
    original = make_version(design, version_number=1)
    original_document = make_document(original)
    # A version-2 row must carry its parent and refinement request in the SAME
    # insert — designs_designversion_v2_requires_parent rejects a bare insert
    # followed by an update.
    refined = DesignVersion.objects.create(
        design_id=design.id,
        version_number=2,
        design_spec={"schema_version": 1},
        design_spec_schema_version=1,
        design_spec_template_version="v1",
        design_spec_provider="fixture",
        design_spec_model="fixture-model",
        design_spec_generated_at=timezone.now(),
        image_prompt="A refined prompt.",
        prompt_builder_version="3.0.0",
        parent_version=original,
        refinement_request={"schema_version": 1, "change_type": "colour_story", "note": ""},
        refinement_request_schema_version=1,
        refinement_request_sha256="d" * 64,
        image_storage_key=f"design-images/{design.id}/v2/original.webp",
        image_sha256="e" * 64,
        image_size_bytes=1000,
        image_width=WIDTH,
        image_height=HEIGHT,
        thumbnail_storage_key=f"design-images/{design.id}/v2/thumbnail.webp",
        thumbnail_sha256="f" * 64,
        thumbnail_size_bytes=100,
        thumbnail_width=384,
        thumbnail_height=512,
        image_processor_version="1.0.0",
        image_ingested_at=timezone.now(),
    )

    # The refinement inherited nothing.
    assert not DesignAnnotationDocument.objects.filter(design_version=refined).exists()

    # Annotating the refinement leaves the original untouched, and vice versa.
    refined_document = make_document(refined)
    assert DesignAnnotationDocument.objects.count() == 2
    refined_document.delete()
    assert DesignAnnotationDocument.objects.filter(pk=original_document.pk).exists()


# --- no duplicated ownership or storage data --------------------------------


def test_the_model_stores_no_ownership_or_storage_fields():
    """Ownership is DERIVED through the version, never copied. A user id,
    session key, storage key or signed URL on this row would create a second
    source of truth that a later ownership change or rights revocation could
    not reach."""
    field_names = {field.name for field in DesignAnnotationDocument._meta.get_fields()}
    forbidden = {
        "user",
        "user_id",
        "design_session",
        "session_key",
        "owner",
        "image_storage_key",
        "storage_key",
        "thumbnail_storage_key",
        "image_sha256",
        "signed_url",
        "url",
        "public_token",
        "share_token",
        "email",
        "recipient",
    }
    assert field_names & forbidden == set()
    assert field_names == {
        "id",
        "design_version",
        "schema_version",
        "document",
        "revision",
        "created_at",
        "updated_at",
    }


def test_annotating_never_alters_the_versions_image_provenance():
    """The generated image is immutable audit data. Creating, changing and
    deleting an overlay must leave every permanent-image field byte-identical."""
    version = make_version(make_design())
    before = {
        name: getattr(version, name)
        for name in (
            *DesignVersion.PERMANENT_IMAGE_CHAR_FIELDS,
            *DesignVersion.PERMANENT_IMAGE_NULLABLE_FIELDS,
            "design_spec",
            "image_prompt",
            "prompt_builder_version",
        )
    }

    document = make_document(version)
    document.document = {
        "schema_version": 1,
        "image_width": version.image_width,
        "image_height": version.image_height,
        "items": [
            {
                "id": str(uuid.uuid4()),
                "type": "pin",
                "geometry": {"point": {"x": 0.5, "y": 0.5}},
                "note": "here",
                "palette": "sage",
                "created_order": 1,
            }
        ],
    }
    document.revision = 2
    document.save(update_fields=["document", "revision"])
    document.delete()

    version.refresh_from_db()
    after = {name: getattr(version, name) for name in before}
    assert after == before
