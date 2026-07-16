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
