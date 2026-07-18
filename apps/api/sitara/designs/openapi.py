"""Documentation-only schema serializers for the design API responses.

These describe the wire shapes for drf-spectacular so the generated
TypeScript is precise; the runtime views build responses from the payload
FUNCTIONS in :mod:`sitara.designs.serializers`. Nothing here ever exposes the
DesignSession id, user, version rows, generation attempts, storage keys,
image hashes, rights evidence, verifier identity or internal notes.
"""

from rest_framework import serializers

from sitara.questionnaire.openapi import QuestionnaireSchemaSerializer

from .models import GenerationAttempt


class DesignListItemSerializer(serializers.Serializer):
    """A compact list row — no questionnaire schema, no inspiration records."""

    id = serializers.UUIDField()
    title = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class DesignListResponseSerializer(serializers.Serializer):
    designs = DesignListItemSerializer(many=True)


class DesignQuestionnaireSerializer(serializers.Serializer):
    """The questionnaire a design is pinned to (null for legacy designs)."""

    id = serializers.UUIDField()
    version = serializers.IntegerField()
    schema = QuestionnaireSchemaSerializer()


class SelectedInspirationAssetSerializer(serializers.Serializer):
    """The public catalogue fields for an inspiration that is still eligible."""

    id = serializers.UUIDField()
    title = serializers.CharField()
    alt_text = serializers.CharField()
    garment_type = serializers.CharField()
    cultural_context = serializers.CharField()
    attribution = serializers.CharField()
    image_url = serializers.CharField()
    thumbnail_url = serializers.CharField()


class SelectedInspirationSerializer(serializers.Serializer):
    """One inspiration selection with its live availability.

    ``available: false`` with ``asset: null`` means the previously-selected
    asset is no longer publicly eligible (retired, expired or revoked). The
    reason is deliberately not disclosed."""

    id = serializers.UUIDField(help_text="The selected inspiration asset id.")
    position = serializers.IntegerField()
    available = serializers.BooleanField()
    asset = SelectedInspirationAssetSerializer(allow_null=True)


class DesignDetailResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    status = serializers.CharField()
    questionnaire = DesignQuestionnaireSerializer(allow_null=True)
    answers = serializers.JSONField(help_text="Answers keyed by stable question id.")
    selected_inspirations = SelectedInspirationSerializer(many=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class DesignValidationSuccessSerializer(serializers.Serializer):
    valid = serializers.BooleanField()


class GenerationJobSerializer(serializers.Serializer):
    """The stable public shape of one generation job (Phase 10).

    Deliberately excludes every provider/storage provenance field (provider,
    model, prediction id, seed, parameters, storage key, image hash/size and
    the Celery task id) — only the lifecycle is public."""

    id = serializers.UUIDField()
    design_id = serializers.UUIDField()
    design_version_id = serializers.UUIDField(allow_null=True)
    # Derived from the model so the documented enum can never drift from the
    # actual lifecycle values (and their DB constraints).
    status = serializers.ChoiceField(choices=GenerationAttempt.Status.values)
    error_code = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


class GenerationJobResponseSerializer(serializers.Serializer):
    job = GenerationJobSerializer()
