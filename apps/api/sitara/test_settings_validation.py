"""Settings-startup validation, isolated per case.

Each scenario imports config.settings in a FRESH subprocess with a
purpose-built environment, so cases cannot contaminate each other and the
validation runs exactly as it would at real process startup. No database,
network or Django app registry is needed — importing the settings module is
what triggers validation."""

import os
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]

# A syntactically valid, non-placeholder production environment.
VALID_PRODUCTION_ENV = {
    "APP_ENV": "production",
    "DJANGO_SECRET_KEY": "k7#realistic-production-secret-0123456789abcdefghij",
    "DJANGO_ALLOWED_HOSTS": "api.sitara.example",
    "DATABASE_URL": "postgres://sitara_prod:s3parate-real-pass@db.internal:5432/sitara",
    "REDIS_URL": "redis://cache.internal:6379/0",
    "S3_ENDPOINT_URL": "https://storage.example.com",
    "S3_ACCESS_KEY_ID": "PRODACCESSKEY01",
    "S3_SECRET_ACCESS_KEY": "prod-storage-secret-xyz",
    "S3_BUCKET_NAME": "sitara-media-prod",
    "CORS_ALLOWED_ORIGINS": "https://app.sitara.example",
    "CSRF_TRUSTED_ORIGINS": "https://app.sitara.example",
}

DEV_ONLY_SECRET = "dev-only-insecure-secret-key-do-not-use-in-production"
COMPOSE_DATABASE_URL = "postgres://sitara:sitara-dev-password@postgres:5432/sitara"


def load_settings(env: dict[str, str]) -> subprocess.CompletedProcess:
    """Import config.settings in a clean subprocess with ONLY this env."""
    minimal = {"PATH": os.environ.get("PATH", "")}
    return subprocess.run(
        [sys.executable, "-c", "import config.settings; print('SETTINGS_OK')"],
        cwd=API_ROOT,
        env={**minimal, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestEnvironmentAllowlist:
    def test_development_default_starts_with_safe_local_defaults(self):
        result = load_settings({})
        assert result.returncode == 0, result.stderr
        assert "SETTINGS_OK" in result.stdout

    def test_explicit_test_environment_is_allowed(self):
        result = load_settings({"APP_ENV": "test"})
        assert result.returncode == 0, result.stderr

    def test_unknown_environments_fail_closed(self):
        for bad in ("prod", "produciton", "Production", "staging", "dev"):
            result = load_settings({"APP_ENV": bad})
            assert result.returncode != 0, f"APP_ENV={bad!r} must be rejected"
            assert "APP_ENV" in result.stderr
            assert "ImproperlyConfigured" in result.stderr


class TestProductionValidation:
    def test_valid_production_configuration_loads(self):
        result = load_settings(VALID_PRODUCTION_ENV)
        assert result.returncode == 0, result.stderr
        assert "SETTINGS_OK" in result.stdout

    def _expect_rejection(self, overrides: dict[str, str], variable: str, secret: str):
        result = load_settings({**VALID_PRODUCTION_ENV, **overrides})
        assert result.returncode != 0, f"{variable} override must be rejected"
        assert variable in result.stderr
        # The rejected value itself must never be echoed.
        assert secret not in result.stderr
        assert secret not in result.stdout

    def test_example_secret_key_fails(self):
        self._expect_rejection(
            {"DJANGO_SECRET_KEY": DEV_ONLY_SECRET}, "DJANGO_SECRET_KEY", DEV_ONLY_SECRET
        )

    def test_placeholder_sentinels_fail(self):
        self._expect_rejection(
            {"DJANGO_SECRET_KEY": "__REPLACE_ME__"}, "DJANGO_SECRET_KEY", "__REPLACE_ME__"
        )
        self._expect_rejection(
            {"DJANGO_SECRET_KEY": "please-change-me-soon"},
            "DJANGO_SECRET_KEY",
            "please-change-me-soon",
        )

    def test_example_database_url_fails(self):
        self._expect_rejection(
            {"DATABASE_URL": COMPOSE_DATABASE_URL}, "DATABASE_URL", "sitara-dev-password"
        )

    def test_example_storage_credentials_fail(self):
        self._expect_rejection(
            {"S3_ACCESS_KEY_ID": "sitara-minio"}, "S3_ACCESS_KEY_ID", "sitara-minio"
        )
        self._expect_rejection(
            {"S3_SECRET_ACCESS_KEY": "sitara-minio-dev-password"},
            "S3_SECRET_ACCESS_KEY",
            "sitara-minio-dev-password",
        )
        self._expect_rejection(
            {"S3_SECRET_ACCESS_KEY": "__REPLACE_ME__"},
            "S3_SECRET_ACCESS_KEY",
            "__REPLACE_ME__",
        )

    def test_empty_required_values_fail(self):
        result = load_settings({**VALID_PRODUCTION_ENV, "DJANGO_ALLOWED_HOSTS": "   "})
        assert result.returncode != 0
        assert "DJANGO_ALLOWED_HOSTS must be set" in result.stderr

    def test_production_never_defaults_browser_origins_to_localhost(self):
        env = {k: v for k, v in VALID_PRODUCTION_ENV.items() if k != "CORS_ALLOWED_ORIGINS"}
        result = load_settings(env)
        assert result.returncode != 0
        assert "CORS_ALLOWED_ORIGINS" in result.stderr
        assert "SAME_ORIGIN_DEPLOYMENT" in result.stderr

    def test_same_origin_deployment_flag_permits_absent_origins(self):
        env = {
            k: v
            for k, v in VALID_PRODUCTION_ENV.items()
            if k not in ("CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS")
        }
        env["SAME_ORIGIN_DEPLOYMENT"] = "true"
        result = load_settings(env)
        assert result.returncode == 0, result.stderr
