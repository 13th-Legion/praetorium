"""Shared fixtures for the recruit-pipeline daemon test suite.

The daemon is a standalone script with a hyphen in its name, so it is loaded
by path rather than imported normally.

NOTHING here touches production. Every Nextcloud call in the daemon funnels
through nc_request() -> requests.request(), so patching that single seam is
enough to intercept all HTTP. The `no_network` fixture is autouse and makes
any unmocked call fail loudly rather than escape to cloud.13thlegion.org.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

DAEMON_PATH = Path(__file__).resolve().parent.parent / "recruit-daemon.py"

# Set env BEFORE the module is executed: it reads these at import time.
os.environ.setdefault("RECRUIT_LOG_DIR", "/tmp/recruit-daemon-tests")
os.environ.setdefault("RECRUIT_STATE_FILE", "/tmp/recruit-daemon-tests/state.json")
os.environ.setdefault("NC_SVC_PASS", "test-nc-pass")
os.environ.setdefault("NC_PORTAL_SVC_PASS", "test-portal-svc-pass")
os.environ.setdefault("SMTP_PASS", "test-smtp-pass")
os.environ.setdefault("POSTGRES_PASSWORD", "test-db-pass")
os.environ.setdefault("KUMA_PUSH_URL", "http://kuma.invalid/push/test?msg=")


# ─── Fake HTTP ───────────────────────────────────────────────────────────────

class FakeResponse:
    """Minimal stand-in for requests.Response.

    Implements exactly the surface nc_request() and its callers use:
    status_code, content, encoding, text, json().
    """

    def __init__(self, status_code=200, body=b"", json_data=None, encoding="utf-8"):
        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.status_code = status_code
        self.content = body
        self.encoding = encoding
        self.headers = {}

    @property
    def text(self):
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)

    def __repr__(self):                                    # pragma: no cover
        return f"<FakeResponse {self.status_code} {self.content[:40]!r}>"


def ocs_response(statuscode=100, message="", data=None, http_status=None):
    """An OCS-shaped FakeResponse.

    Nextcloud reports the *real* outcome in ocs.meta.statuscode, which is why
    the daemon must never decide anything from the HTTP status alone --
    'user already exists' arrives as HTTP 400 / OCS 102.
    """
    if http_status is None:
        http_status = 200 if statuscode in (100, 200) else 400
    return FakeResponse(
        http_status,
        json_data={"ocs": {"meta": {"statuscode": statuscode, "message": message},
                           "data": data if data is not None else {}}},
    )


def propfind_xml(*hrefs):
    """A WebDAV 207 multistatus body containing the given hrefs."""
    items = "".join(
        f"<d:response><d:href>{h}</d:href>"
        f"<d:propstat><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
        f"</d:response>"
        for h in hrefs
    )
    return f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{items}</d:multistatus>'


def propfind_response(*hrefs):
    return FakeResponse(207, propfind_xml(*hrefs))


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def daemon():
    """The loaded recruit-daemon module."""
    spec = importlib.util.spec_from_file_location("recruit_daemon", DAEMON_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["recruit_daemon"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def no_network(daemon, monkeypatch):
    """Fail loudly on any HTTP call a test did not explicitly mock.

    Guarantees the suite can never reach production Nextcloud.
    """
    def _boom(*a, **kw):
        raise AssertionError(
            f"unmocked HTTP call escaped the test: {a[:2]} "
            f"(mock daemon.requests.request in your test)"
        )
    monkeypatch.setattr(daemon.requests, "request", _boom)
    monkeypatch.setattr(daemon.requests, "get", _boom)
    monkeypatch.setattr(daemon.requests, "post", _boom)


@pytest.fixture(autouse=True)
def no_sleep(daemon, monkeypatch):
    """Make retry/backoff instant so tests don't wait 2+4+8 seconds."""
    monkeypatch.setattr(daemon.time, "sleep", lambda *_a, **_kw: None)


@pytest.fixture
def http(daemon, monkeypatch):
    """Patch the single HTTP seam. Returns a recorder you drive per test.

    Usage:
        http.queue(FakeResponse(200))                 # sequence of responses
        http.always(FakeResponse(200))                # same response every time
        http.calls[0].kwargs["data"]                  # inspect what was sent
    """
    class Recorder:
        def __init__(self):
            self.calls = []
            self._queue = None
            self._always = None

        def queue(self, *responses):
            self._queue = list(responses)

        def always(self, response):
            self._always = response

        def __call__(self, method, url, **kwargs):
            import types
            call = types.SimpleNamespace(method=method, url=url, kwargs=kwargs)
            self.calls.append(call)
            if self._queue is not None:
                if not self._queue:
                    raise AssertionError(
                        f"daemon made more HTTP calls than the test queued; "
                        f"extra call: {method} {url}"
                    )
                nxt = self._queue.pop(0)
            elif self._always is not None:
                nxt = self._always
            else:
                raise AssertionError("test used `http` without queue()/always()")
            if isinstance(nxt, Exception) or (
                isinstance(nxt, type) and issubclass(nxt, Exception)
            ):
                raise nxt
            return nxt

        # convenience accessors
        def methods(self):
            return [c.method for c in self.calls]

        def urls(self):
            return [c.url for c in self.calls]

    rec = Recorder()
    monkeypatch.setattr(daemon.requests, "request", rec)
    return rec
