-- À exécuter UNE fois par cluster, en superuser.
-- Le rôle applicatif ne doit être NI superuser NI BYPASSRLS : sinon la RLS est ignorée.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_app') THEN
    CREATE ROLE humanum_app LOGIN PASSWORD 'humanum_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END $$;
-- Super-administration : contourne la RLS, mais ne reçoit AUCUN droit sur les données de coaching.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'humanum_platform') THEN
    CREATE ROLE humanum_platform LOGIN PASSWORD 'humanum_platform' NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END $$;
