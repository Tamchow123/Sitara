"""JSON CSRF failure handling.

Registered via settings.CSRF_FAILURE_VIEW so browser/API clients never
receive Django's HTML CSRF error page, and Django's internal failure reason
is never exposed."""

from django.http import JsonResponse


def csrf_failure(request, reason=""):
    """`reason` (Django's internal explanation) is deliberately unused."""
    response = JsonResponse(
        {
            "error": {
                "code": "csrf_failed",
                "message": "The security token is missing or invalid. Refresh and try again.",
            }
        },
        status=403,
    )
    response["Cache-Control"] = "no-store"
    return response
