"""SSO users keyed by issuer+subject; audit_log append-only triggers

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of orm.AUDIT_APPEND_ONLY_DDL at this revision (migrations must not change when the ORM does).
APPEND_ONLY = {
    "sqlite": (
        "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
        "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
    ),
    "postgresql": (
        "CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$",
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()",
    ),
}
DROP_APPEND_ONLY = {
    "sqlite": ("DROP TRIGGER IF EXISTS audit_log_no_update", "DROP TRIGGER IF EXISTS audit_log_no_delete"),
    "postgresql": ("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log",
                   "DROP FUNCTION IF EXISTS audit_log_append_only()"),
}


def upgrade() -> None:
    with op.batch_alter_table('app_user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('oidc_subject', sa.String(length=300), nullable=True))
        batch_op.create_index(batch_op.f('ix_app_user_oidc_subject'), ['oidc_subject'], unique=True)
    for statement in APPEND_ONLY.get(op.get_bind().dialect.name, ()):
        op.execute(statement)


def downgrade() -> None:
    for statement in DROP_APPEND_ONLY.get(op.get_bind().dialect.name, ()):
        op.execute(statement)
    with op.batch_alter_table('app_user', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_app_user_oidc_subject'))
        batch_op.drop_column('oidc_subject')
