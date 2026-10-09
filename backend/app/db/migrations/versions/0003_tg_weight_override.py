"""admin touch-and-go weight overrides (FR-60)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.orm

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tg_weight_override',
    sa.Column('type_code', sa.String(length=16), nullable=False),
    sa.Column('inspection_type_code', sa.String(length=24), nullable=False),
    sa.Column('unit_code', sa.String(length=16), nullable=False),
    sa.Column('tg_weight', sa.Float(), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('updated_at', app.db.orm.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['updated_by'], ['app_user.user_id'], ),
    sa.PrimaryKeyConstraint('type_code', 'inspection_type_code', 'unit_code'),
    sa.CheckConstraint('tg_weight >= 0 AND tg_weight <= 5', name='ck_tg_weight_override_range')
    )


def downgrade() -> None:
    op.drop_table('tg_weight_override')
