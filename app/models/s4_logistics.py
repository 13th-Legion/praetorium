"""Database models for S4 Logistics (Meals, Expenses, Purchasing, Donations, Inventory)."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Enum
from sqlalchemy.orm import relationship
from app.database import Base

class S4MealPlan(Base):
    """Tracks meal planning for a specific event."""
    __tablename__ = "s4_meal_plans"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Which meals are S4 providing for this FTX
    sat_breakfast = Column(Boolean, default=False)
    sat_lunch = Column(Boolean, default=False)
    sat_dinner = Column(Boolean, default=True)  # Usually true
    sun_breakfast = Column(Boolean, default=True)  # Usually true
    
    # Menu notes and shopping lists
    menu_notes = Column(Text, nullable=True)
    
    # Assigned cook/buyer (usually S4 head or Matos)
    assigned_to_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    event = relationship("Event", backref="meal_plan")
    assigned_to = relationship("Member", foreign_keys=[assigned_to_id])


class S4Expense(Base):
    """Tracks reimbursement requests for out-of-pocket unit expenses (e.g., groceries)."""
    __tablename__ = "s4_expenses"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    
    title = Column(String(100), nullable=False)  # "March FTX Groceries"
    description = Column(Text, nullable=True)
    amount = Column(Float, nullable=False)
    receipt_url = Column(String(255), nullable=True)  # Nextcloud link
    
    # pending, approved, reimbursed, rejected
    status = Column(String(20), default="pending")
    reimbursed_at = Column(DateTime, nullable=True)
    reimbursed_by_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    member = relationship("Member", foreign_keys=[member_id])
    event = relationship("Event")
    reimbursed_by = relationship("Member", foreign_keys=[reimbursed_by_id])


class S4PurchaseRequest(Base):
    """Tracks requests to spend unit funds on gear/supplies."""
    __tablename__ = "s4_purchase_requests"

    id = Column(Integer, primary_key=True, index=True)
    requester_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    
    item_name = Column(String(100), nullable=False)
    url = Column(String(255), nullable=True)
    estimated_cost = Column(Float, nullable=False)
    quantity = Column(Integer, default=1)
    justification = Column(Text, nullable=False)
    
    # pending, approved, denied, purchased, received
    status = Column(String(20), default="pending")
    
    approved_by_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    denial_reason = Column(Text, nullable=True)
    
    purchased_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    requester = relationship("Member", foreign_keys=[requester_id])
    approved_by = relationship("Member", foreign_keys=[approved_by_id])


class S4EquipmentDonation(Base):
    """Tracks member donations of personal gear to unit supply."""
    __tablename__ = "s4_equipment_donations"

    id = Column(Integer, primary_key=True, index=True)
    donor_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    
    item_name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    condition = Column(String(50), nullable=False)  # New, Good, Fair, Poor
    quantity = Column(Integer, default=1)
    photo_url = Column(String(255), nullable=True)
    
    # submitted, accepted_pending_dropoff, received, rejected
    status = Column(String(30), default="submitted")
    
    reviewed_by_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    donor = relationship("Member", foreign_keys=[donor_id])
    reviewed_by = relationship("Member", foreign_keys=[reviewed_by_id])


class S4InventoryItem(Base):
    """The master unit supply inventory."""
    __tablename__ = "s4_inventory_items"

    id = Column(Integer, primary_key=True, index=True)
    qr_code = Column(String(50), unique=True, index=True, nullable=True)  # For scanning
    
    name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)  # Comms, Medical, Camp, Training, etc.
    description = Column(Text, nullable=True)
    serial_number = Column(String(50), nullable=True)
    
    # Tracking provenance
    source_type = Column(String(20), nullable=True)  # 'purchase' or 'donation'
    source_purchase_id = Column(Integer, ForeignKey("s4_purchase_requests.id", ondelete="SET NULL"), nullable=True)
    source_donation_id = Column(Integer, ForeignKey("s4_equipment_donations.id", ondelete="SET NULL"), nullable=True)
    
    # Status
    condition = Column(String(50), default="Good")
    status = Column(String(20), default="available")  # available, checked_out, maintenance, lost, retired
    location = Column(String(100), nullable=True)  # Where is it physically stored (e.g. S4 Connex, Cav's garage)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    purchase_source = relationship("S4PurchaseRequest")
    donation_source = relationship("S4EquipmentDonation")


class S4Checkout(Base):
    """Tracks checking inventory items in/out to members."""
    __tablename__ = "s4_checkouts"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("s4_inventory_items.id", ondelete="CASCADE"), nullable=False)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    
    checked_out_at = Column(DateTime, default=datetime.utcnow)
    checked_out_by_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)  # The S4 rep who issued it
    
    expected_return_at = Column(DateTime, nullable=True)
    
    checked_in_at = Column(DateTime, nullable=True)
    checked_in_by_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)  # The S4 rep who received it
    
    # Notes on condition upon return
    return_condition = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)

    item = relationship("S4InventoryItem", backref="checkouts")
    member = relationship("Member", foreign_keys=[member_id])
    event = relationship("Event")
    checked_out_by = relationship("Member", foreign_keys=[checked_out_by_id])
    checked_in_by = relationship("Member", foreign_keys=[checked_in_by_id])
