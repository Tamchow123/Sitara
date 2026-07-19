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

_JOB_UUID = (
    r"(?P<job_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}" r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

_VERSION_UUID = (
    r"(?P<version_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
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
    re_path(
        rf"^designs/{_UUID}/generate/?$",
        views.DesignGenerateView.as_view(),
        name="design-generate",
    ),
    re_path(
        rf"^designs/{_UUID}/versions/{_VERSION_UUID}/images/?$",
        views.DesignVersionImagesView.as_view(),
        name="design-version-images",
    ),
    re_path(
        rf"^designs/{_UUID}/versions/{_VERSION_UUID}/result/?$",
        views.DesignVersionResultView.as_view(),
        name="design-version-result",
    ),
    re_path(rf"^jobs/{_JOB_UUID}/?$", views.GenerationJobView.as_view(), name="generation-job"),
]
