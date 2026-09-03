"""Startup / periodic integration self-check.

All four production incidents were detectable by probing the integration and
none were detected -- the Deck attachment 400s ran for a month, the applicant
file archive was broken for six. These probes are the tripwire.

Alerting is deliberately transition-based: one DM when something breaks, one
when it recovers, silence in between. An hourly alarm gets muted, and a muted
alarm is the same as no alarm.
"""

import json
import logging

import pytest

from conftest import FakeResponse, ocs_response, propfind_response

STACKS_OK = [{"id": i} for i in (11, 12, 13, 14, 15, 16)]
FORMS_OK = {"ocs": {"data": {"submissions": [{"id": 1}, {"id": 2}]}}}


def _all_healthy(daemon, http):
    http.queue(
        FakeResponse(200, json_data=STACKS_OK),                       # deck
        FakeResponse(200, json_data=FORMS_OK),                        # forms api
        propfind_response("/dav/Forms/3%20-%20x/", "/dav/Forms/3%20-%20x/1/"),
        ocs_response(100, data={"users": ["a"]}),                     # provisioning
    )


# ─── Individual probes ───────────────────────────────────────────────────────

def test_all_probes_pass_against_a_healthy_server(daemon, http):
    _all_healthy(daemon, http)
    results = daemon.self_check()
    assert [name for name, ok, _ in results if not ok] == []
    assert {name for name, _, _ in results} == {
        "deck_api", "forms_api", "forms_storage", "provisioning_api"}


def test_deck_probe_catches_a_missing_stack(daemon, http):
    """Someone deletes or renumbers a stack and onboarding silently stops."""
    http.always(FakeResponse(200, json_data=[{"id": 11}, {"id": 12}]))
    ok, detail = daemon._check_deck_api()
    assert ok is False
    assert "missing configured stacks" in detail
    assert "approved" in detail


def test_deck_probe_reports_the_body_on_an_error(daemon, http):
    http.always(FakeResponse(403, b"not permitted"))
    ok, detail = daemon._check_deck_api()
    assert ok is False
    assert "403" in detail and "not permitted" in detail


def test_forms_storage_probe_catches_the_404_that_hid_for_six_months(daemon, http):
    http.always(FakeResponse(404, b""))
    ok, detail = daemon._check_forms_storage()
    assert ok is False
    assert "NOT FOUND" in detail
    assert "3%20-%20" in detail, "the failing path must be in the alert"


def test_forms_storage_probe_uses_the_canonical_builder(daemon, http):
    http.always(propfind_response("/dav/x/"))
    daemon._check_forms_storage()
    assert http.calls[0].url == daemon.forms_root_url()
    assert http.calls[0].method == "PROPFIND"


def test_provisioning_probe_catches_a_bad_service_password(daemon, http):
    http.always(ocs_response(997, "Unauthorised", http_status=401))
    ok, detail = daemon._check_provisioning_api()
    assert ok is False
    assert "997" in detail or "401" in detail


def test_provisioning_probe_uses_the_service_account(daemon, http):
    http.always(ocs_response(100))
    daemon._check_provisioning_api()
    assert http.calls[0].kwargs["auth"] == (daemon.NC_SVC_USER, daemon.NC_SVC_PASS)


def test_forms_api_probe_catches_an_unexpected_shape(daemon, http):
    http.always(FakeResponse(200, json_data={"ocs": {"data": {}}}))
    ok, detail = daemon._check_forms_api()
    assert ok is False
    assert "unexpected shape" in detail


def test_probes_do_not_retry_so_a_check_stays_fast(daemon, http):
    http.always(FakeResponse(503, b"down"))
    daemon._check_deck_api()
    assert len(http.calls) == 1


def test_a_raising_probe_is_reported_not_propagated(daemon, http, monkeypatch):
    """A broken probe must never take down the poll loop."""
    monkeypatch.setattr(daemon, "SELF_CHECKS", (("boom", lambda: 1 / 0),))
    results = daemon.self_check()
    assert results[0][0] == "boom"
    assert results[0][1] is False
    assert "ZeroDivisionError" in results[0][2]


# ─── Alert transitions ───────────────────────────────────────────────────────

@pytest.fixture
def discord(daemon, monkeypatch):
    sent = []
    monkeypatch.setattr(daemon, "notify_discord", lambda text: sent.append(text) or True)
    return sent


def test_alerts_on_a_new_failure(daemon, http, discord):
    http.always(FakeResponse(500, b"boom"))
    state = {}
    assert daemon.run_self_check(state) is False
    assert len(discord) == 1
    assert "self-check FAILED" in discord[0]
    assert state["self_check_failing"]


def test_does_not_re_alert_while_the_same_thing_stays_broken(daemon, http, discord):
    """One DM per outage, not one per hour."""
    state = {}
    for _ in range(5):
        http.always(FakeResponse(500, b"boom"))
        daemon.run_self_check(state)
    assert len(discord) == 1


def test_alerts_again_when_a_different_check_starts_failing(daemon, http, discord):
    state = {"self_check_failing": ["deck_api"]}
    http.always(FakeResponse(500, b"boom"))
    daemon.run_self_check(state)
    assert len(discord) == 1, "the failure set changed, so re-alert"


def test_sends_a_recovery_notice(daemon, http, discord):
    state = {"self_check_failing": ["deck_api", "forms_storage"]}
    _all_healthy(daemon, http)
    assert daemon.run_self_check(state) is True
    assert len(discord) == 1
    assert "recovered" in discord[0]
    assert state["self_check_failing"] == []


def test_stays_silent_when_healthy_and_previously_healthy(daemon, http, discord):
    _all_healthy(daemon, http)
    assert daemon.run_self_check({}) is True
    assert discord == []


def test_dry_run_never_sends_a_discord_alert(daemon, http, discord):
    """--dry-run must not page anyone."""
    http.always(FakeResponse(500, b"boom"))
    daemon.run_self_check({}, dry_run=True)
    assert discord == []


def test_dry_run_does_not_touch_onboarding_state(daemon, http, discord):
    """STANDING RULE: a dry run must never mark a recruit as handled."""
    http.always(FakeResponse(500, b"boom"))
    state = {"onboarded_cards": [1, 2], "welcome_emailed_cards": [1]}
    daemon.run_self_check(state, dry_run=True)
    assert state["onboarded_cards"] == [1, 2]
    assert state["welcome_emailed_cards"] == [1]


def test_records_the_timestamp_so_the_interval_advances(daemon, http, discord):
    _all_healthy(daemon, http)
    state = {}
    daemon.run_self_check(state)
    assert state["last_self_check"] > 0


# ─── Discord token handling ──────────────────────────────────────────────────

def test_token_is_read_from_the_existing_audit_script(daemon, tmp_path, monkeypatch):
    """One copy of the secret on the box, not two."""
    script = tmp_path / "spooky-bot-audit.sh"
    script.write_text(
        '#!/bin/bash\nBOT=2\nDISCORD_BOT_TOKEN="tok.en.value"\nCAV_DISCORD_ID="1"\n'
    )
    monkeypatch.setattr(daemon, "AUDIT_SCRIPT", str(script))
    assert daemon._discord_token() == "tok.en.value"


def test_missing_audit_script_is_logged_not_fatal(daemon, monkeypatch, caplog):
    monkeypatch.setattr(daemon, "AUDIT_SCRIPT", "/nonexistent/nope.sh")
    with caplog.at_level(logging.WARNING):
        assert daemon._discord_token() == ""
    assert "Could not read Discord token" in caplog.text


def test_no_token_means_the_undelivered_alert_is_logged_as_an_error(daemon, monkeypatch, caplog):
    """A silent failure to alert is worse than the thing being alerted about."""
    monkeypatch.setattr(daemon, "_discord_token", lambda: "")
    with caplog.at_level(logging.ERROR):
        assert daemon.notify_discord("hello") is False
    assert "NOT delivered" in caplog.text


def test_the_token_never_appears_in_the_log(daemon, tmp_path, monkeypatch, caplog):
    script = tmp_path / "audit.sh"
    script.write_text('DISCORD_BOT_TOKEN="super.secret.token"\n')
    monkeypatch.setattr(daemon, "AUDIT_SCRIPT", str(script))
    with caplog.at_level(logging.DEBUG):
        daemon._discord_token()
    assert "super.secret.token" not in caplog.text
