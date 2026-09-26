"""FTX meal plan: standard price, per-event override, and the meal list.

A normal monthly FTX is Saturday–Sunday (Sat dinner + Sun breakfast). April
and October are the multi-company FTXs: arrive Thursday, leave Sunday, so the
plan can also include Thursday dinner and all three Friday meals.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting
from app.models.s4_logistics import S4MealPlan
from app.services import settings_store

# (column, full label, short label). Order is the form order.
MEAL_SLOTS: tuple[tuple[str, str, str], ...] = (
    ("thu_dinner", "Thu Dinner", "Thu D"),
    ("fri_breakfast", "Fri Breakfast", "Fri B"),
    ("fri_lunch", "Fri Lunch", "Fri L"),
    ("fri_dinner", "Fri Dinner", "Fri D"),
    ("sat_breakfast", "Sat Breakfast", "Sat B"),
    ("sat_lunch", "Sat Lunch", "Sat L"),
    ("sat_dinner", "Sat Dinner", "Sat D"),
    ("sun_breakfast", "Sun Breakfast", "Sun B"),
)

# What a brand-new plan offers before S4 has saved one.
_SHORT_DEFAULTS = frozenset({"sat_dinner", "sun_breakfast"})
_LONG_DEFAULTS = frozenset(field for field, *_ in MEAL_SLOTS)

# PayPal adds a fee on top of the amount the member sends. $15 landed near
# $15.76. A dollar and a half covers that without swallowing a different
# event's price when two FTXs are several dollars apart.
MEAL_FEE_SLACK = Decimal("1.50")
PRICE_KEY = "meal_plan_price"


def _cents(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def standard_price() -> Decimal:
    return _cents(settings_store.meal_plan_price())


def is_long_ftx(when: datetime) -> bool:
    """April and October MCFTXs run Thursday through Sunday."""
    return when.month in (4, 10)


def default_slots(when: datetime) -> frozenset[str]:
    return _LONG_DEFAULTS if is_long_ftx(when) else _SHORT_DEFAULTS


def effective_price(event) -> Decimal:
    """Per-FTX override if set, otherwise the unit standard."""
    override = getattr(event, "meal_price_override", None)
    if override is not None:
        return _cents(override)
    return standard_price()


def amount_covers(amount, price: Decimal) -> bool:
    """True when a payment is the meal price plus at most the PayPal-fee slack."""
    paid = _cents(amount)
    return price <= paid <= price + MEAL_FEE_SLACK


def checked_slots(plan: S4MealPlan | None, when: datetime) -> list[str]:
    """Column names that are on. A missing plan uses the month's defaults."""
    if plan is None:
        defaults = default_slots(when)
        return [field for field, *_ in MEAL_SLOTS if field in defaults]
    return [field for field, *_ in MEAL_SLOTS if getattr(plan, field)]


def menu_label(plan: S4MealPlan | None, when: datetime) -> str:
    wanted = set(checked_slots(plan, when))
    labels = [label for field, label, _short in MEAL_SLOTS if field in wanted]
    return ", ".join(labels) if labels else "no meals selected"


def pick_meal_payment(rows, amount) -> tuple | None:
    """Soonest upcoming unpaid RSVP whose event price this payment covers."""
    for rsvp, event in rows:
        if amount_covers(amount, effective_price(event)):
            return rsvp, event
    return None


async def meal_offer(db: AsyncSession, event) -> tuple[str, str]:
    """``("15.00", "Sat Dinner, Sun Breakfast")`` for RSVP copy."""
    plan = (await db.execute(
        select(S4MealPlan).where(S4MealPlan.event_id == event.id)
    )).scalar_one_or_none()
    price = effective_price(event)
    return f"{price:.2f}", menu_label(plan, event.date_start)


async def set_standard_price(db: AsyncSession, amount: Decimal) -> None:
    row = (await db.execute(
        select(AppSetting).where(AppSetting.key == PRICE_KEY)
    )).scalar_one_or_none()
    text = f"{_cents(amount):.2f}"
    if row is None:
        db.add(AppSetting(
            key=PRICE_KEY,
            value=text,
            value_type="float",
            label="Standard FTX meal plan price",
            category="s4",
            updated_at=datetime.utcnow(),
        ))
    else:
        row.value = text
        row.value_type = "float"
        row.updated_at = datetime.utcnow()
    settings_store.invalidate()
