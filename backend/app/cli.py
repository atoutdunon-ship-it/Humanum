"""Commandes d'exploitation.

    python -m app.cli create-superadmin --username ADMIN

Le mot de passe initial est lu dans HC_SUPERADMIN_PASSWORD, sinon demandé au clavier.
Il n'est jamais écrit dans le code ni dans un fichier. Le changement est imposé à la première connexion.
"""
import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import func, select

from app.core.security import hash_password
from app.db.session import dispose, platform_session
from app.models import PlatformAdmin
from app.services import mfa as mfa_svc


async def create_superadmin(username: str, password: str, reset: bool) -> str:
    async with platform_session() as s:
        a = await s.scalar(select(PlatformAdmin).where(func.lower(PlatformAdmin.username) == username.lower()))
        if a and not reset:
            return f"Le compte {username} existe déjà (--reset pour réinitialiser son mot de passe)."
        if a is None:
            s.add(PlatformAdmin(username=username, password_hash=hash_password(password), must_change_password=True))
            msg = f"Compte de super-administration {username} créé."
        else:
            a.password_hash, a.must_change_password, a.is_active = hash_password(password), True, True
            mfa_svc.clear(a)  # accès de secours depuis le serveur : réenrôlement imposé
            msg = f"Mot de passe et double authentification de {username} réinitialisés."
    await dispose()
    return msg + " Changement du mot de passe obligatoire à la première connexion."


def main() -> None:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-superadmin")
    c.add_argument("--username", required=True)
    c.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    pwd = os.environ.get("HC_SUPERADMIN_PASSWORD") or getpass.getpass("Mot de passe initial : ")
    if not pwd:
        sys.exit("Mot de passe vide.")
    print(asyncio.run(create_superadmin(args.username, pwd, args.reset)))


if __name__ == "__main__":
    main()
