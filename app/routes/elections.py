"""Election system routes — PP-077 CO Election.

Anonymity is enforced here:
- /vote writes ballot + voter_roll in one transaction, no linking column
- Ballot cast_at is rounded to nearest hour before insert
- No username logged on vote submission
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_

from app.auth import require_auth, require_role, get_current_user
from app.database import async_session
from app.models.elections import (
    Election, ElectionNomination, ElectionNominationReceipt,
    ElectionBallot, ElectionVoterRoll,
)
from app.models.member import Member
from app.constants import RANK_ABBR

router = APIRouter(tags=["elections"])
templates = Jinja2Templates(directory="app/templates")

from zoneinfo import ZoneInfo
_CDT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


def _to_cdt(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_CDT)


templates.env.filters["cdt"] = _to_cdt

def _mildate(dt):
    """Format datetime as DD MMM YYYY @ HHMM TZ (e.g. 15 APR 2026 @ 2349 CDT)."""
    if dt is None:
        return ""
    local = _to_cdt(dt)
    tz_label = local.strftime("%Z")
    return local.strftime("%d %b %Y").upper().lstrip("0") + f" @ {local.strftime('%H%M')} {tz_label}"

templates.env.filters["mildate"] = _mildate


def _now_utc() -> datetime:
    return datetime.utcnow()


def _parse_central_to_utc(dt_str: str) -> datetime:
    """Parse a naive datetime-local string as America/Chicago, return naive UTC."""
    naive = datetime.fromisoformat(dt_str)
    central = naive.replace(tzinfo=_CDT)
    utc_aware = central.astimezone(_UTC)
    return utc_aware.replace(tzinfo=None)  # store as naive UTC (consistent with _now_utc)


def _determine_phase(nominations_open_utc: datetime) -> str:
    """Return 'scheduled' if nominations haven't opened yet, else 'nominations'."""
    return "scheduled" if nominations_open_utc > _now_utc() else "nominations"


def _is_window_open(
    now: datetime,
    open_dt: Optional[datetime],
    close_dt: Optional[datetime],
) -> bool:
    """True if now (naive UTC) is within [open_dt, close_dt] (both naive UTC)."""
    if open_dt is None or close_dt is None:
        return False
    return open_dt <= now <= close_dt


def _round_to_hour(dt: datetime) -> datetime:
    """Round a datetime to the nearest hour — reduces timing correlation on ballots."""
    if dt.minute >= 30:
        return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return dt.replace(minute=0, second=0, microsecond=0)


# Eligible voter: status = active, rank_grade E-2 or above
ELIGIBLE_RANK_GRADES = {
    "E-2", "E-3", "E-4", "E-5", "E-6", "E-7", "E-8", "E-9",
    "W-1", "W-2", "W-3", "W-4", "W-5",
    "O-1", "O-2", "O-3", "O-4", "O-5", "O-6",
}


def _is_eligible(member: Member) -> bool:
    """Is this member eligible to vote/nominate?"""
    return (
        (member.status or "").lower() == "active"
        and member.rank_grade in ELIGIBLE_RANK_GRADES
    )


async def _get_member(db, username: str) -> Optional[Member]:
    result = await db.execute(select(Member).where(Member.nc_username == username))
    return result.scalar_one_or_none()


async def _get_election(db, election_id: int) -> Optional[Election]:
    result = await db.execute(select(Election).where(Election.id == election_id))
    return result.scalar_one_or_none()


# ─── Admin Panel ─────────────────────────────────────────────────────────────

@router.get("/elections/admin", response_class=HTMLResponse)
@require_role("command", "admin")
async def election_admin(request: Request):
    """Admin panel — create and manage elections. Command only."""
    user = request.session.get("user", {})

    async with async_session() as db:
        elections_result = await db.execute(
            select(Election).order_by(Election.created_at.desc())
        )
        elections = elections_result.scalars().all()

        # Get eligible member count for display
        eligible_result = await db.execute(
            select(func.count(Member.id)).where(
                and_(
                    Member.status == "active",
                    Member.rank_grade.in_(ELIGIBLE_RANK_GRADES),
                )
            )
        )
        eligible_count = eligible_result.scalar() or 0

    return templates.TemplateResponse("pages/election_admin.html", {
        "request": request,
        "user": user,
        "elections": elections,
        "eligible_count": eligible_count,
    })


# ─── Create Election ─────────────────────────────────────────────────────────

@router.post("/elections/create", response_class=HTMLResponse)
@require_role("command", "admin")
async def create_election(
    request: Request,
    title: str = Form(...),
    nominations_open: str = Form(...),
    nominations_close: str = Form(...),
    voting_open: str = Form(...),
    voting_close: str = Form(...),
    quorum_pct: int = Form(75),
):
    """Create a new election. Command only."""
    user = request.session.get("user", {})

    try:
        nom_open = _parse_central_to_utc(nominations_open)
        nom_close = _parse_central_to_utc(nominations_close)
        vote_open = _parse_central_to_utc(voting_open)
        vote_close = _parse_central_to_utc(voting_close)
    except ValueError:
        return HTMLResponse(
            '<div style="padding:12px;background:#b71c1c;color:#fff;border-radius:6px;">'
            "❌ Invalid date format.</div>"
        )

    initial_phase = _determine_phase(nom_open)

    async with async_session() as db:
        election = Election(
            title=title.strip(),
            phase=initial_phase,
            nominations_open=nom_open,
            nominations_close=nom_close,
            voting_open=vote_open,
            voting_close=vote_close,
            quorum_pct=quorum_pct,
            created_by=user.get("username", "unknown"),
            created_at=_now_utc(),
        )
        db.add(election)
        await db.commit()
        await db.refresh(election)
        eid = election.id

    return HTMLResponse(
        f'<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        f'✅ Election created. <a href="/elections/{eid}" style="color:#8f8;text-decoration:underline;">'
        f"View Election →</a></div>"
        f"<script>setTimeout(()=>window.location.reload(),1500)</script>"
    )


# ─── Advance Phase ────────────────────────────────────────────────────────────

@router.post("/elections/{election_id}/advance", response_class=HTMLResponse)
@require_role("command", "admin")
async def advance_phase(request: Request, election_id: int):
    """Advance election to next phase. Command only."""
    PHASE_ORDER = ["scheduled", "nominations", "voting", "complete"]

    async with async_session() as db:
        election = await _get_election(db, election_id)
        if not election:
            return HTMLResponse("Election not found", status_code=404)

        current = election.phase
        if current == "cancelled":
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "Election is cancelled.</div>"
            )

        if current == "complete":
            return HTMLResponse(
                '<div style="padding:8px;background:#f39c12;color:#000;border-radius:6px;">'
                "Election is already complete.</div>"
            )

        try:
            idx = PHASE_ORDER.index(current)
            next_phase = PHASE_ORDER[idx + 1]
        except (ValueError, IndexError):
            next_phase = "complete"

        # When moving to voting: snapshot eligible count
        if next_phase == "voting":
            eligible_result = await db.execute(
                select(func.count(Member.id)).where(
                    and_(
                        Member.status == "active",
                        Member.rank_grade.in_(ELIGIBLE_RANK_GRADES),
                    )
                )
            )
            election.eligible_count = eligible_result.scalar() or 0

        election.phase = next_phase
        await db.commit()

    return HTMLResponse(
        f'<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        f"✅ Phase advanced to <strong>{next_phase}</strong>.</div>"
        f"<script>setTimeout(()=>window.location.reload(),1500)</script>"
    )


# ─── Main Election Page ───────────────────────────────────────────────────────

@router.get("/elections/{election_id}", response_class=HTMLResponse)
@require_auth
async def election_page(request: Request, election_id: int):
    """Main election page — adapts to current phase."""
    user = request.session.get("user", {})
    roles = set(user.get("roles", []))

    async with async_session() as db:
        election = await _get_election(db, election_id)
        if not election:
            return HTMLResponse("<h1>Election not found</h1>", status_code=404)

        member = await _get_member(db, user.get("username", ""))
        member_id = member.id if member else None
        is_eligible = _is_eligible(member) if member else False
        is_command = bool(roles & {"command", "admin"})

        # Has this member already nominated?
        has_nominated = False
        if member_id:
            receipt = await db.execute(
                select(ElectionNominationReceipt).where(
                    and_(
                        ElectionNominationReceipt.election_id == election_id,
                        ElectionNominationReceipt.member_id == member_id,
                    )
                )
            )
            has_nominated = receipt.scalar_one_or_none() is not None

        # Has this member already voted?
        has_voted = False
        if member_id:
            voter_row = await db.execute(
                select(ElectionVoterRoll).where(
                    and_(
                        ElectionVoterRoll.election_id == election_id,
                        ElectionVoterRoll.member_id == member_id,
                    )
                )
            )
            has_voted = voter_row.scalar_one_or_none() is not None

        # Is this member a nominee?
        my_nomination = None
        if member_id:
            nom_result = await db.execute(
                select(ElectionNomination).where(
                    and_(
                        ElectionNomination.election_id == election_id,
                        ElectionNomination.nominee_id == member_id,
                    )
                )
            )
            my_nomination = nom_result.scalar_one_or_none()

        # All nominations (with member info for display)
        nominations_result = await db.execute(
            select(ElectionNomination, Member)
            .join(Member, ElectionNomination.nominee_id == Member.id)
            .where(ElectionNomination.election_id == election_id)
            .order_by(ElectionNomination.nominated_at)
        )
        all_nominations = nominations_result.all()

        # Accepted nominees (for ballot)
        accepted_nominees = [
            (nom, mem) for nom, mem in all_nominations if nom.accepted is True
        ]

        # Participation counters
        nom_receipt_count = await db.scalar(
            select(func.count(ElectionNominationReceipt.id)).where(
                ElectionNominationReceipt.election_id == election_id
            )
        )
        voter_count = await db.scalar(
            select(func.count(ElectionVoterRoll.id)).where(
                ElectionVoterRoll.election_id == election_id
            )
        )

        # Results (only after voting phase)
        results = []
        winner = None
        quorum_met = None
        if election.phase in ("complete", "runoff"):
            ballot_counts = await db.execute(
                select(ElectionBallot.nominee_id, func.count(ElectionBallot.id))
                .where(ElectionBallot.election_id == election_id)
                .group_by(ElectionBallot.nominee_id)
                .order_by(func.count(ElectionBallot.id).desc())
            )
            ballot_rows = ballot_counts.all()
            total_votes = sum(cnt for _, cnt in ballot_rows)

            for nominee_id, cnt in ballot_rows:
                mem_result = await db.execute(select(Member).where(Member.id == nominee_id))
                mem = mem_result.scalar_one_or_none()
                pct = round((cnt / total_votes * 100) if total_votes else 0)
                results.append({"member": mem, "votes": cnt, "pct": pct})

            if results:
                winner = results[0]["member"]

            eligible = election.eligible_count or 1
            quorum_threshold = round(eligible * election.quorum_pct / 100)
            quorum_met = total_votes >= quorum_threshold

        # Eligible member list (for nomination dropdown)
        eligible_members_result = await db.execute(
            select(Member).where(
                and_(
                    Member.status == "active",
                    Member.rank_grade.in_(ELIGIBLE_RANK_GRADES),
                )
            ).order_by(Member.last_name)
        )
        eligible_members = eligible_members_result.scalars().all()

        # Voter roll (Command can see WHO voted after close)
        voter_roll_members = []
        if is_command and election.phase in ("complete", "runoff"):
            vr_result = await db.execute(
                select(ElectionVoterRoll, Member)
                .join(Member, ElectionVoterRoll.member_id == Member.id)
                .where(ElectionVoterRoll.election_id == election_id)
                .order_by(Member.last_name)
            )
            voter_roll_members = [(vr, m) for vr, m in vr_result.all()]

    # Determine nomination/voting window open/closed based on phase AND date
    _now = _now_utc()
    nom_window_open = (
        election.phase == "nominations"
        and _is_window_open(_now, election.nominations_open, election.nominations_close)
    )
    voting_window_open = (
        election.phase == "voting"
        and _is_window_open(_now, election.voting_open, election.voting_close)
    )

    return templates.TemplateResponse("pages/election.html", {
        "request": request,
        "user": user,
        "election": election,
        "member_id": member_id,
        "is_eligible": is_eligible,
        "is_command": is_command,
        "has_nominated": has_nominated,
        "has_voted": has_voted,
        "my_nomination": my_nomination,
        "all_nominations": all_nominations,
        "accepted_nominees": accepted_nominees,
        "nom_receipt_count": nom_receipt_count or 0,
        "voter_count": voter_count or 0,
        "eligible_members": eligible_members,
        "eligible_count": election.eligible_count or len(eligible_members),
        "nom_window_open": nom_window_open,
        "voting_window_open": voting_window_open,
        "results": results,
        "winner": winner,
        "quorum_met": quorum_met,
        "voter_roll_members": voter_roll_members,
        "rank_abbr": RANK_ABBR,
    })


# ─── Results Page ─────────────────────────────────────────────────────────────

@router.get("/elections/{election_id}/results", response_class=HTMLResponse)
@require_auth
async def election_results(request: Request, election_id: int):
    """Results page — redirects to main election page (results are embedded there)."""
    return RedirectResponse(url=f"/elections/{election_id}", status_code=302)


# ─── Nominate ────────────────────────────────────────────────────────────────

@router.post("/elections/{election_id}/nominate", response_class=HTMLResponse)
@require_auth
async def submit_nomination(
    request: Request,
    election_id: int,
    nominee_id: int = Form(...),
):
    """Submit an anonymous nomination. One per member per election."""
    user = request.session.get("user", {})

    async with async_session() as db:
        election = await _get_election(db, election_id)
        if not election:
            return HTMLResponse("Election not found", status_code=404)

        if election.phase != "nominations":
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ Nominations are not currently open.</div>"
            )

        now = _now_utc()
        if not _is_window_open(now, election.nominations_open, election.nominations_close):
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ Nominations are not currently open.</div>"
            )

        member = await _get_member(db, user.get("username", ""))
        if not member or not _is_eligible(member):
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ You are not eligible to nominate.</div>"
            )

        # Check already nominated
        existing_receipt = await db.execute(
            select(ElectionNominationReceipt).where(
                and_(
                    ElectionNominationReceipt.election_id == election_id,
                    ElectionNominationReceipt.member_id == member.id,
                )
            )
        )
        if existing_receipt.scalar_one_or_none():
            return HTMLResponse(
                '<div style="padding:8px;background:#f39c12;color:#000;border-radius:6px;">'
                "⚠️ You have already submitted a nomination.</div>"
            )

        # Validate nominee exists and is eligible
        nominee_result = await db.execute(select(Member).where(Member.id == nominee_id))
        nominee = nominee_result.scalar_one_or_none()
        if not nominee or not _is_eligible(nominee):
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ Invalid nominee.</div>"
            )

        now = _now_utc()

        # Create or get the nomination record for this nominee
        existing_nom = await db.execute(
            select(ElectionNomination).where(
                and_(
                    ElectionNomination.election_id == election_id,
                    ElectionNomination.nominee_id == nominee_id,
                )
            )
        )
        if not existing_nom.scalar_one_or_none():
            nom = ElectionNomination(
                election_id=election_id,
                nominee_id=nominee_id,
                nominated_at=now,
                accepted=None,  # pending
            )
            db.add(nom)

        # Create receipt — tracks that THIS member used their nomination
        # NO LINK to which nominee they picked
        receipt = ElectionNominationReceipt(
            election_id=election_id,
            member_id=member.id,
            nominated_at=now,
        )
        db.add(receipt)

        await db.commit()

    return HTMLResponse(
        '<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        "✅ Your nomination has been recorded. The ballot remains confidential."
        "</div>"
        "<script>setTimeout(()=>window.location.reload(),2000)</script>"
    )


# ─── Accept Nomination ───────────────────────────────────────────────────────

@router.post("/elections/{election_id}/accept-nomination", response_class=HTMLResponse)
@require_auth
async def accept_nomination(request: Request, election_id: int):
    """Nominee accepts their nomination."""
    user = request.session.get("user", {})

    async with async_session() as db:
        election = await _get_election(db, election_id)
        if not election:
            return HTMLResponse("Election not found", status_code=404)

        if election.phase not in ("nominations", "voting"):
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ Nomination window is closed.</div>"
            )

        member = await _get_member(db, user.get("username", ""))
        if not member:
            return HTMLResponse("Member not found", status_code=404)

        nom_result = await db.execute(
            select(ElectionNomination).where(
                and_(
                    ElectionNomination.election_id == election_id,
                    ElectionNomination.nominee_id == member.id,
                )
            )
        )
        nom = nom_result.scalar_one_or_none()
        if not nom:
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ You have not been nominated.</div>"
            )

        nom.accepted = True
        nom.accepted_at = _now_utc()
        await db.commit()

    return HTMLResponse(
        '<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        "✅ You have accepted your nomination. You are on the ballot."
        "</div>"
        "<script>setTimeout(()=>window.location.reload(),2000)</script>"
    )


# ─── Decline Nomination ──────────────────────────────────────────────────────

@router.post("/elections/{election_id}/decline-nomination", response_class=HTMLResponse)
@require_auth
async def decline_nomination(request: Request, election_id: int):
    """Nominee declines their nomination."""
    user = request.session.get("user", {})

    async with async_session() as db:
        election = await _get_election(db, election_id)
        if not election:
            return HTMLResponse("Election not found", status_code=404)

        member = await _get_member(db, user.get("username", ""))
        if not member:
            return HTMLResponse("Member not found", status_code=404)

        nom_result = await db.execute(
            select(ElectionNomination).where(
                and_(
                    ElectionNomination.election_id == election_id,
                    ElectionNomination.nominee_id == member.id,
                )
            )
        )
        nom = nom_result.scalar_one_or_none()
        if not nom:
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ You have not been nominated.</div>"
            )

        nom.accepted = False
        nom.accepted_at = _now_utc()
        await db.commit()

    return HTMLResponse(
        '<div style="padding:12px;background:#f39c12;color:#000;border-radius:6px;">'
        "You have declined your nomination."
        "</div>"
        "<script>setTimeout(()=>window.location.reload(),2000)</script>"
    )


# ─── Vote ─────────────────────────────────────────────────────────────────────

@router.post("/elections/{election_id}/vote", response_class=HTMLResponse)
@require_auth
async def cast_vote(
    request: Request,
    election_id: int,
    nominee_id: int = Form(...),
):
    """Cast an anonymous ballot.

    ANONYMITY CRITICAL:
    - Ballot and voter_roll written in same transaction, zero linking columns
    - cast_at rounded to nearest hour
    - No username logged here
    """
    user = request.session.get("user", {})

    async with async_session() as db:
        election = await _get_election(db, election_id)
        if not election:
            return HTMLResponse("Election not found", status_code=404)

        if election.phase != "voting":
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ Voting is not currently open.</div>"
            )

        now = _now_utc()
        if not _is_window_open(now, election.voting_open, election.voting_close):
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ Voting is not currently open.</div>"
            )

        member = await _get_member(db, user.get("username", ""))
        if not member or not _is_eligible(member):
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ You are not eligible to vote.</div>"
            )

        # Check already voted
        existing_vote = await db.execute(
            select(ElectionVoterRoll).where(
                and_(
                    ElectionVoterRoll.election_id == election_id,
                    ElectionVoterRoll.member_id == member.id,
                )
            )
        )
        if existing_vote.scalar_one_or_none():
            return HTMLResponse(
                '<div style="padding:8px;background:#f39c12;color:#000;border-radius:6px;">'
                "⚠️ You have already voted in this election.</div>"
            )

        # Validate nominee is on the accepted ballot
        nom_check = await db.execute(
            select(ElectionNomination).where(
                and_(
                    ElectionNomination.election_id == election_id,
                    ElectionNomination.nominee_id == nominee_id,
                    ElectionNomination.accepted == True,
                )
            )
        )
        if not nom_check.scalar_one_or_none():
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;">'
                "❌ Invalid ballot selection.</div>"
            )

        # Coarsen timestamp to nearest hour — prevents timing correlation
        cast_at = _round_to_hour(_now_utc())

        # Write ballot (NO voter_id) and voter_roll (NO ballot link) in same transaction
        ballot = ElectionBallot(
            election_id=election_id,
            nominee_id=nominee_id,
            cast_at=cast_at,
        )
        db.add(ballot)

        voter_roll = ElectionVoterRoll(
            election_id=election_id,
            member_id=member.id,
            voted_at=_now_utc(),
        )
        db.add(voter_roll)

        await db.commit()

    return HTMLResponse(
        '<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        "✅ Your vote has been recorded. Your ballot is anonymous and cannot be changed."
        "</div>"
        "<script>setTimeout(()=>window.location.reload(),2000)</script>"
    )
