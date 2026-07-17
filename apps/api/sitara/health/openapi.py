"""Schema serializers for the health and public-configuration responses.

Documentation-only: the views return plain dicts through DRF ``Response``;
these serializers describe those exact shapes for drf-spectacular. No
storage endpoints, bucket names or credentials appear here — only the safe
"ok"/"unavailable" check labels and the public product limits.
"""

from rest_framework import serializers


class LiveResponseSerializer(serializers.Serializer):
    status = serializers.CharField(help_text='Always "ok" when the process answers.')
    service = serializers.CharField()


class ReadyChecksSerializer(serializers.Serializer):
    database = serializers.CharField(help_text='"ok" or "unavailable".')
    redis = serializers.CharField(help_text='"ok" or "unavailable".')
    auth_cache = serializers.CharField(help_text='"ok" or "unavailable".')
    storage = serializers.CharField(help_text='"ok" or "unavailable".')


class ReadyResponseSerializer(serializers.Serializer):
    status = serializers.CharField(help_text='"ok" (200) or "unavailable" (503).')
    checks = ReadyChecksSerializer()


class PublicConfigSerializer(serializers.Serializer):
    demo_mode = serializers.BooleanField()
    generation_enabled = serializers.BooleanField(
        help_text="True only when environment gates AND a paid provider implementation allow it."
    )
    max_inspiration_images = serializers.IntegerField()
    max_refinements = serializers.IntegerField()
