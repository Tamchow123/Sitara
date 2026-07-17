"""Documentation-only schema serializers for the design API responses.

These describe the wire shapes for drf-spectacular so the generated
TypeScript is precise; the runtime views build responses from the payload
FUNCTIONS in :mod:`sitara.designs.serializers`. Nothing here ever exposes the
DesignSession id, user, version rows, generation attempts, storage keys,
image hashes, rights evidence, verifier identity or internal notes.
"""

from rest_framework import serializers

from sitara.questionnaire.openapi import QuestionnaireSchemaSerializer


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
