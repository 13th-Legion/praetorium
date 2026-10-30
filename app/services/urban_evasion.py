"""Urban Evasion 2 — scoring rules and team building."""

import random

SCORE_POINTS = {
    "sighting": 1,
    "positive_id": 3,
    "capture": 5,
    "shadow": 2,
    "false_positive": -3,
}

SCORE_KINDS = sorted(SCORE_POINTS)


def score_points(kind: str) -> int:
    """Point value for a scoring event kind. Unknown kinds score 0."""
    return SCORE_POINTS.get(kind, 0)


def split_teams(member_ids: list[int], seed: int) -> dict[str, list[int]]:
    """Split members into two size-balanced teams, deterministically shuffled.

    Returns {"a": [...], "b": [...]}, each ordered so battle-buddy pairs are
    adjacent (indexes 0-1, 2-3, ...). The same seed reproduces the same split.
    """
    rng = random.Random(seed)
    ids = list(member_ids)
    rng.shuffle(ids)
    return {"a": ids[0::2], "b": ids[1::2]}


def buddy_pairs(team_ids: list[int]) -> list[tuple[int, int]]:
    """Battle-buddy pairs within one team's ordered member list."""
    return [(team_ids[i], team_ids[i + 1]) for i in range(0, len(team_ids) - 1, 2)]

# Sundance Square (downtown Fort Worth) waypoint pool, geocoded via Nominatim
# 2026-10-29. Confirm the AO boundary with S3 before the event — a few of these
# sit just outside the strict 6-8-block core (Water Gardens, Burnett Plaza).
SUNDANCE_SQUARE_WAYPOINTS = [
    {"label": "Sundance Square Plaza", "lat": 32.754907, "lng": -97.33151},
    {"label": "Bass Performance Hall", "lat": 32.754753, "lng": -97.329757},
    {"label": "Sid Richardson Museum", "lat": 32.75552, "lng": -97.33158},
    {"label": "Reata Restaurant", "lat": 32.754855, "lng": -97.33278},
    {"label": "Tarrant County Courthouse", "lat": 32.757479, "lng": -97.333191},
    {"label": "JFK Tribute", "lat": 32.751841, "lng": -97.329469},
    {"label": "Worthington Hotel", "lat": 32.755788, "lng": -97.333621},
    {"label": "Flying Saucer", "lat": 32.754658, "lng": -97.330996},
    {"label": "Scat Jazz Lounge", "lat": 32.754326, "lng": -97.331245},
    {"label": "Bird Cafe", "lat": 32.754849, "lng": -97.330837},
    {"label": "Haltoms Jewelers", "lat": 32.755372, "lng": -97.331821},
    {"label": "Starbucks Main", "lat": 32.754996, "lng": -97.33157},
    {"label": "Water Gardens", "lat": 32.748479, "lng": -97.326081},
    {"label": "Burnett Plaza", "lat": 32.750464, "lng": -97.334543},
    {"label": "Mercedes-Benz", "lat": 32.753999, "lng": -97.33489},
    {"label": "Del Friscos Grille", "lat": 32.75519, "lng": -97.331272},
    {"label": "The Sinclair", "lat": 32.754297, "lng": -97.331103},
    {"label": "Fort Worth Main", "lat": 32.753196, "lng": -97.329733},
    {"label": "Houston Street", "lat": 32.755241, "lng": -97.332795},
    {"label": "West 7th", "lat": 32.750902, "lng": -97.329838},
    {"label": "City Center", "lat": 32.752212, "lng": -97.331704},
    {"label": "Four Day Weekend", "lat": 32.755189, "lng": -97.332759},
    {"label": "Little Red Wasp", "lat": 32.752038, "lng": -97.329841},
    {"label": "The Bird and Branch", "lat": 32.755625, "lng": -97.332077},
    {"label": "500 Main St", "lat": 32.754344, "lng": -97.331134},
    {"label": "600 Main St", "lat": 32.753749, "lng": -97.330737},
    {"label": "700 Main St", "lat": 32.753093, "lng": -97.330298},
    {"label": "815 Main St", "lat": 32.752563, "lng": -97.329304},
    {"label": "100 E 3rd St", "lat": 32.755136, "lng": -97.331381},
    {"label": "100 E 4th St", "lat": 32.75449, "lng": -97.330945},
    {"label": "500 Commerce St", "lat": 32.754772, "lng": -97.330345},
    {"label": "600 Commerce St", "lat": 32.753907, "lng": -97.330054},
    {"label": "400 Throckmorton St", "lat": 32.754216, "lng": -97.333137},
    {"label": "500 Throckmorton St", "lat": 32.753251, "lng": -97.332942},
    {"label": "400 Houston St", "lat": 32.754424, "lng": -97.332504},
    {"label": "500 Houston St", "lat": 32.753968, "lng": -97.331917},
]


def assign_start_end(waypoints, member_ids, seed):
    """Assign unique start and end waypoints to each member.

    Starts and ends are drawn from disjoint halves of a seeded shuffle, so every
    start and every end is unique and a member's start never equals their end.
    Returns {member_id: {"start": waypoint, "end": waypoint}}.
    """
    n = len(member_ids)
    if len(waypoints) < 2 * n:
        raise ValueError(f"need at least {2 * n} waypoints for {n} members, have {len(waypoints)}")
    rng = random.Random(seed)
    pool = list(waypoints)
    rng.shuffle(pool)
    starts = pool[:n]
    ends = pool[n : 2 * n]
    rng.shuffle(ends)
    return {mid: {"start": starts[i], "end": ends[i]} for i, mid in enumerate(member_ids)}


async def build_exercise(db, exercise, waypoints, seed=None):
    """Pull attending RSVPs, split teams, assign points, write participant rows.

    Replaces any existing participants for the exercise. Pre-event admin op —
    do not run after scoring has begun (score events reference participants).
    Returns the created UrbanEvasionParticipant rows, or [] if nobody is attending.
    """
    from sqlalchemy import delete, select

    from app.models.events import EventRSVP
    from app.models.urban_evasion import UrbanEvasionParticipant

    if exercise.event_id is None:
        raise ValueError("exercise has no linked event")

    seed = seed if seed is not None else exercise.id

    result = await db.execute(
        select(EventRSVP.member_id).where(
            EventRSVP.event_id == exercise.event_id,
            EventRSVP.status == "attending",
        )
    )
    member_ids = [row[0] for row in result.all()]
    if not member_ids:
        return []

    teams = split_teams(member_ids, seed)
    points = assign_start_end(waypoints, member_ids, seed)

    await db.execute(
        delete(UrbanEvasionParticipant).where(
            UrbanEvasionParticipant.exercise_id == exercise.id
        )
    )
    await db.flush()

    created = []
    for team, ids in teams.items():
        for mid in ids:
            created.append(
                UrbanEvasionParticipant(
                    exercise_id=exercise.id,
                    member_id=mid,
                    team=team,
                    start_label=points[mid]["start"]["label"],
                    start_lat=points[mid]["start"]["lat"],
                    start_lng=points[mid]["start"]["lng"],
                    end_label=points[mid]["end"]["label"],
                    end_lat=points[mid]["end"]["lat"],
                    end_lng=points[mid]["end"]["lng"],
                )
            )
    db.add_all(created)
    await db.flush()

    member_to_participant = {p.member_id: p.id for p in created}
    for ids in teams.values():
        for a, b in buddy_pairs(ids):
            pa, pb = member_to_participant[a], member_to_participant[b]
            for p in created:
                if p.id == pa:
                    p.buddy_id = pb
                elif p.id == pb:
                    p.buddy_id = pa
    await db.flush()

    return created

