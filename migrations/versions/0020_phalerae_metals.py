"""Split Phalerae into gold, silver, and bronze.

The existing phalerae row stays the gold medal at 25 points, so anyone who
already has it keeps that award. Silver is 20. Bronze is 15.

Revision ID: 0020_phalerae_metals
Revises: 0019_ribbon_citations
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_phalerae_metals"
down_revision: Union[str, None] = "0019_ribbon_citations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE ribbon_catalog
            SET name = 'Phalerae Aurum (Gold)',
                base_points = 25,
                description = 'Gold Phalerae. 25 promotion points.',
                image = COALESCE(NULLIF(image, ''), 'dona/phalerae.png')
            WHERE code = 'phalerae'
            """
        )
    )
    insert = sa.text(
        """
        INSERT INTO ribbon_catalog
            (code, section, name, precedence, base_points, device_increment,
             max_devices, is_auto, claimable, active, image, description)
        VALUES
            (:code, 'dona', :name, :precedence, :points, 0,
             0, false, true, true, :image, :description)
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name,
            base_points = EXCLUDED.base_points,
            precedence = EXCLUDED.precedence,
            image = EXCLUDED.image,
            description = EXCLUDED.description,
            active = true
        """
    )
    for code, name, precedence, points, image, description in (
        ("phalerae_argentum", "Phalerae Argentum (Silver)", 7, 20, "dona/phalerae_argentum.png", "Silver Phalerae. 20 promotion points."),
        ("phalerae_aes", "Phalerae Aes (Bronze)", 8, 15, "dona/phalerae_aes.png", "Bronze Phalerae. 15 promotion points."),
    ):
        bind.execute(insert, {
            "code": code, "name": name, "precedence": precedence,
            "points": points, "image": image, "description": description,
        })


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM member_ribbons WHERE ribbon_code IN ('phalerae_argentum', 'phalerae_aes')"))
    bind.execute(sa.text("DELETE FROM ribbon_catalog WHERE code IN ('phalerae_argentum', 'phalerae_aes')"))
    bind.execute(
        sa.text(
            """
            UPDATE ribbon_catalog
            SET name = 'Phalerae', description = NULL
            WHERE code = 'phalerae'
            """
        )
    )
