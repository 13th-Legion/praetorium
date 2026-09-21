"""Member edit: the personal-email check must validate the SUBMITTED value.

Reported 2026-09-21: a new member (id 138, 'jesse') was fully processed with no
last name, and his Proton address was also stored as his personal email. Trying
to add the last name always failed with:

    "The personal email must be different from your official Proton address..."

and clearing the personal-email field made no difference.

Cause was pure ordering. The check ran against `member.personal_email` -- the
value still loaded from the database -- while the submitted value was not
applied until ~80 lines later:

    line 533   validate_personal(member.personal_email, member.email)   <-- stale
    line 613   member.personal_email = _str_field(form, "personal_email", ...)

So the stored value always failed, and the cleared value was never the one
checked. The record became permanently unsaveable, which is why he sat there
with no last name. The official-email path immediately above already did it
correctly (assign at 517, validate at 523).

These tests pin the ordering and the policy. They deliberately do NOT drive the
route: `auth_client` would not carry edit roles through to `_can_edit` here, and
a test that cannot authenticate proves nothing about ordering. The ordering is
asserted against the source, which is exactly what regressed, and the live fix
was additionally confirmed by saving the real record in production.
"""
import inspect
import re

import pytest

from app.routes import member_edit
from app.services import email_policy


# ─── policy behaviour (pure) ─────────────────────────────────────────────────

def test_blank_personal_email_is_allowed():
    """The whole reason clearing the field SHOULD have worked."""
    ok, _ = email_policy.validate_personal("", "jdtxparty@proton.me")
    assert ok is True
    ok, _ = email_policy.validate_personal(None, "jdtxparty@proton.me")
    assert ok is True


def test_personal_equal_to_official_is_rejected():
    ok, why = email_policy.validate_personal(
        "jdtxparty@proton.me", "jdtxparty@proton.me"
    )
    assert ok is False
    assert "must be different" in why


@pytest.mark.parametrize("personal", [
    "  jdtxparty@proton.me  ",
    "JDTxParty@Proton.me",
    "JDTXPARTY@PROTON.ME",
])
def test_duplicate_detection_ignores_case_and_padding(personal):
    """Same mailbox, different presentation, is still the same mailbox."""
    ok, _ = email_policy.validate_personal(personal, "jdtxparty@proton.me")
    assert ok is False


def test_distinct_personal_email_is_allowed():
    ok, _ = email_policy.validate_personal(
        "jesse.degarmo@gmail.com", "jdtxparty@proton.me"
    )
    assert ok is True


# ─── _str_field: blank means clear ───────────────────────────────────────────

def test_str_field_blank_clears_and_absent_preserves():
    """_str_field was never the bug -- it handles clearing correctly. The bug
    was that its result was computed too late to be validated."""
    assert member_edit._str_field({"personal_email": ""}, "personal_email", "old@x.com") is None
    assert member_edit._str_field({}, "personal_email", "old@x.com") == "old@x.com"
    assert member_edit._str_field(
        {"personal_email": " new@x.com "}, "personal_email", "old@x.com"
    ) == "new@x.com"


# ─── the ordering that actually regressed ────────────────────────────────────

def _save_source() -> str:
    return inspect.getsource(member_edit.save_member_edit)


def test_personal_email_is_assigned_before_it_is_validated():
    """The exact regression: validation must not read the stored value."""
    src = _save_source()
    assign = src.find('member.personal_email = _str_field(')
    validate = src.find('validate_personal(')
    assert assign != -1, "personal_email assignment not found in save_member_edit"
    assert validate != -1, "validate_personal call not found in save_member_edit"
    assert assign < validate, (
        "personal_email must be assigned from the form BEFORE validate_personal "
        "runs, otherwise the check reads the stale database value and a member "
        "whose stored personal email duplicates their official one can never "
        "be saved (member 138, 2026-09-21)"
    )


def test_personal_email_assigned_exactly_once():
    """A leftover second assignment would silently re-introduce the old value
    after validation had already passed."""
    src = _save_source()
    assert len(re.findall(r'member\.personal_email\s*=', src)) == 1


def test_official_email_ordering_unchanged():
    """The official path was already correct; keep it that way."""
    src = _save_source()
    assign = src.find('member.email = _str_field(')
    validate = src.find('validate_official(')
    assert assign != -1 and validate != -1
    assert assign < validate
