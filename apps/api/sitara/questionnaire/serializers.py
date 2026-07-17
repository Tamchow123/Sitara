"""Public questionnaire serializers.

The active-questionnaire response exposes EXACTLY ``id``, ``version`` and
``schema`` — never created_by/activated_by, admin notes, database
timestamps or any user detail.
"""

from rest_framework import serializers

from .models import QuestionnaireVersion


class ActiveQuestionnaireSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionnaireVersion
        fields = ["id", "version", "schema"]
        read_only_fields = fields
