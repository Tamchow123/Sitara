"""Readiness dependency checks.

Each check returns True/False and must never leak credentials, connection
strings or internal exception details to callers — failures are reported
only as a status word."""

import logging

import redis
from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)


def check_database() -> bool:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception:
        logger.warning("readiness: database check failed", exc_info=True)
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
    except Exception:
        logger.warning("readiness: redis check failed", exc_info=True)
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
    except Exception:
        logger.warning("readiness: storage check failed", exc_info=True)
        return False
