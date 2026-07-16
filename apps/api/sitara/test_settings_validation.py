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

import pytest

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

    def test_empty_origin_strings_do_not_count_as_configured(self):
        result = load_settings({**VALID_PRODUCTION_ENV, "CORS_ALLOWED_ORIGINS": ""})
        assert result.returncode != 0
        assert "CORS_ALLOWED_ORIGINS" in result.stderr
        result = load_settings({**VALID_PRODUCTION_ENV, "CSRF_TRUSTED_ORIGINS": "  , "})
        assert result.returncode != 0
        assert "CSRF_TRUSTED_ORIGINS" in result.stderr

    def test_non_scheme_origins_are_rejected(self):
        result = load_settings(
            {**VALID_PRODUCTION_ENV, "CORS_ALLOWED_ORIGINS": "app.sitara.example"}
        )
        assert result.returncode != 0
        assert "scheme-qualified" in result.stderr


class TestProductionHostValidation:
    @pytest.mark.parametrize(
        "hosts",
        [
            "localhost",
            "127.0.0.1",
            "api",
            "localhost,127.0.0.1,api",
            "api.sitara.example,localhost",
        ],
    )
    def test_local_django_hosts_are_rejected_in_production(self, hosts):
        result = load_settings({**VALID_PRODUCTION_ENV, "DJANGO_ALLOWED_HOSTS": hosts})

        assert result.returncode != 0, f"hosts {hosts!r} must be rejected"

        # Inspect the exception message rather than matching short values such
        # as "api" against traceback file paths.
        exception_line = result.stderr.strip().splitlines()[-1]

        assert "DJANGO_ALLOWED_HOSTS" in exception_line
        assert "development-only host" in exception_line

    def test_rejected_host_configuration_is_not_echoed(self):
        leak_marker = "host-leak-marker-7f3a9.invalid"
        hosts = f"{leak_marker},localhost"

        result = load_settings({**VALID_PRODUCTION_ENV, "DJANGO_ALLOWED_HOSTS": hosts})

        assert result.returncode != 0
        assert "DJANGO_ALLOWED_HOSTS" in result.stderr

        # A high-entropy marker avoids accidental matches against traceback
        # paths or Python/Django wording.
        assert leak_marker not in result.stderr
        assert hosts not in result.stderr


class TestStrictBooleanParsing:
    def test_boolean_typos_refuse_startup(self):
        cases = [
            ({"DEMO_MODE": "tru"}, "DEMO_MODE"),
            ({"ALLOW_PAID_AI_CALLS": "enable"}, "ALLOW_PAID_AI_CALLS"),
            ({"DEBUG": "fasle"}, "DEBUG"),
            (
                {**VALID_PRODUCTION_ENV, "SAME_ORIGIN_DEPLOYMENT": "perhaps"},
                "SAME_ORIGIN_DEPLOYMENT",
            ),
        ]
        for overrides, variable in cases:
            result = load_settings(overrides)
            assert result.returncode != 0, f"{variable} typo must refuse startup"
            assert variable in result.stderr
            assert "must be a boolean" in result.stderr

    def test_boolean_typos_are_not_echoed(self):
        for overrides, marker in [
            ({"ALLOW_PAID_AI_CALLS": "enable"}, "enable"),
            ({"DEBUG": "fasle"}, "fasle"),
            ({**VALID_PRODUCTION_ENV, "SAME_ORIGIN_DEPLOYMENT": "perhaps"}, "perhaps"),
        ]:
            result = load_settings(overrides)
            assert result.returncode != 0
            assert marker not in result.stderr

    def test_documented_boolean_spellings_work(self):
        for value in ("1", "true", "YES", " On "):
            result = load_settings({"DEMO_MODE": value})
            assert result.returncode == 0, f"true spelling {value!r}: {result.stderr}"
        for value in ("0", "false", "No", " OFF "):
            result = load_settings({"DEMO_MODE": value, "ALLOW_PAID_AI_CALLS": value})
            assert result.returncode == 0, f"false spelling {value!r}: {result.stderr}"


class TestCookieConfiguration:
    """Project-specific cookie names; secure flags in production; no
    JWT/token cookie machinery anywhere in settings."""

    ATTR_PROBE = (
        "import config.settings as s; "
        "print(s.SESSION_COOKIE_NAME, s.CSRF_COOKIE_NAME, "
        "s.SESSION_COOKIE_HTTPONLY, "
        "getattr(s, 'SESSION_COOKIE_SECURE', False), "
        "getattr(s, 'CSRF_COOKIE_SECURE', False))"
    )

    def _probe(self, env: dict[str, str]) -> subprocess.CompletedProcess:
        minimal = {"PATH": os.environ.get("PATH", "")}
        return subprocess.run(
            [sys.executable, "-c", self.ATTR_PROBE],
            cwd=API_ROOT,
            env={**minimal, **env},
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_sitara_cookie_names_and_httponly_in_development(self):
        result = self._probe({})
        assert result.returncode == 0, result.stderr
        names = result.stdout.split()
        assert names[0] == "sitara_sessionid"
        assert names[1] == "sitara_csrftoken"
        assert names[2] == "True"  # HttpOnly session cookie

    def test_secure_cookie_flags_enabled_in_production(self):
        result = self._probe(VALID_PRODUCTION_ENV)
        assert result.returncode == 0, result.stderr
        values = result.stdout.split()
        assert values == ["sitara_sessionid", "sitara_csrftoken", "True", "True", "True"]

    def test_no_jwt_or_token_cookie_settings_exist(self):
        probe = (
            "import config.settings as s; "
            "names=[n for n in dir(s) if 'JWT' in n.upper() or 'TOKEN_COOKIE' in n.upper()]; "
            "print(names)"
        )
        minimal = {"PATH": os.environ.get("PATH", "")}
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=API_ROOT,
            env=minimal,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[]"
