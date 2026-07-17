"""Catalogue routes.

Slash-optional for the same reason as the other API routes: the Next.js
rewrite strips trailing slashes, and an APPEND_SLASH redirect through the
proxy would loop. The UUID pattern is matched in the URL, so a malformed
id never reaches a view.
"""

from django.urls import re_path

from . import views

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

urlpatterns = [
    re_path(
        r"^inspiration-assets/?$",
        views.InspirationAssetListView.as_view(),
        name="inspiration-asset-list",
    ),
    re_path(
        rf"^inspiration-assets/(?P<asset_id>{_UUID})/image/?$",
        views.InspirationAssetImageView.as_view(),
        name="inspiration-asset-image",
    ),
    re_path(
        rf"^inspiration-assets/(?P<asset_id>{_UUID})/thumbnail/?$",
        views.InspirationAssetThumbnailView.as_view(),
        name="inspiration-asset-thumbnail",
    ),
]
