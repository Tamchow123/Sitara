import pytest
from django.urls import reverse

from sitara.health import checks as health_checks
from sitara.health.tasks import ping


class TestLiveness:
    def test_live_returns_ok_without_dependencies(self, client):
        response = client.get(reverse("health-live"))
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "sitara-api"}


class TestReadiness:
    def _patch(self, monkeypatch, database=True, redis=True, storage=True):
        monkeypatch.setattr(health_checks, "check_database", lambda: database)
        monkeypatch.setattr(health_checks, "check_redis", lambda: redis)
        monkeypatch.setattr(health_checks, "check_storage", lambda: storage)

    def test_ready_ok_when_all_dependencies_ok(self, client, monkeypatch):
        self._patch(monkeypatch)
        response = client.get(reverse("health-ready"))
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "checks": {"database": "ok", "redis": "ok", "storage": "ok"},
        }

    @pytest.mark.parametrize("failing", ["database", "redis", "storage"])
    def test_ready_503_when_a_dependency_is_unavailable(self, client, monkeypatch, failing):
        self._patch(monkeypatch, **{failing: False})
        response = client.get(reverse("health-ready"))
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["checks"][failing] == "unavailable"
        # No connection strings, credentials or exception details leak.
        text = response.content.decode()
        for secret_hint in ("password", "postgres://", "redis://", "Traceback", "SECRET"):
            assert secret_hint not in text


class TestPublicConfig:
    def test_reports_demo_mode_and_generation_disabled(self, client, settings):
        settings.DEMO_MODE = True
        settings.ALLOW_PAID_AI_CALLS = False
        response = client.get(reverse("config-public"))
        assert response.status_code == 200
        assert response.json() == {
            "demo_mode": True,
            "generation_enabled": False,
            "max_inspiration_images": 3,
            "max_refinements": 1,
        }

    def test_generation_requires_both_gates(self, client, settings):
        settings.DEMO_MODE = True
        settings.ALLOW_PAID_AI_CALLS = True  # one gate alone is not enough
        assert client.get(reverse("config-public")).json()["generation_enabled"] is False
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = False
        assert client.get(reverse("config-public")).json()["generation_enabled"] is False

    def test_never_returns_secrets(self, client, settings):
        settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
        settings.REPLICATE_API_TOKEN = "r8_test_not_a_real_token"
        text = client.get(reverse("config-public")).content.decode()
        assert "sk-ant" not in text
        assert "r8_" not in text
        for forbidden in ("ANTHROPIC", "REPLICATE", "S3_", "SECRET", "bucket"):
            assert forbidden not in text


class TestCeleryPing:
    def test_ping_task_returns_serialisable_result(self, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        result = ping.apply()
        assert result.get(timeout=5) == {"pong": True, "service": "sitara-api"}
