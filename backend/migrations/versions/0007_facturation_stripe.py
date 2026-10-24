"""facturation Stripe : abonnement des organismes, prix des formules, factures, journal d'idempotence des webhooks

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-22 20:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
ORG_COLS = ("trial_used", "billing_email", "cancel_at_period_end", "trial_end", "current_period_end",
            "stripe_subscription_item_id", "stripe_subscription_id", "stripe_customer_id", "billing_status")


def upgrade() -> None:
    op.add_column("organizations", sa.Column("billing_status", sa.String(24), nullable=False, server_default="none"))
    op.add_column("organizations", sa.Column("stripe_customer_id", sa.String(64), unique=True))
    op.add_column("organizations", sa.Column("stripe_subscription_id", sa.String(64), unique=True))
    op.add_column("organizations", sa.Column("stripe_subscription_item_id", sa.String(64)))
    op.add_column("organizations", sa.Column("current_period_end", sa.DateTime(timezone=True)))
    op.add_column("organizations", sa.Column("trial_end", sa.DateTime(timezone=True)))
    op.add_column("organizations", sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("organizations", sa.Column("billing_email", sa.String(320)))
    op.add_column("organizations", sa.Column("trial_used", sa.Boolean(), nullable=False, server_default=sa.false()))
    for c in ("stripe_product_id", "stripe_price_id"):
        op.add_column("plans", sa.Column(c, sa.String(64)))
    op.add_column("plans", sa.Column("stripe_price_cents", sa.Integer()))
    op.create_table("billing_invoices",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
                  server_default=sa.text("(current_setting('app.current_org'))::uuid")),
        sa.Column("stripe_invoice_id", sa.String(64), nullable=False, unique=True),
        sa.Column("number", sa.String(64)), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_due", sa.Integer(), nullable=False), sa.Column("amount_paid", sa.Integer(), nullable=False),
        sa.Column("hosted_invoice_url", sa.String(500)), sa.Column("invoice_pdf", sa.String(500)),
        sa.Column("period_start", sa.DateTime(timezone=True)), sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("issued_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_table("stripe_events",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("type", sa.String(80), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.execute("ALTER TABLE billing_invoices ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE billing_invoices FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON billing_invoices USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})")
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_app') THEN
        GRANT SELECT ON billing_invoices TO humanum_app;         -- lecture seule : seul Stripe fait foi
      END IF;
      IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_platform') THEN
        GRANT SELECT, INSERT, UPDATE ON billing_invoices, stripe_events TO humanum_platform;
      END IF;
    END $$""")


def downgrade() -> None:
    op.drop_table("stripe_events")
    op.drop_table("billing_invoices")
    for c in ("stripe_price_cents", "stripe_price_id", "stripe_product_id"):
        op.drop_column("plans", c)
    for c in ORG_COLS:
        op.drop_column("organizations", c)
