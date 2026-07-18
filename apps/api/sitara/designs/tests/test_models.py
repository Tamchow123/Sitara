"""Model and database-constraint tests (PostgreSQL-backed).

Constraint tests write through the ORM directly — deliberately bypassing
the services — to prove the DATABASE is the final backstop."""

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from sitara.accounts.models import User
from sitara.designs.models import Design, DesignSession, DesignVersion, GenerationAttempt

from .utils import STRONG_PASSWORD, unique_email

pytestmark = pytest.mark.django_db


def make_user() -> User:
    return User.objects.create_user(email=unique_email(), password=STRONG_PASSWORD)


def make_design(**kwargs) -> Design:
    session = kwargs.pop("design_session", None) or DesignSession.objects.create()
    return Design.objects.create(design_session=session, **kwargs)


class TestUuidPrimaryKeys:
    def test_all_domain_models_use_generated_uuid_primary_keys(self):
        design = make_design()
        version = DesignVersion.objects.create(design=design, version_number=1)
        attempt = GenerationAttempt.objects.create(design=design, design_version=version)
        for instance in (design.design_session, design, version, attempt):
            assert isinstance(instance.pk, uuid.UUID)
        # Generated, not reused between rows.
        assert len({design.design_session.pk, design.pk, version.pk, attempt.pk}) == 4


class TestDefaults:
    def test_design_defaults_are_draft_and_empty_answers(self):
        design = make_design()
        design.refresh_from_db()
        assert design.status == Design.Status.DRAFT
        assert design.answers == {}
        assert design.title == ""

    def test_answers_default_does_not_share_mutable_state(self):
        first = make_design()
        second = make_design()
        first.answers["poisoned"] = True
        first.save()
        second.refresh_from_db()
        assert second.answers == {}

    def test_generation_attempt_defaults(self):
        design = make_design()
        attempt = GenerationAttempt.objects.create(design=design)
        attempt.refresh_from_db()
        assert attempt.status == GenerationAttempt.Status.QUEUED
        assert attempt.error_code == ""
        assert attempt.started_at is None
        assert attempt.completed_at is None
        assert attempt.design_version_id is None
        assert attempt.image_seed is None
        assert attempt.image_parameters is None
        assert attempt.staged_image_storage_key == ""
        assert isinstance(attempt.idempotency_key, uuid.UUID)

    def test_design_session_starts_unclaimed(self):
        session = DesignSession.objects.create()
        assert session.user_id is None
        assert session.last_seen_at is not None


class TestTitle:
    def test_title_is_trimmed_on_save(self):
        design = make_design(title="  Walima concept  ")
        design.refresh_from_db()
        assert design.title == "Walima concept"

    def test_title_longer_than_120_characters_fails_validation(self):
        design = Design(design_session=DesignSession.objects.create(), title="x" * 121)
        with pytest.raises(ValidationError):
            design.full_clean()

    def test_title_of_exactly_120_characters_is_allowed(self):
        design = make_design(title="x" * 120)
        design.refresh_from_db()
        assert len(design.title) == 120


class TestVersionConstraints:
    def test_version_number_zero_is_blocked_by_the_database(self):
        design = make_design()
        with pytest.raises(IntegrityError), transaction.atomic():
            DesignVersion.objects.create(design=design, version_number=0)

    def test_duplicate_version_number_is_blocked_by_the_database(self):
        design = make_design()
        DesignVersion.objects.create(design=design, version_number=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            DesignVersion.objects.create(design=design, version_number=1)

    def test_same_version_number_on_different_designs_is_fine(self):
        DesignVersion.objects.create(design=make_design(), version_number=1)
        DesignVersion.objects.create(design=make_design(), version_number=1)
        assert DesignVersion.objects.filter(version_number=1).count() == 2


class TestGenerationAttemptConstraints:
    def test_idempotency_key_is_unique_per_design(self):
        design = make_design()
        key = uuid.uuid4()
        GenerationAttempt.objects.create(
            design=design,
            idempotency_key=key,
            status="failed",
            error_code="queue_unavailable",
            completed_at=timezone.now(),
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            GenerationAttempt.objects.create(design=design, idempotency_key=key)

    def test_same_idempotency_key_allowed_on_different_designs(self):
        key = uuid.uuid4()
        GenerationAttempt.objects.create(design=make_design(), idempotency_key=key)
        GenerationAttempt.objects.create(design=make_design(), idempotency_key=key)
        assert GenerationAttempt.objects.filter(idempotency_key=key).count() == 2

    def test_only_one_in_progress_attempt_per_design(self):
        design = make_design()
        GenerationAttempt.objects.create(design=design, status="running_text")
        with pytest.raises(IntegrityError), transaction.atomic():
            GenerationAttempt.objects.create(design=design, status="queued")

    def test_terminal_attempts_do_not_block_a_new_in_progress_one(self):
        design = make_design()
        version = DesignVersion.objects.create(design=design, version_number=1)
        GenerationAttempt.objects.create(
            design=design,
            design_version=version,
            status="succeeded",
            staged_image_storage_key="generation-staging/x/raw.webp",
            staged_image_sha256="a" * 64,
            staged_image_size_bytes=100,
            staged_image_width=768,
            staged_image_height=1024,
            completed_at=timezone.now(),
        )
        GenerationAttempt.objects.create(design=design, status="queued")
        assert GenerationAttempt.objects.filter(design=design).count() == 2

    def test_invalid_status_is_rejected(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            GenerationAttempt.objects.create(design=make_design(), status="banana")

    def test_negative_seed_is_rejected(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            GenerationAttempt.objects.create(design=make_design(), image_seed=-1)

    def test_zero_seed_is_allowed(self):
        attempt = GenerationAttempt.objects.create(design=make_design(), image_seed=0)
        assert attempt.image_seed == 0

    @pytest.mark.parametrize("bad_hash", ["x", "a" * 63, "g" * 64, "A" * 64])
    def test_malformed_sha256_is_rejected(self, bad_hash):
        # A supplied staged hash must be exactly 64 lowercase hex characters.
        with pytest.raises(IntegrityError), transaction.atomic():
            GenerationAttempt.objects.create(
                design=make_design(),
                status="failed",
                error_code="image_staging_failed",
                completed_at=timezone.now(),
                staged_image_storage_key="generation-staging/x/raw.webp",
                staged_image_sha256=bad_hash,
                staged_image_size_bytes=10,
                staged_image_width=1,
                staged_image_height=1,
            )

    def test_partial_staged_metadata_is_rejected(self):
        # Key present but the rest absent violates the all-or-none constraint.
        with pytest.raises(IntegrityError), transaction.atomic():
            GenerationAttempt.objects.create(
                design=make_design(),
                status="failed",
                error_code="image_staging_failed",
                completed_at=timezone.now(),
                staged_image_storage_key="generation-staging/x/raw.webp",
            )

    def test_succeeded_requires_version_and_staged_metadata(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            GenerationAttempt.objects.create(
                design=make_design(), status="succeeded", completed_at=timezone.now()
            )

    def test_failed_requires_error_code_and_completed_at(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            GenerationAttempt.objects.create(design=make_design(), status="failed")


class TestDesignStatusConstraint:
    def test_invalid_design_status_is_rejected(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            Design.objects.create(design_session=DesignSession.objects.create(), status="bogus")

    def test_lifecycle_statuses_are_accepted(self):
        for value in ("draft", "generating", "generated", "generation_failed"):
            design = Design.objects.create(
                design_session=DesignSession.objects.create(), status=value
            )
            assert design.status == value


class TestCascades:
    def _full_chain(self, user=None):
        session = DesignSession.objects.create(user=user)
        design = Design.objects.create(design_session=session)
        version = DesignVersion.objects.create(design=design, version_number=1)
        GenerationAttempt.objects.create(design=design, design_version=version)
        return session, design, version

    def test_deleting_a_user_cascades_their_design_sessions_and_children(self):
        user = make_user()
        self._full_chain(user=user)
        user.delete()
        assert DesignSession.objects.count() == 0
        assert Design.objects.count() == 0
        assert DesignVersion.objects.count() == 0
        assert GenerationAttempt.objects.count() == 0

    def test_deleting_a_design_session_cascades_designs(self):
        session, _, _ = self._full_chain()
        session.delete()
        assert Design.objects.count() == 0
        assert DesignVersion.objects.count() == 0

    def test_deleting_a_design_cascades_versions_and_attempts(self):
        session, design, _ = self._full_chain()
        design.delete()
        assert DesignSession.objects.filter(pk=session.pk).exists()
        assert DesignVersion.objects.count() == 0
        assert GenerationAttempt.objects.count() == 0

    def test_deleting_an_unclaimed_session_never_touches_users(self):
        user = make_user()
        session = DesignSession.objects.create()
        session.delete()
        assert User.objects.filter(pk=user.pk).exists()


class TestTimestamps:
    def test_created_and_updated_are_set_and_updated(self):
        design = make_design(title="before")
        created_at = design.created_at
        first_updated = design.updated_at
        design.title = "after"
        design.save()
        design.refresh_from_db()
        assert design.created_at == created_at
        assert design.updated_at > first_updated


class TestNoRawSessionKeys:
    def test_no_domain_model_has_a_session_key_or_token_column(self):
        """Ownership rides Django session DATA (the DesignSession UUID),
        never a stored raw session key or a custom token column."""
        forbidden = {"session_key", "session", "token", "auth_token", "cookie"}
        for model in (DesignSession, Design, DesignVersion, GenerationAttempt):
            field_names = {field.name for field in model._meta.get_fields()}
            assert not field_names & forbidden, model
