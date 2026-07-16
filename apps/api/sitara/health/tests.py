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
    def _patch(self, monkeypatch, database=True, redis=True, auth_cache=True, storage=True):
        monkeypatch.setattr(health_checks, "check_database", lambda: database)
        monkeypatch.setattr(health_checks, "check_redis", lambda: redis)
        monkeypatch.setattr(health_checks, "check_auth_cache", lambda: auth_cache)
        monkeypatch.setattr(health_checks, "check_storage", lambda: storage)

    def test_ready_ok_when_all_dependencies_ok(self, client, monkeypatch):
        self._patch(monkeypatch)
        response = client.get(reverse("health-ready"))
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "checks": {
                "database": "ok",
                "redis": "ok",
                "auth_cache": "ok",
                "storage": "ok",
            },
        }

    @pytest.mark.parametrize("failing", ["database", "redis", "auth_cache", "storage"])
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

    @pytest.mark.parametrize(
        "demo,allow",
        [(True, False), (True, True), (False, False), (False, True)],
    )
    def test_generation_disabled_in_every_phase_3a_combination(self, client, settings, demo, allow):
        """Even BOTH environment gates open must not claim generation is
        available while no paid provider implementation exists."""
        settings.DEMO_MODE = demo
        settings.ALLOW_PAID_AI_CALLS = allow
        assert client.get(reverse("config-public")).json()["generation_enabled"] is False

    def test_never_returns_secrets(self, client, settings):
        settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"
        settings.REPLICATE_API_TOKEN = "r8_test_not_a_real_token"
        text = client.get(reverse("config-public")).content.decode()
        assert "sk-ant" not in text
        assert "r8_" not in text
        for forbidden in ("ANTHROPIC", "REPLICATE", "S3_", "SECRET", "bucket"):
            assert forbidden not in text


class TestReadinessLoggingSafety:
    """Connection-library exceptions routinely embed credentials; readiness
    failures must keep them out of BOTH the HTTP response and the logs."""

    SECRET = "hunter2-SUPERSECRET-credential"
    POISON = f"connection failed: postgres://sitara:{SECRET}@db:5432/x password={SECRET}"

    def _assert_safe(self, caplog, check_name):
        assert self.SECRET not in caplog.text
        assert "postgres://" not in caplog.text
        assert "Traceback" not in caplog.text
        assert f"readiness {check_name} check failed" in caplog.text
        assert "exception_type=RuntimeError" in caplog.text

    def test_database_failure_logs_only_safe_metadata(self, caplog, monkeypatch):
        class ExplodingConnections:
            def __getitem__(self, alias):
                raise RuntimeError(TestReadinessLoggingSafety.POISON)

        monkeypatch.setattr(health_checks, "connections", ExplodingConnections())
        with caplog.at_level("WARNING"):
            assert health_checks.check_database() is False
        self._assert_safe(caplog, "database")

    def test_redis_failure_logs_only_safe_metadata(self, caplog, monkeypatch):
        class ExplodingRedis:
            @staticmethod
            def from_url(*args, **kwargs):
                raise RuntimeError(TestReadinessLoggingSafety.POISON)

        monkeypatch.setattr(health_checks.redis, "Redis", ExplodingRedis)
        with caplog.at_level("WARNING"):
            assert health_checks.check_redis() is False
        self._assert_safe(caplog, "redis")

    def test_auth_cache_failure_logs_only_safe_metadata(self, caplog, monkeypatch):
        from django.core.cache import cache as django_cache

        def exploding_set(*args, **kwargs):
            raise RuntimeError(TestReadinessLoggingSafety.POISON)

        monkeypatch.setattr(django_cache, "set", exploding_set)
        with caplog.at_level("WARNING"):
            assert health_checks.check_auth_cache() is False
        assert self.SECRET not in caplog.text
        assert "redis://" not in caplog.text
        assert "readiness auth_cache check failed" in caplog.text
        assert "exception_type=RuntimeError" in caplog.text

    def test_storage_failure_logs_only_safe_metadata(self, caplog, monkeypatch):
        import boto3

        def exploding_client(*args, **kwargs):
            raise RuntimeError(TestReadinessLoggingSafety.POISON)

        monkeypatch.setattr(boto3, "client", exploding_client)
        with caplog.at_level("WARNING"):
            assert health_checks.check_storage() is False
        self._assert_safe(caplog, "storage")

    def test_ready_endpoint_leaks_nothing_when_dependencies_explode(
        self, client, caplog, monkeypatch
    ):
        class ExplodingConnections:
            def __getitem__(self, alias):
                raise RuntimeError(TestReadinessLoggingSafety.POISON)

        monkeypatch.setattr(health_checks, "connections", ExplodingConnections())
        with caplog.at_level("WARNING"):
            response = client.get(reverse("health-ready"))
        assert response.status_code == 503
        assert self.SECRET not in response.content.decode()
        assert self.SECRET not in caplog.text


class TestAuthCacheProbe:
    def _capture_probe_keys(self, monkeypatch) -> list[str]:
        from django.core.cache import cache

        keys: list[str] = []
        original_set = cache.set

        def recording_set(key, value, *args, **kwargs):
            keys.append(key)
            return original_set(key, value, *args, **kwargs)

        monkeypatch.setattr(cache, "set", recording_set)
        return keys

    def test_probe_round_trips_and_removes_its_key(self, monkeypatch):
        from django.core.cache import cache

        keys = self._capture_probe_keys(monkeypatch)
        assert health_checks.check_auth_cache() is True
        # The probe cleans up after itself and stores no user data.
        assert len(keys) == 1
        assert keys[0].startswith(health_checks.AUTH_CACHE_PROBE_PREFIX)
        assert cache.get(keys[0]) is None

    def test_probe_key_is_unique_per_invocation(self, monkeypatch):
        """Simultaneous readiness checks must not delete or overwrite each
        other's probe entry, so every invocation uses a fresh key."""
        keys = self._capture_probe_keys(monkeypatch)
        assert health_checks.check_auth_cache() is True
        assert health_checks.check_auth_cache() is True
        assert len(keys) == 2
        assert keys[0] != keys[1]

    def test_probe_survives_a_concurrent_check_deleting_nothing_of_its_own(self, monkeypatch):
        """A second interleaved probe (set+get+delete of ITS key) cannot
        break the first probe's round trip."""
        from django.core.cache import cache

        original_get = cache.get

        def interleaving_get(key, *args, **kwargs):
            # Another readiness check runs to completion in the middle of
            # this probe's set→get window.
            cache.get = original_get
            try:
                assert health_checks.check_auth_cache() is True
            finally:
                cache.get = interleaving_get
            return original_get(key, *args, **kwargs)

        monkeypatch.setattr(cache, "get", interleaving_get)
        assert health_checks.check_auth_cache() is True

    def test_probe_delete_failure_does_not_fail_the_check(self, monkeypatch):
        """Deletion is best-effort cleanup; the short TTL is the backstop."""
        from django.core.cache import cache

        def exploding_delete(key, *args, **kwargs):
            raise RuntimeError("delete refused")

        monkeypatch.setattr(cache, "delete", exploding_delete)
        assert health_checks.check_auth_cache() is True


class TestCeleryPing:
    def test_ping_task_returns_serialisable_result(self, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        result = ping.apply()
        assert result.get(timeout=5) == {"pong": True, "service": "sitara-api"}
