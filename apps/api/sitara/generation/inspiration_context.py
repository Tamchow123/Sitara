"""Versioned, provider-safe inspiration context snapshot (Phase 13).

Selected inspiration IMAGE BYTES are never sent to any provider. This module
builds a strict, versioned snapshot from the frozen catalogue fields already
attached to each currently publicly-eligible selected asset —
``garment_type``, ``alt_text`` (exposed as ``visual_description``) and
``cultural_context`` — plus audit-only ``title``/``attribution`` that may be
shown to the user but are never sent to a provider.

Two disjoint views are derived from one snapshot:

- :func:`provider_inspiration_cues` — position, garment type, visual
  description, cultural context ONLY. No asset id, title or attribution.
- :func:`inspiration_acknowledgements` — position, title, attribution ONLY.
  No provider cues, no asset id beyond what the snapshot itself records for
  audit purposes.

Deliberately absent from every shape here: image URLs, storage keys, image
hashes, image dimensions, rights UUIDs, rights basis, source/licence URLs,
evidence references, verifier identity or any staff identity.
"""

import hashlib
import json
import re
import unicodedata
import uuid
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)

from sitara.catalogue.models import (
    ASSET_ALT_TEXT_MAX_LENGTH,
    ASSET_CULTURAL_CONTEXT_MAX_LENGTH,
    ASSET_TITLE_MAX_LENGTH,
    ATTRIBUTION_TEXT_MAX_LENGTH,
    InspirationAsset,
)

from .input_safety import GeneratedContentRejected, scan_generated_text

# Versions the persisted snapshot SHAPE. Bump only with a documented
# migration strategy for existing persisted DesignVersion rows.
INSPIRATION_CONTEXT_SCHEMA_VERSION = 1

# Mirrors settings.MAX_INSPIRATION_IMAGES (also enforced independently by
# designs.services._replace_inspirations); kept as a local constant so this
# module never depends on Django settings for a pure validation rule.
MAX_INSPIRATION_CONTEXT_ITEMS = 3

_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    str_strip_whitespace=True,
    validate_assignment=True,
)

# Same machine-id shape as InspirationAsset.garment_type's validator.
_GarmentType = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{1,63}$")
]

_VisualDescription = Annotated[
    str, StringConstraints(min_length=1, max_length=ASSET_ALT_TEXT_MAX_LENGTH)
]
_CulturalContextText = Annotated[
    str, StringConstraints(min_length=1, max_length=ASSET_CULTURAL_CONTEXT_MAX_LENGTH)
]
_Title = Annotated[str, StringConstraints(max_length=ASSET_TITLE_MAX_LENGTH)]
_Attribution = Annotated[str, StringConstraints(max_length=ATTRIBUTION_TEXT_MAX_LENGTH)]

_WHITESPACE_RUN = re.compile(r"\s+")


class InspirationAssetIneligible(Exception):
    """A selected inspiration is no longer publicly eligible.

    Raised strictly before any provider selection. Safe message; never
    reveals which asset or why (mirrors
    ``designs.services.inspiration_availability_errors``)."""


class InspirationMetadataUnavailable(Exception):
    """Provider-facing inspiration metadata failed the safety scan.

    A generic pre-spend domain error. Never carries, logs or exposes the
    rejected text."""

    code = "inspiration_metadata_unavailable"

    def __init__(self):
        super().__init__("inspiration metadata unavailable")


def canonical_text(text: str) -> str:
    """NFKC-normalise, fold CRLF/CR to LF, collapse whitespace, strip outer
    whitespace. Preserves meaningful punctuation and does not alter case."""
    normalised = unicodedata.normalize("NFKC", text)
    normalised = normalised.replace("\r\n", "\n").replace("\r", "\n")
    return _WHITESPACE_RUN.sub(" ", normalised).strip()


def _assert_provider_metadata_safe(text: str) -> None:
    try:
        scan_generated_text(text)
    except GeneratedContentRejected:
        raise InspirationMetadataUnavailable() from None


class InspirationProviderCues(BaseModel):
    """Exactly what may reach the provider for one selected inspiration."""

    model_config = _MODEL_CONFIG

    garment_type: _GarmentType | None
    visual_description: _VisualDescription
    cultural_context: _CulturalContextText | None


class InspirationAcknowledgement(BaseModel):
    """Private audit / user-facing acknowledgement. NEVER sent to a provider."""

    model_config = _MODEL_CONFIG

    title: _Title
    attribution: _Attribution


class InspirationContextItem(BaseModel):
    model_config = _MODEL_CONFIG

    asset_id: str
    position: Annotated[int, Field(ge=1, le=MAX_INSPIRATION_CONTEXT_ITEMS)]
    provider_cues: InspirationProviderCues
    acknowledgement: InspirationAcknowledgement

    @field_validator("asset_id")
    @classmethod
    def _validate_asset_id(cls, value: str) -> str:
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError, TypeError):
            raise ValueError("asset_id must be a UUID string") from None
        return value


class InspirationContextSnapshot(BaseModel):
    """The complete, versioned, persisted-and-hashed inspiration context."""

    model_config = _MODEL_CONFIG

    schema_version: Literal[1]
    items: Annotated[list[InspirationContextItem], Field(max_length=MAX_INSPIRATION_CONTEXT_ITEMS)]

    @model_validator(mode="after")
    def _validate_positions_and_uniqueness(self) -> "InspirationContextSnapshot":
        positions = [item.position for item in self.items]
        if positions != list(range(1, len(self.items) + 1)):
            raise ValueError(
                "item positions must be 1..N, unique, contiguous and in selection order"
            )
        asset_ids = [item.asset_id for item in self.items]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("asset ids must be unique")
        return self


def build_inspiration_context_snapshot(design) -> InspirationContextSnapshot:
    """Build the validated, provider-safe snapshot for ``design``'s CURRENT
    selected inspirations, in selection order.

    Raises :class:`InspirationAssetIneligible` if any selected asset is no
    longer returned by ``InspirationAsset.objects.publicly_eligible()``, or
    :class:`InspirationMetadataUnavailable` if any provider-facing metadata
    fails the safety scan — both strictly before any provider is selected.
    Never reads image bytes, storage keys, hashes or rights internals; an
    empty selection yields a valid snapshot with an empty item list."""
    selections = list(design.inspiration_selections.order_by("position"))
    if not selections:
        return InspirationContextSnapshot(
            schema_version=INSPIRATION_CONTEXT_SCHEMA_VERSION, items=[]
        )

    asset_ids = [selection.inspiration_asset_id for selection in selections]
    eligible = {
        asset.pk: asset
        for asset in InspirationAsset.objects.publicly_eligible()
        .select_related("usage_rights")
        .filter(pk__in=asset_ids)
    }

    items: list[InspirationContextItem] = []
    for selection in selections:
        asset = eligible.get(selection.inspiration_asset_id)
        if asset is None:
            raise InspirationAssetIneligible(
                "a selected inspiration is no longer publicly available"
            )
        garment_type = canonical_text(asset.garment_type) or None
        visual_description = canonical_text(asset.alt_text)
        cultural_context = canonical_text(asset.cultural_context) or None
        _assert_provider_metadata_safe(visual_description)
        if cultural_context is not None:
            _assert_provider_metadata_safe(cultural_context)
        title = canonical_text(asset.title)
        attribution = (
            canonical_text(asset.usage_rights.attribution_text) if asset.usage_rights_id else ""
        )
        # Catalogue fields are normally shape-checked by the admin ModelForm,
        # not by a DB constraint or full_clean() — a value written through
        # another path (fixture, future non-form service) could still fail
        # these stricter pydantic bounds. Fold that into the same generic,
        # never-echoing exception this function already documents, rather
        # than letting a raw pydantic.ValidationError (which embeds the
        # offending value) escape as an undocumented failure mode.
        try:
            item = InspirationContextItem(
                asset_id=str(asset.pk),
                position=selection.position,
                provider_cues=InspirationProviderCues(
                    garment_type=garment_type,
                    visual_description=visual_description,
                    cultural_context=cultural_context,
                ),
                acknowledgement=InspirationAcknowledgement(title=title, attribution=attribution),
            )
        except PydanticValidationError:
            raise InspirationMetadataUnavailable() from None
        items.append(item)
    return InspirationContextSnapshot(
        schema_version=INSPIRATION_CONTEXT_SCHEMA_VERSION, items=items
    )


def _canonical_json(snapshot: InspirationContextSnapshot) -> str:
    """One deterministic canonical JSON representation: UTF-8, sorted keys,
    fixed compact separators, no timestamps, no machine-dependent values."""
    return json.dumps(
        snapshot.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def inspiration_context_sha256(snapshot: InspirationContextSnapshot) -> str:
    """SHA-256 hex digest of the canonical JSON representation."""
    return hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()


def provider_inspiration_cues(snapshot: InspirationContextSnapshot) -> list[dict]:
    """The provider-visible subset ONLY: position, garment type, visual
    description, cultural context. Omits asset id, title and attribution."""
    return [
        {
            "position": item.position,
            "garment_type": item.provider_cues.garment_type,
            "visual_description": item.provider_cues.visual_description,
            "cultural_context": item.provider_cues.cultural_context,
        }
        for item in snapshot.items
    ]


def inspiration_acknowledgements(snapshot: InspirationContextSnapshot) -> list[dict]:
    """The audit/acknowledgement subset ONLY: position, title, attribution.
    Omits provider cues and the asset id."""
    return [
        {
            "position": item.position,
            "title": item.acknowledgement.title,
            "attribution": item.acknowledgement.attribution,
        }
        for item in snapshot.items
    ]
