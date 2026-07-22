"""Sitara API settings — environment-driven, fail-closed.

Committed defaults keep the application safe by construction:

    DEFAULT_IMAGE_MODEL = black-forest-labs/flux-1.1-pro   (Phase 2 decision)
    FAST_IMAGE_MODEL    = black-forest-labs/flux-1.1-pro
    DEMO_MODE           = true    (no Anthropic / Replicate calls)
    ALLOW_PAID_AI_CALLS = false   (a present token never enables paid calls)

Production (`APP_ENV=production`) fails startup unless genuinely required
settings are provided; local development gets documented safe defaults via
Docker Compose.
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


# Strict boolean parsing: safety gates must never be silently mis-set by a
# typo like DEMO_MODE=tru (which permissive parsing would read as False).
_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE = frozenset({"0", "false", "no", "off"})


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _BOOL_TRUE:
        return True
    if value in _BOOL_FALSE:
        return False
    # Identify only the variable and the accepted format — never echo the
    # supplied value.
    raise ImproperlyConfigured(
        f"{name} must be a boolean: one of 1/true/yes/on or 0/false/no/off "
        "(case-insensitive); the supplied value is not recognised"
    )


def env_positive_int(name: str, default: int) -> int:
    """Strict positive-integer parsing; never echoes the supplied value."""
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value.isdigit() or int(value) <= 0:
        raise ImproperlyConfigured(
            f"{name} must be a positive integer; the supplied value is not recognised"
        )
    return int(value)


def env_nonnegative_int(name: str, default: int) -> int:
    """Strict non-negative-integer parsing (zero permitted); never echoes the
    supplied value. Used for fail-closed cost ceilings and prices where zero is
    a meaningful "no budget"/"unconfigured" value rather than an error."""
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value.isdigit():
        raise ImproperlyConfigured(
            f"{name} must be a non-negative integer; the supplied value is not recognised"
        )
    return int(value)


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Environment / core
# ---------------------------------------------------------------------------

# Environment classification fails closed: anything outside this exact
# allowlist (typos like "prod", "Production", "produciton") refuses startup
# instead of silently being treated as development.
ALLOWED_APP_ENVS = ("development", "test", "production")

APP_ENV = os.getenv("APP_ENV", "development")
if APP_ENV not in ALLOWED_APP_ENVS:
    raise ImproperlyConfigured(
        f"APP_ENV must be one of {list(ALLOWED_APP_ENVS)}; got {APP_ENV!r}. "
        "Unknown environments are never treated as development."
    )
IS_PRODUCTION = APP_ENV == "production"

DEBUG = env_bool("DEBUG", default=not IS_PRODUCTION)

_DEV_ONLY_SECRET = "dev-only-insecure-secret-key-do-not-use-in-production"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "" if IS_PRODUCTION else _DEV_ONLY_SECRET)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,api")

# ---------------------------------------------------------------------------
# Production configuration validation — fail startup on missing values AND
# on known development/placeholder values. Error messages identify only the
# variable name and reason; rejected values are never echoed or logged.
# ---------------------------------------------------------------------------

# Case-insensitive markers that always indicate an unconfigured value.
_PLACEHOLDER_MARKERS = ("change-me", "__replace_me__")

# Exact known development/CI/example values that must never reach production.
_KNOWN_DEV_VALUES: dict[str, set[str]] = {
    "DJANGO_SECRET_KEY": {_DEV_ONLY_SECRET},
    "DATABASE_URL": {
        "postgres://sitara:sitara-dev-password@localhost:5432/sitara",
        "postgres://sitara:sitara-dev-password@postgres:5432/sitara",
        "postgres://sitara:sitara-ci-password@localhost:5432/sitara",
    },
    "S3_ACCESS_KEY_ID": {"sitara-minio", "ci-placeholder"},
    "S3_SECRET_ACCESS_KEY": {"sitara-minio-dev-password", "ci-placeholder"},
    "DJANGO_ALLOWED_HOSTS": set(),
    # Auth rate limiting fails closed without this cache, so production must
    # not silently fall back to the committed development/CI values.
    "REDIS_CACHE_URL": {
        "redis://localhost:6379/1",
        "redis://redis:6379/1",
    },
}


# Host ENTRIES (matched exactly per comma-separated entry, never by
# substring — api.sitara.example is fine) that must not appear in a
# production DJANGO_ALLOWED_HOSTS unless the narrowly-scoped
# ALLOW_INTERNAL_HOSTNAMES_IN_PRODUCTION flag is deliberately set (e.g. a
# reverse proxy reaching Django via an internal Docker hostname).
_DEV_ONLY_HOST_ENTRIES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "api"})


def _production_value_problem(name: str) -> str | None:
    """Why this environment variable is unacceptable in production, or None.

    Deliberately returns only the variable NAME and a reason — never the
    value, which may be a secret or connection string."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return f"{name} must be set"
    lowered = value.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return f"{name} contains a placeholder value"
    if value in _KNOWN_DEV_VALUES.get(name, set()):
        return f"{name} is a known development-only value"
    return None


def _production_hosts_problem() -> str | None:
    """DJANGO_ALLOWED_HOSTS entries are validated individually so a
    development-only host cannot hide inside a comma-separated list."""
    if env_bool("ALLOW_INTERNAL_HOSTNAMES_IN_PRODUCTION", default=False):
        return None
    entries = env_list("DJANGO_ALLOWED_HOSTS")
    for entry in entries:
        if entry.lower() in _DEV_ONLY_HOST_ENTRIES:
            return (
                "DJANGO_ALLOWED_HOSTS contains a development-only host entry "
                "(set ALLOW_INTERNAL_HOSTNAMES_IN_PRODUCTION=true only for a "
                "documented internal reverse-proxy deployment)"
            )
    return None


def _production_origins_problem(name: str) -> str | None:
    """CORS/CSRF origin lists must contain at least one scheme-qualified
    origin in production; an empty string does not count as configured."""
    origins = env_list(name)
    if not origins:
        return (
            f"{name} must contain at least one origin "
            "(or set SAME_ORIGIN_DEPLOYMENT=true for a same-origin deployment)"
        )
    for origin in origins:
        if not origin.startswith(("http://", "https://")):
            return f"{name} contains an entry that is not a scheme-qualified origin"
    return None


if IS_PRODUCTION:
    _problems: list[str] = []
    if DEBUG:
        _problems.append("DEBUG must be false")
    for _name in (
        "DJANGO_SECRET_KEY",
        "DJANGO_ALLOWED_HOSTS",
        "DATABASE_URL",
        "REDIS_URL",
        "REDIS_CACHE_URL",
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_BUCKET_NAME",
    ):
        _problem = _production_value_problem(_name)
        if _problem:
            _problems.append(_problem)
    _hosts_problem = _production_hosts_problem()
    if _hosts_problem:
        _problems.append(_hosts_problem)
    # Browser origins must be explicit and non-empty in production. A
    # deliberately same-origin deployment (frontend served from the API
    # origin) requires the documented SAME_ORIGIN_DEPLOYMENT flag —
    # production never silently inherits localhost development origins, and
    # an empty string never counts as configured.
    if not env_bool("SAME_ORIGIN_DEPLOYMENT", default=False):
        for _name in ("CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS"):
            _problem = _production_origins_problem(_name)
            if _problem:
                _problems.append(_problem)
    if _problems:
        raise ImproperlyConfigured("invalid production configuration: " + "; ".join(_problems))

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "sitara.accounts",
    "sitara.designs",
    "sitara.questionnaire",
    "sitara.catalogue",
    "sitara.health",
    "sitara.ai_gateway",
    "sitara.generation",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database / Redis / Celery
# ---------------------------------------------------------------------------

DATABASES = {
    "default": dj_database_url.parse(
        os.getenv(
            "DATABASE_URL",
            # Local-development default only; Compose provides the real value.
            "postgres://sitara:sitara-dev-password@localhost:5432/sitara",
        ),
        conn_max_age=60,
    )
}

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", default=False)

# Explicit task routing (Phase 10): the durable generation task runs on its own
# ``generation`` queue so it never blocks (or is blocked by) the default
# ``celery`` queue, which stays available for the health ping. The worker
# process listens to BOTH queues (see compose.yaml). Enqueue also passes the
# queue explicitly, so routing is correct even if this table is missed.
CELERY_TASK_ROUTES = {
    "sitara.generation.tasks.generate_design_attempt": {"queue": "generation"},
    # Phase 16 maintenance tasks share the generation worker/queue.
    "sitara.generation.tasks.purge_expired_designs": {"queue": "generation"},
    "sitara.generation.tasks.reconcile_stuck_generations": {"queue": "generation"},
}
# Deterministic, bounded task behaviour: acknowledge late (redeliver on worker
# loss) and never let a result linger unboundedly. Per-task time limits and the
# no-whole-pipeline-retry policy live on the task itself.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Django cache (rate limiting) — Redis DB 1, separate from Celery's DB 0.
# Uses Django's built-in Redis cache backend; no extra client package.
REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", "redis://localhost:6379/1")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
    }
}

# ---------------------------------------------------------------------------
# REST framework — authenticated by default; JSON only.
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # drf-spectacular generates the committed OpenAPI contract (Phase 6). It
    # is used ONLY through the `spectacular` management command — no runtime
    # schema endpoint, Swagger UI or Redoc is served (see config/urls.py).
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# ---------------------------------------------------------------------------
# OpenAPI contract (drf-spectacular) — the single source of truth for the
# generated frontend TypeScript client. Kept deliberately small: no served
# schema/UI endpoint, deterministic ordering so the committed
# apps/api/openapi/schema.json is byte-stable, and a preprocessing hook that
# collapses the slash-optional runtime routes to their canonical
# trailing-slash spelling (runtime routing is unaffected).
# ---------------------------------------------------------------------------

SPECTACULAR_SETTINGS = {
    "TITLE": "Sitara API",
    "VERSION": "1.0.0",
    "DESCRIPTION": (
        "Sitara is an AI-assisted South Asian bridalwear **concept "
        "visualisation** application (concept only — not sewing patterns or "
        "manufacturing specifications).\n\n"
        "Designs are **private by default**: they are never public merely "
        "because their UUID is known, and every inaccessible design returns "
        "an indistinguishable 404. Authentication is a Django server-side "
        "session (HttpOnly cookie) coordinated with a CSRF token; the "
        "browser calls the API same-origin.\n\n"
        "AI generation is asynchronous and **fail-closed gated**: the "
        "documented generation endpoint enqueues a durable job and its "
        "private job-status endpoint reports lifecycle progress, but live "
        "paid generation stays disabled unless the operator explicitly "
        "enables every provider gate (LIVE_GENERATION_ENABLED defaults to "
        "false). Questionnaire-answer submission, inspiration selection and "
        "provider internals remain outside this contract."
    ),
    "OAS_VERSION": "3.0.3",
    "COMPONENT_SPLIT_REQUEST": True,
    "SERVE_INCLUDE_SCHEMA": False,
    # Deterministic output for the committed contract + CI drift check.
    "SORT_OPERATIONS": True,
    "SORT_OPERATION_PARAMETERS": True,
    # Collapse slash-optional regex routes to one canonical documented path.
    "PREPROCESSING_HOOKS": [
        "config.spectacular.normalise_trailing_slash",
    ],
    # No Django admin, no served schema route in the contract.
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    # Phase 14: two DIFFERENT serializer fields — GenerationJobSerializer.
    # generation_kind and DesignVersionLineageSerializer.kind — legitimately
    # share the exact same two-value choice set (a generation attempt and a
    # version's lineage are both "initial" or "refinement"), which
    # drf-spectacular otherwise names ambiguously per field. Both fields are
    # built with ``.values`` (a flat value list, matching this file's
    # existing ``status``/``error_code`` field style), so the override's
    # (value, label) pairs must match DRF's auto-generated label-equals-value
    # form exactly, not the model's title-cased GenerationKind.choices — a
    # plain literal (rather than a dotted import path) because GenerationKind
    # is a class nested inside the GenerationAttempt model, one level deeper
    # than drf-spectacular's dotted-path resolver supports.
    "ENUM_NAME_OVERRIDES": {
        "GenerationKindEnum": [("initial", "initial"), ("refinement", "refinement")],
    },
}

# ---------------------------------------------------------------------------
# CORS / CSRF — explicit allowlists, never wildcards.
# ---------------------------------------------------------------------------

# Localhost defaults apply ONLY outside production; production requires the
# variables explicitly (validated above) or SAME_ORIGIN_DEPLOYMENT=true.
_DEV_ORIGINS = "" if IS_PRODUCTION else "http://localhost:3000,http://localhost:3001"
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", _DEV_ORIGINS)
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", _DEV_ORIGINS)

# ---------------------------------------------------------------------------
# Private S3-compatible object storage (MinIO locally).
# Buckets are private; no public ACLs; downloads will use signed URLs or
# authenticated streaming in a later phase.
# ---------------------------------------------------------------------------

S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "sitara-minio")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "sitara-minio-dev-password")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "sitara-media")
S3_REGION_NAME = os.getenv("S3_REGION_NAME", "us-east-1")

# Shared private S3 options: no public-read ACL, signed query auth, no
# silent overwrite, SigV4. Used by BOTH the default alias (catalogue images
# and Phase 10 raw generation staging) and the s3 design_images alias below.
_PRIVATE_S3_OPTIONS = {
    "endpoint_url": S3_ENDPOINT_URL,
    "access_key": S3_ACCESS_KEY_ID,
    "secret_key": S3_SECRET_ACCESS_KEY,
    "bucket_name": S3_BUCKET_NAME,
    "region_name": S3_REGION_NAME,
    # Private by default: no public-read ACL, signed query auth.
    "default_acl": None,
    "querystring_auth": True,
    "file_overwrite": False,
    "signature_version": "s3v4",
}

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": dict(_PRIVATE_S3_OPTIONS),
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# ---------------------------------------------------------------------------
# Permanent private design-image storage (Phase 11 Part A).
#
# The ``design_images`` alias is the ONLY storage permanent generated images
# may use, always resolved at call time via
# ``django.core.files.storage.storages["design_images"]`` (never a
# module-level instance, so tests and environment overrides take effect).
# Exactly two backends exist:
#
#   s3          production and local MinIO-compatible private storage;
#   filesystem  offline development and deterministic ingest testing ONLY —
#               it has no public base URL and signed browser delivery fails
#               closed for it (no backend image proxy exists in Phase 11).
#
# The selection is STRICT and case-sensitive; unknown, blank or
# differently-cased values refuse startup without echoing the supplied value.
# ---------------------------------------------------------------------------

ALLOWED_DESIGN_IMAGE_STORAGE_BACKENDS = ("s3", "filesystem")

DESIGN_IMAGE_STORAGE_BACKEND = os.getenv("DESIGN_IMAGE_STORAGE_BACKEND", "s3")
if DESIGN_IMAGE_STORAGE_BACKEND not in ALLOWED_DESIGN_IMAGE_STORAGE_BACKENDS:
    raise ImproperlyConfigured(
        "DESIGN_IMAGE_STORAGE_BACKEND must be exactly 's3' or 'filesystem' "
        "(case-sensitive); the supplied value is not recognised"
    )
# The filesystem backend is development-only (its browser delivery is
# deliberately unavailable), so production fails closed on it.
if IS_PRODUCTION and DESIGN_IMAGE_STORAGE_BACKEND != "s3":
    raise ImproperlyConfigured(
        "DESIGN_IMAGE_STORAGE_BACKEND must be 's3' in production; the "
        "filesystem backend is development-only"
    )

# Private directory for the filesystem backend — outside static files and any
# publicly served media root; never exposed through a URL. Only validated (and
# used) when the filesystem backend is selected.
DESIGN_IMAGE_FILESYSTEM_ROOT = os.getenv(
    "DESIGN_IMAGE_FILESYSTEM_ROOT", str(BASE_DIR / "private-design-images")
)
if DESIGN_IMAGE_STORAGE_BACKEND == "filesystem" and not DESIGN_IMAGE_FILESYSTEM_ROOT.strip():
    raise ImproperlyConfigured(
        "DESIGN_IMAGE_FILESYSTEM_ROOT must be a non-empty private directory "
        "path when the filesystem backend is selected"
    )

if DESIGN_IMAGE_STORAGE_BACKEND == "s3":
    # Client timeouts for the design_images alias. BOTH consumers share it:
    # sitara/media/ingest.py (asynchronous permanent-image reads/writes) and
    # sitara/media/delivery.py (synchronous existence checks). The values
    # here are sized for the SLOWER consumer — a legitimately slow but
    # succeeding ingest PUT must never be spuriously timed out into the
    # Celery bounded-retry budget (tasks.MAX_TRANSIENT_RETRIES is the
    # resilience mechanism there). The synchronous delivery path does NOT
    # depend on these values: it bounds its own wait with an in-process
    # deadline (sitara/media/delivery.py EXISTENCE_DEADLINE_SECONDS) sized
    # under the browser transport's fixed 5s abort. Import deferred so the
    # filesystem branch never touches botocore.
    from botocore.config import Config as _BotoClientConfig

    STORAGES["design_images"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            **_PRIVATE_S3_OPTIONS,
            "client_config": _BotoClientConfig(
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 2},
            ),
        },
    }
else:
    STORAGES["design_images"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": DESIGN_IMAGE_FILESYSTEM_ROOT,
            # No public base URL: ``.url()`` raises instead of ever exposing
            # a filesystem path through the API.
            "base_url": None,
            # Owner-only permissions where the platform supports them.
            "directory_permissions_mode": 0o700,
            "file_permissions_mode": 0o600,
        },
    }

# ---------------------------------------------------------------------------
# Signed design-image delivery endpoint (Phase 11 Part B).
#
# The API container reaches S3/MinIO through S3_ENDPOINT_URL (an internal
# Docker host); a BROWSER following a presigned URL needs an externally
# reachable origin. This setting configures ONLY the presigning host for
# design-image GET URLs — it is never used for ordinary object upload/read
# calls, never exposed through /api/v1/config/public, and blank means "use
# the normal regional S3 endpoint" (the production default on real AWS).
# A configured value must be an absolute http(s) ORIGIN: no userinfo, no
# query, no fragment, no path other than "/", and HTTPS in production.
# Rejected values are never echoed.
# ---------------------------------------------------------------------------

S3_SIGNED_URL_ENDPOINT_URL = os.getenv("S3_SIGNED_URL_ENDPOINT_URL", "").strip()
if S3_SIGNED_URL_ENDPOINT_URL:
    from urllib.parse import urlsplit as _urlsplit

    _signed_parts = _urlsplit(S3_SIGNED_URL_ENDPOINT_URL)
    _signed_problems = []
    if _signed_parts.scheme not in ("http", "https"):
        _signed_problems.append("must be an absolute http(s) URL")
    if IS_PRODUCTION and _signed_parts.scheme != "https":
        _signed_problems.append("must use https in production")
    if not _signed_parts.netloc:
        _signed_problems.append("must include a host")
    if _signed_parts.username is not None or _signed_parts.password is not None:
        _signed_problems.append("must not include credentials")
    if _signed_parts.query:
        _signed_problems.append("must not include a query string")
    if _signed_parts.fragment:
        _signed_problems.append("must not include a fragment")
    if _signed_parts.path not in ("", "/"):
        _signed_problems.append("must not include a path")
    if _signed_problems:
        raise ImproperlyConfigured(
            "S3_SIGNED_URL_ENDPOINT_URL is not a valid signing origin: "
            + "; ".join(_signed_problems)
        )

# Canonical design-image processing bounds (strict positive integers; errors
# identify only the setting, never its value).
DESIGN_IMAGE_MAX_EDGE = env_positive_int("DESIGN_IMAGE_MAX_EDGE", 2048)
DESIGN_IMAGE_THUMBNAIL_EDGE = env_positive_int("DESIGN_IMAGE_THUMBNAIL_EDGE", 512)
DESIGN_IMAGE_WEBP_QUALITY = env_positive_int("DESIGN_IMAGE_WEBP_QUALITY", 90)
DESIGN_IMAGE_THUMBNAIL_QUALITY = env_positive_int("DESIGN_IMAGE_THUMBNAIL_QUALITY", 82)
DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS = env_positive_int("DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS", 300)

for _quality_name in ("DESIGN_IMAGE_WEBP_QUALITY", "DESIGN_IMAGE_THUMBNAIL_QUALITY"):
    if not 1 <= globals()[_quality_name] <= 100:
        raise ImproperlyConfigured(f"{_quality_name} must be between 1 and 100")
if DESIGN_IMAGE_THUMBNAIL_EDGE > DESIGN_IMAGE_MAX_EDGE:
    raise ImproperlyConfigured("DESIGN_IMAGE_THUMBNAIL_EDGE must not exceed DESIGN_IMAGE_MAX_EDGE")
if not 30 <= DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS <= 3600:
    raise ImproperlyConfigured(
        "DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS must be between 30 and 3600 seconds"
    )

# ---------------------------------------------------------------------------
# AI provider gates — fail closed.
# ---------------------------------------------------------------------------

DEMO_MODE = env_bool("DEMO_MODE", default=True)
ALLOW_PAID_AI_CALLS = env_bool("ALLOW_PAID_AI_CALLS", default=False)

# Stripped at assignment so validation, persistence and provider submission
# all use ONE canonical value (a padded env value can never diverge from the
# value that was validated).
DEFAULT_IMAGE_MODEL = os.getenv("DEFAULT_IMAGE_MODEL", "black-forest-labs/flux-1.1-pro").strip()
FAST_IMAGE_MODEL = os.getenv("FAST_IMAGE_MODEL", "black-forest-labs/flux-1.1-pro").strip()

# Tokens may be present in the environment; their presence NEVER enables
# provider calls (see sitara.ai_gateway.policy). Never log or return them.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")

# With the paid gates OPEN, a placeholder-marked provider credential is a
# misconfiguration, not an absent optional secret: refuse startup naming only
# the setting (the value is never echoed). Blank credentials stay permissible
# — availability simply remains False — and closed gates skip this entirely.
if not DEMO_MODE and ALLOW_PAID_AI_CALLS:
    for _credential_name, _credential_value in (
        ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        ("REPLICATE_API_TOKEN", REPLICATE_API_TOKEN),
    ):
        _lowered = _credential_value.lower()
        if any(marker in _lowered for marker in _PLACEHOLDER_MARKERS):
            raise ImproperlyConfigured(
                f"{_credential_name} must not be a placeholder value when "
                "paid AI calls are enabled"
            )

# ---------------------------------------------------------------------------
# Structured DesignSpec generation (Phase 8). Safe development/test defaults;
# the model name is NEVER exposed via the public config endpoint. Numeric
# values use the strict positive-integer parser; the model name must be
# non-empty (a blank value refuses startup without echoing the value).
# ---------------------------------------------------------------------------
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()
if not ANTHROPIC_MODEL:
    raise ImproperlyConfigured("ANTHROPIC_MODEL must be a non-empty model identifier")

DESIGN_SPEC_MAX_INPUT_CHARS = env_positive_int("DESIGN_SPEC_MAX_INPUT_CHARS", 20_000)
DESIGN_SPEC_MAX_OUTPUT_TOKENS = env_positive_int("DESIGN_SPEC_MAX_OUTPUT_TOKENS", 4096)
ANTHROPIC_TIMEOUT_SECONDS = env_positive_int("ANTHROPIC_TIMEOUT_SECONDS", 60)

# ---------------------------------------------------------------------------
# Gated Replicate image rendering (Phase 10 Part B). Fail-closed by default:
# LIVE_GENERATION_ENABLED gates the public end-to-end generation API and
# defaults to false, so accidental paid generation is impossible even with both
# provider gates open and a token present. It does NOT weaken the existing
# standalone Anthropic management-command gates. The model name is never
# exposed via the public config endpoint. Numeric values are strict positive
# integers; malformed configuration refuses startup without echoing values.
# ---------------------------------------------------------------------------
LIVE_GENERATION_ENABLED = env_bool("LIVE_GENERATION_ENABLED", default=False)
REPLICATE_TIMEOUT_SECONDS = env_positive_int("REPLICATE_TIMEOUT_SECONDS", 30)
REPLICATE_POLL_INTERVAL_SECONDS = env_positive_int("REPLICATE_POLL_INTERVAL_SECONDS", 2)
REPLICATE_POLL_TIMEOUT_SECONDS = env_positive_int("REPLICATE_POLL_TIMEOUT_SECONDS", 180)
GENERATION_RAW_MAX_BYTES = env_positive_int("GENERATION_RAW_MAX_BYTES", 20_000_000)
GENERATION_RAW_MAX_PIXELS = env_positive_int("GENERATION_RAW_MAX_PIXELS", 40_000_000)

# The poll interval must be strictly smaller than the overall poll timeout, or
# the pipeline could never poll more than once before giving up.
if REPLICATE_POLL_INTERVAL_SECONDS >= REPLICATE_POLL_TIMEOUT_SECONDS:
    raise ImproperlyConfigured(
        "REPLICATE_POLL_INTERVAL_SECONDS must be strictly less than "
        "REPLICATE_POLL_TIMEOUT_SECONDS"
    )

# The configured image model must be non-empty and fit the persisted
# GenerationAttempt.image_model column bound (100). Validated on the SAME
# canonical (stripped-at-assignment) value the pipeline persists and submits.
# Never echo the value.
if not DEFAULT_IMAGE_MODEL or len(DEFAULT_IMAGE_MODEL) > 100:
    raise ImproperlyConfigured(
        "DEFAULT_IMAGE_MODEL must be a non-empty model identifier of at most 100 characters"
    )

# Product limits surfaced via /api/v1/config/public.
MAX_INSPIRATION_IMAGES = 3
MAX_REFINEMENTS = 1

# Application-level cap on DesignVersions per design (initial concept + one
# refinement). Deliberately NOT a database constraint (future multi-round
# refinement must not need a migration) and deliberately NOT in the public
# config endpoint — max_refinements already communicates the user-facing
# limit. Strict parsing: an invalid value refuses startup in EVERY
# environment, production included.
MAX_DESIGN_VERSIONS = env_positive_int("MAX_DESIGN_VERSIONS", 2)

# ---------------------------------------------------------------------------
# Live-generation cost controls (Phase 16, Part A). Integer micro-US-dollars
# throughout (1 USD = 1_000_000 micro-USD); binary floating point is never used
# for pricing, reservations, reconciliation, totals or ceiling comparisons.
#
# Every value fails closed. The daily budget defaults to ZERO, so live spend is
# impossible until an operator deliberately configures a positive ceiling AND a
# valid pricing profile. Demo mode NEVER depends on any of these — demo cannot
# spend money by construction. Pricing remains operator configuration verified
# against official provider sources on a recorded date; it is never a hard-coded
# consequence of a model identifier, and no value here is exposed through the
# public config endpoint. Names/values never echo a rejected input on error.
# The micro-USD unit (1 USD = 1_000_000 micro-USD) is defined once in
# ``sitara.generation.cost_control`` where the arithmetic lives.
# ---------------------------------------------------------------------------

# Hard daily ceiling (micro-USD) for accepted live-provider spend, UTC-day
# windowed. Zero (the default) means no live spend can ever be reserved.
LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = env_nonnegative_int(
    "LIVE_GENERATION_DAILY_BUDGET_MICRO_USD", 0
)

# Live admission controls (Phase 16, Part B). Global cap on newly accepted live
# generation attempts per UTC day (zero admits none); per-session and per-IP
# request throttles (a zero limit rejects every request — fail closed; windows
# must be strictly positive). The global daily COUNT uses the dedicated budget
# Redis database (atomic Lua, like the cost ledger); the per-session/per-IP
# THROTTLES reuse the existing Django cache backend shared with
# ``sitara.accounts.rate_limits``. Both use HMAC-SHA256 identifiers keyed by
# SECRET_KEY, so no raw IP, session key, workspace UUID, user id or email is
# ever stored.
LIVE_GENERATION_DAILY_COUNT_LIMIT = env_nonnegative_int("LIVE_GENERATION_DAILY_COUNT_LIMIT", 0)
LIVE_GENERATION_SESSION_LIMIT = env_nonnegative_int("LIVE_GENERATION_SESSION_LIMIT", 0)
LIVE_GENERATION_SESSION_WINDOW_SECONDS = env_positive_int(
    "LIVE_GENERATION_SESSION_WINDOW_SECONDS", 3600
)
LIVE_GENERATION_IP_LIMIT = env_nonnegative_int("LIVE_GENERATION_IP_LIMIT", 0)
LIVE_GENERATION_IP_WINDOW_SECONDS = env_positive_int("LIVE_GENERATION_IP_WINDOW_SECONDS", 3600)

# Versioned pricing-profile identifier. Reservations and reconciliations are
# stamped with this so a provider-model or price change (which MUST bump this
# value) can never silently continue accounting under stale, unreviewed prices.
# Blank is invalid for live availability (fail closed).
LIVE_GENERATION_PRICING_PROFILE = os.getenv("LIVE_GENERATION_PRICING_PROFILE", "").strip()

# Provider prices in micro-USD. Anthropic text prices are per one million
# tokens; the Replicate value is a single conservative maximum per image call.
# All default to zero (unconfigured → live unavailable); none is derived from a
# model id. Operator must verify against official provider pricing.
ANTHROPIC_INPUT_MICRO_USD_PER_MTOK = env_nonnegative_int("ANTHROPIC_INPUT_MICRO_USD_PER_MTOK", 0)
ANTHROPIC_OUTPUT_MICRO_USD_PER_MTOK = env_nonnegative_int("ANTHROPIC_OUTPUT_MICRO_USD_PER_MTOK", 0)
REPLICATE_MAX_IMAGE_MICRO_USD = env_nonnegative_int("REPLICATE_MAX_IMAGE_MICRO_USD", 0)

# Conservative upper bound on the Anthropic input-token count for one assembled
# request, used with the input price to compute a never-under-reserving maximum.
# Bounded by DESIGN_SPEC_MAX_INPUT_CHARS-derived worst case; a request that would
# exceed it fails closed rather than under-reserving.
ANTHROPIC_MAX_INPUT_TOKENS = env_positive_int("ANTHROPIC_MAX_INPUT_TOKENS", 8192)

# Dedicated Redis logical database for the live-generation budget ledger and the
# global daily count reservation. Kept separate from Celery (DB 0) and the
# Django cache/throttles (DB 1) so budget keys have an exclusive namespace and
# are never evicted by unrelated cache pressure. See ADR 0017 for the durability
# / no-eviction operational requirement.
LIVE_GENERATION_BUDGET_REDIS_URL = os.getenv(
    "LIVE_GENERATION_BUDGET_REDIS_URL", "redis://localhost:6379/2"
)
# Bounded socket timeout (seconds) for every budget-ledger Redis operation, so a
# stalled ledger fails closed promptly rather than hanging a worker.
LIVE_GENERATION_BUDGET_REDIS_TIMEOUT_SECONDS = env_positive_int(
    "LIVE_GENERATION_BUDGET_REDIS_TIMEOUT_SECONDS", 5
)

# ---------------------------------------------------------------------------
# Retention and stuck-job maintenance (Phase 16, Part C). Celery Beat runs two
# bounded, idempotent periodic tasks. All strict positive integers.
# ---------------------------------------------------------------------------
# Designs older than this are purged (rows + permanent AND staging objects). The
# design purge is also the cleanup boundary for retained crash-recovery staging
# objects (ADR 0017): a design's staging objects live at most this long.
DESIGN_RETENTION_DAYS = env_positive_int("DESIGN_RETENTION_DAYS", 30)
# An attempt in a non-terminal state (queued/running_text/running_image) with no
# progress for this long is treated as stuck and reconciled to failed.
GENERATION_STUCK_AFTER_SECONDS = env_positive_int("GENERATION_STUCK_AFTER_SECONDS", 600)
# Bounded batch sizes so one maintenance run never monopolises the worker.
DESIGN_PURGE_BATCH_SIZE = env_positive_int("DESIGN_PURGE_BATCH_SIZE", 50)
GENERATION_STUCK_BATCH_SIZE = env_positive_int("GENERATION_STUCK_BATCH_SIZE", 50)
# How often (seconds) Celery Beat runs each maintenance task.
DESIGN_PURGE_INTERVAL_SECONDS = env_positive_int("DESIGN_PURGE_INTERVAL_SECONDS", 3600)
GENERATION_STUCK_INTERVAL_SECONDS = env_positive_int("GENERATION_STUCK_INTERVAL_SECONDS", 120)

# ---------------------------------------------------------------------------
# Deterministic zero-cost demo pipeline (Phase 15). The active demo manifest
# itself is resolved from private object storage (see
# sitara.generation.demo.config), never a filesystem path setting, so it
# stays valid across every configured storage backend. DEMO_STAGE_DELAY_MS
# only keeps the genuine persisted progress states visible for a
# demonstration; it is strictly bounded and applies only to demo attempts.
# ---------------------------------------------------------------------------
_DEMO_STAGE_DELAY_MS_MAXIMUM = 5000

_raw_demo_stage_delay_ms = os.getenv("DEMO_STAGE_DELAY_MS")
if _raw_demo_stage_delay_ms is None:
    DEMO_STAGE_DELAY_MS = 0
else:
    _stripped_demo_stage_delay_ms = _raw_demo_stage_delay_ms.strip()
    if (
        not _stripped_demo_stage_delay_ms.isdigit()
        or int(_stripped_demo_stage_delay_ms) > _DEMO_STAGE_DELAY_MS_MAXIMUM
    ):
        raise ImproperlyConfigured(
            "DEMO_STAGE_DELAY_MS must be a non-negative integer of at most "
            f"{_DEMO_STAGE_DELAY_MS_MAXIMUM}"
        )
    DEMO_STAGE_DELAY_MS = int(_stripped_demo_stage_delay_ms)

# ---------------------------------------------------------------------------
# Inspiration catalogue (Phase 5B) — staff-only image ingestion bounds.
# Strict positive integers; invalid values refuse startup in EVERY
# environment. Uploads above INSPIRATION_MAX_UPLOAD_BYTES are rejected
# before decoding; images above INSPIRATION_MAX_IMAGE_PIXELS are rejected
# before full decode (decompression-bomb guard).
# ---------------------------------------------------------------------------

INSPIRATION_MAX_UPLOAD_BYTES = env_positive_int("INSPIRATION_MAX_UPLOAD_BYTES", 15_000_000)
INSPIRATION_MAX_IMAGE_PIXELS = env_positive_int("INSPIRATION_MAX_IMAGE_PIXELS", 40_000_000)
INSPIRATION_OUTPUT_MAX_EDGE = env_positive_int("INSPIRATION_OUTPUT_MAX_EDGE", 2048)
INSPIRATION_THUMBNAIL_EDGE = env_positive_int("INSPIRATION_THUMBNAIL_EDGE", 512)

# ---------------------------------------------------------------------------
# Authentication (Phase 3B) — Django sessions only. No JWT, no token
# cookies, no localStorage tokens.
# ---------------------------------------------------------------------------

# Project-specific cookie names: cookies are scoped by host (not port), and
# other local applications also run on localhost.
SESSION_COOKIE_NAME = "sitara_sessionid"
CSRF_COOKIE_NAME = "sitara_csrftoken"

# Failed CSRF checks return JSON, never Django's HTML error page.
CSRF_FAILURE_VIEW = "sitara.accounts.csrf.csrf_failure"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Pathological-input guard for registration/login request bodies.
AUTH_PASSWORD_MAX_LENGTH = 128

# Fixed-window authentication rate limits (Redis-backed; identifiers are
# HMAC-hashed before entering cache keys — never raw emails or IPs).
AUTH_LOGIN_IP_LIMIT = env_positive_int("AUTH_LOGIN_IP_LIMIT", 20)
AUTH_LOGIN_IP_WINDOW_SECONDS = env_positive_int("AUTH_LOGIN_IP_WINDOW_SECONDS", 300)
AUTH_LOGIN_EMAIL_LIMIT = env_positive_int("AUTH_LOGIN_EMAIL_LIMIT", 5)
AUTH_LOGIN_EMAIL_WINDOW_SECONDS = env_positive_int("AUTH_LOGIN_EMAIL_WINDOW_SECONDS", 300)
AUTH_REGISTER_IP_LIMIT = env_positive_int("AUTH_REGISTER_IP_LIMIT", 5)
AUTH_REGISTER_IP_WINDOW_SECONDS = env_positive_int("AUTH_REGISTER_IP_WINDOW_SECONDS", 3600)

# ---------------------------------------------------------------------------
# Security hardening
# ---------------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=IS_PRODUCTION)
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "3600" if IS_PRODUCTION else "0"))

# Behind a TLS-terminating proxy in production, enable via environment.
if env_bool("USE_X_FORWARDED_PROTO", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# I18n / misc
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
