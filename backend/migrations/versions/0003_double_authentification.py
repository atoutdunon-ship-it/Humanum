"""double authentification TOTP (comptes des organismes et super-administrateurs)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22 12:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

_TABLES = ("users", "platform_admins")


def upgrade() -> None:
    op.drop_column("users", "mfa_secret")  # colonne réservée du lot 0, jamais alimentée
    for t in _TABLES:
        op.add_column(t, sa.Column("mfa_secret_enc", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("mfa_pending_enc", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("mfa_pending_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(t, sa.Column("mfa_enabled_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(t, sa.Column("mfa_last_step", sa.BigInteger(), nullable=True))
        op.add_column(t, sa.Column("mfa_recovery", postgresql.JSONB(astext_type=sa.Text()),
                                   server_default=sa.text("'[]'::jsonb"), nullable=False))
        # Cohérence : un compte actif en double authentification a forcément une date d'activation.
        op.create_check_constraint(f"ck_{t}_mfa_enabled", t, "(mfa_secret_enc IS NULL) = (mfa_enabled_at IS NULL)")


def downgrade() -> None:
    for t in _TABLES:
        op.drop_constraint(f"ck_{t}_mfa_enabled", t, type_="check")
        for c in ("mfa_recovery", "mfa_last_step", "mfa_enabled_at", "mfa_pending_at", "mfa_pending_enc", "mfa_secret_enc"):
            op.drop_column(t, c)
    op.add_column("users", sa.Column("mfa_secret", sa.String(length=64), nullable=True))
