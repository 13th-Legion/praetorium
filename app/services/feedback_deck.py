"""Turn Nextcloud Forms submissions into Deck cards.

Forms stores the response. It does not talk to Deck. This poller reads new
submissions and files one card on the Inbox stack. Other units never get
write access to that board.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from app import database
from app.models.feedback import FeedbackDeckCard
from app.settings import (
    FEEDBACK_BOARD_ID,
    FEEDBACK_FORM_ID,
    FEEDBACK_INBOX_STACK_ID,
    NC_SVC_PASS,
    NC_SVC_USER,
)

log = logging.getLogger(__name__)

NC_URL = "https://cloud.13thlegion.org"
POLL_SECONDS = 600
_MARKER = "forms-submission:"


def _answer_text(answer: dict) -> str:
    text = answer.get("text")
    if text:
        return str(text).strip()
    options = answer.get("options") or []
    parts = []
    for opt in options:
        if isinstance(opt, dict):
            parts.append(str(opt.get("text") or "").strip())
        elif opt:
            parts.append(str(opt).strip())
    return ", ".join(p for p in parts if p)


def card_from_submission(submission: dict, questions: list[dict]) -> tuple[str, str]:
    """Title and description for one form response. Description starts with the dedupe marker."""
    labels = {q.get("id"): (q.get("text") or "") for q in questions}
    by_label: dict[str, list[str]] = {}
    for answer in submission.get("answers") or []:
        label = labels.get(answer.get("questionId")) or "Note"
        text = _answer_text(answer)
        if text:
            by_label.setdefault(label, []).append(text)

    def grab(name: str) -> str:
        return "; ".join(by_label.get(name) or [])

    kind = grab("Type") or "Feedback"
    unit = grab("Unit name / metro") or "Unknown unit"
    severity = grab("Severity")
    title = f"{kind}: {unit}" + (f" ({severity})" if severity else "")
    lines = [f"{_MARKER}{FEEDBACK_FORM_ID}:{submission.get('id')}"]
    seen = set()
    for question in questions:
        label = question.get("text") or ""
        if label and label in by_label and label not in seen:
            lines.append(f"{label}: {'; '.join(by_label[label])}")
            seen.add(label)
    return title[:140], "\n".join(lines)


async def _ocs_json(client: httpx.AsyncClient, path: str):
    resp = await client.get(
        f"{NC_URL}{path}",
        auth=(NC_SVC_USER, NC_SVC_PASS),
        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("ocs", {}).get("data", data)


async def sync_feedback_once() -> int:
    """File cards for submissions we have not filed yet. Returns how many were created."""
    if not NC_SVC_PASS:
        return 0
    async with httpx.AsyncClient() as client:
        payload = await _ocs_json(client, f"/ocs/v2.php/apps/forms/api/v3/forms/{FEEDBACK_FORM_ID}/submissions")
    if not isinstance(payload, dict):
        log.warning("feedback form payload was %s, not an object", type(payload).__name__)
        return 0
    questions = payload.get("questions") or []
    submissions = payload.get("submissions") or []

    created = 0
    async with database.async_session() as db:
        try:
            known = set((await db.execute(select(FeedbackDeckCard.submission_id))).scalars().all())
        except ProgrammingError:
            await db.rollback()
            log.warning("feedback_deck_cards is missing; run migration 0016")
            return 0
        for submission in submissions:
            sid = submission.get("id")
            if sid is None or sid in known:
                continue
            title, description = card_from_submission(submission, questions)
            card_id = await _file_card(title, description)
            if card_id is None:
                continue
            db.add(FeedbackDeckCard(submission_id=sid, card_id=card_id))
            known.add(sid)
            created += 1
        if created:
            await db.commit()
    if created:
        log.info("filed %s feedback card(s) on Deck", created)
    return created


async def _file_card(title: str, description: str) -> int | None:
    url = (
        f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/"
        f"{FEEDBACK_BOARD_ID}/stacks/{FEEDBACK_INBOX_STACK_ID}/cards"
    )
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={"title": title, "type": "plain", "order": 999},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                timeout=30,
            )
            if resp.status_code not in (200, 201):
                log.warning("deck card create returned %s", resp.status_code)
                return None
            card = resp.json()
            card_id = card.get("id")
            if not card_id:
                return None
            put = await client.put(
                f"{url}/{card_id}",
                json={"title": title, "type": "plain", "description": description, "owner": NC_SVC_USER},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                timeout=30,
            )
            if put.status_code not in (200, 201):
                log.warning("deck card description returned %s for card %s", put.status_code, card_id)
            return int(card_id)
    except Exception:
        log.exception("feedback deck filing failed")
        return None


async def feedback_sync_loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await sync_feedback_once()
        except Exception:
            log.exception("feedback sync failed")
        await asyncio.sleep(POLL_SECONDS)
