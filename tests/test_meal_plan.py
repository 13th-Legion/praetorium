"""FTX meal-plan opt-in + payment tracking — model + route behavior."""

import pytest
from datetime import datetime

from sqlalchemy import select

from app.models.events import EventRSVP
from tests.factories import make_member, make_event, make_rsvp

pytestmark = pytest.mark.integration


# ─── Model defaults ─────────────────────────────────────────────────────────

class TestMealPlanModel:
    async def test_defaults_opt_out_and_unpaid(self, db_session):
        m = await make_member(db_session)
        ev = await make_event(db_session, category="ftx")
        r = await make_rsvp(db_session, ev, m, status="attending")
        assert r.meal_plan is False
        assert r.meal_paid is False
        assert r.meal_payment_method is None
        assert r.meal_paid_at is None

    async def test_opt_in_flags_roundtrip(self, db_session):
        m = await make_member(db_session)
        ev = await make_event(db_session, category="ftx")
        r = await make_rsvp(
            db_session, ev, m, status="attending",
            meal_plan=True, meal_paid=True, meal_payment_method="venmo",
            meal_paid_at=datetime.utcnow(),
        )
        await db_session.flush()
        row = (await db_session.execute(
            select(EventRSVP).where(EventRSVP.id == r.id)
        )).scalar_one()
        assert row.meal_plan is True
        assert row.meal_paid is True
        assert row.meal_payment_method == "venmo"
        assert row.meal_paid_at is not None


# ─── RSVP submission requires the meal choice for FTX ───────────────────────

def _csrf(c) -> str:
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


class TestMealPlanRSVP:
    async def test_attending_ftx_without_meal_choice_is_rejected(self, auth_client, db_session, patch_global_session):
        """FTX attending without meal_plan in/out → 400 + error message."""
        m = await make_member(db_session, nc_username="test.soldier")
        ev = await make_event(db_session, category="ftx", title="Meal FTX", rsvp_enabled=True)
        await db_session.commit()

        tok = _csrf(auth_client)
        resp = auth_client.post(
            f"/api/events/{ev.id}/rsvp",
            data={"status": "attending", "csrf_token": tok},
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 400
        assert "meal plan" in resp.text.lower()

    async def test_attending_ftx_with_opt_in_stores_flag(self, auth_client, db_session, patch_global_session):
        m = await make_member(db_session, nc_username="test.soldier")
        ev = await make_event(db_session, category="ftx", title="Meal FTX", rsvp_enabled=True)
        await db_session.commit()

        tok = _csrf(auth_client)
        resp = auth_client.post(
            f"/api/events/{ev.id}/rsvp",
            data={"status": "attending", "meal_plan": "in", "csrf_token": tok},
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 200, resp.text

        row = (await db_session.execute(
            select(EventRSVP).where(
                EventRSVP.event_id == ev.id, EventRSVP.member_id == m.id
            )
        )).scalar_one()
        assert row.status == "attending"
        assert row.meal_plan is True

    async def test_declining_clears_meal_flags(self, db_session):
        m = await make_member(db_session)
        ev = await make_event(db_session, category="ftx")
        r = await make_rsvp(
            db_session, ev, m, status="attending",
            meal_plan=True, meal_paid=True, meal_payment_method="paypal",
            meal_paid_at=datetime.utcnow(),
        )
        # Simulate the decline branch of submit_rsvp
        r.status = "declined"
        r.meal_plan = False
        r.meal_paid = False
        r.meal_payment_method = None
        r.meal_paid_at = None
        await db_session.flush()
        row = (await db_session.execute(
            select(EventRSVP).where(EventRSVP.id == r.id)
        )).scalar_one()
        assert row.meal_plan is False
        assert row.meal_paid is False
        assert row.meal_payment_method is None


# ─── Non-FTX events don't require/use the meal choice ───────────────────────

class TestMealPlanNonFTX:
    async def test_attending_social_without_meal_choice_ok(self, auth_client, db_session, patch_global_session):
        m = await make_member(db_session, nc_username="test.soldier")
        ev = await make_event(db_session, category="social", title="Cookout", rsvp_enabled=True)
        await db_session.commit()

        tok = _csrf(auth_client)
        resp = auth_client.post(
            f"/api/events/{ev.id}/rsvp",
            data={"status": "attending", "csrf_token": tok},
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 200, resp.text

        row = (await db_session.execute(
            select(EventRSVP).where(
                EventRSVP.event_id == ev.id, EventRSVP.member_id == m.id
            )
        )).scalar_one()
        assert row.status == "attending"
        assert row.meal_plan is False  # never set for non-FTX


# ─── Per-FTX meal-planning toggle ───────────────────────────────────────────

class TestMealPlanningToggle:
    async def test_event_defaults_to_meal_planning_enabled(self, db_session):
        ev = await make_event(db_session, category="ftx")
        assert ev.meal_planning_enabled is True

    async def test_disabled_ftx_does_not_require_meal_choice(self, auth_client, db_session, patch_global_session):
        """FTX with meal planning disabled: attending without meal_plan is OK."""
        m = await make_member(db_session, nc_username="test.soldier")
        ev = await make_event(
            db_session, category="ftx", title="Urban Evasion",
            rsvp_enabled=True, meal_planning_enabled=False,
        )
        await db_session.commit()

        tok = _csrf(auth_client)
        resp = auth_client.post(
            f"/api/events/{ev.id}/rsvp",
            data={"status": "attending", "csrf_token": tok},
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 200, resp.text

        row = (await db_session.execute(
            select(EventRSVP).where(
                EventRSVP.event_id == ev.id, EventRSVP.member_id == m.id
            )
        )).scalar_one()
        assert row.status == "attending"
        assert row.meal_plan is False  # no meal opt-in when planning disabled

    async def test_enabled_ftx_still_requires_meal_choice(self, auth_client, db_session, patch_global_session):
        m = await make_member(db_session, nc_username="test.soldier")
        ev = await make_event(db_session, category="ftx", title="Meal FTX", rsvp_enabled=True)
        await db_session.commit()

        tok = _csrf(auth_client)
        resp = auth_client.post(
            f"/api/events/{ev.id}/rsvp",
            data={"status": "attending", "csrf_token": tok},
            headers={"X-CSRF-Token": tok},
        )
        assert resp.status_code == 400  # meal choice still required when enabled
