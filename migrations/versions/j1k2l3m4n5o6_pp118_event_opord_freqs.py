"""PP-118: Rally time, SMEAC OPORD fields, radio frequencies

Revision ID: j1k2l3m4n5o6
Revises: i0j1k2l3m4n5
Create Date: 2026-04-09 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'j1k2l3m4n5o6'
down_revision = 'i0j1k2l3m4n5'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Rally point time
    op.add_column('events', sa.Column('rally_point_time', sa.String(length=8), nullable=True))

    # Radio frequencies
    op.add_column('events', sa.Column('freq_convoy_primary', sa.String(length=16), nullable=True))
    op.add_column('events', sa.Column('freq_convoy_alternate', sa.String(length=16), nullable=True))
    op.add_column('events', sa.Column('freq_fob_primary', sa.String(length=16), nullable=True))
    op.add_column('events', sa.Column('freq_fob_alternate', sa.String(length=16), nullable=True))

    # SMEAC OPORD (5-paragraph order)
    op.add_column('events', sa.Column('opord_situation', sa.Text(), nullable=True))
    op.add_column('events', sa.Column('opord_mission', sa.Text(), nullable=True))
    op.add_column('events', sa.Column('opord_execution', sa.Text(), nullable=True))
    op.add_column('events', sa.Column('opord_admin_logistics', sa.Text(), nullable=True))
    op.add_column('events', sa.Column('opord_command_signal', sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column('events', 'opord_command_signal')
    op.drop_column('events', 'opord_admin_logistics')
    op.drop_column('events', 'opord_execution')
    op.drop_column('events', 'opord_mission')
    op.drop_column('events', 'opord_situation')
    op.drop_column('events', 'freq_fob_alternate')
    op.drop_column('events', 'freq_fob_primary')
    op.drop_column('events', 'freq_convoy_alternate')
    op.drop_column('events', 'freq_convoy_primary')
    op.drop_column('events', 'rally_point_time')
