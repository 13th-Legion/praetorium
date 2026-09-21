"""Nextcloud display name must follow ANY change to it, not just a rank change.

Reported 2026-09-21: member 138 was saved in the roster as "DeGarmo" but
Nextcloud still showed "RCT Jesse". Cav had to be told the name was wrong in
two places.

Cause: `_sync_nc_displayname()` was called from exactly one place, and only
inside the rank-change branch:

    if new_rank != old_rank and member.nc_username:
        await _sync_rank_group(member.nc_username, new_rank)
        await _sync_nc_displayname(member.nc_username, member.display_name)

So the push to Nextcloud only ever happened when rank changed. Editing a last
name or callsign changed `Member.display_name` but never reached Nextcloud, so
every rename without a simultaneous promotion drifted silently.

Fix: capture `old_display_name` before the identity writes, then push whenever
the computed display name actually differs. The rank *group* sync legitimately
stays gated on rank.

Route-level tests are not used here: the `auth_client` fixture's session roles
do not reach `_can_edit` for this router (403 "Command or S1 access required"
even though FAKE_USER holds command+admin), so such a test would prove nothing.
What regressed was the call placement, so that is what is asserted.
"""
import inspect
import re

import pytest

from app.models.member import Member
from app.routes import member_edit


def _save_source() -> str:
    return inspect.getsource(member_edit.save_member_edit)


# ─── display_name shape (the reason "RCT Jesse DeGarmo" would be wrong) ──────

def test_display_name_uses_last_name_not_first():
    """Format is '<RANK ABBR> <last name> [(callsign)]'.

    "RCT Jesse" only appeared because last_name was empty. The correct value
    for member 138 is "RCT DeGarmo" — no first name.
    """
    m = Member(rank_grade="E-1", first_name="Jesse", last_name="DeGarmo")
    dn = m.display_name
    assert "DeGarmo" in dn
    assert "Jesse" not in dn, f"first name must not appear in display_name: {dn!r}"


def test_display_name_includes_callsign_when_present():
    m = Member(rank_grade="O-1", first_name="Levi", last_name="Kavadas", callsign="Cav")
    assert m.display_name.endswith("(Cav)")


def test_display_name_changes_when_only_last_name_changes():
    """The exact scenario: rank identical, name different."""
    before = Member(rank_grade="E-1", first_name="Jesse", last_name="").display_name
    after = Member(rank_grade="E-1", first_name="Jesse", last_name="DeGarmo").display_name
    assert before != after, "a last-name edit must change display_name"


# ─── the call placement that actually regressed ──────────────────────────────

def test_displayname_sync_is_not_gated_on_rank_change():
    """The bug: the push lived inside `if new_rank != old_rank`."""
    src = _save_source()
    m = re.search(
        r"if\s+new_rank\s*!=\s*old_rank[^\n]*:\n((?:[ \t]+.*\n|\n)+?)(?=[ \t]{0,4}\S)",
        src,
    )
    assert m, "could not locate the rank-change branch in save_member_edit"
    branch_body = m.group(1)
    assert "_sync_nc_displayname" not in branch_body, (
        "_sync_nc_displayname must NOT be gated on a rank change — that is why a "
        "last-name edit never reached Nextcloud (member 138, 2026-09-21)"
    )


def test_displayname_sync_is_guarded_by_a_display_name_comparison():
    src = _save_source()
    assert "old_display_name" in src, "old display name is never captured"
    assert re.search(
        r"member\.display_name\s*!=\s*old_display_name", src
    ), "the push must fire only when display_name actually changed"


def test_old_display_name_captured_before_identity_writes():
    """Captured too late and the comparison is always equal, silently
    restoring the original no-op behaviour."""
    src = _save_source()
    capture = src.find("old_display_name = member.display_name")
    last_name_write = src.find("member.last_name =")
    assert capture != -1 and last_name_write != -1
    assert capture < last_name_write, (
        "old_display_name must be captured BEFORE last_name is overwritten, "
        "otherwise display_name != old_display_name can never be true"
    )


def test_rank_group_sync_still_gated_on_rank():
    """Rank *group* membership legitimately depends on rank only — widening
    that would push group changes on every unrelated edit."""
    src = _save_source()
    m = re.search(
        r"if\s+new_rank\s*!=\s*old_rank[^\n]*:\n((?:[ \t]+.*\n|\n)+?)(?=[ \t]{0,4}\S)",
        src,
    )
    assert m and "_sync_rank_group" in m.group(1)


def test_displayname_sync_called_exactly_once():
    """Two call sites would double-push on a simultaneous rank+name change."""
    src = _save_source()
    assert len(re.findall(r"await\s+_sync_nc_displayname\(", src)) == 1
