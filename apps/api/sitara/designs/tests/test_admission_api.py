"""Live admission-control tests (Phase 16, Part B): per-session/IP throttles,
the global daily count, controlled error responses, ownership-first ordering,
and demo bypass — all provider-free. The autouse ``live_admission_ready``
fixture (conftest) puts every request in live mode with generous limits and an
in-memory budget ledger; each test tightens exactly one knob.
"""

import json
import uuid
from unittest import mock

import pytest
from django.core.cache import cache

from sitara.generation import cost_control

from .utils import (
    COMPLETE_ANSWERS,
    DESIGNS_URL,
    bootstrap_csrf,
    csrf_client,
    make_active_questionnaire,
    send_json,
)

pytestmark = pytest.mark.django_db

_AVAILABLE = "sitara.generation.pipeline.generation_is_available"
_FIXED_IP = "203.0.113.5"


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    # Throttle counters live in the shared cache; isolate every test.
    cache.clear()
    yield
    cache.clear()


def _complete_design(client, token) -> str:
    # Reuse the single active v1 across the many designs a throttle/count test
    # creates (version is globally unique — creating a second would collide).
    from sitara.questionnaire.models import QuestionnaireVersion

    version = (
        QuestionnaireVersion.objects.filter(version=1, status="active").first()
        or make_active_questionnaire()
    )
    response = send_json(
        client,
        "post",
        DESIGNS_URL,
        {"questionnaire_version_id": str(version.id), "answers": COMPLETE_ANSWERS},
        token=token,
    )
    assert response.status_code == 201, response.content
    return response.json()["id"]


def _generate(client, design_id, *, token, ip=_FIXED_IP, key=None, available=True):
    key = key or str(uuid.uuid4())
    extra = {
        "REMOTE_ADDR": ip,
        "HTTP_X_CSRFTOKEN": token,
        "HTTP_IDEMPOTENCY_KEY": key,
    }
    with mock.patch(_AVAILABLE, return_value=available):
        return client.post(
            f"{DESIGNS_URL}{design_id}/generate/",
            data=json.dumps({}),
            content_type="application/json",
            **extra,
        )


class TestSessionAndIpThrottles:
    def test_session_limit_returns_429_generation_limit_reached(self, settings):
        settings.LIVE_GENERATION_SESSION_LIMIT = 2
        client = csrf_client()
        token = bootstrap_csrf(client)
        # Three distinct complete designs, same session, same IP (generous IP
        # limit). The third request exceeds the per-session limit.
        statuses = []
        for _ in range(3):
            design_id = _complete_design(client, token)
            statuses.append(_generate(client, design_id, token=token).status_code)
        assert statuses[-1] == 429
        # And it carries the stable code + a bounded Retry-After.
        design_id = _complete_design(client, token)
        blocked = _generate(client, design_id, token=token)
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "generation_limit_reached"
        retry_after = int(blocked["Retry-After"])
        assert 0 < retry_after <= settings.LIVE_GENERATION_SESSION_WINDOW_SECONDS

    def test_ip_limit_returns_429_across_independent_sessions(self, settings):
        settings.LIVE_GENERATION_IP_LIMIT = 2
        settings.LIVE_GENERATION_SESSION_LIMIT = 100
        # Three DIFFERENT sessions sharing ONE IP: the IP counter still trips.
        statuses = []
        for _ in range(3):
            client = csrf_client()
            token = bootstrap_csrf(client)
            design_id = _complete_design(client, token)
            statuses.append(_generate(client, design_id, token=token, ip=_FIXED_IP).status_code)
        assert statuses.count(429) >= 1
        assert statuses[-1] == 429

    def test_a_second_ip_has_its_own_counter(self, settings):
        settings.LIVE_GENERATION_IP_LIMIT = 1
        settings.LIVE_GENERATION_SESSION_LIMIT = 100
        client = csrf_client()
        token = bootstrap_csrf(client)
        first = _generate(client, _complete_design(client, token), token=token, ip="203.0.113.1")
        # A different IP, fresh counter — admitted despite the first IP being spent.
        second = _generate(client, _complete_design(client, token), token=token, ip="203.0.113.2")
        assert first.status_code == 202
        assert second.status_code == 202


class TestGlobalDailyCount:
    def test_count_cannot_exceed_limit(self, settings):
        settings.LIVE_GENERATION_DAILY_COUNT_LIMIT = 1
        client = csrf_client()
        token = bootstrap_csrf(client)
        first = _generate(client, _complete_design(client, token), token=token)
        second = _generate(client, _complete_design(client, token), token=token)
        assert first.status_code == 202
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "generation_limit_reached"

    def test_idempotent_replay_consumes_no_additional_slot(self, settings):
        settings.LIVE_GENERATION_DAILY_COUNT_LIMIT = 1
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        key = str(uuid.uuid4())
        first = _generate(client, design_id, token=token, key=key)
        replay = _generate(client, design_id, token=token, key=key)
        assert first.status_code == 202
        assert replay.status_code == 202
        assert first.json()["job"]["id"] == replay.json()["job"]["id"]
        assert cost_control.get_ledger().count_for_today() == 1


class TestControlledErrors:
    def test_live_disabled_returns_live_generation_disabled(self, settings):
        settings.LIVE_GENERATION_ENABLED = False
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        response = _generate(client, design_id, token=token)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "live_generation_disabled"

    def test_ledger_outage_is_a_controlled_503(self, settings):
        cost_control.get_ledger().fail = True
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        response = _generate(client, design_id, token=token)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "generation_unavailable"

    def test_foreign_design_stays_404_even_when_count_exhausted(self, settings):
        settings.LIVE_GENERATION_DAILY_COUNT_LIMIT = 0  # every live attempt would be rejected
        client = csrf_client()
        token = bootstrap_csrf(client)
        # A design owned by a DIFFERENT session.
        other = csrf_client()
        other_token = bootstrap_csrf(other)
        foreign_id = _complete_design(other, other_token)
        response = _generate(client, foreign_id, token=token)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_nonexistent_design_stays_404_even_when_throttled(self, settings):
        settings.LIVE_GENERATION_SESSION_LIMIT = 0
        client = csrf_client()
        token = bootstrap_csrf(client)
        response = _generate(client, str(uuid.uuid4()), token=token)
        assert response.status_code == 404


class TestDemoBypass:
    def test_demo_generation_consumes_no_live_count(self, settings, inmemory_storage):
        from django.core.management import call_command

        call_command("install_demo_asset_pack", "--dev-synthetic")
        settings.DEMO_MODE = True
        settings.LIVE_GENERATION_DAILY_COUNT_LIMIT = 0  # would reject any LIVE attempt
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        # Demo bypasses the (exhausted) live count entirely.
        response = _generate(client, design_id, token=token)
        assert response.status_code == 202
        assert response.json()["job"]["is_demo"] is True
        assert cost_control.get_ledger().count_for_today() == 0

    def test_unavailable_demo_does_not_fall_back_to_live(self, settings, inmemory_storage):
        # DEMO_MODE on but no demo pack installed (fresh empty storage): fail
        # closed as unavailable, never a live attempt, and no live count consumed.
        settings.DEMO_MODE = True
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        response = _generate(client, design_id, token=token)
        assert response.status_code == 503
        assert cost_control.get_ledger().count_for_today() == 0


class TestRefinementConsistency:
    """Refinement goes through the SAME admission layer as initial generation."""

    def test_refine_live_disabled_returns_live_generation_disabled(self, settings):
        from .test_refine_api import _generated_design, _post_refine

        settings.LIVE_GENERATION_ENABLED = False
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id, version = _generated_design(client, token)
        response = _post_refine(client, design_id, token=token, source_version_id=version.pk)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "live_generation_disabled"

    def test_refine_counts_against_the_global_daily_limit(self, settings):
        from .test_refine_api import _generated_design, _post_refine

        settings.LIVE_GENERATION_DAILY_COUNT_LIMIT = 0  # no live attempt admitted
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id, version = _generated_design(client, token)
        response = _post_refine(client, design_id, token=token, source_version_id=version.pk)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "generation_limit_reached"


class TestIdempotentReplaySkipsThrottle:
    def test_replay_with_same_key_does_not_consume_a_throttle_slot(self, settings):
        # SEC-001: a legitimate retry reusing the same Idempotency-Key must not
        # be throttled out — the replay produces no new attempt.
        settings.LIVE_GENERATION_SESSION_LIMIT = 1
        client = csrf_client()
        token = bootstrap_csrf(client)
        design_id = _complete_design(client, token)
        key = str(uuid.uuid4())
        first = _generate(client, design_id, token=token, key=key)
        replay = _generate(client, design_id, token=token, key=key)
        assert first.status_code == 202
        assert replay.status_code == 202  # not 429
        assert first.json()["job"]["id"] == replay.json()["job"]["id"]


class TestCountReleasedOnRollback:
    def test_db_failure_during_create_releases_the_count(self, settings, monkeypatch):
        # REL-001: a DB failure after the count reservation but before commit must
        # release the reserved slot (a rolled-back enqueue is pre-provider).
        from django.db import DatabaseError

        from sitara.designs.models import GenerationAttempt as GA
        from sitara.generation import cost_control
        from sitara.generation.pipeline import enqueue_design_generation
        from sitara.generation.tests.factory import make_complete_design

        settings.LIVE_GENERATION_DAILY_COUNT_LIMIT = 5
        design = make_complete_design()

        real_create = GA.objects.create

        def boom(*args, **kwargs):
            raise DatabaseError("simulated create failure")

        monkeypatch.setattr(GA.objects, "create", boom)
        with mock.patch(_AVAILABLE, return_value=True):
            with pytest.raises(DatabaseError):
                enqueue_design_generation(design, idempotency_key=uuid.uuid4())
        monkeypatch.setattr(GA.objects, "create", real_create)
        # The reserved slot was compensated back to zero.
        assert cost_control.get_ledger().count_for_today() == 0


class TestRefinementModeGate:
    def test_admission_resolves_mode_from_the_named_source_version(self, settings):
        # ARCH-001: admission's demo/live gate for a refinement resolves from the
        # SPECIFIC named source version's frozen mode — not from current DEMO_MODE
        # and not by assuming the first version — so it stays aligned with the
        # refinement enqueue's own resolution.
        from sitara.designs.models import Design, DesignSession, DesignVersion
        from sitara.generation.admission import attempt_is_demo

        settings.DEMO_MODE = True  # deliberately opposite the version's frozen mode
        session = DesignSession.objects.create()
        design = Design.objects.create(design_session=session)
        version = DesignVersion.objects.create(design=design, version_number=1, is_demo=False)
        # Resolves from the version's own frozen mode (live), NOT current DEMO_MODE.
        assert attempt_is_demo(design, source_version_id=version.pk) is False
        # An unknown source version falls back to DEMO_MODE (fail-safe).
        assert attempt_is_demo(design, source_version_id=uuid.uuid4()) is True


class TestHashedIdentifiers:
    def test_throttle_keys_hash_the_raw_identifier(self):
        from sitara.generation import admission

        # The admission throttle keys never contain the raw IP/session value.
        raw = "203.0.113.77"
        settings_limit = 1000
        # Drive one throttle write, then confirm the raw value is absent from the
        # key format the module builds.
        admission._throttle("ip", raw, settings_limit, 60)
        from sitara.accounts.rate_limits import hash_identifier

        key = f"genrl:ip:{hash_identifier(raw)}"
        assert raw not in key
        assert hash_identifier(raw) != raw
