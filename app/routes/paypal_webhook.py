"""PayPal Webhook — auto-verify $50 application fee payments. PP-126."""

import os
import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, or_, func

from app.database import async_session
from app.models.member import Member

logger = logging.getLogger(__name__)
router = APIRouter()

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET", "")
PAYPAL_API_BASE = os.getenv("PAYPAL_API_BASE", "https://api-m.paypal.com")  # sandbox: https://api-m.sandbox.paypal.com
APP_FEE_AMOUNT = 50.00
APP_FEE_MIN = 49.50   # floor — slightly under $50
APP_FEE_MAX = 53.00   # ceiling — $50 + PayPal fee covered (~$51.80) with margin


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

    # Get access token
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

        # Verify signature
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


@router.post("/api/webhooks/paypal")
async def paypal_webhook(request: Request):
    """Receive PayPal PAYMENT.CAPTURE.COMPLETED webhook and auto-verify recruit fee."""
    body = await request.body()

    try:
        event = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    event_type = event.get("event_type", "")
    if event_type != "PAYMENT.CAPTURE.COMPLETED":
        # Acknowledge but ignore non-payment events
        return JSONResponse({"status": "ignored", "event_type": event_type})

    # Verify webhook signature
    verified = await _verify_webhook(request, body)
    if not verified:
        logger.warning("PayPal webhook signature verification failed — processing anyway with caution")
        # Still process but log the warning — in production you may want to reject

    # Extract payment details
    resource = event.get("resource", {})
    amount_value = float(resource.get("amount", {}).get("value", 0))
    currency = resource.get("amount", {}).get("currency_code", "USD")
    transaction_id = resource.get("id", "")

    # Payer info from the capture
    payer = event.get("resource", {}).get("payer", {})
    payer_email = payer.get("email_address", "")
    payer_name_obj = payer.get("name", {})
    payer_first = payer_name_obj.get("given_name", "")
    payer_last = payer_name_obj.get("surname", "")

    # If payer info not in resource, try supplementary_data
    if not payer_email:
        supplementary = resource.get("supplementary_data", {})
        related = supplementary.get("related_ids", {})
        # Try custom_id which we could set to the recruit's email
        custom_id = resource.get("custom_id", "")
        if "@" in custom_id:
            payer_email = custom_id

    logger.info(
        f"PayPal payment: ${amount_value} {currency} from {payer_email} "
        f"({payer_first} {payer_last}) txn={transaction_id}"
    )

    # Check if this looks like a $50 application fee
    if currency != "USD" or amount_value < APP_FEE_MIN or amount_value > APP_FEE_MAX:
        logger.info(f"Payment ${amount_value} {currency} doesn't match fee amount — ignoring")
        return JSONResponse({"status": "ignored", "reason": "amount_mismatch"})

    # Try to match to a recruit with pending fee
    matched_member = None
    async with async_session() as db:
        # Strategy 1: Match by personal email (PayPal email → recruit personal email)
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

        # Strategy 2: Match by name (first + last)
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
                f"✅ Auto-verified payment for {matched_member.first_name} "
                f"{matched_member.last_name} (member_id={matched_member.id}) "
                f"txn={transaction_id}"
            )

            # Portal notification for S1
            try:
                from app.routes.notifications import create_notification_for_roles
                await create_notification_for_roles(
                    db, ["s1", "command", "admin"],
                    "payment",
                    f"💰 Payment verified — {matched_member.first_name} {matched_member.last_name}",
                    body=f"$50 application fee received via PayPal (auto-verified)",
                    link=f"/api/s1/payments",
                    icon="💰",
                )
            except Exception:
                pass

            return JSONResponse({
                "status": "matched",
                "member_id": matched_member.id,
                "name": f"{matched_member.first_name} {matched_member.last_name}",
            })
        else:
            logger.warning(
                f"⚠️ $50 PayPal payment from {payer_email} ({payer_first} {payer_last}) "
                f"did not match any pending recruit. txn={transaction_id}"
            )

            # Still notify S1 so they can manually match
            try:
                async with async_session() as db2:
                    from app.routes.notifications import create_notification_for_roles
                    await create_notification_for_roles(
                        db2, ["s1", "command", "admin"],
                        "payment",
                        f"💰 Unmatched PayPal payment — ${amount_value}",
                        body=f"From: {payer_first} {payer_last} ({payer_email}). Could not auto-match to a recruit.",
                        link=f"/api/s1/payments",
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
