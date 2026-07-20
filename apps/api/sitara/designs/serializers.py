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
"""

from rest_framework import serializers

from sitara.catalogue.models import InspirationAsset
from sitara.catalogue.serializers import public_asset_payload

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
        "latest_job": _latest_job_payload(design),
        "created_at": _DATETIME.to_representation(design.created_at),
        "updated_at": _DATETIME.to_representation(design.updated_at),
    }


def design_list_item_payload(design: Design) -> dict:
    """A compact list row: no questionnaire schema, no inspiration records."""
    return {
        "id": str(design.id),
        "title": design.title,
        "status": design.status,
        "created_at": _DATETIME.to_representation(design.created_at),
        "updated_at": _DATETIME.to_representation(design.updated_at),
    }
