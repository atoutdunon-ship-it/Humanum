"""espace coaché : compte relié à la fiche CRM, invitations, actions de parcours, partage des séances et synthèses

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22 19:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

ORG = "nullif(current_setting('app.current_org', true), '')::uuid"


def _id_org() -> list[sa.Column]:
    return [sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
                      index=True, server_default=sa.text("(current_setting('app.current_org'))::uuid"))]


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)]


def upgrade() -> None:
    op.add_column("contacts", sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.create_unique_constraint("uq_contacts_org_user", "contacts", ["organization_id", "user_id"])
    op.add_column("meetings", sa.Column("shared_with_coachee", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("syntheses", sa.Column("shared_with_coachee", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table("invitations", *_id_org(),
        sa.Column("contact_id", sa.UUID(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")), *_ts())
    op.create_table("tasks", *_id_org(),
        sa.Column("contact_id", sa.UUID(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("title", sa.String(300), nullable=False), sa.Column("notes", sa.Text()),
        sa.Column("due_on", sa.DateTime(timezone=True)), sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("visible_to_coachee", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_meeting_id", sa.UUID(), sa.ForeignKey("meetings.id", ondelete="SET NULL")),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")), *_ts())
    for t in ("invitations", "tasks"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})")
    for stmt in (
        """CREATE FUNCTION hc_invitation_org(p_token_hash text) RETURNS uuid
             LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
           $$ SELECT organization_id FROM invitations WHERE token_hash = p_token_hash $$""",
        "REVOKE ALL ON FUNCTION hc_invitation_org(text) FROM PUBLIC",
        """DO $$ BEGIN
             IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_app') THEN
               GRANT EXECUTE ON FUNCTION hc_invitation_org(text) TO humanum_app;
               GRANT SELECT, INSERT, UPDATE ON invitations TO humanum_app;
               GRANT SELECT, INSERT, UPDATE, DELETE ON tasks TO humanum_app;
             END IF;
           END $$""",
    ):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS hc_invitation_org(text)")
    op.drop_table("tasks")
    op.drop_table("invitations")
    op.drop_column("syntheses", "shared_with_coachee")
    op.drop_column("meetings", "shared_with_coachee")
    op.drop_constraint("uq_contacts_org_user", "contacts", type_="unique")
    op.drop_column("contacts", "user_id")
