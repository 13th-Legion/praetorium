"""S4 Logistics — RBAC helpers + model behavior."""

import pytest
from datetime import datetime

from sqlalchemy import select

from app.routes.s4_admin import _is_s4_head, _can_approve, S4_ROLES
from app.constants import S4_CONDITIONS, S4_INVENTORY_CATEGORIES
from app.models.s4_logistics import (
    S4Checkout,
    S4EquipmentDonation,
    S4Expense,
    S4InventoryItem,
    S4MealPlan,
    S4PurchaseRequest,
)
from app.models.member import Member
from tests.factories import make_member, make_event

pytestmark = pytest.mark.integration


# ─── RBAC helpers (pure) ────────────────────────────────────────────────────

class TestS4Head:
    def test_lead_billet_is_head(self):
        m = Member(nc_username="wall", first_name="W", last_name="Wall",
                   primary_billet="S4: Logistics (Lead)", status="active")
        assert _is_s4_head(m) is True

    def test_plain_s4_member_is_not_head(self):
        m = Member(nc_username="x", first_name="X", last_name="Y",
                   primary_billet="S4: Logistics", status="active")
        assert _is_s4_head(m) is False

    def test_no_billet_is_not_head(self):
        m = Member(nc_username="x", first_name="X", last_name="Y", status="active")
        assert _is_s4_head(m) is False

    def test_other_shop_lead_is_not_s4_head(self):
        m = Member(nc_username="x", first_name="X", last_name="Y",
                   primary_billet="S1: Administration (Lead)", status="active")
        assert _is_s4_head(m) is False


class TestCanApprove:
    def test_command_approves_without_member(self):
        assert _can_approve({"roles": ["command"]}, None) is True

    def test_admin_approves(self):
        assert _can_approve({"roles": ["admin"]}, None) is True

    def test_s4_head_approves(self):
        m = Member(nc_username="wall", first_name="W", last_name="Wall",
                   primary_billet="S4: Logistics (Lead)")
        assert _can_approve({"roles": ["s4"]}, m) is True

    def test_plain_s4_cannot_approve(self):
        m = Member(nc_username="x", first_name="X", last_name="Y",
                   primary_billet="S4: Logistics")
        assert _can_approve({"roles": ["s4"]}, m) is False

    def test_s4_roles_includes_expected(self):
        assert S4_ROLES == {"command", "admin", "s4"}


# ─── Model behaviour ─────────────────────────────────────────────────────────

class TestMealPlanModel:
    async def test_cook_and_buyer_independent(self, db_session):
        ev = await make_event(db_session, category="ftx")
        cook = await make_member(db_session)
        buyer = await make_member(db_session)
        plan = S4MealPlan(event_id=ev.id, cook_id=cook.id, buyer_id=buyer.id)
        db_session.add(plan)
        await db_session.flush()
        assert plan.cook_id == cook.id
        assert plan.buyer_id == buyer.id
        assert plan.cook_id != plan.buyer_id

    async def test_cook_buyer_optional(self, db_session):
        ev = await make_event(db_session, category="ftx")
        plan = S4MealPlan(event_id=ev.id)
        db_session.add(plan)
        await db_session.flush()
        assert plan.cook_id is None
        assert plan.buyer_id is None


class TestExpenseModel:
    async def test_expense_defaults_pending(self, db_session):
        m = await make_member(db_session)
        e = S4Expense(member_id=m.id, title="Groceries", amount=42.5)
        db_session.add(e)
        await db_session.flush()
        assert e.status == "pending"
        assert e.receipt_url is None

    async def test_expense_status_cycle(self, db_session):
        m = await make_member(db_session)
        e = S4Expense(member_id=m.id, title="Site fees", amount=100.0)
        db_session.add(e)
        await db_session.flush()
        e.status = "approved"
        e.status = "reimbursed"
        e.reimbursed_at = datetime.utcnow()
        e.reimbursed_by_id = m.id
        await db_session.flush()
        assert e.status == "reimbursed"
        assert e.reimbursed_by_id == m.id


class TestPurchaseModel:
    async def test_purchase_defaults(self, db_session):
        m = await make_member(db_session)
        p = S4PurchaseRequest(requester_id=m.id, item_name="Tents",
                              estimated_cost=250.0, quantity=2,
                              justification="FTX shelter")
        db_session.add(p)
        await db_session.flush()
        assert p.status == "pending"
        assert p.quantity == 2


class TestDonationModel:
    async def test_donation_defaults_submitted(self, db_session):
        m = await make_member(db_session)
        d = S4EquipmentDonation(donor_id=m.id, item_name="Radio", condition="Good")
        db_session.add(d)
        await db_session.flush()
        assert d.status == "submitted"


class TestInventoryCheckout:
    async def test_add_serial_format(self, db_session):
        """Serial is '13LG-{id:08d}' — padded 8-digit id."""
        item = S4InventoryItem(name="PRC-152", category="Comms", status="available")
        db_session.add(item)
        await db_session.flush()
        item.serial_number = f"13LG-{item.id:08d}"
        await db_session.flush()
        assert item.serial_number == f"13LG-{item.id:08d}"
        assert item.serial_number.startswith("13LG-")
        # 8 digits after the prefix
        assert len(item.serial_number.split("-")[1]) == 8

    async def test_category_condition_constants(self):
        assert "Comms" in S4_INVENTORY_CATEGORIES
        assert "Medical" in S4_INVENTORY_CATEGORIES
        assert "Good" in S4_CONDITIONS
        assert "Poor" in S4_CONDITIONS

    async def test_checkout_marks_item_and_roundtrip(self, db_session):
        m = await make_member(db_session)
        item = S4InventoryItem(name="PRC-152", category="Comms", status="available")
        db_session.add(item)
        await db_session.flush()

        co = S4Checkout(item_id=item.id, member_id=m.id, checked_out_by_id=m.id)
        db_session.add(co)
        item.status = "checked_out"
        await db_session.flush()

        # Open checkout exists, item is checked out.
        open_co = (await db_session.execute(
            select(S4Checkout).where(
                S4Checkout.item_id == item.id,
                S4Checkout.checked_in_at.is_(None),
            )
        )).scalars().one()
        assert open_co.member_id == m.id

        # Check back in.
        open_co.checked_in_at = datetime.utcnow()
        open_co.return_condition = "Good"
        item.status = "available"
        await db_session.flush()

        none_left = (await db_session.execute(
            select(S4Checkout).where(
                S4Checkout.item_id == item.id,
                S4Checkout.checked_in_at.is_(None),
            )
        )).scalars().first()
        assert none_left is None
        assert item.status == "available"

    async def test_inventory_source_links(self, db_session):
        m = await make_member(db_session)
        p = S4PurchaseRequest(requester_id=m.id, item_name="Generator",
                              estimated_cost=900.0, justification="Power",
                              status="received")
        db_session.add(p)
        await db_session.flush()
        item = S4InventoryItem(name="Generator", category="Purchased",
                               source_type="purchase", source_purchase_id=p.id)
        db_session.add(item)
        await db_session.flush()
        assert item.source_type == "purchase"
        assert item.source_purchase_id == p.id
