from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("sitara.health.urls")),
    path("api/v1/", include("sitara.designs.urls")),
    path("api/v1/", include("sitara.questionnaire.urls")),
    path("api/v1/", include("sitara.catalogue.urls")),
    path("api/v1/auth/", include("sitara.accounts.urls")),
]

# Django admin (Phase 16, Part D): mounted ONLY when explicitly enabled. In
# production it is disabled by default, so the route does not exist at all —
# changing the URL is not the security control; not mounting it is. Enabling it
# never bypasses Django's staff/superuser checks.
if settings.ADMIN_ENABLED:
    urlpatterns.insert(0, path("admin/", admin.site.urls))
