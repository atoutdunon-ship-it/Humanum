"""Configuration — variables d'environnement préfixées HC_."""
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HC_", env_file=".env", extra="ignore")

    env: str = "dev"
    # Connexion applicative : rôle SANS superuser ni BYPASSRLS (sinon la RLS est ignorée).
    database_url: str = "postgresql+asyncpg://humanum_app:humanum_app@127.0.0.1:5433/humanum"
    # Connexion propriétaire : migrations uniquement.
    database_owner_url: str = "postgresql+asyncpg://postgres@127.0.0.1:5433/humanum"

    jwt_secret: SecretStr = SecretStr("change-me-in-production-32-bytes-minimum!!")
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = Field(default=30, ge=5, le=240)

    # Connexion de la super-administration : rôle BYPASSRLS limité aux tables de plateforme
    # (aucun droit sur les données de coaching : contacts, séances, synthèses).
    database_platform_url: str = "postgresql+asyncpg://humanum_platform:humanum_platform@127.0.0.1:5433/humanum"
    platform_token_minutes: int = Field(default=20, ge=5, le=120)
    login_max_attempts: int = Field(default=5, ge=3, le=20)  # par identifiant et par IP, sur 15 minutes

    # Double authentification (TOTP, RFC 6238).
    # Clés Fernet séparées par des virgules : la première chiffre, toutes déchiffrent (rotation sans coupure).
    mfa_keys: SecretStr = SecretStr("J0hO0k2mO3t6m1x2J8u6l2c3tQ2n7k1Lr0w9yZ5aB4c=")
    mfa_enforced: bool = True  # rôles d'encadrement et super-administrateurs : activation obligatoire
    mfa_issuer: str = "HUMANUM CONSULTING"
    mfa_challenge_minutes: int = Field(default=5, ge=1, le=15)

    # Redis : limitation des tentatives partagée entre instances et file de tâches Arq.
    # Vide : limitation en mémoire (une seule instance, développement).
    redis_url: str | None = None

    # Prise de rendez-vous : application OAuth Google (console Google Cloud, type « application Web »).
    public_base_url: str = "http://localhost:3000"   # liens envoyés aux coachés (gestion du rendez-vous)
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:3000/api/v1/integrations/google/callback"
    google_timeout_seconds: float = Field(default=8.0, ge=1, le=30)

    # Facturation Stripe (clés du compte HUMANUM CONSULTING ; mode test « sk_test_… » en recette).
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None   # « whsec_… » du point de terminaison configuré dans Stripe
    stripe_api_version: str = "2024-06-20"
    stripe_automatic_tax: bool = False               # Stripe Tax (TVA DOM 8,5 %, métropole 20 %…) si activé sur le compte
    billing_enforced: bool = True                    # abonnement résilié ou impayé : fonctions coûteuses suspendues

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5-5"

    @model_validator(mode="after")
    def _no_dev_secrets_outside_dev(self) -> "Settings":
        """Hors développement, les secrets de démonstration sont refusés au démarrage."""
        if self.env != "dev":
            defaults = {f: type(self).model_fields[f].default.get_secret_value() for f in ("jwt_secret", "mfa_keys")}
            weak = [f for f, d in defaults.items() if getattr(self, f).get_secret_value() == d]
            if weak:
                raise ValueError(f"Secrets de développement interdits hors HC_ENV=dev : {', '.join('HC_' + w.upper() for w in weak)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
