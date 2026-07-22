"""Webhook event dedup log — idempotency for external webhooks (PayPal).

PayPal delivers webhooks at-least-once and retries on any non-2xx/timeout, so
the same PAYMENT.CAPTURE.COMPLETED can arrive multiple times. Without dedup,
each delivery re-runs matching, re-annotates the Deck card, and re-fires S1
notifications (and, chained with a spoof, could be replayed indefinitely).

This table records every webhook transaction we've already processed. The
handler inserts the row up front inside its own transaction; a duplicate
delivery hits the UNIQUE constraint on (provider, transaction_id) and is
short-circuited with {"status": "duplicate"}.

Match on the stable external transaction id, never on an internal row id.
"""

from datetime import datetime

from sqlalchemy import String, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "transaction_id", name="uq_webhook_provider_txn"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Source of the webhook, e.g. "paypal".
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # External transaction/capture id (PayPal resource.id). The dedup key.
    transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # PayPal event_type (e.g. PAYMENT.CAPTURE.COMPLETED) for audit.
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Terminal disposition of the first processing (matched/unmatched/etc.) for audit.
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
