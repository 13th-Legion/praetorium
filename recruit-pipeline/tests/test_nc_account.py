"""create_nc_account() idempotency.

INCIDENT: RCT John Garcia's onboarding retried 1,942 times over 7 days.

His first create call READ TIMED OUT at 30s *after* Nextcloud had already
created the account. The daemon treated the timeout as failure; every
subsequent retry then died on "user already exists". Two separate mistakes:

  1. "already exists" is a SUCCESS -- an earlier attempt made the account.
     NC reports it as HTTP 400 / OCS statuscode 102, so raising on HTTP
     status meant the OCS code was never even looked at.
  2. An ambiguous transport failure must ASK Nextcloud whether the account
     now exists rather than assuming it does not.
"""

import logging

import pytest

from conftest import FakeResponse, ocs_response

ARGS = ("john.garcia", "pw123", "RCT Garcia", "john@proton.me", ["13th Legion"])


# ─── Normal outcomes ─────────────────────────────────────────────────────────

def test_clean_creation(daemon, http):
    http.always(ocs_response(100))
    assert daemon.create_nc_account(*ARGS) == daemon.NC_ACCOUNT_CREATED


def test_groups_are_sent_as_repeated_form_pairs(daemon, http):
    http.always(ocs_response(100))
    daemon.create_nc_account("a.b", "pw", "RCT B", "e@x", ["13th Legion", "Rank - Recruit"])
    sent = http.calls[0].kwargs["data"]
    assert ("groups[]", "13th Legion") in sent
    assert ("groups[]", "Rank - Recruit") in sent
    assert ("userid", "a.b") in sent


def test_uses_the_provisioning_service_account(daemon, http):
    http.always(ocs_response(100))
    daemon.create_nc_account(*ARGS)
    assert http.calls[0].kwargs["auth"] == (daemon.NC_SVC_USER, daemon.NC_SVC_PASS)


# ─── "Already exists" is success, not failure ────────────────────────────────

def test_ocs_102_on_http_400_is_treated_as_success(daemon, http):
    """The exact reply that aborted onboarding 1,942 times."""
    http.always(ocs_response(102, "User already exists", http_status=400))
    assert daemon.create_nc_account(*ARGS) == daemon.NC_ACCOUNT_EXISTS


def test_already_exists_message_is_honoured_whatever_the_code(daemon, http):
    http.always(ocs_response(996, "User already exists", http_status=400))
    assert daemon.create_nc_account(*ARGS) == daemon.NC_ACCOUNT_EXISTS


def test_exists_result_is_distinguishable_from_created(daemon):
    """Callers must be able to tell them apart: EXISTS means do not mail the
    freshly generated password, because it is not the account's password."""
    assert daemon.NC_ACCOUNT_CREATED != daemon.NC_ACCOUNT_EXISTS


# ─── Timeout-then-account-exists (the actual incident) ───────────────────────

def test_timeout_then_account_exists_recovers_idempotently(daemon, http):
    """NC created the account, then the response timed out.

    4 failed POST attempts (1 + 3 retries), then the follow-up lookup finds
    the user -> EXISTS, and onboarding carries on instead of dying forever.
    """
    http.queue(
        *[daemon.requests.exceptions.Timeout()] * 4,
        ocs_response(100, data={"id": "john.garcia"}),      # nc_user_exists
    )
    assert daemon.create_nc_account(*ARGS) == daemon.NC_ACCOUNT_EXISTS
    assert http.calls[-1].method == "GET", "must ASK whether the user exists"


def test_timeout_and_account_really_absent_is_a_failure(daemon, http):
    http.queue(
        *[daemon.requests.exceptions.Timeout()] * 4,
        FakeResponse(404, b""),                              # nc_user_exists
    )
    assert daemon.create_nc_account(*ARGS) is None


def test_timeout_and_lookup_also_fails_is_a_failure(daemon, http):
    """Ambiguous twice over: never claim success we cannot prove."""
    http.queue(*[daemon.requests.exceptions.Timeout()] * 8)
    assert daemon.create_nc_account(*ARGS) is None


def test_connection_error_is_retried_before_giving_up(daemon, http):
    http.queue(
        daemon.requests.exceptions.ConnectionError(),
        ocs_response(100),
    )
    assert daemon.create_nc_account(*ARGS) == daemon.NC_ACCOUNT_CREATED


# ─── Genuine failures stay failures, and say why ─────────────────────────────

def test_a_real_error_returns_none_and_logs_status_message_and_body(daemon, http, caplog):
    http.always(ocs_response(101, "Invalid input data", http_status=400))
    with caplog.at_level(logging.ERROR):
        assert daemon.create_nc_account(*ARGS) is None
    assert "101" in caplog.text
    assert "Invalid input data" in caplog.text


def test_a_non_json_body_does_not_explode(daemon, http):
    """NC occasionally serves an HTML error page; that must not raise."""
    http.always(FakeResponse(502, b"<html>Bad Gateway</html>"))
    assert daemon.create_nc_account(*ARGS) is None


# ─── nc_user_exists / nc_user_never_logged_in ────────────────────────────────

@pytest.mark.parametrize("resp,expected", [
    (ocs_response(100, data={"id": "a.b"}), True),
    (FakeResponse(404, b""), False),
    (ocs_response(998, "not found", http_status=200), False),
])
def test_nc_user_exists(daemon, http, resp, expected):
    http.always(resp)
    assert daemon.nc_user_exists("a.b") is expected


def test_nc_user_exists_returns_none_when_it_cannot_tell(daemon, http):
    """None means 'unknown' and must never be confused with False."""
    http.always(ocs_response(997, "unauthorised", http_status=200))
    assert daemon.nc_user_exists("a.b") is None


@pytest.mark.parametrize("last_login,expected", [
    (0, True),
    (None, True),
    (1699999999, False),
])
def test_nc_user_never_logged_in(daemon, http, last_login, expected):
    http.always(ocs_response(100, data={"lastLogin": last_login}))
    assert daemon.nc_user_never_logged_in("a.b") is expected


def test_unreadable_lastlogin_is_unknown_and_is_logged(daemon, http, caplog):
    """This gates a PASSWORD RESET during recovery.

    Returning None (unknown) must be loud: silently treating 'I could not
    read the body' as 'never logged in' would clobber a password the member
    chose themselves.
    """
    http.always(FakeResponse(200, b"<html>nope</html>"))
    with caplog.at_level(logging.WARNING):
        assert daemon.nc_user_never_logged_in("a.b") is None
    assert "lastLogin" in caplog.text


# ─── set_nc_password ─────────────────────────────────────────────────────────

def test_set_nc_password_success(daemon, http):
    http.always(ocs_response(100))
    assert daemon.set_nc_password("a.b", "newpw") is True
    assert http.calls[0].method == "PUT"
    assert http.calls[0].kwargs["data"]["key"] == "password"


def test_set_nc_password_failure_logs_ocs_status_and_body(daemon, http, caplog):
    http.always(ocs_response(103, "Password too weak", http_status=400))
    with caplog.at_level(logging.ERROR):
        assert daemon.set_nc_password("a.b", "x") is False
    assert "103" in caplog.text
    assert "Password too weak" in caplog.text
