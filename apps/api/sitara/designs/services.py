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

from sitara.catalogue.models import InspirationAsset
from sitara.questionnaire.answer_validation import (
    QuestionnaireAnswerError,
    validate_questionnaire_answers,
)
from sitara.questionnaire.models import QuestionnaireVersion

from .models import Design, DesignInspiration, DesignSession, DesignVersion

logger = logging.getLogger(__name__)

# Sentinel distinguishing "field omitted from this partial update" from an
# explicit value (including None / empty). Each field of ``update_design_draft``
# is only touched when its argument is not UNSET.
UNSET = object()

# Key inside Django session DATA (which survives the login key rotation —
# django.contrib.auth.login() cycles the session KEY but keeps the data).
DESIGN_SESSION_KEY = "sitara_design_session_id"


class DesignVersionLimitReached(Exception):
    """The design already has MAX_DESIGN_VERSIONS versions."""


class CrossDesignLineageError(Exception):
    """A supplied ``parent_version`` belongs to a different Design, or is not
    a :class:`~sitara.designs.models.DesignVersion` instance at all.

    Database CHECK constraints cannot express the cross-row invariant, so
    this is the defence-in-depth backstop below every future refinement
    caller (Part B/C) — safe message; never exposes a UUID or design id."""


class UnsupportedVersionFieldError(Exception):
    """``create_next_design_version_locked`` was called with a
    ``version_fields`` key outside its documented, narrow accepted set."""


class DraftUpdateError(Exception):
    """A draft update was rejected for a controlled, safe reason.

    ``code`` is a stable machine code and ``field_errors`` (when present)
    maps a request field or question id to safe messages, so the view can
    return a 400 ``validation_failed`` without leaking storage keys, rights
    data or which private object exists."""

    def __init__(self, code: str, message: str, *, field_errors: dict | None = None):
        self.code = code
        self.message = message
        self.field_errors = field_errors
        super().__init__(message)


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


_REFINEMENT_VERSION_FIELDS = frozenset(
    {
        "parent_version",
        "refinement_request",
        "refinement_request_schema_version",
        "refinement_request_sha256",
    }
)


def create_next_design_version_locked(locked_design: Design, **version_fields) -> DesignVersion:
    """Create the next DesignVersion for an ALREADY-locked Design.

    The caller MUST already be inside a ``transaction.atomic()`` block holding
    a ``select_for_update()`` lock on ``locked_design`` — this lets a caller
    that must first perform its own checks under that same lock (e.g. the
    generation service's final freshness re-check) create the version without
    opening a second, disconnected check/write sequence. Applies the same
    application-level maximum and relies on the same uniqueness backstop.

    ``version_fields`` are passed straight into the single ``.create()`` call
    and MUST be a subset of :data:`_REFINEMENT_VERSION_FIELDS`
    (``parent_version``/``refinement_request``/
    ``refinement_request_schema_version``/``refinement_request_sha256`` for a
    refinement's version 2) — a version 2 row must carry complete refinement
    provenance from the moment it is created, never a bare row populated by a
    later ``save()``, because the database CHECK constraints tying
    ``parent_version`` to ``version_number`` are evaluated immediately, not
    deferred to transaction commit.

    Raises :class:`UnsupportedVersionFieldError` for a ``version_fields`` key
    outside :data:`_REFINEMENT_VERSION_FIELDS`, and :class:`CrossDesignLineageError`
    for a ``parent_version`` that is not a :class:`DesignVersion` instance or
    does not belong to ``locked_design`` — no CHECK constraint can express
    that last, cross-row invariant, so every caller is protected here rather
    than trusting each call site individually."""
    unsupported = set(version_fields) - _REFINEMENT_VERSION_FIELDS
    if unsupported:
        raise UnsupportedVersionFieldError(f"unsupported version field(s): {sorted(unsupported)}")
    parent_version = version_fields.get("parent_version")
    if parent_version is not None:
        if not isinstance(parent_version, DesignVersion):
            raise CrossDesignLineageError("parent_version must be a DesignVersion instance")
        if parent_version.design_id != locked_design.pk:
            raise CrossDesignLineageError("parent_version must belong to the same design")
    highest = (
        DesignVersion.objects.filter(design=locked_design).aggregate(highest=Max("version_number"))[
            "highest"
        ]
        or 0
    )
    if highest >= settings.MAX_DESIGN_VERSIONS:
        raise DesignVersionLimitReached(
            f"design already has the maximum of {settings.MAX_DESIGN_VERSIONS} versions"
        )
    return DesignVersion.objects.create(
        design=locked_design, version_number=highest + 1, **version_fields
    )


def create_next_design_version(design: Design, **version_fields) -> DesignVersion:
    """Create the next DesignVersion under the application-level maximum.

    The Design row is locked for the duration, so concurrent calls serialise
    and cannot compute the same next number; the database uniqueness
    constraint on (design, version_number) remains the final backstop. See
    :func:`create_next_design_version_locked` for ``version_fields``."""
    with transaction.atomic():
        locked = Design.objects.select_for_update().get(pk=design.pk)
        return create_next_design_version_locked(locked, **version_fields)


def _assign_questionnaire_version(design: Design, questionnaire_version_id) -> None:
    """Assign the design's questionnaire version — at most once, ever.

    A version may be linked only if it is active or retired (a draft
    questionnaire can never receive user answers). Once assigned, the link is
    immutable: re-sending the SAME id is a no-op; a DIFFERENT id is rejected,
    because persisted answers reference that version's stable ids forever."""
    try:
        target = uuid.UUID(str(questionnaire_version_id))
    except (ValueError, AttributeError, TypeError):
        raise DraftUpdateError(
            "validation_failed",
            "Invalid questionnaire version.",
            field_errors={"questionnaire_version_id": ["Unknown questionnaire version."]},
        ) from None

    if design.questionnaire_version_id is not None:
        if design.questionnaire_version_id == target:
            return
        raise DraftUpdateError(
            "validation_failed",
            "The questionnaire version cannot be changed once assigned.",
            field_errors={
                "questionnaire_version_id": [
                    "This design's questionnaire version is already set and cannot be changed."
                ]
            },
        )

    version = QuestionnaireVersion.objects.filter(pk=target).first()
    if version is None or version.status not in (
        QuestionnaireVersion.Status.ACTIVE,
        QuestionnaireVersion.Status.RETIRED,
    ):
        # A missing version and a draft (never-answerable) version are the
        # same controlled rejection.
        raise DraftUpdateError(
            "validation_failed",
            "Invalid questionnaire version.",
            field_errors={"questionnaire_version_id": ["Unknown questionnaire version."]},
        )
    design.questionnaire_version = version
    design.save(update_fields=["questionnaire_version", "updated_at"])


def _replace_inspirations(design: Design, inspiration_asset_ids) -> None:
    """Replace the design's inspiration selections with one ordered set.

    Rejects duplicates and more than ``settings.MAX_INSPIRATION_IMAGES``, and
    accepts ONLY assets currently returned by ``publicly_eligible()`` — so a
    draft, retired, expired, unverified or incompletely-permitted asset is
    refused with the same indistinguishable message and never linked. The
    submitted order becomes positions 1..n. The Design row is already locked
    by the caller, so concurrent updates serialise and can never create
    duplicate positions or exceed the limit."""
    if not isinstance(inspiration_asset_ids, list):
        raise DraftUpdateError(
            "validation_failed",
            "Invalid inspiration selection.",
            field_errors={"inspiration_asset_ids": ["Must be a list of asset ids."]},
        )
    if len(inspiration_asset_ids) > settings.MAX_INSPIRATION_IMAGES:
        raise DraftUpdateError(
            "validation_failed",
            "Too many inspiration images selected.",
            field_errors={
                "inspiration_asset_ids": [
                    f"Select at most {settings.MAX_INSPIRATION_IMAGES} inspiration images."
                ]
            },
        )

    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in inspiration_asset_ids:
        try:
            asset_id = uuid.UUID(str(raw))
        except (ValueError, AttributeError, TypeError):
            raise DraftUpdateError(
                "validation_failed",
                "Invalid inspiration selection.",
                field_errors={"inspiration_asset_ids": ["That inspiration is not available."]},
            ) from None
        if asset_id in seen:
            raise DraftUpdateError(
                "validation_failed",
                "Duplicate inspiration selected.",
                field_errors={
                    "inspiration_asset_ids": ["The same inspiration was selected more than once."]
                },
            )
        seen.add(asset_id)
        parsed.append(asset_id)

    eligible = {
        asset.pk: asset
        for asset in InspirationAsset.objects.publicly_eligible().filter(pk__in=parsed)
    }
    for asset_id in parsed:
        if asset_id not in eligible:
            # Missing, retired, expired, unverified or incompletely-permitted:
            # one indistinguishable rejection, no private reason revealed.
            raise DraftUpdateError(
                "validation_failed",
                "That inspiration is not available.",
                field_errors={"inspiration_asset_ids": ["That inspiration is not available."]},
            )

    # Replace as one ordered set. Deleting first then recreating is safe
    # under the Design row lock the caller holds.
    DesignInspiration.objects.filter(design=design).delete()
    DesignInspiration.objects.bulk_create(
        [
            DesignInspiration(design=design, inspiration_asset=eligible[asset_id], position=index)
            for index, asset_id in enumerate(parsed, start=1)
        ]
    )


def _design_editability(locked: Design) -> tuple[bool, bool]:
    """(ordinary_editable, recovery_edit) for an already-locked Design.

    A design with ANY DesignVersion is never draft-editable — regardless of its
    lifecycle status. This covers legacy Phase 8/9 designs whose version was
    created by the management command while the status stayed ``draft``: their
    persisted answers are the immutable inputs of that version's spec and
    prompt, and editing them would silently desynchronise the draft from the
    version a later image-only generation resumes. Ordinary edits therefore
    require ``draft`` AND no version; a recovery edit additionally allows a
    ``generation_failed`` design that never produced a version."""
    has_version = DesignVersion.objects.filter(design=locked).exists()
    ordinary = locked.status == Design.Status.DRAFT and not has_version
    recovery = locked.status == Design.Status.GENERATION_FAILED and not has_version
    return ordinary, recovery


def update_design_draft(
    design: Design,
    *,
    title=UNSET,
    questionnaire_version_id=UNSET,
    answers=UNSET,
    inspiration_asset_ids=UNSET,
) -> Design:
    """Atomically apply a partial draft update to one owned design.

    Ownership MUST be enforced by the caller before this runs (the view
    resolves the design through ``accessible_designs`` first). Everything
    here — version assignment, answer validation/persistence and the ordered
    inspiration replacement — happens inside one transaction under a Design
    row lock, so answers and selections roll back together on any failure and
    no partial update (answers saved but inspirations failed, or vice versa)
    can ever occur.

    Editability (Phase 10 lifecycle): ordinary edits require ``draft`` status
    AND no DesignVersion (a version — even one created while the status was
    still draft, as the Phase 8/9 commands did — freezes the design's inputs);
    a recovery edit is additionally allowed while it is ``generation_failed``
    with no DesignVersion, and the FIRST successful such edit returns the
    design to ``draft``. Everything else is rejected with the safe
    ``design_not_editable`` code and never touches an existing DesignVersion.

    Raises :class:`DraftUpdateError` (controlled, safe) or
    :class:`~sitara.questionnaire.answer_validation.QuestionnaireAnswerError`
    (per-question), both mapped by the view."""
    with transaction.atomic():
        locked = Design.objects.select_for_update().get(pk=design.pk)

        is_ordinary_editable, is_recovery = _design_editability(locked)
        if not is_ordinary_editable and not is_recovery:
            raise DraftUpdateError(
                "design_not_editable",
                "This design can no longer be edited.",
            )

        if title is not UNSET:
            locked.title = title
            locked.save(update_fields=["title", "updated_at"])

        if questionnaire_version_id is not UNSET and questionnaire_version_id is not None:
            _assign_questionnaire_version(locked, questionnaire_version_id)

        if answers is not UNSET:
            if locked.questionnaire_version_id is None:
                raise DraftUpdateError(
                    "validation_failed",
                    "A questionnaire version must be selected before answering.",
                    field_errors={
                        "answers": ["Select a questionnaire version before saving answers."]
                    },
                )
            # Draft persistence: partial answers are validated structurally,
            # against option allowlists, active restrictions, exclusivity and
            # maximum counts/lengths — but missing required answers and
            # minimums are only enforced at completion (the validate endpoint).
            normalised = validate_questionnaire_answers(
                locked.questionnaire_version.schema, answers, require_complete=False
            )
            locked.answers = normalised
            locked.save(update_fields=["answers", "updated_at"])

        if inspiration_asset_ids is not UNSET:
            _replace_inspirations(locked, inspiration_asset_ids)

        if is_recovery:
            # A successful recovery edit clears the failed state so the design
            # can be completed and re-generated.
            locked.status = Design.Status.DRAFT
            locked.save(update_fields=["status", "updated_at"])

        return locked


def inspiration_availability_errors(design: Design) -> list[str]:
    """Complete validation must fail while any selected inspiration is no
    longer publicly eligible. The message never reveals which one or why."""
    selections = list(design.inspiration_selections.all())
    if not selections:
        return []
    selected_ids = [selection.inspiration_asset_id for selection in selections]
    eligible = set(
        InspirationAsset.objects.publicly_eligible()
        .filter(pk__in=selected_ids)
        .values_list("pk", flat=True)
    )
    if any(asset_id not in eligible for asset_id in selected_ids):
        return ["Remove or replace inspirations that are no longer available."]
    return []


def design_completion_errors(design: Design) -> dict:
    """The single definition of "is this design ready?", shared by the design
    validate endpoint and the generation service so the two cannot drift.

    Returns a dict of field errors (empty means complete): a missing
    questionnaire link, any complete-mode answer-validation error, and any
    no-longer-eligible inspiration selection. Purely read-only — no paid call,
    no mutation."""
    if design.questionnaire_version_id is None:
        return {"questionnaire_version_id": ["Select a questionnaire before validating."]}
    errors: dict = {}
    try:
        validate_questionnaire_answers(
            design.questionnaire_version.schema, design.answers, require_complete=True
        )
    except QuestionnaireAnswerError as exc:
        errors.update(exc.errors)
    selection_errors = inspiration_availability_errors(design)
    if selection_errors:
        errors["inspiration_asset_ids"] = selection_errors
    return errors


# Re-exported so callers can catch answer-validation failures alongside
# DraftUpdateError without importing from the questionnaire app directly.
__all__ = [
    "CrossDesignLineageError",
    "DesignVersionLimitReached",
    "DraftUpdateError",
    "QuestionnaireAnswerError",
    "UnsupportedVersionFieldError",
    "WorkspaceCoordinationError",
    "create_next_design_version",
    "create_next_design_version_locked",
    "design_completion_errors",
    "inspiration_availability_errors",
    "resolve_current_design_session",
    "update_design_draft",
]
