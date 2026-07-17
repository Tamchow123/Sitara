"""Schema serializers for the public inspiration-catalogue responses.

Documentation-only: the view builds each entry with
:func:`~sitara.catalogue.serializers.public_asset_payload`. This mirrors
EXACTLY the public surface — id, title, alt text, garment type, cultural
context, approved attribution text and the two relative image endpoints.
Deliberately absent (never documented, never returned): storage keys, image
SHA-256, dimensions/byte size, rights record id, evidence references,
verifier identity, internal notes and any licence detail beyond attribution.
"""

from rest_framework import serializers


class PublicInspirationAssetSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    alt_text = serializers.CharField()
    garment_type = serializers.CharField()
    cultural_context = serializers.CharField()
    attribution = serializers.CharField(help_text="Approved public attribution text, if any.")
    image_url = serializers.CharField(help_text="Relative Django endpoint streaming WebP bytes.")
    thumbnail_url = serializers.CharField(
        help_text="Relative Django endpoint streaming WebP bytes."
    )


class InspirationCatalogueResponseSerializer(serializers.Serializer):
    assets = PublicInspirationAssetSerializer(many=True)
