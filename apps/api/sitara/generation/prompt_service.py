"""Atomic image-prompt persistence (Phase 9).

Builds the deterministic image prompt for a persisted DesignVersion and stores
it alongside the builder version, under a row lock, with strict immutability.
No provider call ever happens here — the prompt is produced entirely by local
application code from the already-validated DesignSpec.
"""

import logging

from django.db import transaction

from sitara.designs.models import DesignVersion

from .design_spec import DESIGN_SPEC_SCHEMA_VERSION, DesignSpec
from .prompt_builder import (
    PROMPT_BUILDER_VERSION,
    ImagePromptBuildError,
    build_image_prompt,
)

logger = logging.getLogger(__name__)


class ImagePromptImmutable(Exception):
    """A different image prompt or builder version already exists on the row.

    Historical audit data is never overwritten. Carries only a safe message —
    never the stored or newly-built prompt."""


def build_and_store_image_prompt(design_version: DesignVersion) -> DesignVersion:
    """Build and persist the image prompt for ``design_version``, atomically.

    Locks the DesignVersion row, requires a persisted DesignSpec of the
    supported schema version, revalidates the stored JSON through
    :class:`DesignSpec`, builds the deterministic prompt (which runs the
    generated-content safety scan), and stores the exact prompt together with
    :data:`PROMPT_BUILDER_VERSION`.

    Immutability:

    - the first build populates the two empty fields;
    - rerunning with the SAME builder version and identical prompt is
      idempotent (returns the row unchanged);
    - an existing DIFFERENT prompt or builder version is never overwritten
      (raises :class:`ImagePromptImmutable`) — a future builder must create a
      NEW DesignVersion rather than rewrite this row's audit trail.

    Raises :class:`ImagePromptBuildError` (missing/unsupported spec, unsafe
    content or overrun) or :class:`ImagePromptImmutable`. Neither carries prompt
    contents or spec text."""
    with transaction.atomic():
        version = DesignVersion.objects.select_for_update().get(pk=design_version.pk)

        if version.design_spec is None:
            raise ImagePromptBuildError("design version has no design spec")
        if version.design_spec_schema_version != DESIGN_SPEC_SCHEMA_VERSION:
            raise ImagePromptBuildError("design version has an unsupported design spec schema")

        # Revalidate the stored JSON before building (defence in depth); a
        # ValidationError is surfaced as a controlled, contents-free error.
        try:
            spec = DesignSpec.model_validate(version.design_spec)
        except Exception as exc:
            raise ImagePromptBuildError("stored design spec failed validation") from exc

        prompt = build_image_prompt(spec)

        if version.image_prompt or version.prompt_builder_version:
            # Already built: idempotent only when the builder version AND the
            # exact prompt match; otherwise refuse to overwrite audit history.
            if (
                version.prompt_builder_version == PROMPT_BUILDER_VERSION
                and version.image_prompt == prompt
            ):
                return version
            raise ImagePromptImmutable(
                "an image prompt already exists for this version and cannot be overwritten"
            )

        version.image_prompt = prompt
        version.prompt_builder_version = PROMPT_BUILDER_VERSION
        version.save(update_fields=["image_prompt", "prompt_builder_version", "updated_at"])

    logger.info(
        "image prompt stored design_version=%s builder_version=%s chars=%s",
        version.id,
        PROMPT_BUILDER_VERSION,
        len(prompt),
    )
    return version
