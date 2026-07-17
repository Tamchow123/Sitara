"""Health and public-configuration endpoints.

These are the only anonymous endpoints in Phase 3A; everything else in the
API defaults to authenticated access (see REST_FRAMEWORK settings). The
public config endpoint returns ONLY safe, non-secret values — never tokens,
storage credentials, bucket details, Django secrets or internal limits.
"""

from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from sitara.ai_gateway.policy import generation_is_available

from . import checks
from .openapi import LiveResponseSerializer, PublicConfigSerializer, ReadyResponseSerializer


@extend_schema(
    operation_id="health_live",
    tags=["Health"],
    responses={200: LiveResponseSerializer},
    summary="Liveness",
    description="The process answers. No dependency checks. No authentication required.",
)
@api_view(["GET"])
@permission_classes([AllowAny])
def live(request):
    """Liveness: the process answers. No dependency checks here."""
    return Response({"status": "ok", "service": "sitara-api"})


@extend_schema(
    operation_id="health_ready",
    tags=["Health"],
    responses={
        200: ReadyResponseSerializer,
        503: OpenApiResponse(
            ReadyResponseSerializer,
            description="At least one dependency is unavailable; the body still lists each check.",
        ),
    },
    summary="Readiness",
    description=(
        "PostgreSQL, the Redis broker, the auth-rate-limit cache and private "
        "object storage. Returns 503 with a displayable per-check body when "
        "any dependency is down. No authentication required."
    ),
)
@api_view(["GET"])
@permission_classes([AllowAny])
def ready(request):
    """Readiness: PostgreSQL, Redis broker, the Django auth-rate-limit
    cache, and private object storage."""
    results = {
        "database": "ok" if checks.check_database() else "unavailable",
        "redis": "ok" if checks.check_redis() else "unavailable",
        "auth_cache": "ok" if checks.check_auth_cache() else "unavailable",
        "storage": "ok" if checks.check_storage() else "unavailable",
    }
    all_ok = all(value == "ok" for value in results.values())
    return Response(
        {"status": "ok" if all_ok else "unavailable", "checks": results},
        status=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@extend_schema(
    operation_id="config_public",
    tags=["Configuration"],
    responses={200: PublicConfigSerializer},
    summary="Public configuration",
    description=(
        "Safe, non-secret configuration for the frontend: demo mode, whether "
        "generation is available, and the inspiration/refinement limits. No "
        "authentication required; no tokens, credentials or storage details."
    ),
)
@api_view(["GET"])
@permission_classes([AllowAny])
def public_config(request):
    """Safe public configuration for the frontend.

    generation_enabled comes from the central capability policy
    (environment authorisation AND implementation availability), so this
    endpoint can never claim generation exists when no paid provider is
    implemented — even with both environment gates open."""
    return Response(
        {
            "demo_mode": settings.DEMO_MODE,
            "generation_enabled": generation_is_available(),
            "max_inspiration_images": settings.MAX_INSPIRATION_IMAGES,
            "max_refinements": settings.MAX_REFINEMENTS,
        }
    )
