"""Private ownership filtering.

Every design read or write goes through ``accessible_designs`` BEFORE any
UUID lookup, so a design that is not the caller's simply does not exist from
their perspective: nonexistent designs, another anonymous browser's designs,
another user's designs and formerly-anonymous designs already claimed by a
user are all indistinguishable 404s. Nothing is ever loaded globally and
then rejected with 403 — that would confirm the UUID exists.
"""

from django.db.models import QuerySet

from .models import Design, GenerationAttempt
from .services import resolve_current_design_session


def accessible_designs(request) -> QuerySet[Design]:
    """Designs the current request may see, newest first.

    Resolving the workspace first (without creating one) also performs the
    lazy post-login promotion: an authenticated request whose Django session
    still points at an unclaimed anonymous workspace claims it here, so the
    browser's pre-login designs join the user's collection on their next
    design API interaction."""
    current = resolve_current_design_session(request, create=False)
    if request.user.is_authenticated:
        # Across ALL of the user's workspaces (any browser they used).
        return Design.objects.filter(design_session__user=request.user)
    if current is None:
        return Design.objects.none()
    return Design.objects.filter(design_session=current)


def accessible_generation_attempts(request) -> QuerySet[GenerationAttempt]:
    """Generation jobs the current request may see.

    A job inherits its Design's private ownership, so the accessible set is
    exactly the attempts whose design is in ``accessible_designs``. Like every
    other private lookup, this is applied BEFORE the UUID lookup so an
    inaccessible or nonexistent job is one indistinguishable 404 — the job
    endpoint never reveals whether a foreign UUID exists."""
    return GenerationAttempt.objects.filter(design__in=accessible_designs(request))
