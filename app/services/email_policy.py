"""Unit policy for member email addresses.

Cav, 2026-09-10:

  * The **official** address — ``members.email`` in the portal AND the
    Nextcloud account address, which ``nc_users.set_email`` keeps in step —
    **must be a Proton address. Nothing else is allowed.**
  * The **personal** address — ``members.personal_email`` — may be anything
    *except* their own official Proton address. It exists precisely so there
    is a second, non-unit way to reach someone; duplicating the official
    address defeats the point.

Why this is enforced in code rather than by convention: an audit on the day the
policy was set found **4** members whose official address was not Proton and
**27** whose personal address was a byte-for-byte copy of their official one.
Convention had not held.

Interpretation note: "anything but their Proton address" is read as *their own
official address*, not "no Proton address at all" — a member may legitimately
have a second Proton mailbox as their personal contact. The comparison is
case-insensitive, because ``Romanovtsm@proton.me`` and ``romanovtsm@proton.me``
are the same mailbox.
"""

from __future__ import annotations

from typing import Optional

#: Domains Proton issues. Note these are NOT interchangeable aliases for a
#: given user — PFC Pope's @protonmail.com hard-bounced while his @proton.me
#: worked, and Chaplain Pati's was the exact reverse. Both are *allowed*; they
#: are simply not assumed to be the same mailbox.
PROTON_DOMAINS = ("proton.me", "protonmail.com")


def _norm(addr: Optional[str]) -> str:
    return (addr or "").strip().lower()


def is_proton(addr: Optional[str]) -> bool:
    """True if ``addr`` is on a Proton domain."""
    a = _norm(addr)
    return any(a.endswith("@" + d) for d in PROTON_DOMAINS)


def same_address(a: Optional[str], b: Optional[str]) -> bool:
    """Case-insensitive address equality."""
    na, nb = _norm(a), _norm(b)
    return bool(na) and na == nb


def validate_official(addr: Optional[str]) -> tuple[bool, str]:
    """The official address must be present and on a Proton domain."""
    a = _norm(addr)
    if not a:
        return False, "An official email address is required."
    if "@" not in a:
        return False, f"{addr!r} is not a valid email address."
    if not is_proton(a):
        return False, (
            "The official address must be a Proton address "
            f"({' or '.join('@' + d for d in PROTON_DOMAINS)}). "
            f"{addr!r} is not. Put a non-Proton address in the personal email "
            "field instead."
        )
    return True, ""


def validate_personal(
    personal: Optional[str], official: Optional[str]
) -> tuple[bool, str]:
    """The personal address is optional, but must not be the official one."""
    p = _norm(personal)
    if not p:
        return True, ""
    if "@" not in p:
        return False, f"{personal!r} is not a valid email address."
    if same_address(p, official):
        return False, (
            "The personal email must be different from your official Proton "
            "address — it exists so there is a second way to reach you. "
            "Leave it blank if you don't have one."
        )
    return True, ""
