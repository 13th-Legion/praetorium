"""Regressions from the DeepSeek v4 pro audit."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from app.models.s2_intel import IIR
from app.routes.paypal_webhook import unpaid_upcoming_meal_stmt
from app.routes.s2_admin import _sniff_map
from app.routes.s4_admin import _sniff_receipt, _money
from app.services.attendance import counts_as_no_show
from app.services.training_sites import choose_site_list, _seed
from tests.conftest import make_session_cookie
from tests.factories import make_event, make_member, make_rsvp
from fastapi.testclient import TestClient
from app.main import app as fastapi_app

pytestmark = pytest.mark.integration


def _client(roles, username="audit.user"):
    cookie = make_session_cookie({
        "user": {
            "username": username,
            "display_name": username,
            "email": "audit@13thlegion.org",
            "groups": [],
            "roles": list(roles),
        },
        "contact_verified": True,
    })
    c = TestClient(fastapi_app, raise_server_exceptions=False, follow_redirects=False)
    c.cookies.set("session", cookie)
    return c


def test_no_show_analytics_ignores_unconfirmed_rsvp():
    pending = SimpleNamespace(no_show=False, status="attending", attended=False)
    flagged = SimpleNamespace(no_show=True, status="attending", attended=False)
    assert counts_as_no_show(pending) is False
    assert counts_as_no_show(flagged) is True


def test_deactivated_sites_are_not_replaced_by_the_seed():
    seed = _seed()
    assert len(choose_site_list(0, [])) == len(seed)
    assert choose_site_list(3, []) == []
    assert choose_site_list(3, [{"key": "able"}]) == [{"key": "able"}]


def test_money_quantizes_to_cents():
    assert _money("15") == _money("15.00")
    assert _money("15.005") == _money("15.01")
    with pytest.raises(ValueError):
        _money("0")
    with pytest.raises(ValueError):
        _money("nope")


def test_receipt_and_map_sniff_rejects_html():
    html = b"<!doctype html><script>alert(1)</script>"
    assert _sniff_receipt(html) is None
    assert _sniff_map(html) is None
    assert _sniff_receipt(b"%PDF-1.7\n") == "application/pdf"
    assert _sniff_map(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "image/png"


class TestGuestCannotReadS2Secrets:
    async def test_guest_does_not_see_command_only_iir(self, db_session, patch_global_session):
        db_session.add(IIR(
            report_number="IIR-0099",
            dtg="251200Z SEP 26",
            country_area="AO",
            subject="COMMAND-ONLY-SECRET",
            details="<p>body</p>",
            dissemination_tier="command_only",
        ))
        db_session.add(IIR(
            report_number="IIR-0100",
            dtg="251300Z SEP 26",
            country_area="AO",
            subject="UNIT-WIDE-OK",
            details="<p>body</p>",
            dissemination_tier="unit_wide",
        ))
        await db_session.commit()

        guest = _client(["guest", "s2", "command", "admin"], username="guest.reader")
        resp = guest.get("/api/s2/iir")
        assert resp.status_code == 200
        assert "COMMAND-ONLY-SECRET" not in resp.text
        assert "UNIT-WIDE-OK" in resp.text

        denied = guest.get("/api/s2/challenge")
        assert denied.status_code == 403

    async def test_rally_partial_escapes_event_title(self, db_session, patch_global_session):
        await make_event(
            db_session,
            title="<script>alert(1)</script>",
            category="ftx",
            date_start=datetime(2027, 6, 1, 8, 0),
            warno_issued_at=datetime(2026, 9, 1, 8, 0),
            rally_point=None,
            status="active",
        )
        await db_session.commit()

        client = _client(["command", "admin"])
        resp = client.get("/api/s2/events-needing-rally-point")
        assert resp.status_code == 200
        assert "<script>" not in resp.text
        assert "&lt;script&gt;" in resp.text


class TestMealPaymentPicksSoonestFuture:
    async def test_later_unpaid_ftx_is_not_chosen(self, db_session):
        member = await make_member(db_session)
        soon = await make_event(
            db_session, title="Soon FTX", category="ftx",
            date_start=datetime(2026, 10, 3, 7, 0), meal_planning_enabled=True,
        )
        later = await make_event(
            db_session, title="Later FTX", category="ftx",
            date_start=datetime(2026, 11, 7, 7, 0), meal_planning_enabled=True,
        )
        past = await make_event(
            db_session, title="Past FTX", category="ftx",
            date_start=datetime(2026, 1, 4, 7, 0), meal_planning_enabled=True,
        )
        await make_rsvp(db_session, soon, member, attended=False, meal_plan=True, meal_paid=False)
        await make_rsvp(db_session, later, member, attended=False, meal_plan=True, meal_paid=False)
        await make_rsvp(db_session, past, member, attended=False, meal_plan=True, meal_paid=False)
        await db_session.commit()

        row = (await db_session.execute(
            unpaid_upcoming_meal_stmt(member.id, datetime(2026, 9, 25, 12, 0))
        )).first()
        assert row is not None
        _rsvp, event = row
        assert event.title == "Soon FTX"

    async def test_past_only_unpaid_is_not_auto_matched(self, db_session):
        member = await make_member(db_session)
        past = await make_event(
            db_session, title="Past FTX", category="ftx",
            date_start=datetime(2026, 1, 4, 7, 0), meal_planning_enabled=True,
        )
        await make_rsvp(db_session, past, member, attended=False, meal_plan=True, meal_paid=False)
        await db_session.commit()

        row = (await db_session.execute(
            unpaid_upcoming_meal_stmt(member.id, datetime(2026, 9, 25, 12, 0))
        )).first()
        assert row is None


from decimal import Decimal
from app.services.meals import (
    amount_covers,
    default_slots,
    effective_price,
    menu_label,
    pick_meal_payment,
)


def test_april_and_october_default_to_the_long_meal_list():
    april = default_slots(datetime(2026, 4, 18))
    october = default_slots(datetime(2026, 10, 17))
    june = default_slots(datetime(2026, 6, 6))
    assert "thu_dinner" in april and "fri_lunch" in april and "sun_breakfast" in april
    assert october == april
    assert june == frozenset({"sat_dinner", "sun_breakfast"})
    assert menu_label(None, datetime(2026, 6, 6)) == "Sat Dinner, Sun Breakfast"
    assert "Thu Dinner" in menu_label(None, datetime(2026, 4, 18))


def test_override_price_beats_the_standard(monkeypatch):
    monkeypatch.setattr("app.services.meals.standard_price", lambda: Decimal("15.00"))
    overridden = SimpleNamespace(meal_price_override=Decimal("25"))
    plain = SimpleNamespace(meal_price_override=None)
    assert effective_price(overridden) == Decimal("25.00")
    assert effective_price(plain) == Decimal("15.00")


def test_payment_matches_the_event_whose_price_fits():
    soon = SimpleNamespace(title="Soon", meal_price_override=Decimal("15.00"))
    later = SimpleNamespace(title="Later", meal_price_override=Decimal("25.00"))
    rows = [(SimpleNamespace(), soon), (SimpleNamespace(), later)]
    assert pick_meal_payment(rows, Decimal("25.40"))[1].title == "Later"
    assert pick_meal_payment(rows, Decimal("15.76"))[1].title == "Soon"
    assert pick_meal_payment(rows, Decimal("40.00")) is None
    assert amount_covers(Decimal("16.50"), Decimal("15.00")) is True
    assert amount_covers(Decimal("16.51"), Decimal("15.00")) is False
