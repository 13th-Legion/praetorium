"""Forms storage path builders + submission-id parsing.

LIVE BUG, confirmed on production 2026-09-03: the daemon built the Forms
folder path three different ways. Two hardcoded the correct prefixed string;
move_applicant_files() built 'Forms/{form_title}' WITHOUT the '3 - ' form-id
prefix, so its PROPFIND 404'd every time. 20 'No Forms folder yet' log lines
back to 2026-03-07, zero successful moves, an empty Applications/ folder --
no applicant document had ever been archived.

Verified against production while fixing:
  prefixed path   -> 207, 39+ submission folders
  unprefixed path -> 404
"""

from urllib.parse import unquote

import pytest


# ─── The canonical folder name ───────────────────────────────────────────────

def test_form_folder_name_carries_the_form_id_prefix(daemon):
    """THE regression guard. Dropping this prefix is the whole bug."""
    assert daemon.FORM_FOLDER_NAME.startswith(f"{daemon.FORM_ID} - ")
    assert daemon.FORM_FOLDER_NAME == (
        "3 - Texas State Militia \u2014 Application & Background Check Release"
    )


def test_form_folder_name_is_derived_not_retyped(daemon):
    """Built from FORM_ID + FORM_TITLE, so the two can never drift apart."""
    assert daemon.FORM_FOLDER_NAME == f"{daemon.FORM_ID} - {daemon.FORM_TITLE}"


# ─── URL builders ────────────────────────────────────────────────────────────

def test_forms_root_url_matches_the_path_production_answers_207_for(daemon):
    url = daemon.forms_root_url()
    assert url.startswith(f"{daemon.NC_URL}/remote.php/dav/files/{daemon.NC_USER}/Forms/")
    assert "/Forms/3%20-%20Texas%20State%20Militia%20" in url
    assert unquote(url).endswith(f"/Forms/{daemon.FORM_FOLDER_NAME}")


def test_forms_root_url_is_not_the_404ing_legacy_path(daemon):
    """Explicitly pin that the old, broken shape can never come back."""
    url = daemon.forms_root_url()
    assert "/Forms/Texas%20State%20Militia" not in url
    assert not unquote(url).endswith(f"/Forms/{daemon.FORM_TITLE}")


@pytest.mark.parametrize("sub_id", [1, 7, 39, "12"])
def test_forms_submission_url_appends_the_submission_id(daemon, sub_id):
    url = daemon.forms_submission_url(sub_id)
    assert url == f"{daemon.forms_root_url()}/{sub_id}"
    assert unquote(url).endswith(f"/{daemon.FORM_FOLDER_NAME}/{sub_id}")


def test_special_characters_are_percent_encoded(daemon):
    url = daemon.forms_root_url()
    assert " " not in url
    assert "%20" in url          # spaces
    assert "%26" in url          # &
    assert "%E2%80%94" in url    # em dash


def test_applications_folder_url_encodes_the_bracketed_share(daemon):
    url = daemon.applications_folder_url("John Garcia")
    assert "%5BS-1%5D%20Admin/Applications/" in url
    assert url.endswith("/John_Garcia")


@pytest.mark.parametrize("name,expected", [
    ("John Garcia", "John_Garcia"),
    ("Garcia, John", "Garcia_John"),
    ("Mary Jane Watson", "Mary_Jane_Watson"),
    ("Cher", "Cher"),
])
def test_applicant_folder_name_sanitising(daemon, name, expected):
    assert daemon.applicant_folder_name(name) == expected


def test_dav_base_url_can_target_another_account(daemon):
    assert daemon.dav_base_url("portal-svc").endswith("/remote.php/dav/files/portal-svc")


# ─── '*Submission ID:* N' parsing ────────────────────────────────────────────

CARD_DESC = """**Company:** 13th Legion (DFW)
**Email:** jane@example.com
**Legal Name:** Jane Doe

---
**\U0001f4e7 Proton Mail:** jane@proton.me

---
*Suggested Team:* **Bravo** (bearing: 212.4\u00b0)
*Submitted:* 2026-09-01 10:12
*Submission ID:* 41
"""


def test_parses_the_submission_id_from_a_real_card_description(daemon):
    assert daemon.parse_submission_id({"description": CARD_DESC}) == 41


def test_returns_none_when_the_field_is_absent(daemon):
    assert daemon.parse_submission_id({"description": "**Email:** a@b.c"}) is None
    assert daemon.parse_submission_id({"description": ""}) is None
    assert daemon.parse_submission_id({}) is None


def test_returns_none_rather_than_crashing_on_a_null_description(daemon):
    assert daemon.parse_submission_id({"description": None}) is None


def test_parse_card_for_onboarding_cannot_see_the_submission_id(daemon):
    """Documents WHY parse_submission_id exists.

    parse_card_for_onboarding only reads '**Key:** value' lines. The
    submission id is written with SINGLE asterisks, so it is invisible there.
    """
    info = daemon.parse_card_for_onboarding({"description": CARD_DESC, "title": "\U0001f4cb Jane Doe"})
    assert info["Legal Name"] == "Jane Doe"
    assert info["Email"] == "jane@example.com"
    assert "Submission ID" not in info


def test_submission_id_survives_a_card_with_trailing_onboarding_notes(daemon):
    desc = CARD_DESC + "\n\n---\n*Onboarded: 2026-09-02 11:00*\n*NC Account: jane.doe*\n"
    assert daemon.parse_submission_id({"description": desc}) == 41
