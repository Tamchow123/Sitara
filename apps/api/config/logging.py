"""Structured logging (Phase 16, Part E).

A small standard-library JSON formatter for production and a filter that injects
the request/attempt correlation ids from ``config.correlation``. Development
keeps human-readable console logs (configured in settings).

The formatter is deliberately conservative: it emits only safe fields —
timestamp, level, logger, message, the correlation ids, and (at a controlled
boundary) the EXCEPTION TYPE — never the raw exception text/traceback, which can
carry secrets, user input, storage keys or provider data. Callers are still
responsible for keeping their log MESSAGES free of sensitive values (the
repository's existing rule); this formatter never re-introduces the traceback
body that the boundary logs deliberately avoid.
"""

from __future__ import annotations

import json
import logging

from .correlation import get_attempt_id, get_request_id


class CorrelationFilter(logging.Filter):
    """Attach the current request/attempt correlation ids to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.attempt_id = get_attempt_id()
        return True


class JsonFormatter(logging.Formatter):
    """Render a log record as a single JSON line with only safe fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        attempt_id = getattr(record, "attempt_id", None)
        if attempt_id:
            payload["attempt_id"] = attempt_id
        # A controlled boundary may log with exc_info; record ONLY the exception
        # TYPE name, never the formatted traceback (which can carry secrets).
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)
