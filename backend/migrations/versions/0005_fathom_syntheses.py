"""Fathom (comptes des coachs, réunions), groupes de coachés, synthèses générées par Claude

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-22 19:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
TABLES = ("fathom_accounts", "meetings", "cohorts", "cohort_members", "syntheses")


def _id_org() -> list[sa.Column]:
    return [sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
                      index=True, server_default=sa.text("(current_setting('app.current_org'))::uuid"))]


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)]


def _jsonb_list(name: str) -> sa.Column:
    return sa.Column(name, JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"))


def upgrade() -> None:
    users = lambda: sa.ForeignKey("users.id", ondelete="CASCADE")  # noqa: E731
    op.create_table("fathom_accounts", *_id_org(),
        sa.Column("user_id", sa.UUID(), users(), nullable=False),
        sa.Column("fathom_email", sa.String(320), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=False),
        sa.Column("webhook_id", sa.String(128)), sa.Column("webhook_secret_enc", sa.Text()),
        sa.Column("hook_token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_error", sa.Text()), sa.Column("last_sync_at", sa.DateTime(timezone=True)), *_ts(),
        sa.UniqueConstraint("organization_id", "user_id"),
        sa.CheckConstraint("status in ('active','error')", name="ck_fathom_accounts_status"))
    op.create_table("cohorts", *_id_org(),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("program", sa.String(200)), sa.Column("funder", sa.String(200)),
        sa.Column("starts_on", sa.DateTime(timezone=True)), sa.Column("ends_on", sa.DateTime(timezone=True)), *_ts())
    op.create_table("cohort_members", *_id_org(),
        sa.Column("cohort_id", sa.UUID(), sa.ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("contact_id", sa.UUID(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.UniqueConstraint("cohort_id", "contact_id"))
    op.create_table("meetings", *_id_org(),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("coach_id", sa.UUID(), users(), nullable=False, index=True),
        sa.Column("contact_id", sa.UUID(), sa.ForeignKey("contacts.id", ondelete="SET NULL"), index=True),
        sa.Column("appointment_id", sa.UUID(), sa.ForeignKey("appointments.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(300), nullable=False), sa.Column("url", sa.String(500)), sa.Column("share_url", sa.String(500)),
        sa.Column("started_at", sa.DateTime(timezone=True), index=True), sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("language", sa.String(16)), _jsonb_list("invitees"), sa.Column("summary_md", sa.Text()),
        _jsonb_list("action_items"), _jsonb_list("transcript"), *_ts(),
        sa.UniqueConstraint("organization_id", "recording_id"))
    op.create_table("syntheses", *_id_org(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("contact_id", sa.UUID(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), index=True),
        sa.Column("cohort_id", sa.UUID(), sa.ForeignKey("cohorts.id", ondelete="CASCADE"), index=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("content_md", sa.Text()), sa.Column("sources", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model", sa.String(64)), sa.Column("input_tokens", sa.Integer()), sa.Column("output_tokens", sa.Integer()),
        sa.Column("error", sa.Text()), sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")), *_ts(),
        sa.CheckConstraint("kind in ('parcours','groupe','financeur')", name="ck_syntheses_kind"),
        sa.CheckConstraint("status in ('pending','running','done','error')", name="ck_syntheses_status"),
        sa.CheckConstraint("(kind = 'parcours') = (contact_id IS NOT NULL) AND (kind <> 'parcours') = (cohort_id IS NOT NULL)",
                           name="ck_syntheses_subject"))
    op.create_index("ix_syntheses_month", "syntheses", ["organization_id", "created_at"])

    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})")

    for stmt in (
        # Réception des webhooks Fathom : chemin secret → (organisme, compte). Rien d'autre ne franchit la RLS.
        """CREATE FUNCTION hc_fathom_hook(p_token_hash text) RETURNS TABLE (organization_id uuid, account_id uuid)
             LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
           $$ SELECT organization_id, id FROM fathom_accounts WHERE hook_token_hash = p_token_hash $$""",
        "REVOKE ALL ON FUNCTION hc_fathom_hook(text) FROM PUBLIC",
        """DO $$ BEGIN
             IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_app') THEN
               GRANT EXECUTE ON FUNCTION hc_fathom_hook(text) TO humanum_app;
               GRANT SELECT, INSERT, UPDATE, DELETE ON fathom_accounts, meetings, cohorts, cohort_members TO humanum_app;
               GRANT SELECT, INSERT, UPDATE, DELETE ON syntheses TO humanum_app;
             END IF;
           END $$""",
    ):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS hc_fathom_hook(text)")
    for t in ("syntheses", "meetings", "cohort_members", "cohorts", "fathom_accounts"):
        op.drop_table(t)
