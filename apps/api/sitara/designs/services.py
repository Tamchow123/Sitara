"""Domain services: current-workspace resolution and version numbering.

``resolve_current_design_session`` is the ONLY code that reads or writes the
``sitara_design_session_id`` pointer in Django session data. The pointer is
an internal DesignSession UUID — never a Django session key, and never
exposed through the API.
"""

import logging
import uuid

from django.conf import settings
from django.contrib.sessions.models import Session
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


class WorkspaceCoordinationError(Exception):
    """The browser's database session could not be locked or persisted.

    Workspace creation must FAIL (a controlled 503 in the view) rather than
    proceed unlocked — an unlocked fallback would reintroduce the exact
    duplicate-workspace race the lock exists to prevent. The message never
    contains session keys, cookie values or store payloads."""


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


def _workspace_for_pointer(raw, user) -> DesignSession | None:
    """Evaluate a stored pointer value under the ownership rules.

    Returns the usable workspace or None (malformed pointer, deleted
    workspace, claimed-while-anonymous, another user's workspace, or a
    claim race lost to a different user). Never mutates session data — the
    caller decides whether to drop the pointer. A malformed pointer must
    never crash a request or leak whether any DesignSession exists."""
    if raw is None:
        return None
    try:
        pointer = uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        logger.warning("ignoring malformed design-session pointer")
        return None
    design_session = DesignSession.objects.filter(pk=pointer).first()
    if design_session is None:
        return None
    if user is None:
        # Claimed workspaces are never reachable anonymously.
        return design_session if design_session.user_id is None else None
    if design_session.user_id is None:
        # Lazy post-login promotion; None when a DIFFERENT user won a
        # concurrent claim (never reuse another user's workspace).
        claimed = _claim_for_user(design_session, user)
        if claimed is not None:
            logger.info("design session claimed design_session=%s user_id=%s", claimed.pk, user.pk)
        return claimed
    if design_session.user_id == user.pk:
        return design_session
    # Another user's workspace (e.g. shared browser): never reuse, never
    # transfer.
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

    ``create=False`` is the lightweight read path. ``create=True`` (which
    may raise WorkspaceCoordinationError) serialises against other requests
    sharing the same browser session by locking the django_session row —
    see ``_resolve_for_create``.
    """
    if create:
        return _resolve_for_create(request)

    user = request.user if request.user.is_authenticated else None
    raw = request.session.get(DESIGN_SESSION_KEY)
    if raw is None:
        return None
    design_session = _workspace_for_pointer(raw, user)
    if design_session is None:
        # Stale/malformed/foreign pointer: discard (hygiene only — an
        # ignored pointer would be equally unusable next time).
        _drop_pointer(request)
        return None
    _touch(design_session)
    return design_session


def _lock_browser_session(request) -> tuple[Session, dict]:
    """Lock the browser's django_session row and return it with its
    FRESHEST decoded data (what other, already-committed requests wrote —
    not this request's possibly stale snapshot). Must run inside a
    transaction; the row lock is released when that transaction ends.

    Never logs or raises session keys, cookie values or session payloads."""
    try:
        if request.session.session_key is None:
            request.session.create()
        row = (
            Session.objects.select_for_update()
            .filter(session_key=request.session.session_key)
            .first()
        )
        if row is None:
            # A cookie without a live row (stale key, or a session that was
            # never saved): materialise a fresh session and lock that.
            request.session.create()
            row = Session.objects.select_for_update().get(session_key=request.session.session_key)
        return row, request.session.decode(row.session_data)
    except Exception as exc:
        raise WorkspaceCoordinationError("the browser session could not be locked") from exc


def _persist_pointer(request, row: Session, fresh_data: dict, pointer: str) -> None:
    """Write the chosen pointer into the LOCKED session row."""
    try:
        fresh_data[DESIGN_SESSION_KEY] = pointer
        row.session_data = request.session.encode(fresh_data)
        row.save(update_fields=["session_data"])
    except Exception as exc:
        raise WorkspaceCoordinationError("the browser session could not be persisted") from exc


def _resolve_for_create(request) -> DesignSession:
    """Concurrency-safe workspace resolution for the create path.

    Two requests sharing one browser session (e.g. two tabs) must never
    both observe 'no pointer' and create separate workspaces — competing
    session saves would strand one design. The browser's django_session
    ROW is the coordination boundary: lock it, re-read the freshest data,
    re-check the pointer under the ownership rules, and only create a new
    DesignSession if the locked data still has no usable workspace. The
    loser of the race blocks on the lock and then reuses the winner's
    workspace instead of creating a second one.

    Raises WorkspaceCoordinationError when the session store cannot be
    locked or persisted — there is deliberately NO unlocked fallback."""
    user = request.user if request.user.is_authenticated else None
    with transaction.atomic():
        row, fresh_data = _lock_browser_session(request)
        design_session = _workspace_for_pointer(fresh_data.get(DESIGN_SESSION_KEY), user)
        if design_session is None:
            design_session = DesignSession.objects.create(user=user)
        pointer = str(design_session.id)
        if fresh_data.get(DESIGN_SESSION_KEY) != pointer:
            _persist_pointer(request, row, fresh_data, pointer)
        # Synchronise the in-memory session too: SessionMiddleware saves
        # THIS request's snapshot at response time, and without the sync a
        # snapshot taken before another tab wrote the pointer would
        # overwrite the row without it.
        _store_pointer(request, design_session)
        _touch(design_session)
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
