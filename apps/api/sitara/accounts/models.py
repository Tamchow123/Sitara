"""Custom user model, created before the first production migration.

Email is the login identifier; the primary key is a UUID. Password handling
is Django's standard machinery. Authentication ENDPOINTS (login,
registration, password reset) are deliberately not implemented in Phase 3A —
they arrive in a later Phase 3 task.

Email identity is CASE-INSENSITIVE and whitespace-trimmed:
``Bride@Example.com``, ``bride@example.com`` and `` BRIDE@example.com ``
are the same canonical account. Canonicalisation happens on every save (not
only in forms or the manager), and PostgreSQL enforces uniqueness on
``Lower(email)`` so even bulk/raw ORM paths cannot create case-only
duplicates.
"""

import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower


def canonicalize_email(email: str) -> str:
    """Canonical account identity: trimmed and lower-cased."""
    return (email or "").strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        email = canonicalize_email(email)
        if not email:
            raise ValueError("an email address is required")
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def get_by_natural_key(self, username: str | None):
        """Authentication lookup is case-insensitive on the canonical email."""
        return self.get(email=canonicalize_email(username or ""))

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["is_staff"] is not True:
            raise ValueError("a superuser must have is_staff=True")
        if extra_fields["is_superuser"] is not True:
            raise ValueError("a superuser must have is_superuser=True")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    email = models.EmailField("email address", unique=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]

    def save(self, *args, **kwargs):
        # Canonicalise on EVERY persistence path (admin, ORM, manager); the
        # database constraint above remains the final backstop for paths
        # that skip save() entirely (e.g. bulk_create).
        self.email = canonicalize_email(self.email)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.email
