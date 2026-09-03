#!/usr/bin/env python3
"""Backfill the [S-1] Admin/Applications archive.

`move_applicant_files()` has never worked: it builds the Forms folder path
WITHOUT the "3 - " form-id prefix, so every call 404s and logs the misleading
"No Forms folder yet". Verified on production -- the prefixed path returns 207,
the unprefixed one 404s, [S-1] Admin/Applications/ is empty, and the daemon log
shows 0 successful moves against 20 skips dating to 2026-03-07.

Nothing was lost; the files are all still in Forms storage. This files the
backlog into the archive the daemon was always meant to build.

SCOPE: archiving happens at ONBOARDING time (move_applicant_files is only
called from _onboard_member step 4), so this only touches submissions belonging
to cards that have actually been onboarded. Files for applicants still moving
through the pipeline stay in Forms where the daemon expects them.

MOVE (not copy) matches the daemon's intended behaviour. A WebDAV MOVE inside
the same storage preserves the Nextcloud fileId, so Nextcloud Forms -- which
references uploads by fileId, not path -- keeps resolving them. The --limit
flag exists so that can be piloted on one submission and verified before the
rest are touched.

Dry-run by default. Idempotent: skips a file already present at the destination.
"""
import argparse
import json
import re
import sys
import urllib.parse

import requests

NC = "https://cloud.13thlegion.org"
BOARD = 5
COMPLETE_STACK = 16
FORM_ID = 3
FORM_TITLE = "Texas State Militia — Application & Background Check Release"
FORM_FOLDER_NAME = f"{FORM_ID} - {FORM_TITLE}"
STATE_FILE = "/opt/recruit-pipeline/state.json"


def dav(path: str) -> str:
    return f"{NC}/remote.php/dav/files/spooky/{urllib.parse.quote(path)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="only process N submissions (pilot)")
    ap.add_argument("--env", default="/opt/praetorium/.env")
    args = ap.parse_args()

    pw = ""
    with open(args.env, encoding="utf-8") as f:
        for line in f:
            if line.startswith("NC_SVC_PASS="):
                pw = line.split("=", 1)[1].strip()
                break
    if not pw:
        sys.exit("FATAL: NC_SVC_PASS not found")
    auth = ("spooky", pw)
    H = {"OCS-APIRequest": "true", "Accept": "application/json"}

    try:
        onboarded = set(json.load(open(STATE_FILE, encoding="utf-8")).get("onboarded_cards", []))
    except Exception as e:
        sys.exit(f"FATAL: cannot read {STATE_FILE}: {e}")
    print(f"onboarded_cards in state: {len(onboarded)}")

    # Cards that represent a completed onboarding: state list + Complete stack.
    targets = {}
    for stack_id in (COMPLETE_STACK,):
        r = requests.get(f"{NC}/index.php/apps/deck/api/v1.0/boards/{BOARD}/stacks/{stack_id}/cards",
                         auth=auth, headers=H, timeout=60)
        if r.status_code == 200:
            for c in r.json():
                targets[c["id"]] = c
    for cid in onboarded:
        if cid in targets:
            continue
        for sid in (11, 12, 13, 14, 15, 16, 81):
            rr = requests.get(f"{NC}/index.php/apps/deck/api/v1.0/boards/{BOARD}/stacks/{sid}/cards/{cid}",
                              auth=auth, headers=H, timeout=30)
            if rr.status_code == 200:
                targets[cid] = rr.json()
                break

    print(f"candidate onboarded cards: {len(targets)}")

    moved = skipped = nofiles = failed = 0
    processed = 0
    for cid, card in sorted(targets.items()):
        desc = card.get("description") or ""
        title = re.sub(r"^[^\w]*", "", card.get("title") or "").strip()
        m = re.search(r"\*Submission ID:\*\s*(\d+)", desc)
        if not m:
            continue
        sub_id = m.group(1)

        # Applicant name: prefer the Legal Name line, else the card title.
        nm = re.search(r"\*\*Legal Name:\*\*\s*(.+)", desc) or re.search(r"Legal Name[:\s]+(.+)", desc)
        name = (nm.group(1).strip() if nm else title).split("\n")[0].strip()
        name = re.sub(r"[^\w \-'.]", "", name).strip()
        if not name:
            continue
        folder_name = name.replace(" ", "_").replace(",", "")

        src_dir = f"Forms/{FORM_FOLDER_NAME}/{sub_id}"
        r = requests.request("PROPFIND", dav(src_dir), auth=auth,
                             headers={"Depth": "3"}, timeout=30)
        if r.status_code == 404:
            continue
        hrefs = [h for h in re.findall(r"<d:href>([^<]+)</d:href>", r.text) if not h.endswith("/")]
        if not hrefs:
            nofiles += 1
            continue

        if args.limit and processed >= args.limit:
            break
        processed += 1

        dest_dir = f"[S-1] Admin/Applications/{folder_name}"
        print(f"\ncard #{cid} — {name} (submission {sub_id}) -> {dest_dir}")

        if args.apply:
            requests.request("MKCOL", dav("[S-1] Admin/Applications"), auth=auth, timeout=30)
            mk = requests.request("MKCOL", dav(dest_dir), auth=auth, timeout=30)
            if mk.status_code not in (201, 405):  # 405 = already exists
                print(f"  MKCOL {dest_dir} -> {mk.status_code} {mk.text[:120]!r}")

        for href in hrefs:
            filename = urllib.parse.unquote(href.split("/")[-1])
            dest = dav(f"{dest_dir}/{filename}")

            if args.apply:
                head = requests.request("PROPFIND", dest, auth=auth,
                                        headers={"Depth": "0"}, timeout=20)
                if head.status_code == 207:
                    print(f"  = {filename} (already archived)")
                    skipped += 1
                    continue

            if not args.apply:
                print(f"  [dry-run] MOVE {filename}")
                moved += 1
                continue

            mv = requests.request("MOVE", f"{NC}{href}", auth=auth,
                                  headers={"Destination": dest, "Overwrite": "F"}, timeout=60)
            if mv.status_code in (201, 204):
                print(f"  -> {filename}  OK")
                moved += 1
            else:
                print(f"  !! {filename} MOVE failed {mv.status_code} {mv.text[:150]!r}")
                failed += 1

    print(f"\n{'would move' if not args.apply else 'moved'}: {moved} | "
          f"already archived: {skipped} | submissions with no files: {nofiles} | failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
