#!/bin/bash
# HUMANUM CONSULTING — lancement local en mode démonstration (double-clic depuis le Finder).
# Prérequis : Docker Desktop installé et ouvert.
set -e
cd "$(dirname "$0")"
echo ""
echo "  HUMANUM CONSULTING — démarrage"
echo ""
if ! command -v docker >/dev/null 2>&1; then
  echo "  Docker Desktop n'est pas installé : https://www.docker.com/products/docker-desktop/"
  read -r -p "  Appuyez sur Entrée pour fermer." _; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "  Ouverture de Docker Desktop…"; open -a Docker || true
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { echo "  Docker ne répond pas : ouvrez Docker Desktop puis relancez."; read -r -p "  Entrée pour fermer." _; exit 1; }
fi
if [ ! -f backend/.env ]; then
  echo "  Création des secrets locaux (backend/.env)…"
  {
    echo "HC_ENV=dev"
    echo "HC_JWT_SECRET=$(openssl rand -hex 32)"
    echo "HC_MFA_KEYS=$(openssl rand -base64 32 | tr '+/' '-_')"
    echo "HC_PUBLIC_BASE_URL=http://localhost:3000"
  } > backend/.env
  chmod 600 backend/.env
fi
COMPOSE="docker compose -p humanum -f docker-compose.yml -f docker-compose.demo.yml"
echo "  Construction et démarrage (la première fois : quelques minutes)…"
$COMPOSE up -d --build
echo -n "  Attente de la plateforme"
for _ in $(seq 1 90); do
  curl -fs http://localhost:3000/login >/dev/null 2>&1 && curl -fs http://localhost:3000/api/v1/public/plans >/dev/null 2>&1 && break
  echo -n "."; sleep 2
done
echo ""
if ! $COMPOSE exec -T api python -c "
import asyncio, sys
from sqlalchemy import text
from app.db.session import platform_session
async def m():
    async with platform_session() as s:
        n = (await s.execute(text('select count(*) from platform_admins'))).scalar()
    sys.exit(0 if n else 1)
asyncio.run(m())" 2>/dev/null; then
  echo ""
  echo "  Premier lancement : création du compte de super-administration ADMIN."
  echo "  Saisissez le mot de passe initial (il ne s'affiche pas) ; il devra être changé à la première connexion."
  $COMPOSE exec api python -m app.cli create-superadmin --username ADMIN
fi
echo ""
echo "  Plateforme prête :"
echo "    Administration : http://localhost:3000/admin/login"
echo "    Organismes     : http://localhost:3000/login"
echo "  Pour arrêter : double-cliquez sur « Arrêter HUMANUM.command »."
open "http://localhost:3000/admin/login"
