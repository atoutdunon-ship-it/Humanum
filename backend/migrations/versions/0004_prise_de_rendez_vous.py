"""prise de rendez-vous : agendas Google des coachs, disponibilités, types de RDV, rendez-vous

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22 18:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

ORG = "nullif(current_setting('app.current_org', true), '')::uuid"
TABLES = ("google_accounts", "coach_schedules", "availability_rules", "booking_types", "appointments")


def _tenant(name: str) -> sa.Column:
    return sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
                     index=True, server_default=sa.text("(current_setting('app.current_org'))::uuid"))


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")  # égalité sur uuid dans une contrainte d'exclusion
    user_fk = lambda: sa.ForeignKey("users.id", ondelete="CASCADE")  # noqa: E731

    op.create_table("google_accounts",
        sa.Column("id", sa.UUID(), primary_key=True), _tenant("google_accounts"),
        sa.Column("user_id", sa.UUID(), user_fk(), nullable=False),
        sa.Column("google_email", sa.String(320), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False),
        sa.Column("access_token_enc", sa.Text()), sa.Column("access_expires_at", sa.DateTime(timezone=True)),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("calendar_id", sa.String(255), nullable=False, server_default="primary"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_error", sa.Text()), *_ts(),
        sa.UniqueConstraint("organization_id", "user_id"),
        sa.CheckConstraint("status in ('active','revoked')", name="ck_google_accounts_status"))

    op.create_table("coach_schedules",
        sa.Column("id", sa.UUID(), primary_key=True), _tenant("coach_schedules"),
        sa.Column("user_id", sa.UUID(), user_fk(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="America/Martinique"),
        sa.Column("min_notice_minutes", sa.Integer(), nullable=False, server_default="240"),
        sa.Column("max_days_ahead", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("slot_step_minutes", sa.Integer(), nullable=False, server_default="30"), *_ts(),
        sa.UniqueConstraint("organization_id", "user_id"),
        sa.CheckConstraint("min_notice_minutes between 0 and 20160 and max_days_ahead between 1 and 365 "
                           "and slot_step_minutes in (5,10,15,20,30,45,60,90,120)", name="ck_coach_schedules_bounds"))

    op.create_table("availability_rules",
        sa.Column("id", sa.UUID(), primary_key=True), _tenant("availability_rules"),
        sa.Column("user_id", sa.UUID(), user_fk(), nullable=False, index=True),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=False), sa.Column("end_minute", sa.Integer(), nullable=False),
        sa.CheckConstraint("weekday between 0 and 6 and start_minute >= 0 and end_minute <= 1440 and start_minute < end_minute",
                           name="ck_availability_rules_window"))

    op.create_table("booking_types",
        sa.Column("id", sa.UUID(), primary_key=True), _tenant("booking_types"),
        sa.Column("coach_id", sa.UUID(), user_fk(), nullable=False, index=True),
        sa.Column("slug", sa.String(80), nullable=False), sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("buffer_before_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("buffer_after_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), *_ts(),
        sa.UniqueConstraint("organization_id", "slug"),
        sa.CheckConstraint("duration_minutes between 10 and 480 and buffer_before_minutes between 0 and 120 "
                           "and buffer_after_minutes between 0 and 120", name="ck_booking_types_bounds"))

    op.create_table("appointments",
        sa.Column("id", sa.UUID(), primary_key=True), _tenant("appointments"),
        sa.Column("booking_type_id", sa.UUID(), sa.ForeignKey("booking_types.id", ondelete="SET NULL")),
        sa.Column("coach_id", sa.UUID(), user_fk(), nullable=False, index=True),
        sa.Column("contact_id", sa.UUID(), sa.ForeignKey("contacts.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("attendee_name", sa.String(200), nullable=False), sa.Column("attendee_email", sa.String(320), nullable=False),
        sa.Column("attendee_phone", sa.String(40)), sa.Column("message", sa.Text()),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("busy_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("busy_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="confirmed"),
        sa.Column("source", sa.String(16), nullable=False, server_default="public"),
        sa.Column("cancel_reason", sa.Text()), sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("manage_token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("manage_token_enc", sa.Text(), nullable=False),
        sa.Column("google_event_id", sa.String(255)), sa.Column("meet_url", sa.String(255)),
        sa.Column("sync_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("sync_error", sa.Text()), sa.Column("sync_version", sa.Integer(), nullable=False, server_default="1"), *_ts(),
        sa.CheckConstraint("starts_at < ends_at and busy_from <= starts_at and busy_to >= ends_at", name="ck_appointments_times"),
        sa.CheckConstraint("status in ('confirmed','cancelled')", name="ck_appointments_status"),
        sa.CheckConstraint("source in ('public','staff')", name="ck_appointments_source"),
        sa.CheckConstraint("sync_status in ('pending','synced','error','skipped')", name="ck_appointments_sync"))
    # Double réservation impossible, même en cas de requêtes simultanées : arbitrage par PostgreSQL.
    op.execute("ALTER TABLE appointments ADD CONSTRAINT ex_appointments_no_overlap EXCLUDE USING gist "
               "(coach_id WITH =, tstzrange(busy_from, busy_to, '[)') WITH &&) WHERE (status = 'confirmed')")
    op.create_index("ix_appointments_sync", "appointments", ["sync_status"],
                    postgresql_where=sa.text("sync_status in ('pending','error')"))

    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})")

    # Pages publiques (sans compte) : seules deux questions franchissent la RLS, par fonctions dédiées,
    # et elles ne renvoient qu'un identifiant d'organisme. Tout le reste se lit ensuite SOUS la RLS.
    for stmt in (
        """CREATE FUNCTION hc_public_org(p_slug text) RETURNS uuid
             LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
           $$ SELECT id FROM organizations WHERE slug = p_slug AND is_active $$""",
        """CREATE FUNCTION hc_appointment_org(p_token_hash text) RETURNS uuid
             LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
           $$ SELECT organization_id FROM appointments WHERE manage_token_hash = p_token_hash $$""",
        # Rattrapage de synchronisation par le worker : couples (organisme, rendez-vous) seulement.
        """CREATE FUNCTION hc_pending_syncs(p_limit int) RETURNS TABLE (organization_id uuid, appointment_id uuid)
             LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
           $$ SELECT organization_id, id FROM appointments
               WHERE sync_status IN ('pending','error') AND updated_at < now() - interval '2 minutes'
                 AND ends_at > now() - interval '1 day'
               ORDER BY updated_at LIMIT least(p_limit, 500) $$""",
        "REVOKE ALL ON FUNCTION hc_public_org(text), hc_appointment_org(text), hc_pending_syncs(int) FROM PUBLIC",
        """DO $$ BEGIN
             IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_app') THEN
               GRANT EXECUTE ON FUNCTION hc_public_org(text), hc_appointment_org(text), hc_pending_syncs(int) TO humanum_app;
               GRANT SELECT, INSERT, UPDATE, DELETE ON google_accounts, coach_schedules, availability_rules, booking_types TO humanum_app;
               GRANT SELECT, INSERT, UPDATE ON appointments TO humanum_app;  -- un rendez-vous s'annule, il ne s'efface pas
             END IF;
           END $$""",
    ):
        op.execute(stmt)


def downgrade() -> None:
    for f in ("hc_pending_syncs(int)", "hc_appointment_org(text)", "hc_public_org(text)"):
        op.execute(f"DROP FUNCTION IF EXISTS {f}")
    for t in reversed(TABLES):
        op.drop_table(t)
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
