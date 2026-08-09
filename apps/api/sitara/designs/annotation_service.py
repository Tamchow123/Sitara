"""Annotation persistence and optimistic concurrency (Phase 19 Part B).

The view stays thin; everything that spans more than one row or depends on
concurrent ordering lives here, following the same shape as
``designs.upload_service``.

The concurrency contract in one paragraph. A client sends the ``revision`` it
believes it holds. If that matches the stored revision the write applies and the
revision increments; if it does not, the write is refused with a conflict and
the stored document is left exactly as it was. ``revision`` counts WRITES, not
content changes — re-sending identical content with the current revision still
increments, which is the documented behaviour, because the alternative (compare
content) would make an idempotent retry indistinguishable from a real edit.

**Why the lock is on the parent.** ``select_for_update()`` can only lock rows
that already exist. Before the first save there is no annotation document, so
two concurrent first writes — both carrying ``expected_revision: 0`` — would each
find nothing to lock, neither would block, and both would proceed to insert. So
the lock is taken on the ``DesignVersion``, which always exists. That is the same
technique ``CLAUDE.md`` §11 already documents for concurrent first creates
sharing one session ("serialise on the database session row"). The second
transaction then re-reads the just-created document and correctly conflicts. The
unique constraint is still caught as a final backstop, because a backstop that
raises IntegrityError out of a view would be a 500 rather than a controlled 409.
"""

from django.db import IntegrityError, transaction

from .annotation_schema import (
    ANNOTATION_SCHEMA_VERSION,
    AnnotationDocumentInvalid,
    empty_annotation_document,
    normalise_annotation_document,
)
from .models import DesignAnnotationDocument, DesignVersion

# The revision a caller sends when it believes nothing has been saved yet. A
# stored document always has revision >= 1 (enforced by a database constraint),
# so this value can never collide with a real one.
UNSAVED_REVISION = 0


class AnnotationImageNotReady(Exception):
    """The version has no permanent image, so there is nothing to annotate.

    Annotations bind to the canonical image's dimensions, which do not exist
    until ingest completes — a document created before then would be bound to
    nothing."""


class AnnotationVersionGone(Exception):
    """The design version disappeared between the ownership check and the lock.

    A narrow window — the retention purge only reaches abandoned designs — but the
    alternative is proceeding on a stale in-memory row and reporting the resulting
    foreign-key violation as a revision conflict, which would tell the client to
    reload something that no longer exists."""


class AnnotationConflict(Exception):
    """The caller's ``expected_revision`` is not the stored revision.

    Carries the server's CURRENT revision so the client can reload. Deliberately
    carries nothing else: the conflict response must never echo the stored
    private document back."""

    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__("annotation revision conflict")


def _require_ready(version: DesignVersion) -> None:
    if not version.has_permanent_image:
        raise AnnotationImageNotReady("this design version has no permanent image yet")


def _stored_revision(version: DesignVersion) -> int:
    """The current revision on a FRESH read.

    Used only after an IntegrityError, which leaves the failed transaction
    unusable — so this must run once that transaction has rolled back, never
    inside it."""
    revision = (
        DesignAnnotationDocument.objects.filter(design_version=version)
        .values_list("revision", flat=True)
        .first()
    )
    return UNSAVED_REVISION if revision is None else revision


def _lock_version(version: DesignVersion) -> None:
    """Take the parent row lock that serialises annotation writes.

    The result is checked rather than discarded: a ``None`` means the version was
    removed between the view's ownership lookup and this lock, and proceeding
    would insert against a foreign key that no longer resolves."""
    locked = DesignVersion.objects.select_for_update().filter(pk=version.pk).first()
    if locked is None:
        raise AnnotationVersionGone("this design version no longer exists")


def read_annotation_document(version: DesignVersion) -> tuple[dict, int, object]:
    """The stored document, or the empty one bound to this version's image.

    Returns ``(document, revision, updated_at)``. A version nobody has annotated
    yet gets ``revision`` 0 and an empty item list rather than a 404, so the
    client needs no separate never-saved branch and can render immediately."""
    _require_ready(version)
    stored = DesignAnnotationDocument.objects.filter(design_version=version).first()
    if stored is None:
        return (
            empty_annotation_document(
                image_width=version.image_width, image_height=version.image_height
            ),
            UNSAVED_REVISION,
            None,
        )
    return stored.document, stored.revision, stored.updated_at


def replace_annotation_document(
    version: DesignVersion, payload: object, *, expected_revision: int
) -> tuple[dict, int, object]:
    """Validate and atomically replace the whole document.

    Raises :class:`AnnotationDocumentInvalid` for a bad payload,
    :class:`AnnotationImageNotReady` before ingest, and
    :class:`AnnotationConflict` when ``expected_revision`` is stale."""
    _require_ready(version)

    # Validate BEFORE opening the transaction and taking the lock: a malformed
    # payload must not hold a row lock while it is being parsed, and the server's
    # own canonical dimensions are what the document is checked against — a
    # client-supplied width/height is never trusted.
    document = normalise_annotation_document(
        payload,
        image_width=version.image_width,
        image_height=version.image_height,
    )

    try:
        with transaction.atomic():
            # Lock the parent, which always exists. See the module docstring.
            _lock_version(version)
            stored = DesignAnnotationDocument.objects.filter(design_version=version).first()

            if stored is None:
                if expected_revision != UNSAVED_REVISION:
                    # The caller believes it holds a saved revision, but nothing
                    # is stored — the document was deleted under it. A conflict,
                    # not a silent create, so its unsaved work is never lost
                    # quietly.
                    raise AnnotationConflict(UNSAVED_REVISION)
                created = DesignAnnotationDocument.objects.create(
                    design_version=version,
                    schema_version=ANNOTATION_SCHEMA_VERSION,
                    document=document,
                    revision=1,
                )
                return created.document, created.revision, created.updated_at

            if expected_revision != stored.revision:
                raise AnnotationConflict(stored.revision)

            stored.document = document
            stored.revision = stored.revision + 1
            stored.save(update_fields=["document", "revision", "updated_at"])
            return stored.document, stored.revision, stored.updated_at
    except IntegrityError:
        # Defensive backstop. The parent-row lock should make a duplicate insert
        # unreachable; if the one-to-one constraint fires anyway, this is
        # reported as the same controlled conflict rather than escaping the view
        # as a 500. The true revision is re-read AFTER the rollback above,
        # because reporting a stale 0 here would send the client straight back
        # into the identical conflict.
        raise AnnotationConflict(_stored_revision(version)) from None


def delete_annotation_document(version: DesignVersion) -> None:
    """Clear the overlay. Never touches the generated image.

    Idempotent: deleting when nothing is stored succeeds, so a client that
    retries a clear-all is not told it failed."""
    _require_ready(version)
    with transaction.atomic():
        _lock_version(version)
        DesignAnnotationDocument.objects.filter(design_version=version).delete()


def annotation_payload(document: dict, revision: int, updated_at) -> dict:
    """The wire shape for a document response.

    Exposes the document, its revision and when it last changed — never a
    storage key, hash, user id, DesignSession id or signed URL."""
    return {
        "annotations": {
            "schema_version": document["schema_version"],
            "image_width": document["image_width"],
            "image_height": document["image_height"],
            "items": document["items"],
            "revision": revision,
            "updated_at": updated_at.isoformat().replace("+00:00", "Z") if updated_at else None,
        }
    }


__all__ = [
    "UNSAVED_REVISION",
    "AnnotationConflict",
    "AnnotationDocumentInvalid",
    "AnnotationImageNotReady",
    "AnnotationVersionGone",
    "annotation_payload",
    "delete_annotation_document",
    "read_annotation_document",
    "replace_annotation_document",
]
