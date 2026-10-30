"""Create Urban Evasion 2 exercise, participant, evolution, and score tables.

Revision ID: 0023_urban_evasion
Revises: 0022_company_routing
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0023_urban_evasion"
down_revision: Union[str, None] = "0022_company_routing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ue2_exercises",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("date_start", sa.DateTime(), nullable=True),
        sa.Column("date_end", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("radio_primary", sa.String(length=64), nullable=True),
        sa.Column("radio_alternate", sa.String(length=64), nullable=True),
        sa.Column("radio_realworld", sa.String(length=64), nullable=True),
        sa.Column("team_a_name", sa.String(length=64), nullable=False),
        sa.Column("team_b_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.UniqueConstraint("event_id", name="uq_ue2_exercise_event"),
    )
    op.create_table(
        "ue2_participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exercise_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("team", sa.String(length=1), nullable=False),
        sa.Column("start_label", sa.String(length=255), nullable=True),
        sa.Column("start_lat", sa.Float(), nullable=True),
        sa.Column("start_lng", sa.Float(), nullable=True),
        sa.Column("end_label", sa.String(length=255), nullable=True),
        sa.Column("end_lat", sa.Float(), nullable=True),
        sa.Column("end_lng", sa.Float(), nullable=True),
        sa.Column("buddy_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["exercise_id"], ["ue2_exercises.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.ForeignKeyConstraint(["buddy_id"], ["ue2_participants.id"]),
        sa.UniqueConstraint("exercise_id", "member_id", name="uq_ue2_participant"),
    )
    op.create_table(
        "ue2_evolutions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exercise_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("time_start", sa.DateTime(), nullable=True),
        sa.Column("time_end", sa.DateTime(), nullable=True),
        sa.Column("team_a_role", sa.String(length=16), nullable=False),
        sa.Column("team_b_role", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["exercise_id"], ["ue2_exercises.id"]),
        sa.UniqueConstraint("exercise_id", "seq", name="uq_ue2_evolution"),
    )
    op.create_table(
        "ue2_score_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exercise_id", sa.Integer(), nullable=False),
        sa.Column("evolution_seq", sa.Integer(), nullable=True),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target_participant_id", sa.Integer(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["exercise_id"], ["ue2_exercises.id"]),
        sa.ForeignKeyConstraint(["participant_id"], ["ue2_participants.id"]),
        sa.ForeignKeyConstraint(["target_participant_id"], ["ue2_participants.id"]),
    )


def downgrade() -> None:
    op.drop_table("ue2_score_events")
    op.drop_table("ue2_evolutions")
    op.drop_table("ue2_participants")
    op.drop_table("ue2_exercises")
