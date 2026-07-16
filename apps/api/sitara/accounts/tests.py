import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_user_has_uuid_primary_key_and_unique_email():
    user = User.objects.create_user(email="bride@example.com", password="s3cret-pass")
    assert isinstance(user.id, uuid.UUID)
    assert user.email == "bride@example.com"
    assert user.check_password("s3cret-pass")
    assert user.created_at is not None and user.updated_at is not None
    with pytest.raises(IntegrityError):
        User.objects.create_user(email="bride@example.com", password="other")


def test_email_is_the_login_identifier():
    assert User.USERNAME_FIELD == "email"
    assert not hasattr(User, "username") or User.username is None


def test_create_superuser():
    admin = User.objects.create_superuser(email="admin@example.com", password="s3cret-pass")
    assert admin.is_staff and admin.is_superuser


def test_email_is_required():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="x")
    with pytest.raises(ValueError):
        User.objects.create_user(email="   ", password="x")


class TestEmailCanonicalisation:
    def test_mixed_case_email_is_stored_canonically(self):
        user = User.objects.create_user(email="Bride@Example.COM", password="s3cret-pass")
        assert user.email == "bride@example.com"

    def test_surrounding_whitespace_is_trimmed(self):
        user = User.objects.create_user(email="  BRIDE@example.com  ", password="s3cret-pass")
        assert user.email == "bride@example.com"

    def test_direct_model_save_also_canonicalises(self):
        user = User(email=" Mixed@Example.Com ")
        user.set_password("s3cret-pass")
        user.save()
        user.refresh_from_db()
        assert user.email == "mixed@example.com"

    def test_manager_rejects_case_only_duplicates(self):
        User.objects.create_user(email="bride@example.com", password="s3cret-pass")
        with pytest.raises(IntegrityError):
            User.objects.create_user(email="BRIDE@Example.com", password="other")

    def test_database_constraint_blocks_paths_that_skip_save(self):
        """bulk_create bypasses save() (no canonicalisation) — the
        PostgreSQL Lower(email) unique constraint must still refuse."""
        User.objects.create_user(email="bride@example.com", password="s3cret-pass")
        with pytest.raises(IntegrityError):
            User.objects.bulk_create([User(email="BrIdE@Example.com")])

    def test_authentication_lookup_is_case_insensitive_and_email_based(self):
        user = User.objects.create_user(email="bride@example.com", password="s3cret-pass")
        assert User.objects.get_by_natural_key(" BRIDE@Example.COM ") == user
        assert User.USERNAME_FIELD == "email"

    def test_superuser_creation_is_canonicalised_too(self):
        admin = User.objects.create_superuser(email="Admin@Example.com", password="s3cret-pass")
        assert admin.email == "admin@example.com"
        assert admin.is_staff and admin.is_superuser


class TestDatabaseLayerCanonicalIdentity:
    """Persistence paths that skip save() must be stopped by PostgreSQL
    itself: uniqueness over Lower(Trim(email)) plus a check constraint that
    stored values are already canonical."""

    def test_whitespace_variant_duplicate_blocked_via_bulk_create(self):
        User.objects.create_user(email="bride@example.com", password="s3cret-pass")
        with pytest.raises(IntegrityError):
            User.objects.bulk_create([User(email=" bride@example.com ")])

    def test_mixed_case_and_whitespace_duplicate_blocked_via_bulk_create(self):
        User.objects.create_user(email="bride@example.com", password="s3cret-pass")
        with pytest.raises(IntegrityError):
            User.objects.bulk_create([User(email=" BRIDE@EXAMPLE.COM ")])

    def test_case_only_duplicate_blocked_via_bulk_create(self):
        User.objects.create_user(email="bride@example.com", password="s3cret-pass")
        with pytest.raises(IntegrityError):
            User.objects.bulk_create([User(email="Bride@Example.com")])

    def test_noncanonical_standalone_value_rejected_even_when_unique(self):
        """Not a duplicate of anything — rejected purely because the stored
        value would not be canonical."""
        with pytest.raises(IntegrityError):
            User.objects.bulk_create([User(email=" Solo@Example.com ")])

    def test_queryset_update_cannot_store_noncanonical_values(self):
        User.objects.create_user(email="bride@example.com", password="s3cret-pass")
        with pytest.raises(IntegrityError):
            User.objects.filter(email="bride@example.com").update(email=" Bride@Example.com ")

    def test_canonical_values_succeed_through_every_path(self):
        User.objects.bulk_create([User(email="canonical@example.com")])
        User.objects.filter(email="canonical@example.com").update(email="renamed@example.com")
        assert User.objects.filter(email="renamed@example.com").exists()


class TestCollisionMigrationSafety:
    def test_no_collisions_on_canonical_data(self):
        from sitara.accounts.migration_utils import count_canonical_email_collisions

        User.objects.create_user(email="one@example.com", password="s3cret-pass")
        User.objects.create_user(email="two@example.com", password="s3cret-pass")
        assert count_canonical_email_collisions(User.objects.all()) == 0

    def test_collision_error_message_contains_count_but_no_addresses(self):
        from sitara.accounts.migration_utils import collision_error_message

        message = collision_error_message(3)
        assert "3" in message
        assert "never merged or deleted" in message
        assert "re-run the migration" in message
        # Built only from the count: no email address can appear. The only
        # permitted '@'-free schema reference is the documented SQL hint.
        assert "@" not in message
