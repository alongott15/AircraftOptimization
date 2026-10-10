"""maintenance plan: manual schedule of maintenance tasks, one row per task cycle (FR-91)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.orm

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('maintenance_plan',
    sa.Column('plan_id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('item_key', sa.String(length=120), nullable=False),
    sa.Column('aircraft_id', sa.Integer(), nullable=False),
    sa.Column('tail_number', sa.String(length=16), nullable=False),
    sa.Column('action', sa.String(length=24), nullable=False),
    sa.Column('ref', sa.String(length=80), nullable=False),
    sa.Column('fingerprint', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('planned_start', app.db.orm.UTCDateTime(), nullable=True),
    sa.Column('planned_end', app.db.orm.UTCDateTime(), nullable=True),
    sa.Column('assignee', sa.String(length=80), nullable=True),
    sa.Column('notes', sa.String(length=500), nullable=True),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('updated_at', app.db.orm.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['updated_by'], ['app_user.user_id'], ),
    sa.PrimaryKeyConstraint('plan_id'),
    sa.UniqueConstraint('item_key', 'fingerprint', name='uq_maintenance_plan_cycle'),
    sa.CheckConstraint("status IN ('REQUIRED', 'SCHEDULED', 'IN_PROGRESS', 'DONE')",
                       name='ck_maintenance_plan_status')
    )
    op.create_index(op.f('ix_maintenance_plan_item_key'), 'maintenance_plan', ['item_key'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_maintenance_plan_item_key'), table_name='maintenance_plan')
    op.drop_table('maintenance_plan')
