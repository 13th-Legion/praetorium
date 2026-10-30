"""Urban Evasion 2 — model registration and data-model sanity."""

from app.database import Base
from app.models import (
    UrbanEvasionEvolution,
    UrbanEvasionExercise,
    UrbanEvasionParticipant,
    UrbanEvasionScoreEvent,
)

EXPECTED_TABLES = [
    "ue2_exercises",
    "ue2_participants",
    "ue2_evolutions",
    "ue2_score_events",
]


def test_urban_evasion_route_registered():
    from app.main import app

    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/urban-evasion/{exercise_id}" in paths


def test_ue2_tables_are_registered():
    for name in EXPECTED_TABLES:
        assert name in Base.metadata.tables, f"{name} missing from Base.metadata"


def test_ue2_score_event_has_expected_columns():
    cols = {c.name for c in UrbanEvasionScoreEvent.__table__.columns}
    assert {"kind", "points", "target_participant_id", "participant_id"} <= cols


def test_ue2_participant_has_geo_columns():
    cols = {c.name for c in UrbanEvasionParticipant.__table__.columns}
    assert {"start_lat", "start_lng", "end_lat", "end_lng", "team", "buddy_id"} <= cols


def test_score_points_match_approved_table():
    from app.services.urban_evasion import score_points

    assert score_points("sighting") == 1
    assert score_points("positive_id") == 3
    assert score_points("capture") == 5
    assert score_points("shadow") == 2
    assert score_points("false_positive") == -3
    assert score_points("burned") == 0  # dropped category, no points
    assert score_points("bogus") == 0


def test_split_teams_is_deterministic_and_balanced():
    from app.services.urban_evasion import split_teams

    ids = list(range(1, 14))  # 13 members -> 7 / 6
    a = split_teams(ids, seed=1234)
    b = split_teams(ids, seed=1234)
    assert a == b  # deterministic
    assert set(a["a"]) | set(a["b"]) == set(ids)
    assert set(a["a"]).isdisjoint(a["b"])
    assert len(a["a"]) == 7 and len(a["b"]) == 6


def test_buddy_pairs_pair_adjacent_members():
    from app.services.urban_evasion import buddy_pairs

    assert buddy_pairs([1, 2, 3, 4, 5]) == [(1, 2), (3, 4)]  # odd leftover unpaired


def test_assign_start_end_unique_and_disjoint():
    from app.services.urban_evasion import assign_start_end

    waypoints = [{"label": f"wp{i}", "lat": 0.0, "lng": float(i)} for i in range(20)]
    member_ids = list(range(1, 8))  # 7 members
    assignment = assign_start_end(waypoints, member_ids, seed=99)

    starts = [assignment[m]["start"]["label"] for m in member_ids]
    ends = [assignment[m]["end"]["label"] for m in member_ids]
    assert len(set(starts)) == 7  # all starts unique
    assert len(set(ends)) == 7  # all ends unique
    assert set(starts).isdisjoint(ends)  # start never equals end


def test_assign_start_end_forces_traversal():
    from app.services.urban_evasion import _distance_m, assign_start_end

    north = [{"label": f"n{i}", "lat": 32.7549, "lng": -97.3305} for i in range(8)]
    south = [{"label": f"s{i}", "lat": 32.7480, "lng": -97.3305} for i in range(8)]
    assignment = assign_start_end(north + south, list(range(1, 6)), seed=3, min_separation_m=500)
    assert len(assignment) == 5
    for mid, pair in assignment.items():
        d = _distance_m(pair["start"], pair["end"])
        assert d >= 500, f"member {mid}: separation {d:.0f}m < 500m"


def test_assign_start_end_needs_enough_waypoints():
    import pytest
    from app.services.urban_evasion import assign_start_end

    with pytest.raises(ValueError):
        assign_start_end([{"label": "only", "lat": 0.0, "lng": 0.0}], [1, 2], seed=1)


async def test_build_exercise_creates_teams_points_and_buddies(db_session):
    from datetime import datetime

    from app.models.urban_evasion import UrbanEvasionExercise
    from app.services.urban_evasion import build_exercise
    from tests.factories import make_event, make_member, make_rsvp

    ev = await make_event(db_session, title="UE2", date_start=datetime(2026, 11, 7, 9, 0))
    members = [await make_member(db_session) for _ in range(6)]
    for m in members:
        await make_rsvp(db_session, ev, m, status="attending")

    ex = UrbanEvasionExercise(name="Urban Evasion Part Deux", event_id=ev.id)
    db_session.add(ex)
    await db_session.flush()

    waypoints = [{"label": f"wp{i}", "lat": 0.0, "lng": float(i)} for i in range(20)]
    created = await build_exercise(db_session, ex, waypoints)

    assert len(created) == 6
    by_id = {p.id: p for p in created}
    assert {p.team for p in created} == {"a", "b"}
    for p in created:
        assert p.start_label and p.end_label and p.start_label != p.end_label
        if p.buddy_id is not None:
            assert by_id[p.buddy_id].buddy_id == p.id
            assert by_id[p.buddy_id].team == p.team


async def test_build_exercise_skips_non_attending(db_session):
    from app.models.urban_evasion import UrbanEvasionExercise
    from app.services.urban_evasion import build_exercise
    from tests.factories import make_event, make_member, make_rsvp

    ev = await make_event(db_session, title="UE2")
    m = await make_member(db_session)
    await make_rsvp(db_session, ev, m, status="declined")
    ex = UrbanEvasionExercise(name="UE2", event_id=ev.id)
    db_session.add(ex)
    await db_session.flush()

    created = await build_exercise(db_session, ex, [{"label": "w", "lat": 0.0, "lng": 0.0}])
    assert created == []
