"""onboard_member() bounded-retry / dead-letter behaviour.

Without a retry bound, ANY card that cannot be onboarded (missing email, no
Proton address, NC failure) is retried every single poll forever. That is how
one card produced 1,942 failed attempts over 7 days and filled nextcloud.log
with 'Failed addUser attempt' noise.

After MAX_ONBOARD_ATTEMPTS the card is dead-lettered and skipped until a human
removes it from state.json.
"""

import logging

import pytest

CARD = {"id": 4242, "title": "\U0001f4cb Jane Doe", "description": "**Email:** j@x.c"}


@pytest.fixture
def state():
    return {"processed_submissions": [], "onboarded_cards": [], "last_check": 0}


@pytest.fixture
def always_fails(daemon, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "_onboard_member",
                        lambda card, st, dry_run=False: calls.append(card["id"]) or False)
    return calls


@pytest.fixture
def always_succeeds(daemon, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "_onboard_member",
                        lambda card, st, dry_run=False: calls.append(card["id"]) or True)
    return calls


# ─── Failure counting ────────────────────────────────────────────────────────

def test_each_failure_increments_the_counter(daemon, state, always_fails):
    for expected in (1, 2, 3):
        assert daemon.onboard_member(CARD, state) is False
        assert state["onboard_failures"]["4242"] == expected


def test_dead_letters_at_the_cap(daemon, state, always_fails, caplog):
    with caplog.at_level(logging.ERROR):
        for _ in range(daemon.MAX_ONBOARD_ATTEMPTS):
            daemon.onboard_member(CARD, state)

    assert 4242 in state["onboard_dead_letter"]
    assert "DEAD-LETTER" in caplog.text
    assert len(always_fails) == daemon.MAX_ONBOARD_ATTEMPTS


def test_a_dead_lettered_card_is_never_attempted_again(daemon, state, always_fails):
    """The actual fix for 1,942 retries: stop calling it."""
    for _ in range(daemon.MAX_ONBOARD_ATTEMPTS):
        daemon.onboard_member(CARD, state)
    attempts_at_cap = len(always_fails)

    for _ in range(50):
        assert daemon.onboard_member(CARD, state) is False

    assert len(always_fails) == attempts_at_cap, "must not retry after dead-lettering"


def test_the_dead_letter_log_says_how_to_recover(daemon, state, always_fails, caplog):
    with caplog.at_level(logging.ERROR):
        for _ in range(daemon.MAX_ONBOARD_ATTEMPTS):
            daemon.onboard_member(CARD, state)
    assert "onboard_dead_letter" in caplog.text
    assert "state.json" in caplog.text


def test_clearing_the_dead_letter_list_re_enables_the_card(daemon, state, always_fails):
    for _ in range(daemon.MAX_ONBOARD_ATTEMPTS):
        daemon.onboard_member(CARD, state)
    before = len(always_fails)

    state["onboard_dead_letter"].remove(4242)
    daemon.onboard_member(CARD, state)
    assert len(always_fails) == before + 1


# ─── Success paths ───────────────────────────────────────────────────────────

def test_success_clears_a_partial_failure_count(daemon, state, daemon_flaky):
    """A transient failure must not count against a card forever."""
    assert daemon.onboard_member(CARD, state) is False
    assert state["onboard_failures"]["4242"] == 1

    assert daemon.onboard_member(CARD, state) is True
    assert "4242" not in state.get("onboard_failures", {})


@pytest.fixture
def daemon_flaky(daemon, monkeypatch):
    seq = iter([False, True])
    monkeypatch.setattr(daemon, "_onboard_member",
                        lambda card, st, dry_run=False: next(seq))


def test_an_already_onboarded_card_is_skipped(daemon, state, always_succeeds):
    state["onboarded_cards"].append(4242)
    assert daemon.onboard_member(CARD, state) is False
    assert always_succeeds == [], "must not re-onboard"


def test_a_successful_card_is_not_counted_as_a_failure(daemon, state, always_succeeds):
    assert daemon.onboard_member(CARD, state) is True
    assert state.get("onboard_failures", {}) == {}


# ─── state.json shape ────────────────────────────────────────────────────────

def test_missing_state_keys_are_created_not_crashed_on(daemon, always_fails):
    """load_state() on an old state.json has none of these keys."""
    bare = {}
    assert daemon.onboard_member(CARD, bare) is False
    assert bare["onboard_failures"]["4242"] == 1


def test_failure_keys_are_strings_because_json_has_no_int_keys(daemon, state, always_fails):
    """A round-trip through json.dumps would turn 4242 into '4242' anyway;
    using str() from the start keeps the counter from silently resetting."""
    daemon.onboard_member(CARD, state)
    assert list(state["onboard_failures"]) == ["4242"]


def test_state_survives_a_json_round_trip(daemon, state, always_fails):
    import json
    for _ in range(2):
        daemon.onboard_member(CARD, state)
    reloaded = json.loads(json.dumps(state))
    daemon.onboard_member(CARD, reloaded)
    assert reloaded["onboard_failures"]["4242"] == 3
