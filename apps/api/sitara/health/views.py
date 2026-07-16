"""Health and public-configuration endpoints.

These are the only anonymous endpoints in Phase 3A; everything else in the
API defaults to authenticated access (see REST_FRAMEWORK settings). The
public config endpoint returns ONLY safe, non-secret values — never tokens,
storage credentials, bucket details, Django secrets or internal limits.
"""

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import checks


@api_view(["GET"])
@permission_classes([AllowAny])
def live(request):
    """Liveness: the process answers. No dependency checks here."""
    return Response({"status": "ok", "service": "sitara-api"})


@api_view(["GET"])
@permission_classes([AllowAny])
def ready(request):
    """Readiness: PostgreSQL, Redis and private object storage."""
    results = {
        "database": "ok" if checks.check_database() else "unavailable",
        "redis": "ok" if checks.check_redis() else "unavailable",
        "storage": "ok" if checks.check_storage() else "unavailable",
    }
    all_ok = all(value == "ok" for value in results.values())
    return Response(
        {"status": "ok" if all_ok else "unavailable", "checks": results},
        status=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def public_config(request):
    """Safe public configuration for the frontend. generation_enabled is
    False throughout Phase 3A: paid generation requires BOTH
    DEMO_MODE=false and ALLOW_PAID_AI_CALLS=true, and no paid provider is
    implemented yet."""
    return Response(
        {
            "demo_mode": settings.DEMO_MODE,
            "generation_enabled": (not settings.DEMO_MODE) and settings.ALLOW_PAID_AI_CALLS,
            "max_inspiration_images": settings.MAX_INSPIRATION_IMAGES,
            "max_refinements": settings.MAX_REFINEMENTS,
        }
    )
