import threading

import pytest

from bottles.backend.params import APP_VERSION
from bottles.backend.utils import httpclient
from bottles.tests.backend.localserver import STALL, serve

SHORT = (0.5, 0.3)  # (connect, read): shorter than the server's stall


@pytest.fixture(autouse=True)
def _fresh_session(monkeypatch):
    # each test gets its own shared session so proxy/env changes cannot leak
    monkeypatch.setattr(httpclient, "_shared", None)


def test_fetch_returns_body_follows_redirects_and_identifies_bottles():
    script = {"/start": ("redirect", "/final"), "/final": (200, b"payload")}
    with serve(script) as (server, base):
        assert httpclient.fetch(f"{base}/start", timeout=SHORT) == b"payload"

    paths = [path for _, path, _ in server.requests]
    assert paths == ["/start", "/final"]
    assert server.requests[0][2]["user-agent"] == f"Bottles/{APP_VERSION}"


def test_fetch_raises_a_status_error_with_the_code():
    with serve({"/x": 403}) as (_server, base):
        with pytest.raises(httpclient.HttpStatusError) as excinfo:
            httpclient.fetch(f"{base}/x", timeout=SHORT)
    assert excinfo.value.response.status_code == 403
    assert isinstance(excinfo.value, httpclient.HttpError)


def test_fetch_times_out_against_a_stalled_server():
    with serve({"/slow": STALL}) as (_server, base):
        with pytest.raises(httpclient.HttpError):
            httpclient.fetch(f"{base}/slow", timeout=SHORT)


def test_fetch_enforces_the_size_cap():
    with serve({"/big": (200, b"x" * 10_000)}) as (_server, base):
        with pytest.raises(ValueError):
            httpclient.fetch(f"{base}/big", timeout=SHORT, max_bytes=1_000)
        assert len(httpclient.fetch(f"{base}/big", timeout=SHORT, max_bytes=10_000)) == 10_000


def test_fetch_stops_when_cancelled_between_chunks():
    # larger than CHUNK_SIZE so the body arrives over several reads
    chunks = [b"a" * 100_000, b"b" * 100_000, b"c" * 100_000]
    seen = []
    stop = threading.Event()

    def cancel():
        if seen:
            stop.set()
        seen.append(True)
        return stop.is_set()

    with serve({"/stream": ("stream", chunks, 0.05)}) as (_server, base):
        with pytest.raises(httpclient.Cancelled):
            httpclient.fetch(f"{base}/stream", timeout=SHORT, cancel=cancel)


def test_fetch_serves_file_urls_from_disk(tmp_path):
    source = tmp_path / "index.yml"
    source.write_bytes(b"entry: value\n")
    assert httpclient.fetch(source.as_uri(), timeout=SHORT) == b"entry: value\n"
    with pytest.raises(OSError):
        httpclient.fetch((tmp_path / "absent.yml").as_uri(), timeout=SHORT)
    with pytest.raises(ValueError):
        httpclient.fetch(source.as_uri(), timeout=SHORT, max_bytes=4)


def test_head_ok_reports_only_a_final_200():
    script = {"/ok": 200, "/missing": 404, "/moved": ("redirect", "/ok")}
    with serve(script) as (server, base):
        assert httpclient.head_ok(f"{base}/ok", timeout=SHORT) is True
        assert httpclient.head_ok(f"{base}/missing", timeout=SHORT) is False
        assert httpclient.head_ok(f"{base}/moved", timeout=SHORT) is True
        with pytest.raises(httpclient.Cancelled):
            httpclient.head_ok(f"{base}/ok", timeout=SHORT, cancel=lambda: True)
    assert all(method == "HEAD" for method, _, _ in server.requests)


def test_head_ok_times_out_against_a_stalled_server():
    with serve({"/slow": STALL}) as (_server, base):
        with pytest.raises(httpclient.HttpError):
            httpclient.head_ok(f"{base}/slow", timeout=SHORT)


def test_proxy_environment_is_honoured(monkeypatch):
    # an HTTP proxy receives the absolute URL as the request target
    target = "http://example.invalid/catalog.yml"
    with serve({target: (200, b"via proxy")}) as (proxy, base):
        for name in ("HTTP_PROXY", "http_proxy"):
            monkeypatch.setenv(name, base)
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        assert httpclient.fetch(target, timeout=SHORT) == b"via proxy"
    assert proxy.requests[0][1] == target


def test_ca_bundle_prefers_the_system_store(monkeypatch, tmp_path):
    bundle = tmp_path / "ca-certificates.crt"
    bundle.write_text("")
    monkeypatch.setattr(httpclient, "SYSTEM_CA_BUNDLE", str(bundle))
    assert httpclient.ca_bundle() == str(bundle)
    assert httpclient.new_session().verify == str(bundle)

    monkeypatch.setattr(httpclient, "SYSTEM_CA_BUNDLE", str(tmp_path / "absent"))
    assert httpclient.ca_bundle() is True
    assert httpclient.new_session().verify is True


def test_sessions_carry_the_user_agent_and_the_shared_one_is_reused():
    assert httpclient.new_session().headers["User-Agent"] == f"Bottles/{APP_VERSION}"
    assert httpclient.session() is httpclient.session()
