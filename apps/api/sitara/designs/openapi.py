"""Schema serializers for the design API responses.

The list endpoint wraps designs under a ``designs`` key. The item shape is
the existing :class:`~sitara.designs.serializers.DesignReadSerializer` — the
public read contract (never the DesignSession id, user, version rows,
generation attempts or storage keys).
"""

from rest_framework import serializers

from .serializers import DesignReadSerializer


class DesignListResponseSerializer(serializers.Serializer):
    designs = DesignReadSerializer(many=True)
