"""Reference upload grants: minting, resolution and revocation (ADR 0026).

The one place a handoff secret is created, hashed, checked or destroyed.

Three rules govern everything below, and they are the reason this is a module
rather than a few lines in a view:

1. **The plaintext exists for one return value and is never stored.** It is
   generated with :func:`secrets.token_urlsafe`, hashed immediately, and handed
   back exactly once to the owner who asked for it so their screen can draw a QR
   code. The row keeps only the digest. Nothing here ever logs it, and no log
   line anywhere in the request path may carry it — it is the same category as
   an attachment filename (ADR 0022) or an annotation note.

2. **Every unusable grant answers identically.** Expired, revoked, spent and
   never-existed are one outcome, :data:`GrantUnusable`, with no attribute
   distinguishing them. That is CLAUDE.md §15's private-resource enumeration
   rule applied to a new identifier: a caller must not be able to learn that a
   design exists but its code lapsed.

3. **A grant grants UPLOAD, into one named design, and nothing else.** There is
   deliberately no read function in this module and no read endpoint behind it,
   permanently (ADR 0026 non-goal).
"""

import hashlib
import logging
import secrets

from django.conf import settings
from django.db import DatabaseError, InterfaceError, transaction
from django.db.models import F
from django.utils import timezone

from sitara.accounts.rate_limits import RateLimitUnavailable, check_and_count, client_ip

from .models import Design, ReferenceUploadGrant

logger = logging.getLogger(__name__)

# Key namespaces of their own, so a handoff counter can never collide with — or
# be cleared by — an authentication or an owner-side upload one. Minting and
# consuming are counted separately: they are different actors (the shop's iPad
# and the customer's phone) doing different things at different rates.
_MINT_THROTTLE_PREFIX = "grantrl"
_UPLOAD_THROTTLE_PREFIX = "grantul"

#: Bytes of entropy behind the plaintext. 32 bytes is ~43 url-safe characters —
#: comfortably beyond guessing, and still a QR code an old phone scans at arm's
#: length in bad shop lighting, which is the actual constraint on the upper end.
_TOKEN_BYTES = 32


class GrantMintingUnavailable(Exception):
    """The throttle cache cannot be reached, so minting must not proceed.

    Distinct from an over-limit result on purpose: an infrastructure fault is
    never reported to the caller — or to whoever watches the 429 rate — as their
    own abuse. -> 503, exactly as the upload throttle does it."""


class GrantMintingThrottled(Exception):
    """Too many codes asked for from this session or address.

    ``retry_after`` is the window length, a conservative and non-revealing
    hint rather than a precise countdown."""

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__("grant minting rate limit reached")


def _count_upload_attempt(scope: str, identifier: str, limit: int, window: int) -> None:
    """Shared body of the two phone-upload windows below.

    Fails CLOSED, as a 503 rather than a 429, for the same reason every other
    throttle here does: an infrastructure fault is not the caller's abuse, and a
    cache outage must not quietly remove the only bound guessing has."""
    try:
        retry_after = check_and_count(
            scope, identifier, limit, window, prefix=_UPLOAD_THROTTLE_PREFIX
        )
    except RateLimitUnavailable:
        raise GrantMintingUnavailable("grant throttle cache unavailable") from None
    if retry_after is not None:
        raise GrantMintingThrottled(retry_after)


def enforce_grant_upload_address_throttle(request) -> None:
    """Bound how hard ONE address may try, whatever it presents.

    Runs ahead of the serializer, the grant lookup, the database and any image
    processing, because an unresolvable code is exactly what a guesser produces:
    a window that only counted resolvable codes would leave guessing unbounded.
    It also bounds a flood of malformed bodies, which never reach the
    serializer.

    It does NOT run before the body is parsed, and the difference matters enough
    to write down. Django's CSRF check reads ``request.POST`` before falling
    back to the ``X-CSRFToken`` header, and touching ``POST`` fully receives and
    parses the multipart body — so by the time any check inside the view runs,
    the body has already been taken. What stands between an enormous body and
    the parser is the ``Content-Length`` gate wrapped OUTSIDE ``csrf_protect``,
    not this. Do not relax that gate on the strength of this window."""
    _count_upload_attempt(
        "grant_upload_ip",
        client_ip(request),
        settings.REFERENCE_UPLOAD_GRANT_IP_LIMIT,
        settings.REFERENCE_UPLOAD_GRANT_IP_WINDOW_SECONDS,
    )


def enforce_grant_upload_code_throttle(plaintext: str) -> None:
    """Bound how hard ONE code may be used, from however many addresses.

    Must run BEFORE the code is resolved. A window that only opened for codes
    which resolved would answer 429 for a real code and 404 for an unknown one
    at the same request count, which is a distinguisher — the very thing
    :class:`GrantUnusable` exists to prevent. Counting every presented code,
    each under its own digest-keyed window, makes the two answer alike.

    Keyed on the DIGEST, never the plaintext: the limiter HMACs whatever it is
    handed before it reaches the cache, but handing it a secret at all would put
    one in a call frame and a traceback for no reason.

    Takes the code as a required positional argument rather than an optional
    keyword, so that a future caller cannot silently get the address window by
    forgetting to pass it."""
    _count_upload_attempt(
        "grant_upload_code",
        _digest(plaintext),
        settings.REFERENCE_UPLOAD_GRANT_LIMIT,
        settings.REFERENCE_UPLOAD_GRANT_WINDOW_SECONDS,
    )


def enforce_mint_throttle(request) -> None:
    """Session- and IP-scoped fixed window on minting.

    Ownership and CSRF already decide WHO may mint; neither bounds how often.
    Each mint writes a durable row that only the retention purge ever removes,
    and the row is the reason this exists: a loop against one's own design costs
    an attacker nothing and grows a table without bound.

    The counter is the project's ONE fixed-window primitive
    (``accounts.rate_limits.check_and_count``) under its own key namespace —
    never a second hand-maintained copy — and it fails CLOSED, because a cache
    outage must not quietly remove the only rate bound this endpoint has."""
    try:
        retry_after = check_and_count(
            "grant_mint_session",
            request.session.session_key or client_ip(request),
            settings.REFERENCE_UPLOAD_GRANT_MINT_LIMIT,
            settings.REFERENCE_UPLOAD_GRANT_MINT_WINDOW_SECONDS,
            prefix=_MINT_THROTTLE_PREFIX,
        ) or check_and_count(
            "grant_mint_ip",
            client_ip(request),
            settings.REFERENCE_UPLOAD_GRANT_MINT_IP_LIMIT,
            settings.REFERENCE_UPLOAD_GRANT_MINT_IP_WINDOW_SECONDS,
            prefix=_MINT_THROTTLE_PREFIX,
        )
    except RateLimitUnavailable:
        raise GrantMintingUnavailable("grant throttle cache unavailable") from None
    if retry_after is not None:
        raise GrantMintingThrottled(retry_after)


class GrantUnusable(Exception):
    """This grant cannot be used, and the caller is told no more than that.

    Deliberately carries no reason, no code and no design id. Expired, revoked,
    spent and unknown all raise this same bare exception, so a view mapping it
    onto a response cannot accidentally leak which one it was."""


def _digest(plaintext: str) -> str:
    """SHA-256 of the secret.

    Plain SHA-256 rather than a password hash: this is a 256-bit random value,
    not a human-chosen password, so there is no dictionary to slow down and no
    rainbow table to salt against — and a per-use bcrypt would put an
    intentionally slow function on the hot path of a phone upload."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def create_reference_upload_grant(design: Design) -> tuple[ReferenceUploadGrant, str]:
    """Mint one grant for ``design``, returning it with its PLAINTEXT secret.

    The plaintext is returned, never stored, and must reach exactly one place:
    the QR code on the owner's own screen. Any earlier live grant for this
    design is revoked first, so a design has at most one usable code and
    re-showing the panel cannot quietly leave an older photographed code
    working.

    "At most one" is a guarantee, not a tendency, which is why the Design row is
    LOCKED for the revoke-then-create pair — the same lock, on the same row,
    that ``upload_service`` takes to enforce the reference cap. Without it two
    concurrent mints (a double-tapped button on a laggy shop iPad is the
    realistic case) each see nothing to revoke and each insert, leaving the
    customer's already-photographed code alive after the stylist believed they
    had replaced it. That would break the exact control ADR 0026 accepts the
    bearer exposure on. The partial unique constraint on the model is the
    database's backstop; this lock is what turns a would-be constraint violation
    into an orderly wait.

    Both timestamps come from ONE clock reading, so the model's
    ``expires_at > created_at`` CHECK compares two values that cannot drift
    apart across a slow insert."""
    plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
    now = timezone.now()
    expires_at = now + timezone.timedelta(seconds=settings.REFERENCE_UPLOAD_GRANT_TTL_SECONDS)
    with transaction.atomic():
        # Re-read under the lock. The caller's instance may be stale, and it is
        # the ROW that serialises us against a concurrent mint, not the object.
        locked = Design.objects.select_for_update().get(pk=design.pk)
        revoke_reference_upload_grants(locked, now=now)
        grant = ReferenceUploadGrant.objects.create(
            design=locked,
            token_digest=_digest(plaintext),
            created_at=now,
            expires_at=expires_at,
        )
    # Row and design only. Never the plaintext, never the digest.
    logger.info(
        "reference upload grant issued design_id=%s grant_id=%s",
        design.pk,
        grant.pk,
    )
    return grant, plaintext


def revoke_reference_upload_grants(design: Design, *, now=None) -> int:
    """Revoke every live grant on ``design``; returns how many were revoked.

    Idempotent, and safe to call on a design that never had one — which is why
    the automatic callers (leaving the reference step, ending a shop-floor
    session) can call it unconditionally."""
    stamped = now or timezone.now()
    revoked = ReferenceUploadGrant.objects.filter(design=design, revoked_at__isnull=True).update(
        revoked_at=stamped
    )
    if revoked:
        logger.info("reference upload grants revoked design_id=%s count=%s", design.pk, revoked)
    return revoked


def resolve_reference_upload_grant(plaintext: str) -> ReferenceUploadGrant:
    """The grant behind ``plaintext``, or :class:`GrantUnusable`.

    Resolves by digest, so the lookup never needs the secret in a query log or
    an ORM repr. Liveness (not revoked, not expired) is re-checked HERE on every
    request rather than trusted from the moment of minting — a grant revoked
    while a phone had the page open must stop working on that phone's next
    attempt, not at its next page load.

    "Spent" is NOT checked here, and deliberately so: what spends a grant is the
    owning design's reference slots filling up, and that is decided under the
    Design row lock in ``upload_service`` where every other reference passes the
    same check. Duplicating it here would be a second, unlocked answer to a
    question that already has a locked one — and the two would eventually
    disagree under concurrency, which is the exact failure the lock exists to
    prevent. The caller maps a full design onto the same
    :class:`GrantUnusable` response, so the indistinguishability holds
    end to end."""
    if not isinstance(plaintext, str) or not plaintext:
        raise GrantUnusable()
    grant = (
        ReferenceUploadGrant.objects.select_related("design")
        .filter(token_digest=_digest(plaintext))
        .first()
    )
    if grant is None or grant.revoked_at is not None or grant.expires_at <= timezone.now():
        # One outcome for unknown, revoked and expired. No branch below this
        # line may tell them apart, and no log line records which it was.
        raise GrantUnusable()
    return grant


def record_grant_use(grant: ReferenceUploadGrant, *, now=None) -> None:
    """Count one accepted upload against ``grant``. Best-effort, by design.

    Audit only — see the model docstring. Nothing cap-relevant or
    security-relevant ever reads ``uses``: what spends a grant is the design's
    reference slots filling up, decided under the Design row lock in
    ``upload_service``.

    Which is why this SWALLOWS a database fault rather than raising. By the time
    it runs, the upload it describes has already committed — the row is durable
    and the object is in storage. Letting a transient fault here propagate would
    turn an upload that genuinely succeeded into a client-visible failure, and
    the customer's natural retry with the same photograph would then come back
    as ``duplicate_image``, which makes no sense as an answer to "it failed".
    Losing a counter is the cheaper failure, and the log line records that it
    happened. Same idiom as ``upload_service._delete_quietly`` and
    ``rate_limits.clear()``.

    The write itself is a targeted UPDATE with an F() increment, so two
    concurrent uploads through one grant cannot lose a count to a
    read-modify-write race, and nothing else on the row is rewritten by a stray
    full ``save()``."""
    stamped = now or timezone.now()
    try:
        ReferenceUploadGrant.objects.filter(pk=grant.pk).update(
            uses=F("uses") + 1, last_used_at=stamped
        )
    except (DatabaseError, InterfaceError) as exc:
        # BOTH, because they are siblings in Django's hierarchy rather than
        # parent and child: a live database rejecting a live query raises
        # DatabaseError, while a connection object torn down under a pooler
        # surfaces as InterfaceError. Catching only the first would leave the
        # second reproducing exactly the failure this guard exists to prevent.
        #
        # Type and row id only — never the exception text, which can carry
        # query fragments, and never anything about the code itself.
        logger.warning(
            "reference upload grant use not recorded grant_id=%s error=%s",
            grant.pk,
            type(exc).__name__,
        )
