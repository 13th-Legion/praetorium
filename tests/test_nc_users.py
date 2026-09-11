"""Nextcloud account email sync (`app.services.nc_users`).

The bug this guards against: a member's email lives in the roster AND on their
Nextcloud account, nothing kept them in step, and editing it in the portal only
wrote the roster. PFC Pope's portal address was correct the whole time while
Nextcloud hard-bounced digests off a stale one for days.

The other trap covered here: **the OCS provisioning API returns HTTP 200 even
when the operation fails** -- the real result is the statuscode in the body. A
sync that only checks resp.status_code reports success while having changed
nothing, which is worse than failing, because the drift then looks fixed.
"""

import pytest

from app.services import nc_users

pytestmark = pytest.mark.unit


class _Resp:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _ocs(statuscode, data=None):
    return {"ocs": {"meta": {"statuscode": statuscode, "message": "x"},
                    "data": data if data is not None else []}}


class _Client:
    """Async context-manager double recording the calls made."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    async def put(self, url, **kw):
        self.calls.append(("PUT", url, kw.get("data")))
        if self._exc:
            raise self._exc
        return self._response

    async def get(self, url, **kw):
        self.calls.append(("GET", url, None))
        if self._exc:
            raise self._exc
        return self._response


# ─── ocs_status: the 200-but-failed trap ──────────────────────────────────────

def test_ocs_status_reads_json():
    assert nc_users.ocs_status(_Resp(200, _ocs(100))) == 100


def test_ocs_status_falls_back_to_xml():
    xml = "<?xml version='1.0'?><ocs><meta><statuscode>997</statuscode></meta></ocs>"
    assert nc_users.ocs_status(_Resp(200, None, xml)) == 997


def test_ocs_status_none_when_unreadable():
    assert nc_users.ocs_status(_Resp(200, None, "<html>proxy error</html>")) is None


# ─── set_email ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_email_success():
    c = _Client(_Resp(200, _ocs(100)))
    ok, detail = await nc_users.set_email("zachary.pope", "z@proton.me", client=c)
    assert ok is True and detail == "ok"
    method, url, data = c.calls[0]
    assert method == "PUT"
    assert url.endswith("/ocs/v2.php/cloud/users/zachary.pope")
    assert data == {"key": "email", "value": "z@proton.me"}


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [101, 102, 997, 998])
async def test_set_email_rejects_http200_with_failure_ocs_code(code):
    """HTTP 200 + a failure statuscode must NOT be reported as success."""
    c = _Client(_Resp(200, _ocs(code)))
    ok, detail = await nc_users.set_email("someone", "a@b.test", client=c)
    assert ok is False
    assert str(code) in detail


@pytest.mark.asyncio
async def test_set_email_unreadable_body_is_a_failure():
    c = _Client(_Resp(200, None, "<html>gateway</html>"))
    ok, detail = await nc_users.set_email("someone", "a@b.test", client=c)
    assert ok is False and "unknown" in detail.lower()


@pytest.mark.asyncio
async def test_set_email_non_200_is_a_failure():
    c = _Client(_Resp(502, None, "bad gateway"))
    ok, detail = await nc_users.set_email("someone", "a@b.test", client=c)
    assert ok is False and "502" in detail


@pytest.mark.asyncio
async def test_set_email_never_raises_on_transport_error():
    """Portal saves must survive a Nextcloud outage."""
    c = _Client(exc=RuntimeError("connection refused"))
    ok, detail = await nc_users.set_email("someone", "a@b.test", client=c)
    assert ok is False and "connection refused" in detail


@pytest.mark.asyncio
@pytest.mark.parametrize("user,email", [("", "a@b.test"), ("someone", "")])
async def test_set_email_requires_both_fields(user, email):
    c = _Client(_Resp(200, _ocs(100)))
    ok, _ = await nc_users.set_email(user, email, client=c)
    assert ok is False
    assert c.calls == [], "must not call Nextcloud with missing input"


# ─── get_email ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_email_returns_address():
    c = _Client(_Resp(200, _ocs(100, {"email": "z@proton.me"})))
    assert await nc_users.get_email("zachary.pope", client=c) == "z@proton.me"


@pytest.mark.asyncio
async def test_get_email_none_on_failure():
    c = _Client(_Resp(200, _ocs(998)))
    assert await nc_users.get_email("ghost", client=c) is None


# ─── emails_differ: case-insensitive on purpose ───────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("CC082954@proton.me", "cc082954@proton.me"),
    ("  Same@Proton.me ", "same@proton.me"),
    ("x@y.test", "x@y.test"),
])
def test_capitalisation_is_not_drift(a, b):
    """11 of the 17 audit 'mismatches' were pure capitalisation. Same mailbox."""
    assert nc_users.emails_differ(a, b) is False


@pytest.mark.parametrize("a,b", [
    ("chaplain-13th@protonmail.com", "chaplain-13th@proton.me"),   # different domain
    ("kyleconrey@yahoo.com", "cerberus1979@proton.me"),            # different mailbox
    ("zpope49@proton.me", "zpope49@protonmail.com"),               # the Pope case
    ("a@b.test", ""),
])
def test_real_differences_are_drift(a, b):
    assert nc_users.emails_differ(a, b) is True
