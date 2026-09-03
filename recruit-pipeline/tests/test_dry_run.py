"""--dry-run must have no external side effects.

The deploy procedure mandates a dry run after every edit, so this is the code
path we execute most deliberately. It previously guarded only the blacklist
lookup: card creation, application forwarding to other units' S1s and the
applicant confirmation emails all ran for real. It was harmless only because
every submission already happened to be in processed_submissions.
"""

import pytest

SUBMISSION_13TH = {
    "id": 900,
    "answers": [
        {"questionId": 3, "text": "Jane Doe"},
        {"questionId": 2, "text": "jane@example.com"},
        {"questionId": 28, "text": "13th Legion (DFW)"},
    ],
}

SUBMISSION_OTHER = {
    "id": 901,
    "answers": [
        {"questionId": 3, "text": "Bob Roberts"},
        {"questionId": 2, "text": "bob@example.com"},
        {"questionId": 28, "text": "Vikings (Houston)"},
    ],
}


@pytest.fixture
def spy(daemon, monkeypatch):
    """Trip-wire every external side effect check_new_submissions can cause."""
    fired = []
    for fn in ("create_deck_card", "forward_application_to_unit",
               "send_application_received_email",
               "send_generic_application_received_email",
               "send_rejection_email", "notify_s1_nctalk"):
        monkeypatch.setattr(
            daemon, fn,
            (lambda name: lambda *a, **kw: fired.append(name))(fn),
        )

    # Blacklist screen: record the call, and answer "clean" so routing proceeds.
    def _clean(*a, **kw):
        fired.append("check_blacklist")
        return None, None
    monkeypatch.setattr(daemon, "check_blacklist", _clean)
    return fired


@pytest.fixture
def state():
    return {"processed_submissions": [], "onboarded_cards": [], "last_check": 0}


def _submissions(daemon, monkeypatch, *subs):
    monkeypatch.setattr(daemon, "get_submissions", lambda: list(subs))


def test_dry_run_creates_no_cards_and_sends_no_mail(daemon, monkeypatch, state, spy):
    _submissions(daemon, monkeypatch, SUBMISSION_13TH, SUBMISSION_OTHER)
    assert daemon.check_new_submissions(state, dry_run=True) == 2
    assert spy == [], f"dry run caused real side effects: {spy}"


def test_dry_run_does_not_forward_applications_to_other_units(daemon, monkeypatch, state, spy):
    """Forwarding emails another unit's S1 and CCs state admin. Never in a dry run."""
    _submissions(daemon, monkeypatch, SUBMISSION_OTHER)
    daemon.check_new_submissions(state, dry_run=True)
    assert "forward_application_to_unit" not in spy


def test_dry_run_still_marks_submissions_seen_so_the_count_is_honest(daemon, monkeypatch, state, spy):
    _submissions(daemon, monkeypatch, SUBMISSION_13TH)
    daemon.check_new_submissions(state, dry_run=True)
    assert state["processed_submissions"] == [900]


def test_dry_run_logs_what_it_would_have_done(daemon, monkeypatch, state, spy, caplog):
    import logging
    _submissions(daemon, monkeypatch, SUBMISSION_13TH, SUBMISSION_OTHER)
    with caplog.at_level(logging.INFO):
        daemon.check_new_submissions(state, dry_run=True)
    assert "[DRY RUN]" in caplog.text
    assert "create Deck card" in caplog.text
    assert "admin@tsmhouston.org" in caplog.text, "should name the forward target"


def test_already_processed_submissions_are_skipped(daemon, monkeypatch, state, spy):
    state["processed_submissions"] = [900]
    _submissions(daemon, monkeypatch, SUBMISSION_13TH)
    assert daemon.check_new_submissions(state, dry_run=True) == 0


# ─── The real path still works ───────────────────────────────────────────────

def test_a_real_run_does_create_the_card_and_send_the_email(daemon, monkeypatch, state, spy):
    _submissions(daemon, monkeypatch, SUBMISSION_13TH)
    daemon.check_new_submissions(state, dry_run=False)
    assert "create_deck_card" in spy
    assert "send_application_received_email" in spy
    assert "check_blacklist" in spy, "real runs must still screen applicants"


def test_a_real_run_forwards_other_companies(daemon, monkeypatch, state, spy):
    _submissions(daemon, monkeypatch, SUBMISSION_OTHER)
    daemon.check_new_submissions(state, dry_run=False)
    assert "forward_application_to_unit" in spy
    assert "send_generic_application_received_email" in spy


def test_a_failed_card_creation_leaves_the_submission_unprocessed(daemon, monkeypatch, state, spy):
    """So the next poll retries it instead of losing the applicant."""
    def boom(*a, **kw):
        raise RuntimeError("deck down")
    monkeypatch.setattr(daemon, "create_deck_card", boom)  # after spy, so it wins
    _submissions(daemon, monkeypatch, SUBMISSION_13TH)
    daemon.check_new_submissions(state, dry_run=False)
    assert state["processed_submissions"] == []


# ─── Onboarding dry run ──────────────────────────────────────────────────────

def test_onboard_dry_run_marks_the_card_without_touching_nextcloud(daemon, http):
    """Documents the known state.json poisoning hazard.

    _onboard_member appends to onboarded_cards even in dry-run, and main()
    calls save_state() unconditionally -- so a dry run against a real
    state.json can permanently mark a recruit as onboarded. The deploy
    procedure backs up and restores state.json around the dry run for exactly
    this reason.
    """
    state = {"onboarded_cards": []}
    card = {
        "id": 999,
        "title": "\U0001f4cb Jane Doe",
        "description": "**Legal Name:** Jane Doe\n**Email:** j@x.c\n"
                       "**\U0001f4e7 Proton Mail:** jane@proton.me\n",
    }
    assert daemon.onboard_member(card, state, dry_run=True) is True
    assert 999 in state["onboarded_cards"], "the documented hazard"
    assert http.calls == [], "but it must not touch Nextcloud"
