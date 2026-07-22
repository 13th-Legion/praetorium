"""Phase 1 — PayPal webhook (money path).

Covers the P0 security fixes:
  * signature verification failure REJECTS in prod (no side effects)
  * idempotency: a replayed transaction_id is deduped
  * amount-window boundaries
  * name-only match routes to manual review (never auto-verifies)
"""

import json
import pytest
from unittest import mock

from starlette.requests import Request

import app.routes.paypal_webhook as pw

pytestmark = pytest.mark.integration


def _make_request(body_dict):
    body = json.dumps(body_dict).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "method": "POST",
        "headers": [(b"content-type", b"application/json")],
        "path": "/api/webhooks/paypal", "query_string": b"",
    }
    return Request(scope, receive)


def _event(txn="TXN-1", value="50.00", first="No", last="Body",
           email="nobody@example.com"):
    return {
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": txn,
            "amount": {"value": value, "currency_code": "USD"},
            "payer": {"email_address": email,
                      "name": {"given_name": first, "surname": last}},
        },
    }


async def _body(resp):
    return json.loads(bytes(resp.body).decode())


async def test_bad_signature_rejected_in_prod(patch_global_session, monkeypatch):
    monkeypatch.setattr(pw.get_settings(), "debug", False, raising=False)
    with mock.patch.object(pw, "_verify_webhook", new=mock.AsyncMock(return_value=False)):
        resp = await pw.paypal_webhook(_make_request(_event()))
    assert resp.status_code == 401


async def test_valid_first_delivery_processes(patch_global_session):
    with mock.patch.object(pw, "_verify_webhook", new=mock.AsyncMock(return_value=True)), \
         mock.patch.object(pw, "_find_deck_card", new=mock.AsyncMock(return_value=None)):
        resp = await pw.paypal_webhook(_make_request(_event(txn="TXN-FIRST")))
    body = await _body(resp)
    assert resp.status_code == 200
    assert body["status"] == "unmatched"   # no card/member in empty test DB


async def test_replay_is_deduped(patch_global_session):
    ev = _event(txn="TXN-REPLAY")
    with mock.patch.object(pw, "_verify_webhook", new=mock.AsyncMock(return_value=True)), \
         mock.patch.object(pw, "_find_deck_card", new=mock.AsyncMock(return_value=None)):
        first = await pw.paypal_webhook(_make_request(ev))
        second = await pw.paypal_webhook(_make_request(ev))
    assert (await _body(first))["status"] == "unmatched"
    assert (await _body(second))["status"] == "duplicate"


async def test_name_only_match_needs_review(patch_global_session):
    """A name-only deck match must NOT auto-verify — it routes to S1 review."""
    name_match = {
        "card_id": 99, "stack_id": 14, "name": "No Body",
        "email": "nobody@example.com", "match_type": "name", "needs_review": True,
    }
    with mock.patch.object(pw, "_verify_webhook", new=mock.AsyncMock(return_value=True)), \
         mock.patch.object(pw, "_find_deck_card", new=mock.AsyncMock(return_value=name_match)), \
         mock.patch.object(pw, "_annotate_deck_card", new=mock.AsyncMock(return_value=True)):
        resp = await pw.paypal_webhook(_make_request(_event(txn="TXN-NAME")))
    body = await _body(resp)
    assert body["status"] == "needs_review"


class TestAmountWindow:
    def test_underpay_below_floor_not_app_fee(self):
        assert not (pw.APP_FEE_MIN <= 49.49 <= pw.APP_FEE_MAX)

    def test_floor_is_full_fee(self):
        assert pw.APP_FEE_MIN == 50.00
        assert pw.APP_FEE_MIN <= 50.00 <= pw.APP_FEE_MAX

    def test_ceiling_covers_paypal_fee(self):
        assert pw.APP_FEE_MIN <= 51.80 <= pw.APP_FEE_MAX

    def test_above_ceiling_not_app_fee(self):
        assert not (pw.APP_FEE_MIN <= 53.01 <= pw.APP_FEE_MAX)
