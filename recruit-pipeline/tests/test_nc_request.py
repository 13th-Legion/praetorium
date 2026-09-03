"""nc_request() / nc_api() — the single HTTP helper.

Incident 2026-08-04..09-03: every Deck attachment upload 400'd for a month
with an EMPTY body. nc_api() called resp.raise_for_status(), which discards
the body, so the only signal anywhere was a status code nobody logged.

These tests pin the two properties that would have caught it:
  * a non-2xx ALWAYS logs a truncated body
  * an empty body is reported as '<empty body>', not as nothing at all
"""

import logging

import pytest

from conftest import FakeResponse


# ─── Body logging (incident 2 guard) ─────────────────────────────────────────

def test_non_2xx_logs_the_response_body(daemon, http, caplog):
    http.always(FakeResponse(400, b'{"message":"data is required"}'))
    with caplog.at_level(logging.WARNING):
        daemon.nc_request("POST", "/index.php/apps/deck/x")
    assert "400" in caplog.text
    assert "data is required" in caplog.text, "the body must reach the log"


def test_empty_error_body_is_reported_explicitly(daemon, http, caplog):
    """The exact Deck failure mode: bare 400, zero bytes of body.

    '<empty body>' is itself the diagnostic -- it means Nextcloud's
    AppFramework rejected the request before the controller ran.
    """
    http.always(FakeResponse(400, b""))
    with caplog.at_level(logging.WARNING):
        daemon.nc_request("POST", "/index.php/apps/deck/x")
    assert "<empty body>" in caplog.text


def test_body_excerpt_truncates_long_bodies(daemon):
    resp = FakeResponse(500, b"x" * 5000)
    excerpt = daemon._body_excerpt(resp)
    assert len(excerpt) < 400
    assert "more bytes" in excerpt


def test_body_excerpt_survives_binary_and_bad_encoding(daemon):
    resp = FakeResponse(500, b"\xff\xfe\x00garbage", encoding="not-a-real-codec")
    assert isinstance(daemon._body_excerpt(resp), str)


def test_returns_raw_response_not_parsed_json(daemon, http):
    """The whole point: callers get the Response and decide for themselves."""
    http.always(FakeResponse(207, b"<xml/>"))
    resp = daemon.nc_request("PROPFIND", "/remote.php/dav/x")
    assert resp.status_code == 207
    assert resp.text == "<xml/>"


def test_2xx_logs_nothing(daemon, http, caplog):
    http.always(FakeResponse(200, b"{}"))
    with caplog.at_level(logging.WARNING):
        daemon.nc_request("GET", "/ok")
    assert caplog.text == ""


def test_expected_statuses_suppresses_the_warning(daemon, http, caplog):
    """A 404 the caller treats as a normal answer must not cry wolf."""
    http.always(FakeResponse(404, b"not found"))
    with caplog.at_level(logging.WARNING):
        daemon.nc_request("GET", "/users/nobody", expected_statuses=(404,))
    assert caplog.text == ""


# ─── Request construction ────────────────────────────────────────────────────

def test_endpoint_is_prefixed_with_nc_url(daemon, http):
    http.always(FakeResponse(200, b"{}"))
    daemon.nc_request("GET", "/ocs/v2.php/cloud/users")
    assert http.calls[0].url == f"{daemon.NC_URL}/ocs/v2.php/cloud/users"


def test_absolute_url_is_passed_through_untouched(daemon, http):
    http.always(FakeResponse(200, b"{}"))
    url = "https://cloud.13thlegion.org/remote.php/dav/files/spooky/Forms"
    daemon.nc_request("PROPFIND", url)
    assert http.calls[0].url == url


@pytest.mark.parametrize("verb", ["PROPFIND", "MKCOL", "MOVE", "COPY"])
def test_supports_non_standard_webdav_verbs(daemon, http, verb):
    http.always(FakeResponse(207, b"<xml/>"))
    daemon.nc_request(verb, "/remote.php/dav/x")
    assert http.calls[0].method == verb


def test_supports_multipart_files_and_params(daemon, http):
    http.always(FakeResponse(200, b"{}"))
    daemon.nc_request(
        "POST", "/upload",
        files={"file": ("a.pdf", b"bytes")},
        params={"format": "json"},
    )
    kw = http.calls[0].kwargs
    assert kw["files"] == {"file": ("a.pdf", b"bytes")}
    assert kw["params"] == {"format": "json"}


def test_custom_credentials_override_the_default_account(daemon, http):
    http.always(FakeResponse(200, b"{}"))
    daemon.nc_request("GET", "/x", user="portal-svc", passwd="secret")
    assert http.calls[0].kwargs["auth"] == ("portal-svc", "secret")


def test_ocs_headers_on_by_default_and_removable(daemon, http):
    http.always(FakeResponse(200, b"{}"))
    daemon.nc_request("GET", "/x")
    assert http.calls[0].kwargs["headers"]["OCS-APIRequest"] == "true"

    # WebDAV calls send no OCS headers, and a None value deletes a default.
    daemon.nc_request("PROPFIND", "/dav", ocs=False, headers={"Depth": "1"})
    assert http.calls[1].kwargs["headers"] == {"Depth": "1"}


# ─── Retry / backoff ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 520, 522, 524])
def test_retries_transient_statuses_then_succeeds(daemon, http, status):
    http.queue(FakeResponse(status, b"transient"), FakeResponse(200, b'{"ok":1}'))
    assert daemon.nc_api("GET", "/x") == {"ok": 1}
    assert len(http.calls) == 2


def test_does_not_retry_a_404(daemon, http):
    http.queue(FakeResponse(404, b"gone"))
    with pytest.raises(daemon.NCRequestError):
        daemon.nc_api("GET", "/x")
    assert len(http.calls) == 1, "404 is not transient; must fail fast"


def test_retries_connection_errors_then_reraises(daemon, http):
    http.queue(*[daemon.requests.exceptions.Timeout()] * 4)
    with pytest.raises(daemon.requests.exceptions.Timeout):
        daemon.nc_request("GET", "/x")
    assert len(http.calls) == 4, "1 try + 3 retries"


def test_retry_can_be_disabled(daemon, http):
    http.queue(FakeResponse(503, b"nope"))
    daemon.nc_request("GET", "/x", retry=False)
    assert len(http.calls) == 1


# ─── nc_api() as a thin JSON wrapper ─────────────────────────────────────────

def test_nc_api_returns_decoded_json(daemon, http):
    http.always(FakeResponse(200, json_data={"id": 42}))
    assert daemon.nc_api("GET", "/card") == {"id": 42}


def test_nc_api_raises_with_the_body_in_the_message(daemon, http):
    """requests' own HTTPError throws the body away. Ours must not."""
    http.always(FakeResponse(403, b"insufficient privileges"))
    with pytest.raises(daemon.NCRequestError) as exc:
        daemon.nc_api("GET", "/card")
    assert "insufficient privileges" in str(exc.value)
    assert exc.value.response.status_code == 403


def test_nc_api_reports_a_2xx_that_is_not_json(daemon, http):
    http.always(FakeResponse(200, b"<html>login page</html>"))
    with pytest.raises(daemon.NCRequestError) as exc:
        daemon.nc_api("GET", "/card")
    assert "not JSON" in str(exc.value)
    assert "login page" in str(exc.value)


def test_nc_api_sends_json_content_type(daemon, http):
    http.always(FakeResponse(200, b"{}"))
    daemon.nc_api("POST", "/x", json_data={"a": 1})
    assert http.calls[0].kwargs["headers"]["Content-Type"] == "application/json"
