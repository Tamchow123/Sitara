"""Output-download boundary tests (Phase 10 Part B) — no real network.

The httpx client is stubbed so URL validation, redirect re-validation and the
byte cap are exercised deterministically. A real socket connection would be a
bug (guarded below).
"""

import socket

import httpx
import pytest

from sitara.generation.image_download import ImageDownloadError, download_replicate_output


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("network access attempted during download tests")

    monkeypatch.setattr(socket.socket, "connect", guard)


class _FakeStream:
    def __init__(self, *, status=200, chunks=(b"",), location=None):
        self.status_code = status
        self._chunks = list(chunks)
        self.headers = {"location": location} if location else {}

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    def iter_bytes(self):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeClient:
    """A stand-in for httpx.Client that returns scripted stream responses keyed
    by the number of GET calls (to model a redirect chain)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requested_urls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url):
        self.requested_urls.append(url)
        return self._responses.pop(0)


def _install(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    return client


VALID = "https://replicate.delivery/abc/raw.webp"


class TestUrlValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "http://replicate.delivery/x/raw.webp",  # non-https
            "https://evil.com/x/raw.webp",  # wrong host
            "https://replicate.delivery.evil.com/x",  # suffix trick
            "https://user:pass@replicate.delivery/x",  # embedded credentials
            "https://replicate.delivery-evil.com/x",  # not a subdomain
        ],
    )
    def test_disallowed_urls_are_rejected_before_any_request(self, monkeypatch, url):
        client = _install(monkeypatch, [])
        with pytest.raises(ImageDownloadError):
            download_replicate_output(url, max_bytes=1000, timeout_seconds=5)
        assert client.requested_urls == []  # never fetched

    def test_subdomain_is_allowed(self, monkeypatch):
        _install(monkeypatch, [_FakeStream(chunks=[b"img"])])
        data = download_replicate_output(
            "https://pbxt.replicate.delivery/x/raw.webp", max_bytes=1000, timeout_seconds=5
        )
        assert data == b"img"


class TestDownload:
    def test_successful_download_returns_bytes(self, monkeypatch):
        _install(monkeypatch, [_FakeStream(chunks=[b"abc", b"def"])])
        data = download_replicate_output(VALID, max_bytes=1000, timeout_seconds=5)
        assert data == b"abcdef"

    def test_byte_cap_aborts_streaming(self, monkeypatch):
        _install(monkeypatch, [_FakeStream(chunks=[b"a" * 600, b"b" * 600])])
        with pytest.raises(ImageDownloadError):
            download_replicate_output(VALID, max_bytes=1000, timeout_seconds=5)

    def test_non_200_is_rejected(self, monkeypatch):
        _install(monkeypatch, [_FakeStream(status=404, chunks=[b""])])
        with pytest.raises(ImageDownloadError):
            download_replicate_output(VALID, max_bytes=1000, timeout_seconds=5)

    def test_empty_body_is_rejected(self, monkeypatch):
        _install(monkeypatch, [_FakeStream(chunks=[b""])])
        with pytest.raises(ImageDownloadError):
            download_replicate_output(VALID, max_bytes=1000, timeout_seconds=5)

    def test_redirect_to_allowed_host_is_followed(self, monkeypatch):
        client = _install(
            monkeypatch,
            [
                _FakeStream(status=302, location="https://pbxt.replicate.delivery/y/raw.webp"),
                _FakeStream(chunks=[b"ok"]),
            ],
        )
        data = download_replicate_output(VALID, max_bytes=1000, timeout_seconds=5)
        assert data == b"ok"
        assert len(client.requested_urls) == 2

    def test_redirect_to_disallowed_host_is_rejected(self, monkeypatch):
        _install(
            monkeypatch,
            [_FakeStream(status=302, location="https://evil.com/steal")],
        )
        with pytest.raises(ImageDownloadError):
            download_replicate_output(VALID, max_bytes=1000, timeout_seconds=5)

    def test_transport_error_is_wrapped_without_leaking(self, monkeypatch):
        def boom(**kwargs):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr(httpx, "Client", boom)
        with pytest.raises(ImageDownloadError) as exc:
            download_replicate_output(VALID, max_bytes=1000, timeout_seconds=5)
        assert "replicate.delivery" not in str(exc.value)
        assert VALID not in str(exc.value)

    def test_soft_time_limit_mid_stream_propagates_not_wrapped(self, monkeypatch):
        # A worker interruption while streaming must PROPAGATE from this
        # module (the pipeline retries it) — never be wrapped into
        # ImageDownloadError("transport_error"), which the pipeline would
        # classify as a terminal image_download_failed.
        from celery.exceptions import SoftTimeLimitExceeded

        class _InterruptedStream(_FakeStream):
            def iter_bytes(self):
                raise SoftTimeLimitExceeded()

        _install(monkeypatch, [_InterruptedStream()])
        with pytest.raises(SoftTimeLimitExceeded):
            download_replicate_output(VALID, max_bytes=1000, timeout_seconds=5)
