"""Design API serializers and response payloads.

The write serializer accepts EXACTLY ``title``, ``questionnaire_version_id``,
``answers`` and ``inspiration_asset_ids`` (all optional, for partial draft
operations) and rejects everything else with a controlled 400 — server-owned
fields (id, design_session, status, versions, generation attempts,
timestamps, storage fields) must never be silently ignored, because silence
teaches clients they worked. Answer content and inspiration eligibility are
validated authoritatively in the service layer, not here.

The read payloads never expose the DesignSession identifier, the user,
version rows, storage keys, image hashes, rights evidence, verifier identity
or internal notes. The list payload is compact (no questionnaire schema, no
inspiration records, no job data); only the detail payload embeds the linked
questionnaire, the selected inspirations and, since Phase 12, one sanitised
public snapshot of the latest generation job (``latest_job``) — still no
private provenance (provider, model, prediction id, seed, storage key).

Since Phase 21 the list payload also carries each design's versions, so the
account gallery can group a refinement with the concept it came from. That adds
an id, a version number, a demo flag, whether an image exists and that version's
own job status — and nothing else. In particular it adds NO signed image URL: the
gallery mints one per card through the ownership-checked images endpoint, because a
signed URL is a short-lived bearer token and a list is the worst possible place to
put a fistful of them.
"""

from rest_framework import serializers

from sitara.catalogue.models import InspirationAsset
from sitara.catalogue.serializers import public_asset_payload
from sitara.media.account_delivery import safe_stored_filename

from .jobs import latest_generation_attempt, public_job_payload
from .models import DESIGN_TITLE_MAX_LENGTH, Design

# One shared DRF field to render timestamps in the same ISO-8601 form the
# Phase 4 ModelSerializer produced.
_DATETIME = serializers.DateTimeField()


class DesignWriteSerializer(serializers.Serializer):
    title = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=DESIGN_TITLE_MAX_LENGTH,
    )
    questionnaire_version_id = serializers.UUIDField(required=False)
    # Arbitrary JSON object; totality-validated against the linked
    # questionnaire schema in ``services.update_design_draft``.
    answers = serializers.JSONField(required=False)
    inspiration_asset_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True
    )

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError(
                {"non_field_errors": ["The request body must be a JSON object."]}
            )
        unknown = sorted(set(data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError(
                {name: ["This field cannot be set."] for name in unknown}
            )
        return super().to_internal_value(data)


class RefinementWriteSerializer(serializers.Serializer):
    """Coarse wire-shape validation for a refinement request (Phase 14):
    exactly ``source_version_id``, ``change_type`` and an optional ``note``.

    Deliberately loose on ``change_type``/``note`` content — the strict
    allowlist/schema/safety-scan validation belongs to
    ``sitara.generation.refinement.normalise_refinement_request``, which the
    view calls next; this layer only rejects unknown fields and wrong JSON
    types, matching ``DesignWriteSerializer``'s pattern."""

    source_version_id = serializers.UUIDField()
    change_type = serializers.CharField()
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError(
                {"non_field_errors": ["The request body must be a JSON object."]}
            )
        unknown = sorted(set(data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError(
                {name: ["This field cannot be set."] for name in unknown}
            )
        return super().to_internal_value(data)


#: An abuse backstop on the field's length, NOT the naming rule.
#:
#: The naming rule is the 60-character base cap in
#: :func:`sitara.media.account_delivery.safe_attachment_filename`, which
#: TRUNCATES rather than refusing — a stylist who types a long description
#: should get a shortened name, not an error. This bound exists only so an
#: unbounded string cannot arrive at all, and sits far enough above the cap that
#: no plausible name meets it.
RENDER_FILENAME_MAX_INPUT_LENGTH = 200


class RenderSendSerializer(serializers.Serializer):
    """The send endpoints' request body: exactly one optional ``filename``.

    The only caller-influenced value in the whole delivery path (Phase 21,
    ADR 0022). Every other field is rejected rather than ignored, which matters
    more here than anywhere else in this API: these endpoints mail an attachment,
    and the guarantee that no request can express a destination used to be
    structural — there was no body to put one in. Now that a body exists the
    guarantee is an explicit, tested one, so ``email``, ``to``, ``cc``, ``bcc``,
    ``from_email``, ``reply_to`` and anything else fail the whole request rather
    than being silently dropped beside a valid ``filename``.

    Validation is total over arbitrary JSON. A number, ``null``, a list or a
    nested object becomes a controlled 400, never a ``TypeError``.

    The edge REFUSES what the choke point refuses and accepts what it repairs,
    by asking the sanitiser itself rather than reimplementing its rules. So a
    name carrying a control character is a 400 the stylist can see and correct,
    while one merely containing a path separator is quietly cleaned — the
    difference being that a refusal would otherwise deliver a name nobody chose."""

    filename = serializers.CharField(
        required=False,
        allow_blank=True,
        # Not trimmed here: the sanitiser owns whitespace handling, and trimming
        # twice in two places is how the two eventually disagree.
        trim_whitespace=False,
        max_length=RENDER_FILENAME_MAX_INPUT_LENGTH,
    )

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError(
                {"non_field_errors": ["The request body must be a JSON object."]}
            )
        unknown = sorted(set(data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError(
                {name: ["This field cannot be set."] for name in unknown}
            )
        if "filename" in data and not isinstance(data["filename"], str):
            # DRF's CharField would coerce a number to its string form; a name
            # arriving as 12, null or a list is a client defect, and answering it
            # with the attachment called "12" teaches the client it worked.
            raise serializers.ValidationError({"filename": ["A file name must be text."]})
        return super().to_internal_value(data)

    def validate_filename(self, value: str) -> str:
        if not value.strip():
            # Blank means "no new choice", which is not the same as "forget the
            # name I chose last time".
            return ""
        if not safe_stored_filename(value):
            raise serializers.ValidationError(
                "This name cannot be used. Please avoid line breaks and other "
                "control characters, and try a simpler name."
            )
        return value


def _questionnaire_payload(design: Design) -> dict | None:
    """The linked questionnaire as {id, version, schema}, or None for legacy
    (Phase 4) designs that were never linked to a questionnaire."""
    version = design.questionnaire_version
    if version is None:
        return None
    return {"id": str(version.id), "version": version.version, "schema": version.schema}


def _selected_inspirations_payload(design: Design) -> list[dict]:
    """The design's inspiration selections, ordered by position.

    Each entry reports whether the asset is STILL publicly eligible right
    now. An asset that has become retired, expired or otherwise ineligible is
    rendered as ``available: false`` with ``asset: null`` — the reason is
    never revealed, and no storage key, hash, rights evidence or internal
    metadata is ever exposed. The linked asset and its live rights record
    remain authoritative; nothing is snapshotted onto the selection."""
    selections = list(design.inspiration_selections.all())
    if not selections:
        return []
    selected_ids = [selection.inspiration_asset_id for selection in selections]
    eligible = {
        asset.pk: asset
        for asset in InspirationAsset.objects.publicly_eligible()
        .filter(pk__in=selected_ids)
        .select_related("usage_rights")
    }
    payload = []
    for selection in selections:
        asset = eligible.get(selection.inspiration_asset_id)
        payload.append(
            {
                "id": str(selection.inspiration_asset_id),
                "position": selection.position,
                "available": asset is not None,
                "asset": public_asset_payload(asset) if asset is not None else None,
            }
        )
    return payload


def inspiration_upload_payload(upload) -> dict:
    """One of the design's OWN uploaded inspirations.

    Deliberately minimal: an id, its position, its displayed dimensions and when
    the rights affirmation was recorded. The storage key, the image hash, the
    byte size and every other private detail stay server-side; the bytes are
    reachable only through the ownership-checked image endpoint."""
    return {
        "id": str(upload.id),
        "position": upload.position,
        "width": upload.image_width,
        "height": upload.image_height,
        "rights_acknowledged_at": _DATETIME.to_representation(upload.rights_acknowledged_at),
        "created_at": _DATETIME.to_representation(upload.created_at),
    }


def _inspiration_uploads_payload(design: Design) -> list[dict]:
    """Every upload on this design, ordered by position."""
    return [inspiration_upload_payload(upload) for upload in design.inspiration_uploads.all()]


def _latest_job_payload(design: Design) -> dict | None:
    """The design's latest generation attempt as the one public job shape, or
    None when it has never attempted generation. Supports durable resume
    navigation (returning to a generating/generated/failed design) without
    exposing any private attempt provenance."""
    attempt = latest_generation_attempt(design)
    if attempt is None:
        return None
    return public_job_payload(attempt)["job"]


def design_detail_payload(design: Design) -> dict:
    """The full private detail response for one owned design."""
    return {
        "id": str(design.id),
        "title": design.title,
        "status": design.status,
        "questionnaire": _questionnaire_payload(design),
        "answers": design.answers,
        "selected_inspirations": _selected_inspirations_payload(design),
        "inspiration_uploads": _inspiration_uploads_payload(design),
        "latest_job": _latest_job_payload(design),
        "created_at": _DATETIME.to_representation(design.created_at),
        "updated_at": _DATETIME.to_representation(design.updated_at),
    }


# The account gallery's page size (Phase 21). Bounded because the list is the one
# design endpoint whose result set grows without limit as someone keeps designing,
# and an unbounded response would eventually carry every version of every concept
# they have ever made in one payload.
GALLERY_PAGE_SIZE_DEFAULT = 20
GALLERY_PAGE_SIZE_MAX = 50

# ``offset`` needs a ceiling for a different reason than ``limit`` needs one, which
# is why it is a separate constant rather than a reuse. Django writes LIMIT/OFFSET
# into the SQL text with ``%d`` interpolation instead of binding a parameter, so a
# Python int of any size becomes a SQL literal of any size; PostgreSQL raises
# ``DataError: bigint out of range`` above 2**63-1 (measured), and a raw database
# error is not an APIException, so DRF would let it through unwrapped as an HTML
# 500 rather than this project's JSON envelope. A million pages past the start of
# a personal gallery is not a request any real client makes, so the ceiling sits
# far below the point where the database would object.
GALLERY_OFFSET_MAX = 1_000_000


def _version_row_payload(version) -> dict:
    """One version inside a gallery card.

    Enough to draw a card and know whether it can show a picture, and nothing
    else. Explicitly NOT here: any signed URL, storage key, bucket, endpoint,
    image hash, byte size, prompt, DesignSpec, inspiration provenance or note
    text. The gallery mints its own short-lived URL per card through the
    ownership-checked images endpoint, which is the only issuer of one
    (CLAUDE.md §14) — putting one in a list payload would multiply a bearer
    token by the number of concepts someone owns, and cache it in whatever
    holds the list.

    ``job_status`` is this version's own progress, taken from the attempt that
    produced it. It is not a constant: a DesignVersion is created before the
    permanent image ingest completes, so a row can legitimately exist with the
    image still arriving, or with a failed job and no image at all. That is
    exactly the case the gallery must label rather than render as a broken
    image."""
    # Newest attempt wins, and from the PREFETCHED list — sorting in Python
    # rather than with .order_by() is what keeps the gallery's query count flat
    # instead of one query per version.
    attempts = sorted(
        version.generation_attempts.all(), key=lambda a: (a.created_at, a.id), reverse=True
    )
    latest = attempts[0] if attempts else None
    return {
        "id": str(version.id),
        "version_number": version.version_number,
        "is_demo": version.is_demo,
        # Whether an image can be requested for this version at all — both
        # derivatives are written together by the one ingest service, so either
        # missing means there is nothing to show yet.
        "has_image": bool(version.image_storage_key and version.thumbnail_storage_key),
        "job_status": latest.status if latest is not None else None,
        "created_at": _DATETIME.to_representation(version.created_at),
    }


def design_list_item_payload(design: Design) -> dict:
    """A gallery row: no questionnaire schema, no inspiration records, no job.

    Since Phase 21 this carries the design's versions, because the gallery has to
    group them visually to show that a refinement belongs to the same sitting as
    the concept it came from. It still carries no ``latest_job``: that snapshot is
    documented as belonging to design detail only, and ``status`` here plus each
    version's own ``job_status`` — one lifecycle enum, NOT the snapshot, and never
    its id, error code, kind or timestamps — already say everything a card needs to
    label itself. Read that as a ceiling rather than a precedent: the next job
    field does not get in because this one did.

    ``is_demo`` is the design's newest version's value rather than a field of its
    own. A demo and a live version can never mix in one lineage — a refinement
    inherits its source's value, enforced by the refinement enqueue service — so
    the newest version speaks for the design. It is null before there is any
    version to speak for it, which is honest: nothing has been generated, so
    there is no mode to report. Never re-derived from the current DEMO_MODE
    setting, which would relabel old concepts every time an operator changed it."""
    # Meta.ordering on DesignVersion is ["version_number"], so this is creation
    # order already; sorted() again in Python so a future ordering change cannot
    # silently reverse the gallery, and so it reads from the prefetch.
    versions = sorted(design.versions.all(), key=lambda v: v.version_number)
    return {
        "id": str(design.id),
        "title": design.title,
        "status": design.status,
        "created_at": _DATETIME.to_representation(design.created_at),
        "updated_at": _DATETIME.to_representation(design.updated_at),
        "is_demo": versions[-1].is_demo if versions else None,
        "version_count": len(versions),
        "versions": [_version_row_payload(version) for version in versions],
    }
