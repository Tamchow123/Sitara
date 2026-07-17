"""Design routes.

Slash-optional patterns for the same reason as the auth routes: the Next.js
rewrite strips trailing slashes from proxied paths, and an APPEND_SLASH
redirect through the proxy would loop (and drop POST/PATCH bodies).

The UUID is matched structurally in the URL; anything that is not a UUID
never reaches a view. Whether a matched UUID EXISTS is decided only after
ownership filtering — and answered with 404 either way.
"""

from django.urls import re_path

from . import views

_UUID = (
    r"(?P<design_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

urlpatterns = [
    re_path(r"^designs/?$", views.DesignListCreateView.as_view(), name="design-list"),
    re_path(rf"^designs/{_UUID}/?$", views.DesignDetailView.as_view(), name="design-detail"),
    re_path(
        rf"^designs/{_UUID}/validate/?$",
        views.DesignValidateView.as_view(),
        name="design-validate",
    ),
]
