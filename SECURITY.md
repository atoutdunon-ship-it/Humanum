# Sécurité

- Aucun secret n'est versionné : chaque environnement crée son propre `backend/.env` (modèle : `backend/.env.example`).
- Hors `HC_ENV=dev`, l'API refuse de démarrer avec les secrets de démonstration.
- Dépôt à garder **privé**. Signaler toute vulnérabilité à atout.dunon@gmail.com, sans la publier.
