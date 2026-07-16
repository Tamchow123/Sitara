"""Helpers for email-canonicalisation migrations.

Kept importable (and unit-tested) so the safety property — collision errors
NEVER contain email addresses — is enforced by construction and by test."""

from django.db.models import Count
from django.db.models.functions import Lower, Trim


def count_canonical_email_collisions(user_queryset) -> int:
    """Number of canonical-identity groups (Lower(Trim(email))) that map to
    more than one existing account."""
    return (
        user_queryset.annotate(email_canonical=Lower(Trim("email")))
        .values("email_canonical")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .count()
    )


def collision_error_message(collision_count: int) -> str:
    """Built ONLY from the count — no email address can ever appear."""
    return (
        f"cannot enforce canonical email uniqueness: {collision_count} "
        "group(s) of existing accounts share the same canonical identity "
        "(same email ignoring case and surrounding whitespace). Accounts are "
        "never merged or deleted automatically. Identify them with: "
        "SELECT LOWER(BTRIM(email)), COUNT(*) FROM accounts_user "
        "GROUP BY 1 HAVING COUNT(*) > 1; then resolve each group manually "
        "and re-run the migration."
    )
