#!/usr/bin/env python3
"""
Nightly sync: replicate spooky's NC Maps favorites to Command/S1 accounts.
Run via cron: 0 3 * * * /usr/bin/python3 /opt/recruit-pipeline/sync-map-pins.py
"""
import os
import requests, json, logging, sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sync-map-pins")

NC_URL = "https://cloud.13thlegion.org"
HEADERS = {"OCS-APIRequest": "true", "Accept": "application/json", "Content-Type": "application/json"}

# Source account (daemon manages this one)
SOURCE = ("spooky", os.environ.get("NC_SVC_PASS", ""))

# Target accounts (Command + S1) — app passwords from env
TARGETS = [
    ("levi.kavadas", os.environ.get("NC_APPPASS_KAVADAS", "")),
    ("adam.locy", os.environ.get("NC_APPPASS_LOCY", "")),
]

def get_favorites(auth):
    r = requests.get(f"{NC_URL}/index.php/apps/maps/api/1/favorites", auth=auth, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def add_favorite(auth, fav):
    data = {"name": fav["name"], "lat": fav["lat"], "lng": fav["lng"],
            "category": fav["category"], "comment": fav.get("comment", "")}
    r = requests.post(f"{NC_URL}/index.php/apps/maps/api/1/favorites", auth=auth, headers=HEADERS, json=data, timeout=30)
    return r.status_code == 200

def delete_favorite(auth, fav_id):
    r = requests.delete(f"{NC_URL}/index.php/apps/maps/api/1/favorites/{fav_id}", auth=auth, headers=HEADERS, timeout=30)
    return r.status_code == 200

def update_favorite(auth, fav_id, fav):
    data = {"name": fav["name"], "lat": fav["lat"], "lng": fav["lng"],
            "category": fav["category"], "comment": fav.get("comment", "")}
    r = requests.put(f"{NC_URL}/index.php/apps/maps/api/1/favorites/{fav_id}", auth=auth, headers=HEADERS, json=data, timeout=30)
    return r.status_code == 200

def fav_key(f):
    """Unique key for matching: name + category."""
    return (f["name"], f["category"])

def fav_changed(src, tgt):
    """Check if lat/lng/comment differ."""
    return (abs(src["lat"] - tgt["lat"]) > 0.0001 or
            abs(src["lng"] - tgt["lng"]) > 0.0001 or
            src.get("comment", "") != tgt.get("comment", ""))

def sync_account(source_favs, target_user, target_pass):
    auth = (target_user, target_pass)
    target_favs = get_favorites(auth)

    src_by_key = {fav_key(f): f for f in source_favs}
    tgt_by_key = {fav_key(f): f for f in target_favs}

    added = removed = updated = 0

    # Add missing
    for key, src in src_by_key.items():
        if key not in tgt_by_key:
            if add_favorite(auth, src):
                added += 1
                log.info(f"  Added: {src['name']} ({src['category']})")
        else:
            # Update if changed
            tgt = tgt_by_key[key]
            if fav_changed(src, tgt):
                if update_favorite(auth, tgt["id"], src):
                    updated += 1
                    log.info(f"  Updated: {src['name']} ({src['category']})")

    # Remove extras
    for key, tgt in tgt_by_key.items():
        if key not in src_by_key:
            if delete_favorite(auth, tgt["id"]):
                removed += 1
                log.info(f"  Removed: {tgt['name']} ({tgt['category']})")

    return added, removed, updated

def main():
    log.info("Starting NC Maps sync")
    source_favs = get_favorites(SOURCE)
    log.info(f"Source (spooky): {len(source_favs)} favorites")

    for user, pw in TARGETS:
        log.info(f"Syncing to {user}...")
        added, removed, updated = sync_account(source_favs, user, pw)
        log.info(f"  {user}: +{added} -{removed} ~{updated}")

    log.info("Sync complete")

if __name__ == "__main__":
    main()
