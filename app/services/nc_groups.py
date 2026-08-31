"""Nextcloud group + Talk-room self-healing helpers.

Why this exists
---------------
Team assignment in the portal drives NC group membership via
``member_edit._sync_leadership_groups``, which builds the group name on the fly
as ``f"Team-{team}"``. Historically NOTHING kept the NC side in step with the
portal:

  * If a team's ``Team-<name>`` group didn't exist in NC, the add-user POST
    silently failed (404) and members were never grouped. This is exactly how
    the Alpha→Aquila drift happened: the portal renamed the team to "Aquila"
    (2026-07-21) but NC still only had ``Team-Alpha``, so every Aquila sync
    silently no-op'd for weeks.
  * Team Talk rooms are gated by a *group actor* (``source=groups``). Deleting
    or renaming the group without swapping the room actor silently drops every
    member who was in the room only via the group (bit us 2026-08-30 when
    deleting ``Team-Alpha`` dropped Archer + Walker from T1 · Aquila).

These helpers make both operations idempotent and self-healing:

  * ``ensure_group`` — create a group if missing (safe to call every sync).
  * ``ensure_team_room_group_actor`` — make sure a group gates a Talk room.
  * ``rename_team_group`` — full rename: create new group, migrate members,
    swap the Talk-room group actor, delete the old group.

All calls are best-effort and log on failure — a NC hiccup must never 500 a
portal save. NC's OCS API has no group-rename endpoint, so a "rename" is
create-new + migrate + delete-old.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

_OCS_HEADERS = {"OCS-APIRequest": "true", "Accept": "application/json"}


def _auth():
    return (settings.nc_api_user, settings.nc_api_password)


def _ocs_status(resp: httpx.Response) -> Optional[int]:
    """Pull the OCS meta statuscode out of a JSON response, if present."""
    try:
        return resp.json().get("ocs", {}).get("meta", {}).get("statuscode")
    except Exception:
        return None


async def group_exists(client: httpx.AsyncClient, group: str) -> bool:
    """True if the NC group already exists.

    Uses the group-detail endpoint; a missing group returns OCS 404.
    """
    try:
        r = await client.get(
            f"{settings.nc_url}/ocs/v2.php/cloud/groups/{group}/users",
            auth=_auth(), headers=_OCS_HEADERS, timeout=10,
        )
        # 200 with data => exists. OCS wraps HTTP 200 even for "not found",
        # so trust the OCS meta statuscode.
        return _ocs_status(r) == 200 and r.status_code == 200
    except Exception as e:
        log.error(f"group_exists({group}) check failed: {e}")
        # Assume it might exist to avoid a duplicate-create storm on transient
        # errors; ensure_group's create is idempotent anyway.
        return True


async def ensure_group(group: str, client: Optional[httpx.AsyncClient] = None) -> bool:
    """Create the NC group if it doesn't already exist. Idempotent.

    Returns True if the group exists (or was created) afterwards.
    """
    if not group:
        return False

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient()
    try:
        # POST create is safe: NC returns OCS 102 "group already exists" which
        # we treat as success, so we can skip the existence probe on the hot
        # path and just attempt the create.
        try:
            r = await client.post(
                f"{settings.nc_url}/ocs/v2.php/cloud/groups",
                auth=_auth(), headers=_OCS_HEADERS,
                data={"groupid": group}, timeout=10,
            )
            code = _ocs_status(r)
            if code == 100 or code == 200:
                log.info(f"Created NC group '{group}'")
                return True
            if code == 102:
                # Already exists — the normal case on repeated syncs.
                return True
            log.warning(f"ensure_group('{group}') unexpected OCS code {code}")
            # Fall back to an explicit existence check.
            return await group_exists(client, group)
        except Exception as e:
            log.error(f"ensure_group('{group}') create failed: {e}")
            return await group_exists(client, group)
    finally:
        if own_client:
            await client.aclose()


async def _group_members(client: httpx.AsyncClient, group: str) -> list[str]:
    try:
        r = await client.get(
            f"{settings.nc_url}/ocs/v2.php/cloud/groups/{group}/users",
            auth=_auth(), headers=_OCS_HEADERS, timeout=10,
        )
        if _ocs_status(r) == 200:
            return list(r.json().get("ocs", {}).get("data", {}).get("users", []))
    except Exception as e:
        log.error(f"_group_members('{group}') failed: {e}")
    return []


async def ensure_team_room_group_actor(
    talk_token: str, group: str, client: Optional[httpx.AsyncClient] = None
) -> bool:
    """Ensure ``group`` gates the Talk room ``talk_token`` (source=groups).

    Team rooms are gated by a group actor so membership self-maintains. Adding
    an already-present group actor is a harmless no-op on NC's side.
    """
    if not talk_token or not group:
        return False

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient()
    try:
        r = await client.post(
            f"{settings.nc_url}/ocs/v2.php/apps/spreed/api/v4/room/{talk_token}/participants",
            auth=_auth(), headers=_OCS_HEADERS,
            data={"newParticipant": group, "source": "groups"}, timeout=10,
        )
        code = _ocs_status(r)
        if code == 200:
            log.info(f"Ensured group '{group}' gates Talk room {talk_token}")
            return True
        # 200-family OCS with a different code (e.g. already a participant) is
        # still fine for our purposes.
        log.info(f"ensure_team_room_group_actor({talk_token},{group}) OCS code {code}")
        return True
    except Exception as e:
        log.error(f"ensure_team_room_group_actor({talk_token},{group}) failed: {e}")
        return False
    finally:
        if own_client:
            await client.aclose()


async def _remove_room_group_actor(
    client: httpx.AsyncClient, talk_token: str, group: str
) -> None:
    """Remove a group actor from a Talk room (used after rename migration).

    Uses the participants list to find the group's attendeeId, then DELETEs it.
    Best-effort — a leftover old actor is harmless (it'll gate an empty group
    that gets deleted), so we don't fail the rename if this can't complete.
    """
    try:
        r = await client.get(
            f"{settings.nc_url}/ocs/v2.php/apps/spreed/api/v4/room/{talk_token}/participants",
            auth=_auth(), headers=_OCS_HEADERS, timeout=10,
        )
        if _ocs_status(r) != 200:
            return
        parts = r.json().get("ocs", {}).get("data", [])
        attendee_id = None
        for p in parts:
            if p.get("actorType") == "groups" and p.get("actorId") == group:
                attendee_id = p.get("attendeeId")
                break
        if attendee_id is None:
            return
        d = await client.request(
            "DELETE",
            f"{settings.nc_url}/ocs/v2.php/apps/spreed/api/v4/room/{talk_token}/attendees",
            auth=_auth(), headers=_OCS_HEADERS,
            params={"attendeeId": attendee_id}, timeout=10,
        )
        log.info(f"Removed old group actor '{group}' from room {talk_token} "
                 f"(OCS {_ocs_status(d)})")
    except Exception as e:
        log.error(f"_remove_room_group_actor({talk_token},{group}) failed: {e}")


async def rename_team_group(
    old_team: str,
    new_team: str,
    talk_token: Optional[str] = None,
) -> None:
    """Self-healing NC-side follow-through for a portal team rename.

    NC has no group-rename API, so:
      1. Create ``Team-<new_team>`` (idempotent).
      2. Migrate every member of ``Team-<old_team>`` into it.
      3. Point the team's Talk room at the new group actor (and drop the old).
      4. Delete the now-empty ``Team-<old_team>`` group.

    Headquarters is skipped (it uses the ``Team - Headquarters`` legacy group
    and isn't a geo/rename-managed team). Best-effort throughout.
    """
    if not old_team or not new_team or old_team == new_team:
        return
    if old_team == "Headquarters" or new_team == "Headquarters":
        return

    old_group = f"Team-{old_team}"
    new_group = f"Team-{new_team}"

    async with httpx.AsyncClient() as client:
        # 1. Create the new group.
        await ensure_group(new_group, client=client)

        # 2. Migrate members old -> new.
        members = await _group_members(client, old_group)
        for username in members:
            try:
                await client.post(
                    f"{settings.nc_url}/ocs/v2.php/cloud/users/{username}/groups",
                    auth=_auth(), headers=_OCS_HEADERS,
                    data={"groupid": new_group}, timeout=10,
                )
            except Exception as e:
                log.error(f"rename_team_group: add {username} to {new_group} failed: {e}")
        log.info(f"rename_team_group: migrated {len(members)} members "
                 f"{old_group} → {new_group}")

        # 3. Swap the Talk-room group actor (if this team has a room).
        if talk_token:
            await ensure_team_room_group_actor(talk_token, new_group, client=client)
            await _remove_room_group_actor(client, talk_token, old_group)

        # 4. Delete the old (now-empty) group.
        try:
            d = await client.request(
                "DELETE",
                f"{settings.nc_url}/ocs/v2.php/cloud/groups/{old_group}",
                auth=_auth(), headers=_OCS_HEADERS, timeout=10,
            )
            log.info(f"rename_team_group: deleted old group {old_group} "
                     f"(OCS {_ocs_status(d)})")
        except Exception as e:
            log.error(f"rename_team_group: delete {old_group} failed: {e}")
