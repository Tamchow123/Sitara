"""Domain services: current-workspace resolution and version numbering.

``resolve_current_design_session`` is the ONLY code that reads or writes the
``sitara_design_session_id`` pointer in Django session data. The pointer is
an internal DesignSession UUID — never a Django session key, and never
exposed through the API.
"""

import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Design, DesignSession, DesignVersion

logger = logging.getLogger(__name__)

# Key inside Django session DATA (which survives the login key rotation —
# django.contrib.auth.login() cycles the session KEY but keeps the data).
DESIGN_SESSION_KEY = "sitara_design_session_id"


class DesignVersionLimitReached(Exception):
    """The design already has MAX_DESIGN_VERSIONS versions."""


def _pointer_uuid(request) -> uuid.UUID | None:
    """The session-stored DesignSession UUID, or None when absent/malformed.

    A malformed pointer (tampered or legacy data) is dropped and treated as
    'no workspace yet' — it must never crash a request or leak whether any
    DesignSession exists."""
    raw = request.session.get(DESIGN_SESSION_KEY)
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        logger.warning("dropping malformed design-session pointer")
        del request.session[DESIGN_SESSION_KEY]
        return None


def _drop_pointer(request) -> None:
    request.session.pop(DESIGN_SESSION_KEY, None)


def _store_pointer(request, design_session: DesignSession) -> None:
    request.session[DESIGN_SESSION_KEY] = str(design_session.id)


def _touch(design_session: DesignSession) -> None:
    """Update last_seen_at without racing other fields (no full save())."""
    now = timezone.now()
    DesignSession.objects.filter(pk=design_session.pk).update(last_seen_at=now)
    design_session.last_seen_at = now


def _claim_for_user(design_session: DesignSession, user) -> DesignSession | None:
    """Atomically associate an unclaimed workspace with ``user``.

    The conditional UPDATE means concurrent requests cannot both claim (or
    re-claim) a workspace: exactly one request flips ``user`` from NULL, and
    everyone re-reads the winner's result. Returns the (re-fetched) session
    when it now belongs to ``user``, else None."""
    with transaction.atomic():
        DesignSession.objects.filter(pk=design_session.pk, user__isnull=True).update(
            user=user, updated_at=timezone.now()
        )
        current = DesignSession.objects.filter(pk=design_session.pk).first()
    if current is not None and current.user_id == user.pk:
        return current
    return None


def resolve_current_design_session(request, *, create: bool) -> DesignSession | None:
    """The request's current design workspace (creating one only if asked).

    Anonymous browser: the workspace referenced by the session pointer, but
    only while it is still unclaimed — a workspace claimed by ANY user is
    invisible to anonymous requests even if the pointer survives.

    Authenticated browser: a pointer to an unclaimed workspace claims it for
    the user (lazy promotion after login); the user's own workspace is
    reused; another user's workspace is never reused. With no usable
    pointer, ``create=True`` starts a fresh user-owned workspace; list-style
    callers pass ``create=False`` and query by user instead.
    """
    user = request.user if request.user.is_authenticated else None

    pointer = _pointer_uuid(request)
    if pointer is not None:
        design_session = DesignSession.objects.filter(pk=pointer).first()
        if design_session is None:
            # Stale pointer (workspace deleted): drop and fall through.
            _drop_pointer(request)
        elif user is None:
            if design_session.user_id is None:
                _touch(design_session)
                return design_session
            # Claimed workspaces are never reachable anonymously.
            _drop_pointer(request)
        else:
            if design_session.user_id is None:
                claimed = _claim_for_user(design_session, user)
                if claimed is not None:
                    logger.info(
                        "design session claimed design_session=%s user_id=%s",
                        claimed.pk,
                        user.pk,
                    )
                    _touch(claimed)
                    return claimed
                # A different user won a concurrent claim: never reuse.
                _drop_pointer(request)
            elif design_session.user_id == user.pk:
                _touch(design_session)
                return design_session
            else:
                # Another user's workspace (e.g. shared browser): never
                # reuse, never transfer.
                _drop_pointer(request)

    if not create:
        return None

    design_session = DesignSession.objects.create(user=user)
    _store_pointer(request, design_session)
    return design_session


def create_next_design_version(design: Design) -> DesignVersion:
    """Create the next DesignVersion under the application-level maximum.

    The Design row is locked for the duration, so concurrent calls serialise
    and cannot compute the same next number; the database uniqueness
    constraint on (design, version_number) remains the final backstop."""
    with transaction.atomic():
        locked = Design.objects.select_for_update().get(pk=design.pk)
        highest = (
            DesignVersion.objects.filter(design=locked).aggregate(highest=Max("version_number"))[
                "highest"
            ]
            or 0
        )
        if highest >= settings.MAX_DESIGN_VERSIONS:
            raise DesignVersionLimitReached(
                f"design already has the maximum of {settings.MAX_DESIGN_VERSIONS} versions"
            )
        return DesignVersion.objects.create(design=locked, version_number=highest + 1)
