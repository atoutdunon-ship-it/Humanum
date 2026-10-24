"""pilotage : agrégats d'usage par organisme pour la super-administration (chiffres seulement, aucun contenu)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-22 22:00:00
"""
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for stmt in (
        # Le rôle plateforme n'a aucun droit sur les données de coaching : il n'en obtient que des comptages.
        """CREATE FUNCTION hc_platform_usage(p_since timestamptz) RETURNS TABLE (
               organization_id uuid, coaches bigint, coachee_spaces bigint, contacts bigint, sessions bigint,
               meetings bigint, syntheses bigint, input_tokens bigint, output_tokens bigint)
             LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
           $$ SELECT o.id,
                (SELECT count(*) FROM memberships m WHERE m.organization_id = o.id AND m.role IN ('coach','org_admin')),
                (SELECT count(*) FROM contacts c WHERE c.organization_id = o.id AND c.user_id IS NOT NULL),
                (SELECT count(*) FROM contacts c WHERE c.organization_id = o.id),
                (SELECT count(*) FROM appointments a WHERE a.organization_id = o.id AND a.status = 'confirmed'
                   AND a.starts_at >= p_since AND a.ends_at <= now()),
                (SELECT count(*) FROM meetings mt WHERE mt.organization_id = o.id AND mt.started_at >= p_since),
                (SELECT count(*) FROM syntheses s WHERE s.organization_id = o.id AND s.created_at >= p_since AND s.status = 'done'),
                (SELECT coalesce(sum(s.input_tokens), 0) FROM syntheses s WHERE s.organization_id = o.id AND s.created_at >= p_since),
                (SELECT coalesce(sum(s.output_tokens), 0) FROM syntheses s WHERE s.organization_id = o.id AND s.created_at >= p_since)
              FROM organizations o $$""",
        "REVOKE ALL ON FUNCTION hc_platform_usage(timestamptz) FROM PUBLIC",
        """DO $$ BEGIN
             IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_platform') THEN
               GRANT EXECUTE ON FUNCTION hc_platform_usage(timestamptz) TO humanum_platform;
             END IF;
           END $$""",
    ):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS hc_platform_usage(timestamptz)")
