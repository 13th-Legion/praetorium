#!/usr/bin/env python3
"""Backfill Deck attachments that were silently dropped from ~2026-08-04.

Deck's AttachmentApiController::create signature is
    create(int $cardId, string $type, string $data)
with no default on $data. The recruit daemon posted only `type` + the file, so
Nextcloud's AppFramework rejected every upload with a bare HTTP 400 and an
empty body BEFORE the controller ran -- nothing reached nextcloud.log. Uploads
had been failing silently for a month.

The source files were never lost: they still live in spooky's
  Forms/3 - Texas State Militia — Application & Background Check Release/<submission_id>/

This walks board 5, finds cards with no attachments, reads the
"*Submission ID:* N" line the daemon writes into every card description, and
re-attaches that submission's files using the corrected call.

Dry-run by default. --apply to write. Idempotent: skips any card that already
has an attachment with the same filename.
"""
import argparse
import re
import sys
import urllib.parse

import requests

NC = "https://cloud.13thlegion.org"
BOARD = 5
FORM_FOLDER = "Forms/3 - Texas State Militia — Application & Background Check Release"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
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

    stacks = requests.get(f"{NC}/index.php/apps/deck/api/v1.0/boards/{BOARD}/stacks",
                          auth=auth, headers=H, timeout=60).json()

    total_cards = fixed = skipped = failed = 0
    for stack in stacks:
        sid = stack.get("id")
        for card in stack.get("cards") or []:
            cid = card.get("id")
            title = (card.get("title") or "")[:40]

            # Full card (list view omits attachments)
            full = requests.get(
                f"{NC}/index.php/apps/deck/api/v1.0/boards/{BOARD}/stacks/{sid}/cards/{cid}",
                auth=auth, headers=H, timeout=30).json()
            existing = {a.get("data") for a in (full.get("attachments") or [])}
            desc = full.get("description") or ""

            m = re.search(r"\*Submission ID:\*\s*(\d+)", desc)
            if not m:
                continue
            sub_id = m.group(1)
            total_cards += 1

            folder = f"{NC}/remote.php/dav/files/spooky/{urllib.parse.quote(FORM_FOLDER)}/{sub_id}"
            r = requests.request("PROPFIND", folder, auth=auth,
                                 headers={"Depth": "3"}, timeout=30)
            if r.status_code == 404:
                print(f"  card #{cid} {title!r}: submission {sub_id} has no Forms folder")
                continue
            hrefs = [h for h in re.findall(r"<d:href>([^<]+)</d:href>", r.text)
                     if not h.endswith("/")]
            if not hrefs:
                continue

            for href in hrefs:
                filename = urllib.parse.unquote(href.split("/")[-1])
                if filename in existing:
                    skipped += 1
                    continue
                if not args.apply:
                    print(f"  [dry-run] card #{cid} {title!r} <- {filename}")
                    fixed += 1
                    continue

                dl = requests.get(f"{NC}{href}", auth=auth, timeout=60)
                if dl.status_code != 200:
                    print(f"  card #{cid}: download {filename} failed {dl.status_code}")
                    failed += 1
                    continue
                up = requests.post(
                    f"{NC}/index.php/apps/deck/api/v1.0/boards/{BOARD}/stacks/{sid}/cards/{cid}/attachments",
                    auth=auth, headers={"OCS-APIRequest": "true"},
                    data={"type": "deck_file", "data": filename},   # <-- the fix
                    files={"file": (filename, dl.content)},
                    timeout=60,
                )
                if up.status_code in (200, 201):
                    print(f"  card #{cid} {title!r} <- {filename}  OK")
                    fixed += 1
                else:
                    print(f"  card #{cid} {title!r} <- {filename}  FAILED "
                          f"{up.status_code} {up.text[:120]!r}")
                    failed += 1

    print(f"\ncards with a Submission ID: {total_cards}")
    print(f"{'would attach' if not args.apply else 'attached'}: {fixed} | "
          f"already present: {skipped} | failed: {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
