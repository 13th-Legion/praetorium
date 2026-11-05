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
    """Senior-junior battle-buddy pairs from a seniority-ordered team list.

    Pairs the most senior with the least senior, the next most with the next
    least, and so on. An odd-sized team leaves the median member unpaired.
    """
    pairs = []
    lo, hi = 0, len(team_ids) - 1
    while lo < hi:
        pairs.append((team_ids[lo], team_ids[hi]))
        lo += 1
        hi -= 1
    return pairs

# Sundance Square (downtown Fort Worth) waypoint pool, geocoded via Nominatim
# 2026-10-29. Confirm the AO boundary with S3 before the event — a few of these
# sit just outside the strict 6-8-block core (Water Gardens, Burnett Plaza).
SUNDANCE_SQUARE_WAYPOINTS = [
    {"label": "Tarrant County Courthouse", "lat": 32.75746, "lng": -97.33316},
    {"label": "Paddock Viaduct", "lat": 32.759261, "lng": -97.334345},
    {"label": "Calhoun & 8th St", "lat": 32.757082, "lng": -97.330884},
    {"label": "Calhoun & 9th St", "lat": 32.75638, "lng": -97.330448},
    {"label": "Fort Worth Public Library", "lat": 32.75393, "lng": -97.33489},
    {"label": "First United Methodist Church", "lat": 32.752343, "lng": -97.33632},
    {"label": "Burnett Plaza", "lat": 32.750324, "lng": -97.334539},
    {"label": "Mercedes-Benz of Fort Worth", "lat": 32.753999, "lng": -97.33489},
    {"label": "500 W 3rd St", "lat": 32.753647, "lng": -97.334812},
    {"label": "Worthington Renaissance Hotel", "lat": 32.755788, "lng": -97.333621},
    {"label": "Saint Patrick Cathedral", "lat": 32.749237, "lng": -97.329921},
    {"label": "Burk Burnett Park", "lat": 32.750451, "lng": -97.333705},
    {"label": "Fort Worth Convention Center", "lat": 32.749699, "lng": -97.327691},
    {"label": "Omni Fort Worth Hotel", "lat": 32.748526, "lng": -97.328349},
    {"label": "Fort Worth Water Gardens", "lat": 32.747749, "lng": -97.32663},
    {"label": "JFK Tribute", "lat": 32.751841, "lng": -97.329469},
    {"label": "West 7th & Houston", "lat": 32.750902, "lng": -97.329838},
    {"label": "1000 Throckmorton St", "lat": 32.750259, "lng": -97.330834},
    {"label": "The Fort Worth Club", "lat": 32.75196, "lng": -97.332106},
    {"label": "General Worth Square", "lat": 32.751891, "lng": -97.329209},
    {"label": "Hilton Fort Worth", "lat": 32.752403, "lng": -97.329196},
    {"label": "Fort Worth Central Station", "lat": 32.752667, "lng": -97.32648},
    {"label": "Sheraton Fort Worth Downtown", "lat": 32.747469, "lng": -97.325135},
    {"label": "400 Throckmorton St", "lat": 32.754216, "lng": -97.333137},
    {"label": "815 Main St", "lat": 32.752563, "lng": -97.329304},
    {"label": "500 Houston St", "lat": 32.753968, "lng": -97.331917},
    {"label": "Sundance Square Plaza", "lat": 32.75570, "lng": -97.33060},
    {"label": "Bass Performance Hall", "lat": 32.75460, "lng": -97.32940},
    {"label": "Sid Richardson Museum", "lat": 32.75600, "lng": -97.33240},
    {"label": "Sanger Building", "lat": 32.75640, "lng": -97.33070},
    {"label": "Wells Fargo Tower", "lat": 32.75450, "lng": -97.33400},
    {"label": "Chisholm Trail Mural", "lat": 32.75590, "lng": -97.33140},
    {"label": "300 Main St", "lat": 32.75500, "lng": -97.33190},
    {"label": "400 Main St", "lat": 32.75460, "lng": -97.33140},
    {"label": "900 Main St", "lat": 32.75260, "lng": -97.33000},
    {"label": "300 Throckmorton St", "lat": 32.75390, "lng": -97.33320},
    {"label": "600 Throckmorton St", "lat": 32.75240, "lng": -97.33260},
    {"label": "Flatiron Building", "lat": 32.75580, "lng": -97.33080},
    {"label": "Land Title Building", "lat": 32.75550, "lng": -97.33100},
    {"label": "W.T. Waggoner Building", "lat": 32.75520, "lng": -97.33200},
]


def _distance_m(a: dict, b: dict) -> float:
    """Great-circle distance in metres between two {lat, lng} points (haversine)."""
    import math

    R = 6371000.0
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat = lat2 - lat1
    dlng = math.radians(b["lng"] - a["lng"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def assign_start_end(waypoints, member_ids, seed, target_distance_m=550):
    """Assign unique start/end waypoints so every target traverses a uniform distance.

    Each member's end is chosen to land as close as possible to target_distance_m
    from their start, then a 2-opt swap pass tightens the spread so the whole
    cohort covers roughly the same ground instead of a few going much farther.
    Returns {member_id: {"start": waypoint, "end": waypoint}}.
    """
    n = len(member_ids)
    if len(waypoints) < 2 * n:
        raise ValueError(f"need at least {2 * n} waypoints for {n} members, have {len(waypoints)}")
    rng = random.Random(seed)
    pool = list(waypoints)
    rng.shuffle(pool)
    starts = pool[:n]
    remaining = pool[n:]
    pairs: list[tuple[dict, dict]] = []
    for s in starts:
        best = min(range(len(remaining)), key=lambda j: abs(_distance_m(s, remaining[j]) - target_distance_m))
        pairs.append((s, remaining.pop(best)))
    improved = True
    while improved:
        improved = False
        for x in range(len(pairs)):
            for y in range(x + 1, len(pairs)):
                sa, ea = pairs[x]
                sb, eb = pairs[y]
                cur = abs(_distance_m(sa, ea) - target_distance_m) + abs(_distance_m(sb, eb) - target_distance_m)
                new = abs(_distance_m(sa, eb) - target_distance_m) + abs(_distance_m(sb, ea) - target_distance_m)
                if new < cur - 1e-9:
                    pairs[x] = (sa, eb)
                    pairs[y] = (sb, ea)
                    improved = True
    return {mid: {"start": s, "end": e} for mid, (s, e) in zip(member_ids, pairs)}


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

    # Sort each team by seniority (rank sort_order, descending) so buddy pairs
    # land senior-with-junior rather than randomly.
    from app.models.member import Member
    from app.models.rank import Rank

    sort_rows = (
        await db.execute(
            select(Member.id, Rank.sort_order)
            .join(Rank, Rank.grade == Member.rank_grade)
            .where(Member.id.in_(member_ids))
        )
    ).all()
    sort_map = {mid: so for mid, so in sort_rows}
    for ids in teams.values():
        ids.sort(key=lambda mid: sort_map.get(mid, 0), reverse=True)

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

