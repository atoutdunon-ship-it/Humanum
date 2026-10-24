"""réglages de plateforme (TVA optionnelle) et promotions administrables (coupons et codes Stripe)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22 21:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("platform_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.execute("""INSERT INTO platform_settings (key, value) VALUES
      ('vat', '{"enabled": false, "rate": 8.5, "label": "TVA", "jurisdiction": "Martinique", "stripe_tax_rate_id": null}')""")
    op.create_table("promotions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("percent_off", sa.Numeric(5, 2)),
        sa.Column("amount_off_cents", sa.Integer()),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("duration", sa.String(16), nullable=False),
        sa.Column("duration_in_months", sa.Integer()),
        sa.Column("plans", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("max_redemptions", sa.Integer()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("first_time_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("stripe_coupon_id", sa.String(64)),
        sa.Column("stripe_promotion_code_id", sa.String(64)),
        sa.Column("times_redeemed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("(percent_off IS NULL) <> (amount_off_cents IS NULL)", name="ck_promotions_one_discount"),
        sa.CheckConstraint("percent_off IS NULL OR (percent_off > 0 AND percent_off <= 100)", name="ck_promotions_percent"),
        sa.CheckConstraint("amount_off_cents IS NULL OR amount_off_cents > 0", name="ck_promotions_amount"),
        sa.CheckConstraint("duration in ('once','repeating','forever')", name="ck_promotions_duration"),
        sa.CheckConstraint("(duration = 'repeating') = (duration_in_months IS NOT NULL)", name="ck_promotions_months"),
        sa.CheckConstraint("code ~ '^[A-Z0-9_-]{3,40}$'", name="ck_promotions_code"))
    op.add_column("organizations", sa.Column("discount_label", sa.String(160)))
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_platform') THEN
        GRANT SELECT, INSERT, UPDATE ON platform_settings, promotions TO humanum_platform;
      END IF;
      IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_app') THEN
        GRANT SELECT ON platform_settings, promotions TO humanum_app;   -- affichage TVA et contrôle d'un code saisi
      END IF;
    END $$""")


def downgrade() -> None:
    op.drop_column("organizations", "discount_label")
    op.drop_table("promotions")
    op.drop_table("platform_settings")
