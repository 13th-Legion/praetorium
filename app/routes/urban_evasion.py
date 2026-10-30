"""Urban Evasion 2 — exercise page, live scoring, and scoreboard."""

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth import get_current_user, require_auth, require_role
from app.models.member import Member
from app.models.urban_evasion import (
    UrbanEvasionExercise,
    UrbanEvasionParticipant,
    UrbanEvasionScoreEvent,
)
from app.services.urban_evasion import SCORE_KINDS, SCORE_POINTS, score_points

router = APIRouter(prefix="/urban-evasion", tags=["urban-evasion"])
templates = Jinja2Templates(directory="app/templates")
log = logging.getLogger(__name__)


async def _load_exercise(db, exercise_id: int) -> UrbanEvasionExercise:
    exercise = await db.get(UrbanEvasionExercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return exercise


async def _participant_for_member(db, exercise_id: int, member_id: int):
    result = await db.execute(
        select(UrbanEvasionParticipant).where(
            UrbanEvasionParticipant.exercise_id == exercise_id,
            UrbanEvasionParticipant.member_id == member_id,
        )
    )
    return result.scalar_one_or_none()


@router.get("/{exercise_id}")
@require_auth
async def exercise_page(request: Request, exercise_id: int):
    user = get_current_user(request)
    from app.database import async_session

    async with async_session() as db:
        exercise = await _load_exercise(db, exercise_id)
        participants = list(
            (
                await db.execute(
                    select(UrbanEvasionParticipant)
                    .where(UrbanEvasionParticipant.exercise_id == exercise_id)
                    .order_by(UrbanEvasionParticipant.team, UrbanEvasionParticipant.id)
                )
            ).scalars().all()
        )
        member_ids = {p.member_id for p in participants}
        members = {}
        if member_ids:
            members = {
                m.id: m
                for m in (
                    await db.execute(select(Member).where(Member.id.in_(member_ids)))
                ).scalars().all()
            }
        scores = list(
            (
                await db.execute(
                    select(UrbanEvasionScoreEvent).where(
                        UrbanEvasionScoreEvent.exercise_id == exercise_id
                    )
                )
            ).scalars().all()
        )

    names = {
        p.id: (members[p.member_id].display_name if p.member_id in members else f"member {p.member_id}")
        for p in participants
    }
    per_participant: dict[int, int] = {}
    for s in scores:
        per_participant[s.participant_id] = per_participant.get(s.participant_id, 0) + s.points
    per_team: dict[str, int] = {}
    for p in participants:
        per_team[p.team] = per_team.get(p.team, 0) + per_participant.get(p.id, 0)

    return templates.TemplateResponse(
        "pages/urban_evasion.html",
        {
            "request": request,
            "user": user,
            "exercise": exercise,
            "participants": participants,
            "names": names,
            "scores": scores,
            "per_participant": per_participant,
            "per_team": per_team,
            "score_kinds": SCORE_KINDS,
            "score_points": SCORE_POINTS,
        },
    )


@router.post("/{exercise_id}/score")
@require_auth
async def log_score(
    request: Request,
    exercise_id: int,
    kind: str = Form(...),
    target_participant_id: int | None = Form(None),
    note: str = Form(""),
):
    user = get_current_user(request)
    username = (user or {}).get("username") or (user or {}).get("nc_username")
    if not username:
        raise HTTPException(status_code=403, detail="No session user")

    from app.database import async_session

    async with async_session() as db:
        await _load_exercise(db, exercise_id)
        member = (
            await db.execute(select(Member).where(Member.nc_username == username))
        ).scalar_one_or_none()
        if not member:
            raise HTTPException(status_code=403, detail="Not a member")
        participant = await _participant_for_member(db, exercise_id, member.id)
        if not participant:
            raise HTTPException(status_code=403, detail="Not a participant in this exercise")

        db.add(
            UrbanEvasionScoreEvent(
                exercise_id=exercise_id,
                participant_id=participant.id,
                kind=kind,
                target_participant_id=target_participant_id or None,
                points=score_points(kind),
                note=note or None,
            )
        )
        await db.commit()

    return RedirectResponse(url=f"/urban-evasion/{exercise_id}", status_code=303)


@router.post("/create")
@require_role("command", "s3", "admin")
async def create_exercise(request: Request, name: str = Form(...), event_id: int = Form(...)):
    from app.database import async_session

    from app.models.events import Event

    async with async_session() as db:
        event = await db.get(Event, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        ex = UrbanEvasionExercise(
            name=name,
            event_id=event_id,
            date_start=event.date_start,
            date_end=event.date_end,
        )
        db.add(ex)
        await db.commit()
        await db.refresh(ex)
    return RedirectResponse(url=f"/urban-evasion/{ex.id}", status_code=303)


@router.post("/{exercise_id}/build")
@require_role("command", "s3", "admin")
async def build_teams(request: Request, exercise_id: int):
    from app.database import async_session

    from app.services.urban_evasion import SUNDANCE_SQUARE_WAYPOINTS, build_exercise

    async with async_session() as db:
        exercise = await _load_exercise(db, exercise_id)
        await build_exercise(db, exercise, SUNDANCE_SQUARE_WAYPOINTS)
        await db.commit()
    return RedirectResponse(url=f"/urban-evasion/{exercise_id}", status_code=303)


@router.post("/{exercise_id}/freqs")
@require_role("command", "s3", "admin")
async def set_freqs(
    request: Request,
    exercise_id: int,
    radio_primary: str = Form(""),
    radio_alternate: str = Form(""),
    radio_realworld: str = Form(""),
):
    from app.database import async_session

    async with async_session() as db:
        exercise = await _load_exercise(db, exercise_id)
        exercise.radio_primary = radio_primary or None
        exercise.radio_alternate = radio_alternate or None
        exercise.radio_realworld = radio_realworld or None
        await db.commit()
    return RedirectResponse(url=f"/urban-evasion/{exercise_id}", status_code=303)
