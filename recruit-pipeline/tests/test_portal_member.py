"""create_portal_member() return contract + onboarding abort behaviour.

A recruit holding an NC account and a welcome email but NO roster row is worse
than a clean failure: they are live in Nextcloud, invisible to S1, and nothing
retries them. So a portal insert failure aborts onboarding (2026-09-03).

That abort is only safe because "row already exists" is reported DISTINCTLY
from "insert failed". The function used to return a bare False for both, plus
for a missing psycopg2 -- so aborting on False would have made every RETRY
abort permanently, since the second attempt always lands on
ON CONFLICT (nc_username) DO NOTHING and gets no row back. That is the same
ambiguous-return bug that stranded RCT Garcia for 7 days, which is exactly why
these tests exist.
"""

import pytest

INFO = {
    "Legal Name": "Jane Doe",
    "Email": "jane@example.com",
    "\U0001F4E7 Proton Mail": "jane@proton.me",
    "Address": "1 Main St, Dallas, TX 75001",
    "City": "Dallas",
}


# ─── Fake psycopg2 ───────────────────────────────────────────────────────────

class FakeCursor:
    """Returns a queued result per fetchone() call, in order."""

    def __init__(self, fetch_results, fail_on_execute=False):
        self._results = list(fetch_results)
        self.fail_on_execute = fail_on_execute
        self.executed = []

    def execute(self, sql, params=None):
        if self.fail_on_execute:
            raise RuntimeError("connection reset by peer")
        self.executed.append((" ".join(sql.split())[:60], params))

    def fetchone(self):
        return self._results.pop(0) if self._results else None

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        pass


@pytest.fixture
def portal_db(daemon, monkeypatch):
    """Install a fake psycopg2 whose connect() yields a scripted cursor."""
    import types

    def _install(fetch_results, fail_on_execute=False, raise_on_connect=None):
        cur = FakeCursor(fetch_results, fail_on_execute=fail_on_execute)
        conn = FakeConn(cur)

        fake = types.ModuleType("psycopg2")

        def connect(**kwargs):
            if raise_on_connect:
                raise raise_on_connect
            return conn

        fake.connect = connect
        monkeypatch.setitem(__import__("sys").modules, "psycopg2", fake)
        monkeypatch.setattr(daemon, "geocode_address", lambda a: (32.8, -96.8))
        monkeypatch.setattr(daemon, "_resolve_portal_db_host", lambda: "127.0.0.1")
        return cur

    return _install


# ─── Return contract ─────────────────────────────────────────────────────────

def test_insert_returning_row_reports_created(daemon, portal_db):
    # next_seq lookup, then the INSERT ... RETURNING id
    portal_db([(105,), (999,)])
    assert daemon.create_portal_member(INFO, "jane.doe", "Bravo") == \
        daemon.PORTAL_MEMBER_CREATED


def test_conflict_with_existing_row_reports_exists_not_failure(daemon, portal_db):
    """THE regression guard: a re-run must not look like a failure."""
    # next_seq, INSERT returns nothing (conflict), confirming SELECT finds the row
    portal_db([(105,), None, (42,)])
    result = daemon.create_portal_member(INFO, "jane.doe", "Bravo")
    assert result == daemon.PORTAL_MEMBER_EXISTS
    assert result is not None, "an existing row must never read as failure"


def test_no_row_and_no_existing_row_is_a_failure(daemon, portal_db):
    # next_seq, INSERT returns nothing, confirming SELECT ALSO finds nothing
    portal_db([(105,), None, None])
    assert daemon.create_portal_member(INFO, "jane.doe", "Bravo") is None


def test_db_exception_is_a_failure(daemon, portal_db):
    portal_db([(105,)], fail_on_execute=True)
    assert daemon.create_portal_member(INFO, "jane.doe", "Bravo") is None


def test_conflict_path_issues_a_confirming_select(daemon, portal_db):
    """Existence is verified against the DB, not assumed from 'no row'."""
    cur = portal_db([(105,), None, (42,)])
    daemon.create_portal_member(INFO, "jane.doe", "Bravo")
    selects = [sql for sql, _ in cur.executed if sql.upper().startswith("SELECT ID FROM MEMBERS")]
    assert selects, f"expected a confirming SELECT, got {cur.executed}"


# ─── Onboarding abort behaviour ──────────────────────────────────────────────

@pytest.fixture
def onboard_harness(daemon, monkeypatch):
    """Stub every side effect around the portal insert so we can assert on it."""
    calls = {"welcome": [], "moved": [], "notified": [], "archived": []}

    monkeypatch.setattr(daemon, "create_nc_account",
                        lambda *a, **k: daemon.NC_ACCOUNT_CREATED)
    monkeypatch.setattr(daemon, "geocode_address", lambda a: (32.8, -96.8))
    monkeypatch.setattr(daemon, "geo_assign_team", lambda lat, lon: ("Bravo", 66.4))
    monkeypatch.setattr(daemon, "add_to_nc_groups", lambda *a, **k: True)
    monkeypatch.setattr(daemon, "add_map_pin", lambda *a, **k: True)
    monkeypatch.setattr(daemon, "archive_applicant_files",
                        lambda *a, **k: calls["archived"].append(a) or True)
    monkeypatch.setattr(daemon, "send_welcome_email",
                        lambda *a, **k: calls["welcome"].append(a) or True)
    monkeypatch.setattr(daemon, "move_card_to_stack",
                        lambda *a, **k: calls["moved"].append(a) or True)
    monkeypatch.setattr(daemon, "notify_s1_nctalk",
                        lambda msg, **k: calls["notified"].append(msg) or True)
    monkeypatch.setattr(daemon, "nc_api", lambda *a, **k: {})
    return calls


# parse_card_for_onboarding() only reads lines shaped `**Key:** value`, so the
# Proton Mail line needs the bold markers. (Submission ID deliberately uses
# SINGLE asterisks in real cards, which is why parse_submission_id() is a
# separate helper -- the onboarding parser cannot see it.)
CARD = {
    "id": 7777,
    "title": "\U0001f4cb Jane Doe",
    "description": (
        "**Legal Name:** Jane Doe\n"
        "**Email:** jane@example.com\n"
        "**\U0001F4E7 Proton Mail:** jane@proton.me\n"
        "**Address:** 1 Main St, Dallas, TX 75001\n"
        "**City:** Dallas\n"
        "*Submission ID:* 200"
    ),
}


def _state():
    return {"processed_submissions": [], "onboarded_cards": [], "last_check": 0}


def test_portal_failure_aborts_before_sending_credentials(daemon, monkeypatch, onboard_harness):
    monkeypatch.setattr(daemon, "create_portal_member", lambda *a, **k: None)
    st = _state()

    assert daemon._onboard_member(CARD, st) is False
    assert onboard_harness["welcome"] == [], "must NOT email credentials after aborting"
    assert onboard_harness["moved"] == [], "must NOT advance the card after aborting"
    assert CARD["id"] not in st.get("onboarded_cards", []), \
        "must stay retryable — not marked onboarded"
    assert onboard_harness["notified"], "S1 must be told the onboarding aborted"


def test_portal_exists_still_completes_onboarding(daemon, monkeypatch, onboard_harness):
    """A retry whose roster row is already present must finish, not abort."""
    monkeypatch.setattr(daemon, "create_portal_member",
                        lambda *a, **k: daemon.PORTAL_MEMBER_EXISTS)
    st = _state()

    assert daemon._onboard_member(CARD, st) is True
    assert onboard_harness["welcome"], "existing roster row must not block the welcome email"
    assert onboard_harness["moved"], "existing roster row must not block the card move"


def test_portal_created_completes_onboarding(daemon, monkeypatch, onboard_harness):
    monkeypatch.setattr(daemon, "create_portal_member",
                        lambda *a, **k: daemon.PORTAL_MEMBER_CREATED)
    st = _state()
    assert daemon._onboard_member(CARD, st) is True
    assert onboard_harness["welcome"]


def test_abort_feeds_the_dead_letter_bound(daemon, monkeypatch, onboard_harness):
    """Repeated portal failures must dead-letter, not loop forever."""
    monkeypatch.setattr(daemon, "create_portal_member", lambda *a, **k: None)
    st = _state()

    for _ in range(daemon.MAX_ONBOARD_ATTEMPTS):
        daemon.onboard_member(CARD, st)

    assert CARD["id"] in st.get("onboard_dead_letter", []), \
        "a permanently failing portal insert must end up dead-lettered"
