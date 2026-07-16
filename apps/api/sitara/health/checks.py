"""Readiness dependency checks.

Each check returns True/False and must never leak credentials, connection
strings or internal exception details — neither to HTTP callers nor to
LOGS. Connection-library exceptions routinely embed passwords, URLs and
access keys, so on failure we log only safe metadata (which check failed
and the exception TYPE), never ``str(exception)`` and never a traceback."""

import logging
import uuid

import redis
from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)


def _log_failure(check_name: str, exc: Exception) -> None:
    """Safe operational breadcrumb: check name + exception type only."""
    logger.warning("readiness %s check failed exception_type=%s", check_name, type(exc).__name__)


def check_database() -> bool:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception as exc:
        _log_failure("database", exc)
        return False


def check_redis() -> bool:
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception as exc:
        _log_failure("redis", exc)
        return False


AUTH_CACHE_PROBE_PREFIX = "sitara-readiness-cache-probe"


def check_auth_cache() -> bool:
    """Authentication rate limiting fails CLOSED when the Django cache is
    down (503 on login/registration), so cache reachability is part of
    readiness. The probe key and value are unique per invocation (random,
    non-sensitive, no user data) so simultaneous readiness checks cannot
    delete or overwrite one another's probe; the entry expires in seconds
    regardless, and deletion is best-effort cleanup only."""
    try:
        from django.core.cache import cache

        nonce = uuid.uuid4().hex
        probe_key = f"{AUTH_CACHE_PROBE_PREFIX}:{nonce}"
        cache.set(probe_key, nonce, timeout=5)
        value = cache.get(probe_key)
        try:
            cache.delete(probe_key)
        except Exception:
            pass  # best-effort; the short TTL removes it anyway
        return value == nonce
    except Exception as exc:
        _log_failure("auth_cache", exc)
        return False


def check_storage() -> bool:
    """Confirm the private bucket is reachable with the configured
    credentials (HeadBucket; no object data is read or written)."""
    try:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name=settings.S3_REGION_NAME,
            config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}),
        )
        client.head_bucket(Bucket=settings.S3_BUCKET_NAME)
        return True
    except Exception as exc:
        _log_failure("storage", exc)
        return False
