"""PayPal Webhook — auto-verify $50 application fee payments. PP-126.

Matches incoming PayPal payments against Deck pipeline cards (not the members
table) so that payments can be verified *before* full onboarding creates a
member record.  Falls back to members table for edge cases where someone
already has a DB row (re-applicants, manual record, etc.).
"""

import os
import re
import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, or_, func

from app.database import async_session
from app.models.member import Member
from app.settings import (
    NC_SVC_USER as NC_SPOOKY_USER,
    NC_SVC_PASS as NC_SPOOKY_PASS,
)

logger = logging.getLogger(__name__)
router = APIRouter()

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET", "")
PAYPAL_API_BASE = os.getenv("PAYPAL_API_BASE", "https://api-m.paypal.com")
APP_FEE_AMOUNT = 50.00
APP_FEE_MIN = 49.50   # floor — slightly under $50
APP_FEE_MAX = 53.00   # ceiling — $50 + PayPal fee covered (~$51.80) with margin

# Nextcloud Deck config (mirrors recruit-daemon & s1_admin constants)
NC_URL = "https://cloud.13thlegion.org"
DECK_BOARD_ID = 5
# Stacks where an applicant could plausibly pay from
DECK_PAYMENT_STACKS = {14, 13, 12, 11}  # Documents & Payment first, then earlier stages


# ─── PayPal signature verification ──────────────────────────────────────────

async def _verify_webhook(request: Request, body: bytes) -> bool:
    """Verify PayPal webhook signature using PayPal's verification API."""
    if not PAYPAL_CLIENT_ID or not PAYPAL_SECRET:
        logger.warning("PayPal credentials not configured — skipping verification")
        return False

    headers = request.headers
    verification_body = {
        "auth_algo": headers.get("paypal-auth-algo", ""),
        "cert_url": headers.get("paypal-cert-url", ""),
        "transmission_id": headers.get("paypal-transmission-id", ""),
        "transmission_sig": headers.get("paypal-transmission-sig", ""),
        "transmission_time": headers.get("paypal-transmission-time", ""),
        "webhook_id": os.getenv("PAYPAL_WEBHOOK_ID", ""),
        "webhook_event": body.decode("utf-8"),
    }

    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            f"{PAYPAL_API_BASE}/v1/oauth2/token",
            auth=(PAYPAL_CLIENT_ID, PAYPAL_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            logger.error(f"PayPal token request failed: {token_resp.status_code}")
            return False

        access_token = token_resp.json().get("access_token", "")

        verify_resp = await client.post(
            f"{PAYPAL_API_BASE}/v1/notifications/verify-webhook-signature",
            json=verification_body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        if verify_resp.status_code == 200:
            result = verify_resp.json()
            return result.get("verification_status") == "SUCCESS"

    return False


# ─── Deck card helpers ───────────────────────────────────────────────────────

def _parse_card_emails(description: str) -> list[str]:
    """Extract all email addresses from a Deck card description.

    Returns a list of emails found in the **Email:** and **📧 Proton Mail:**
    fields (personal email first, then Proton if present and valid).
    """
    emails = []
    for line in description.split("\n"):
        line = line.strip()
        if line.startswith("**Email:**"):
            val = line.split(":**", 1)[1].strip().strip("*_ ")
            if val and "@" in val:
                emails.append(val)
        elif "**📧 Proton Mail:**" in line:
            val = line.split(":**", 1)[1].strip().strip("*_ ")
            # Skip placeholder text
            if val and "@" in val and "pending" not in val.lower():
                emails.append(val)
    return emails


def _parse_card_name(title: str) -> str:
    """Extract clean name from a Deck card title (strip emoji prefixes)."""
    return re.sub(r"^[📋✅🔍⚠️❌\s]+", "", title).strip()


async def _find_deck_card(payer_email: str, payer_first: str, payer_last: str,
                          custom_id_email: str) -> dict | None:
    """Search Deck pipeline cards for a matching applicant.

    Returns a dict with card info on match, or None.
    Match priority:
      1. custom_id email (from checkout page) == card Email field
      2. PayPal payer email == card Email field
      3. PayPal payer name == card title name (case-insensitive)
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SPOOKY_USER, NC_SPOOKY_PASS),
            )
            if resp.status_code != 200:
                logger.error(f"Deck API returned {resp.status_code}")
                return None

            stacks = resp.json()
    except Exception as e:
        logger.error(f"Deck API request failed: {e}")
        return None

    # Collect candidate cards from relevant stacks, ordered by stack priority
    candidates = []
    for stack in stacks:
        stack_id = stack.get("id")
        if stack_id not in DECK_PAYMENT_STACKS:
            continue
        for card in stack.get("cards", []):
            candidates.append((stack_id, card))

    # Sort so Documents & Payment (14) cards are checked first
    candidates.sort(key=lambda x: (x[0] != 14, x[0]))

    emails_to_match = set()
    if custom_id_email:
        emails_to_match.add(custom_id_email.lower())
    if payer_email:
        emails_to_match.add(payer_email.lower())

    payer_full = f"{payer_first} {payer_last}".strip().lower()

    for stack_id, card in candidates:
        desc = card.get("description", "")
        card_emails = {e.lower() for e in _parse_card_emails(desc)}
        card_name = _parse_card_name(card.get("title", "")).lower()

        # Strategy 1 & 2: Email match (custom_id or payer email vs card emails)
        matched_email = card_emails & emails_to_match
        if matched_email:
            return {
                "card_id": card["id"],
                "stack_id": stack_id,
                "name": _parse_card_name(card.get("title", "")),
                "email": next(iter(matched_email)),
                "match_type": "email",
            }

        # Strategy 3: Name match
        if payer_full and card_name and card_name == payer_full:
            return {
                "card_id": card["id"],
                "stack_id": stack_id,
                "name": _parse_card_name(card.get("title", "")),
                "email": card_email,
                "match_type": "name",
            }

    return None


async def _annotate_deck_card(card_id: int, stack_id: int, transaction_id: str,
                              amount: float, payer_email: str) -> bool:
    """Append a payment confirmation note to the card description."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Fetch current card
            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}"
                f"/stacks/{stack_id}/cards/{card_id}",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SPOOKY_USER, NC_SPOOKY_PASS),
            )
            if resp.status_code != 200:
                logger.error(f"Failed to fetch card {card_id}: {resp.status_code}")
                return False

            card = resp.json()
            current_desc = card.get("description", "")

            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            payment_note = (
                f"\n\n---\n"
                f"**💰 Payment Verified (auto)**\n"
                f"Amount: ${amount:.2f} via PayPal\n"
                f"PayPal email: {payer_email}\n"
                f"Transaction: {transaction_id}\n"
                f"Verified: {timestamp}"
            )
            updated_desc = current_desc + payment_note

            # Update card — include If-Match for ETag if available
            update_headers = {
                "OCS-APIRequest": "true",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            etag = card.get("ETag") or resp.headers.get("ETag")
            if etag:
                update_headers["If-Match"] = etag

            put_resp = await client.put(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}"
                f"/stacks/{stack_id}/cards/{card_id}",
                headers=update_headers,
                auth=(NC_SPOOKY_USER, NC_SPOOKY_PASS),
                json={
                    "title": card.get("title", ""),
                    "description": updated_desc,
                    "type": card.get("type", "plain"),
                    "order": card.get("order", 0),
                },
            )
            if put_resp.status_code in (200, 201):
                logger.info(f"Annotated Deck card #{card_id} with payment confirmation")
                return True
            else:
                logger.error(
                    f"Failed to update card #{card_id}: {put_resp.status_code} "
                    f"{put_resp.text[:200]}"
                )
                return False

    except Exception as e:
        logger.error(f"Error annotating Deck card #{card_id}: {e}")
        return False


# ─── Webhook endpoint ────────────────────────────────────────────────────────

@router.post("/api/webhooks/paypal")
async def paypal_webhook(request: Request):
    """Receive PayPal PAYMENT.CAPTURE.COMPLETED webhook and auto-verify recruit fee.

    Match order:
      1. Deck pipeline cards (by email from custom_id / payer, then by name)
      2. Members table fallback (for re-applicants or manually-created records)
      3. Unmatched — notify S1 for manual resolution
    """
    body = await request.body()

    try:
        event = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    event_type = event.get("event_type", "")
    if event_type != "PAYMENT.CAPTURE.COMPLETED":
        return JSONResponse({"status": "ignored", "event_type": event_type})

    # Verify webhook signature
    verified = await _verify_webhook(request, body)
    if not verified:
        logger.warning("PayPal webhook signature verification failed — processing anyway with caution")

    # Extract payment details
    resource = event.get("resource", {})
    amount_value = float(resource.get("amount", {}).get("value", 0))
    currency = resource.get("amount", {}).get("currency_code", "USD")
    transaction_id = resource.get("id", "")

    # Payer info
    payer = resource.get("payer", {})
    payer_email = payer.get("email_address", "")
    payer_name_obj = payer.get("name", {})
    payer_first = payer_name_obj.get("given_name", "")
    payer_last = payer_name_obj.get("surname", "")

    # custom_id carries the applicant's email from the checkout page
    custom_id = resource.get("custom_id", "")
    custom_id_email = custom_id if "@" in custom_id else ""

    # If payer_email still empty, use custom_id
    if not payer_email and custom_id_email:
        payer_email = custom_id_email

    logger.info(
        f"PayPal payment: ${amount_value:.2f} {currency} from {payer_email} "
        f"({payer_first} {payer_last}) custom_id={custom_id} txn={transaction_id}"
    )

    # Validate amount
    if currency != "USD" or amount_value < APP_FEE_MIN or amount_value > APP_FEE_MAX:
        logger.info(f"Payment ${amount_value:.2f} {currency} doesn't match fee amount — ignoring")
        return JSONResponse({"status": "ignored", "reason": "amount_mismatch"})

    # ── Strategy 1: Match against Deck pipeline cards ────────────────────
    deck_match = await _find_deck_card(payer_email, payer_first, payer_last, custom_id_email)

    if deck_match:
        logger.info(
            f"✅ Deck match ({deck_match['match_type']}): card #{deck_match['card_id']} "
            f"— {deck_match['name']} (stack {deck_match['stack_id']}) txn={transaction_id}"
        )

        # Annotate the card with payment confirmation
        await _annotate_deck_card(
            deck_match["card_id"], deck_match["stack_id"],
            transaction_id, amount_value, payer_email,
        )

        # If a member record happens to exist already, update it too
        async with async_session() as db:
            name_parts = deck_match["name"].split(None, 1)
            if len(name_parts) >= 2:
                result = await db.execute(
                    select(Member).where(
                        func.lower(Member.first_name) == name_parts[0].lower(),
                        func.lower(Member.last_name) == name_parts[1].lower(),
                    )
                )
                member = result.scalar_one_or_none()
                if member and member.app_fee_status != "paid":
                    member.app_fee_status = "paid"
                    member.app_fee_method = "paypal"
                    member.app_fee_paid_at = datetime.utcnow()
                    await db.commit()
                    logger.info(f"Also updated existing member record #{member.id}")

            # Notify S1
            try:
                from app.routes.notifications import create_notification_for_roles
                await create_notification_for_roles(
                    db, ["s1", "command", "admin"],
                    "payment",
                    f"💰 Payment verified — {deck_match['name']}",
                    body=(
                        f"$50 application fee received via PayPal (auto-verified from pipeline card). "
                        f"Match: {deck_match['match_type']}."
                    ),
                    link="/api/s1/payments",
                    icon="💰",
                )
            except Exception:
                pass

        return JSONResponse({
            "status": "matched",
            "source": "deck",
            "card_id": deck_match["card_id"],
            "name": deck_match["name"],
            "match_type": deck_match["match_type"],
        })

    # ── Strategy 2: Fallback to members table (re-applicants, etc.) ──────
    matched_member = None
    async with async_session() as db:
        if payer_email:
            result = await db.execute(
                select(Member).where(
                    or_(
                        func.lower(Member.personal_email) == payer_email.lower(),
                        func.lower(Member.email) == payer_email.lower(),
                    ),
                    Member.app_fee_status.in_(["pending", None]),
                )
            )
            matched_member = result.scalar_one_or_none()

        if not matched_member and payer_first and payer_last:
            result = await db.execute(
                select(Member).where(
                    func.lower(Member.first_name) == payer_first.lower(),
                    func.lower(Member.last_name) == payer_last.lower(),
                    Member.app_fee_status.in_(["pending", None]),
                )
            )
            matched_member = result.scalar_one_or_none()

        if matched_member:
            matched_member.app_fee_status = "paid"
            matched_member.app_fee_method = "paypal"
            matched_member.app_fee_paid_at = datetime.utcnow()
            await db.commit()

            logger.info(
                f"✅ DB fallback match: {matched_member.first_name} "
                f"{matched_member.last_name} (member_id={matched_member.id}) "
                f"txn={transaction_id}"
            )

            try:
                from app.routes.notifications import create_notification_for_roles
                await create_notification_for_roles(
                    db, ["s1", "command", "admin"],
                    "payment",
                    f"💰 Payment verified — {matched_member.first_name} {matched_member.last_name}",
                    body="$50 application fee received via PayPal (matched from member record).",
                    link="/api/s1/payments",
                    icon="💰",
                )
            except Exception:
                pass

            return JSONResponse({
                "status": "matched",
                "source": "members",
                "member_id": matched_member.id,
                "name": f"{matched_member.first_name} {matched_member.last_name}",
            })

    # ── Strategy 3: No match — alert S1 for manual resolution ────────────
    logger.warning(
        f"⚠️ $50 PayPal payment from {payer_email} ({payer_first} {payer_last}) "
        f"did not match any pipeline card or member record. txn={transaction_id}"
    )

    try:
        async with async_session() as db2:
            from app.routes.notifications import create_notification_for_roles
            await create_notification_for_roles(
                db2, ["s1", "command", "admin"],
                "payment",
                f"💰 Unmatched PayPal payment — ${amount_value:.2f}",
                body=(
                    f"From: {payer_first} {payer_last} ({payer_email}). "
                    f"Could not auto-match to a pipeline card or member record."
                ),
                link="/api/s1/payments",
                icon="⚠️",
            )
    except Exception:
        pass

    return JSONResponse({
        "status": "unmatched",
        "payer_email": payer_email,
        "payer_name": f"{payer_first} {payer_last}",
        "amount": amount_value,
        "transaction_id": transaction_id,
    })
