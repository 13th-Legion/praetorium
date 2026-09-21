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


class _FakeVerifyClient:
    """Captures what we actually POST to PayPal's verify endpoint.

    Every other test in this file mocks _verify_webhook wholesale, so nothing
    ever exercised the verification body itself -- which is exactly how the
    webhook_event-as-string bug survived from April to September.
    """

    def __init__(self):
        self.verify_payload = None

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        if "oauth2/token" in url:
            return _FakeResp(200, {"access_token": "fake-token"})
        if "verify-webhook-signature" in url:
            self.verify_payload = kw.get("json")
            return _FakeResp(200, {"verification_status": "SUCCESS"})
        return _FakeResp(404, {})


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class TestVerificationBody:
    """PayPal requires webhook_event to be the event OBJECT, not a JSON string.

    Sending body.decode() produced {"webhook_event": "{\\"id\\": ...}"}, so
    PayPal could never reconstruct the payload and verification always failed.
    Harmless while a failure only logged a warning; a hard outage once the
    2026-07-22 audit made verification fail-closed with a 401.
    """

    async def test_webhook_event_is_an_object_not_a_string(self, monkeypatch):
        fake = _FakeVerifyClient()
        monkeypatch.setattr(pw.httpx, "AsyncClient", fake)
        monkeypatch.setattr(pw, "PAYPAL_CLIENT_ID", "cid", raising=False)
        monkeypatch.setattr(pw, "PAYPAL_SECRET", "sec", raising=False)

        evt = _event()
        req = _make_request(evt)
        body = json.dumps(evt).encode()

        ok = await pw._verify_webhook(req, body)
        assert ok is True

        sent = fake.verify_payload
        assert sent is not None, "never called PayPal's verify endpoint"
        assert isinstance(sent["webhook_event"], dict), (
            "webhook_event must be the event OBJECT; sending a JSON string "
            "makes PayPal return FAILURE for every genuine delivery"
        )
        assert sent["webhook_event"]["event_type"] == "PAYMENT.CAPTURE.COMPLETED"

    async def test_verification_body_carries_all_signature_headers(self, monkeypatch):
        fake = _FakeVerifyClient()
        monkeypatch.setattr(pw.httpx, "AsyncClient", fake)
        monkeypatch.setattr(pw, "PAYPAL_CLIENT_ID", "cid", raising=False)
        monkeypatch.setattr(pw, "PAYPAL_SECRET", "sec", raising=False)

        evt = _event()
        await pw._verify_webhook(_make_request(evt), json.dumps(evt).encode())

        sent = fake.verify_payload
        for key in ("auth_algo", "cert_url", "transmission_id",
                    "transmission_sig", "transmission_time", "webhook_id"):
            assert key in sent, f"verification body missing {key}"

    async def test_failure_status_is_reported_not_swallowed(self, monkeypatch, caplog):
        """A permanently-broken verification must not look like a spoof.

        The old code returned a bare False and logged nothing, so this ran
        silently for two months.
        """
        class _FailClient(_FakeVerifyClient):
            async def post(self, url, **kw):
                if "oauth2/token" in url:
                    return _FakeResp(200, {"access_token": "fake-token"})
                return _FakeResp(200, {"verification_status": "FAILURE"})

        monkeypatch.setattr(pw.httpx, "AsyncClient", _FailClient())
        monkeypatch.setattr(pw, "PAYPAL_CLIENT_ID", "cid", raising=False)
        monkeypatch.setattr(pw, "PAYPAL_SECRET", "sec", raising=False)

        evt = _event()
        with caplog.at_level("ERROR"):
            ok = await pw._verify_webhook(_make_request(evt), json.dumps(evt).encode())

        assert ok is False
        assert any("FAILURE" in r.message or "FAILURE" in str(r.args)
                   for r in caplog.records), "the failure reason must be logged"

    async def test_missing_credentials_fail_closed(self, monkeypatch):
        """No credentials must never mean 'assume valid'."""
        monkeypatch.setattr(pw, "PAYPAL_CLIENT_ID", "", raising=False)
        monkeypatch.setattr(pw, "PAYPAL_SECRET", "", raising=False)
        evt = _event()
        ok = await pw._verify_webhook(_make_request(evt), json.dumps(evt).encode())
        assert ok is False


class TestOutcomeRecorded:
    """The audit row must record what actually happened to a payment.

    The dedup row is inserted as "processing" before matching so retries can be
    short-circuited, but nothing ever wrote the final state back. Every row sat
    at "processing" forever, so the audit trail could not answer "what happened
    to this payment?" -- confirmed in production on 2026-09-21, where all four
    rows read "processing".
    """

    @staticmethod
    async def _outcome_for(txn):
        from sqlalchemy import select as _select
        from app.models.webhook_event import WebhookEvent
        from app import database
        async with database.async_session() as db:
            row = (await db.execute(
                _select(WebhookEvent).where(WebhookEvent.transaction_id == txn)
            )).scalar_one_or_none()
            return row.outcome if row else None

    async def test_unmatched_payment_records_unmatched(self, patch_global_session):
        with mock.patch.object(pw, "_verify_webhook", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(pw, "_find_deck_card", new=mock.AsyncMock(return_value=None)):
            resp = await pw.paypal_webhook(_make_request(_event(txn="TXN-OUT-UNMATCHED")))

        assert (await _body(resp))["status"] == "unmatched"
        assert await self._outcome_for("TXN-OUT-UNMATCHED") == "unmatched"

    async def test_needs_review_records_needs_review(self, patch_global_session):
        name_match = {
            "card_id": 99, "stack_id": 14, "name": "No Body",
            "email": "nobody@example.com", "match_type": "name", "needs_review": True,
        }
        with mock.patch.object(pw, "_verify_webhook", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(pw, "_find_deck_card", new=mock.AsyncMock(return_value=name_match)), \
             mock.patch.object(pw, "_annotate_deck_card", new=mock.AsyncMock(return_value=True)):
            resp = await pw.paypal_webhook(_make_request(_event(txn="TXN-OUT-REVIEW")))

        assert (await _body(resp))["status"] == "needs_review"
        assert await self._outcome_for("TXN-OUT-REVIEW") == "needs_review"

    async def test_outcome_never_left_as_processing(self, patch_global_session):
        """The exact production symptom."""
        with mock.patch.object(pw, "_verify_webhook", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(pw, "_find_deck_card", new=mock.AsyncMock(return_value=None)):
            await pw.paypal_webhook(_make_request(_event(txn="TXN-OUT-TERMINAL")))

        assert await self._outcome_for("TXN-OUT-TERMINAL") != "processing"

    async def test_audit_write_failure_is_swallowed(self, patch_global_session, monkeypatch):
        """An audit-trail problem must never cost us a payment.

        Breaks the DB session rather than mocking _set_outcome: the protection
        lives INSIDE that function, so replacing it would remove the very thing
        under test.
        """
        def _boom(*a, **kw):
            raise RuntimeError("db exploded")

        monkeypatch.setattr(pw.database, "async_session", _boom)
        # Must not raise.
        await pw._set_outcome("TXN-OUT-BOOM", "unmatched")

    async def test_blank_transaction_id_is_a_noop(self, patch_global_session):
        """No txn id means no audit row to update; must not error."""
        await pw._set_outcome("", "unmatched")

    async def test_unknown_transaction_id_is_a_noop(self, patch_global_session):
        """A txn with no row (e.g. insert was skipped) must not error."""
        await pw._set_outcome("TXN-DOES-NOT-EXIST", "unmatched")
