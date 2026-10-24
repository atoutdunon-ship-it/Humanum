#!/bin/bash
# Arrête HUMANUM CONSULTING (les données sont conservées pour le prochain lancement).
cd "$(dirname "$0")"
docker compose -p humanum -f docker-compose.yml -f docker-compose.demo.yml down
echo "  HUMANUM CONSULTING arrêté. Données conservées."
sleep 2
