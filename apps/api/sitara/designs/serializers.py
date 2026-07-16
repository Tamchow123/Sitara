"""Design API serializers.

The write serializer accepts EXACTLY ``title`` and rejects everything else
with a controlled 400 — server-owned fields (id, design_session, status,
answers, versions, generation attempts, timestamps) must never be silently
ignored, because silence teaches clients they worked.

The read serializer never exposes the DesignSession identifier, the user,
version rows, generation attempts or storage keys.
"""

from rest_framework import serializers

from .models import DESIGN_TITLE_MAX_LENGTH, Design


class DesignWriteSerializer(serializers.Serializer):
    title = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=DESIGN_TITLE_MAX_LENGTH,
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


class DesignReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Design
        # Exactly the public contract — nothing about sessions, users,
        # versions, attempts or storage.
        fields = ["id", "title", "status", "answers", "created_at", "updated_at"]
        read_only_fields = fields
