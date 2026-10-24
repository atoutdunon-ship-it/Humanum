"""promotions ajustables : historique des utilisations à travers les versions d'un même code

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-23 00:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("promotions", sa.Column("redeemed_before", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("promotions", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("promotions", "revision")
    op.drop_column("promotions", "redeemed_before")
