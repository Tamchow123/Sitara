"""Correlation + structured-logging tests (Phase 16, Part E). No Sentry network
calls occur (Sentry is disabled without a DSN — see test_sentry_disabled)."""

import json
import logging
import uuid

import pytest
from django.test import Client

from config import correlation
from config.logging import CorrelationFilter, JsonFormatter

pytestmark = pytest.mark.django_db


class TestRequestCorrelation:
    def test_valid_client_request_id_is_honoured_and_echoed(self):
        given = str(uuid.uuid4())
        response = Client().get("/api/v1/health/live", HTTP_X_REQUEST_ID=given)
        assert response.headers.get("X-Request-ID") == given

    def test_malformed_client_request_id_is_replaced_with_a_uuid(self):
        response = Client().get("/api/v1/health/live", HTTP_X_REQUEST_ID="not-a-uuid; drop table")
        returned = response.headers.get("X-Request-ID")
        # A fresh canonical UUID, never the malformed/oversized client value.
        assert returned != "not-a-uuid; drop table"
        uuid.UUID(returned)  # parses as a canonical UUID

    def test_absent_request_id_is_generated(self):
        response = Client().get("/api/v1/health/live")
        uuid.UUID(response.headers.get("X-Request-ID"))

    def test_context_does_not_leak_between_requests(self):
        client = Client()
        client.get("/api/v1/health/live", HTTP_X_REQUEST_ID=str(uuid.uuid4()))
        # After the request completes the middleware cleared the context.
        assert correlation.get_request_id() is None
        assert correlation.get_attempt_id() is None

    def test_coerce_bounds_and_validates(self):
        good = str(uuid.uuid4())
        assert correlation.coerce_request_id(good) == good
        assert correlation.coerce_request_id("x" * 5000) != "x" * 5000
        assert correlation.coerce_request_id(None) != correlation.coerce_request_id(None)


class TestJsonFormatter:
    def _record(self, msg, **extra):
        record = logging.LogRecord("sitara.test", logging.INFO, __file__, 1, msg, None, None)
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_emits_only_safe_fields(self):
        record = self._record("design generated design=%s", args=("abc",))
        record.request_id = "req-1"
        record.attempt_id = "att-1"
        payload = json.loads(JsonFormatter().format(record))
        assert set(payload) == {
            "timestamp",
            "level",
            "logger",
            "message",
            "request_id",
            "attempt_id",
        }
        assert payload["message"] == "design generated design=abc"
        assert payload["request_id"] == "req-1"

    def test_exception_records_only_the_type_never_the_traceback(self):
        try:
            raise ValueError("a secret token sk-ant-SENSITIVE")
        except ValueError:
            import sys

            record = self._record("boom")
            record.exc_info = sys.exc_info()
        formatted = JsonFormatter().format(record)
        payload = json.loads(formatted)
        assert payload["exception_type"] == "ValueError"
        # The exception MESSAGE / traceback (which could carry secrets) is never
        # serialised.
        assert "sk-ant-SENSITIVE" not in formatted

    def test_correlation_filter_injects_ids(self):
        token = correlation.set_request_id("rid-9")
        try:
            record = self._record("hi")
            assert CorrelationFilter().filter(record) is True
            assert record.request_id == "rid-9"
        finally:
            token.var.reset(token)


class TestSentry:
    def test_disabled_without_a_dsn_constructs_no_client(self):
        # No DSN -> init returns False and never imports/constructs sentry_sdk,
        # so tests and CI make no Sentry network call.
        from config.sentry import init_sentry

        assert init_sentry(dsn="", environment="test", release="") is False

    def test_init_disables_locals_and_neutralises_logging_integration(self, monkeypatch):
        # With a DSN we still make NO network call: sentry_sdk.init is stubbed so
        # only the passed options are inspected. Locals capture is off and the
        # LoggingIntegration is neutralised (no log record becomes a Sentry
        # breadcrumb or event), so Sentry cannot reopen the traceback/secret leak.
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        captured: dict = {}
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: captured.update(kwargs))

        from config.sentry import init_sentry, scrub_event

        assert init_sentry(dsn="https://k@example.test/1", environment="t", release="") is True
        assert captured["include_local_variables"] is False
        assert captured["send_default_pii"] is False
        assert captured["max_request_body_size"] == "never"
        assert captured["traces_sample_rate"] == 0.0
        assert captured["before_send"] is scrub_event
        logging_integrations = [
            i for i in captured["integrations"] if isinstance(i, LoggingIntegration)
        ]
        assert logging_integrations, "an explicit LoggingIntegration must override the default"
        integration = logging_integrations[0]
        # level=None -> no breadcrumb handler; event_level=None -> no event handler.
        assert integration._handler is None
        assert integration._breadcrumb_handler is None

    def test_scrub_removes_bodies_cookies_headers_and_query(self):
        from config.sentry import scrub_event

        event = {
            "request": {
                "data": {"note": "private refinement note"},
                "cookies": {"sitara_sessionid": "secret"},
                "query_string": "X-Amz-Signature=abc",
                "headers": {
                    "Cookie": "sitara_sessionid=secret",
                    "Authorization": "Bearer t",
                    "X-CSRFToken": "csrf",
                    "User-Agent": "ua",
                },
                "url": "https://media.example/x.webp?X-Amz-Signature=abc",
            },
            "user": {"email": "a@b.test", "ip_address": "203.0.113.1"},
        }
        scrubbed = scrub_event(event)
        req = scrubbed["request"]
        assert "data" not in req
        assert "cookies" not in req
        assert "query_string" not in req
        assert req["headers"] == {"User-Agent": "ua"}
        assert req["url"] == "https://media.example/x.webp"
        assert "user" not in scrubbed  # no PII

    def test_scrub_attaches_correlation_tags(self):
        from config.sentry import scrub_event

        token = correlation.set_request_id("req-77")
        try:
            scrubbed = scrub_event({})
            assert scrubbed["tags"]["request_id"] == "req-77"
        finally:
            token.var.reset(token)

    def test_scrub_reduces_exceptions_to_type_and_drops_frame_locals(self):
        # The exception message and any captured stack-frame locals can embed
        # secrets/user input; only the exception TYPE and location may survive.
        from config.sentry import scrub_event

        event = {
            "exception": {
                "values": [
                    {
                        "type": "ValueError",
                        "value": "token sk-ant-SENSITIVE in message",
                        "stacktrace": {
                            "frames": [
                                {"function": "f", "vars": {"api_key": "r8_SECRET"}},
                            ]
                        },
                    }
                ]
            },
            "threads": {
                "values": [{"stacktrace": {"frames": [{"vars": {"note": "private note"}}]}}]
            },
        }
        scrubbed = scrub_event(event)
        serialised = json.dumps(scrubbed)
        assert scrubbed["exception"]["values"][0]["type"] == "ValueError"
        assert scrubbed["exception"]["values"][0]["value"] == ""
        assert "vars" not in scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert "vars" not in scrubbed["threads"]["values"][0]["stacktrace"]["frames"][0]
        assert "sk-ant-SENSITIVE" not in serialised
        assert "r8_SECRET" not in serialised
        assert "private note" not in serialised


class TestCeleryLoggingHandoff:
    """The generation worker must keep Django's JSON/correlation logging config;
    Celery's default root-logger hijack would strip it, leaving worker logs
    unstructured and without request_id/attempt_id."""

    def test_worker_does_not_hijack_or_redirect_logging(self):
        from django.conf import settings

        assert settings.CELERY_WORKER_HIJACK_ROOT_LOGGER is False
        assert settings.CELERY_WORKER_REDIRECT_STDOUTS is False
