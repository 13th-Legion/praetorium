"""PP-070: Ops Console models — EventGuest, EventBuddyPair, EventGuardSlot,
EventGuardDuty, EventVexillation, EventVexillationAssignment + caldav_uid on events.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-18 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add caldav_uid to events ──────────────────────────────────────────
    op.add_column("events", sa.Column("caldav_uid", sa.String(255), nullable=True))
    op.create_unique_constraint("uq_events_caldav_uid", "events", ["caldav_uid"])

    # ── 2. event_guests ──────────────────────────────────────────────────────
    op.create_table(
        "event_guests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("rsvp_id", sa.Integer(), sa.ForeignKey("event_rsvps.id"), nullable=True),
        sa.Column("sponsor_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("first_name", sa.String(64), nullable=False),
        sa.Column("last_name", sa.String(64), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False, server_default="other"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("waiver_ack", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_walkin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("checked_in_at", sa.DateTime(), nullable=True),
        sa.Column("registered_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_event_guests_event_id", "event_guests", ["event_id"])

    # ── 3. event_buddy_pairs ─────────────────────────────────────────────────
    op.create_table(
        "event_buddy_pairs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("member_a_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=True),
        sa.Column("member_b_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=True),
        sa.Column("guest_a_id", sa.Integer(), sa.ForeignKey("event_guests.id"), nullable=True),
        sa.Column("guest_b_id", sa.Integer(), sa.ForeignKey("event_guests.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", "member_a_id", name="uq_buddy_member_a"),
        sa.UniqueConstraint("event_id", "member_b_id", name="uq_buddy_member_b"),
    )
    op.create_index("ix_event_buddy_pairs_event_id", "event_buddy_pairs", ["event_id"])

    # ── 4. event_guard_slots ─────────────────────────────────────────────────
    op.create_table(
        "event_guard_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("slot_label", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_event_guard_slots_event_id", "event_guard_slots", ["event_id"])

    # ── 5. event_guard_duty ──────────────────────────────────────────────────
    op.create_table(
        "event_guard_duty",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("slot_id", sa.Integer(), sa.ForeignKey("event_guard_slots.id"), nullable=True),
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("slot_label", sa.String(32), nullable=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=True),
        sa.Column("guest_id", sa.Integer(), sa.ForeignKey("event_guests.id"), nullable=True),
        sa.Column("assigned_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", "slot_number", "member_id", name="uq_guard_event_slot_member"),
    )
    op.create_index("ix_event_guard_duty_event_id", "event_guard_duty", ["event_id"])

    # ── 6. event_vexillations ────────────────────────────────────────────────
    op.create_table(
        "event_vexillations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("commander_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=True),
        sa.Column("field_status", sa.String(16), nullable=False, server_default="in_assembly"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", "name", name="uq_vex_event_name"),
    )
    op.create_index("ix_event_vexillations_event_id", "event_vexillations", ["event_id"])

    # ── 7. event_vexillation_assignments ─────────────────────────────────────
    op.create_table(
        "event_vexillation_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("vexillation_id", sa.Integer(), sa.ForeignKey("event_vexillations.id"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=True),
        sa.Column("guest_id", sa.Integer(), sa.ForeignKey("event_guests.id"), nullable=True),
        sa.Column("assigned_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", "member_id", name="uq_vex_assign_member"),
    )
    op.create_index("ix_event_vex_assign_event_id", "event_vexillation_assignments", ["event_id"])
    op.create_index("ix_event_vex_assign_vex_id", "event_vexillation_assignments", ["vexillation_id"])


def downgrade() -> None:
    op.drop_index("ix_event_vex_assign_vex_id", table_name="event_vexillation_assignments")
    op.drop_index("ix_event_vex_assign_event_id", table_name="event_vexillation_assignments")
    op.drop_table("event_vexillation_assignments")

    op.drop_index("ix_event_vexillations_event_id", table_name="event_vexillations")
    op.drop_table("event_vexillations")

    op.drop_index("ix_event_guard_duty_event_id", table_name="event_guard_duty")
    op.drop_table("event_guard_duty")

    op.drop_index("ix_event_guard_slots_event_id", table_name="event_guard_slots")
    op.drop_table("event_guard_slots")

    op.drop_index("ix_event_buddy_pairs_event_id", table_name="event_buddy_pairs")
    op.drop_table("event_buddy_pairs")

    op.drop_index("ix_event_guests_event_id", table_name="event_guests")
    op.drop_table("event_guests")

    op.drop_constraint("uq_events_caldav_uid", "events", type_="unique")
    op.drop_column("events", "caldav_uid")
