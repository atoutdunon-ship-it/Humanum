"""Modèle de données du lot 0 : socle multi-organismes + premier objet métier (Contact)."""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import Role
from app.db.base import Base, PKMixin, TenantMixin, TimestampMixin

ROLE_ENUM = Enum(Role, name="member_role", values_callable=lambda e: [m.value for m in e])


class Organization(PKMixin, TimestampMixin, Base):
    """Organisme client (tenant). Pas de RLS : seul l'API plateforme y écrit."""
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    plan: Mapped[str] = mapped_column(String(32), default="starter")
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Abonnement Stripe (écrit uniquement par la plateforme : webhooks et routes de facturation)
    billing_status: Mapped[str] = mapped_column(String(24), default="none")  # none | trialing | active | past_due | canceled | unpaid | incomplete…
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    stripe_subscription_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    billing_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    discount_label: Mapped[str | None] = mapped_column(String(160), nullable=True)  # remise en cours (affichage)


class MfaMixin:
    """Double authentification TOTP : secret chiffré, enrôlement en attente, anti-rejeu, codes de secours."""
    mfa_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_pending_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_pending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mfa_recovery: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")

    @property
    def mfa_enabled(self) -> bool:
        return self.mfa_secret_enc is not None


class User(PKMixin, TimestampMixin, MfaMixin, Base):
    """Compte global : une personne peut appartenir à plusieurs organismes."""
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(PKMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(ROLE_ENUM)


class Contact(PKMixin, TenantMixin, TimestampMixin, Base):
    """Fiche CRM (coaché, prescripteur, financeur)."""
    __tablename__ = "contacts"
    kind: Mapped[str] = mapped_column(String(24), default="coachee")  # coachee | prescriber | funder
    first_name: Mapped[str] = mapped_column(String(120))
    last_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)  # indépendant, porteur de projet, porteur d'idée
    pipeline_stage: Mapped[str] = mapped_column(String(24), default="prospect")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)  # compte de l'espace coaché


class AuditLog(PKMixin, TenantMixin, Base):
    __tablename__ = "audit_logs"
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


# ---------- Plateforme (super-administration) : tables globales, sans RLS ----------

class PlatformAdmin(PKMixin, TimestampMixin, MfaMixin, Base):
    """Compte de super-administration, distinct des comptes des organismes."""
    __tablename__ = "platform_admins"
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Plan(TimestampMixin, Base):
    """Formule SaaS modifiable depuis le panneau d'administration."""
    __tablename__ = "plans"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    monthly_price_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    max_coaches: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = illimité
    max_active_coachees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_syntheses_per_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exports: Mapped[list] = mapped_column(JSONB, default=list)
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    stripe_product_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)  # montant du prix Stripe actuel


class FeatureFlag(TimestampMixin, Base):
    """Option activable : valeur par défaut globale, surchargeable par organisme."""
    __tablename__ = "feature_flags"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled_by_default: Mapped[bool] = mapped_column(Boolean, default=False)


class OrgFeature(Base):
    __tablename__ = "org_features"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), ForeignKey("feature_flags.key", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)


class PlatformText(Base):
    """Textes de l'interface, modifiables sans redéploiement (clé + langue)."""
    __tablename__ = "platform_texts"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    locale: Mapped[str] = mapped_column(String(8), primary_key=True, default="fr")
    value: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlatformAudit(PKMixin, Base):
    __tablename__ = "platform_audit"
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


# ---------------------------------------------------------------- lot 2 : prise de rendez-vous
class GoogleAccount(PKMixin, TenantMixin, TimestampMixin, Base):
    """Agenda Google d'un coach (OAuth hors ligne). Jetons chiffrés au repos (clés HC_MFA_KEYS)."""
    __tablename__ = "google_accounts"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    google_email: Mapped[str] = mapped_column(String(320))
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str] = mapped_column(Text, default="")
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | revoked
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CoachSchedule(PKMixin, TenantMixin, TimestampMixin, Base):
    """Règles de réservation d'un coach : fuseau, délai de prévenance, horizon, pas des créneaux."""
    __tablename__ = "coach_schedules"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Martinique")
    min_notice_minutes: Mapped[int] = mapped_column(Integer, default=240)
    max_days_ahead: Mapped[int] = mapped_column(Integer, default=120)  # 4 mois
    slot_step_minutes: Mapped[int] = mapped_column(Integer, default=30)


class AvailabilityRule(PKMixin, TenantMixin, Base):
    """Plage hebdomadaire récurrente, en minutes depuis minuit (heure locale du coach). weekday : 0 = lundi."""
    __tablename__ = "availability_rules"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_minute: Mapped[int] = mapped_column(Integer)
    end_minute: Mapped[int] = mapped_column(Integer)


class BookingType(PKMixin, TenantMixin, TimestampMixin, Base):
    """Type de rendez-vous réservable en ligne (séance individuelle, entretien de diagnostic…)."""
    __tablename__ = "booking_types"
    __table_args__ = (UniqueConstraint("organization_id", "slug"),)
    coach_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, default=0)
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, default=15)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Appointment(PKMixin, TenantMixin, TimestampMixin, Base):
    """Rendez-vous. [busy_from, busy_to) inclut les battements : PostgreSQL interdit tout chevauchement confirmé."""
    __tablename__ = "appointments"
    booking_type_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("booking_types.id", ondelete="SET NULL"), nullable=True)
    coach_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    attendee_name: Mapped[str] = mapped_column(String(200))
    attendee_email: Mapped[str] = mapped_column(String(320))
    attendee_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    busy_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    busy_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="confirmed")  # confirmed | cancelled
    source: Mapped[str] = mapped_column(String(16), default="public")      # public | staff
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manage_token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # recherche par le lien reçu
    manage_token_enc: Mapped[str] = mapped_column(Text)                      # pour réécrire le lien dans l'invitation
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meet_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | synced | error | skipped
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_version: Mapped[int] = mapped_column(Integer, default=1)


# ---------------------------------------------------------------- Fathom et synthèses
class FathomAccount(PKMixin, TenantMixin, TimestampMixin, Base):
    """Compte Fathom d'un coach (un compte par coach). Clé d'API et secret du webhook chiffrés au repos."""
    __tablename__ = "fathom_accounts"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    fathom_email: Mapped[str] = mapped_column(String(320))
    api_key_enc: Mapped[str] = mapped_column(Text)
    webhook_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    webhook_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    hook_token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # chemin secret de l'URL de réception
    status: Mapped[str] = mapped_column(String(16), default="active")       # active | error
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Meeting(PKMixin, TenantMixin, TimestampMixin, Base):
    """Réunion enregistrée par Fathom, rattachée au coaché (e-mail des invités) et au rendez-vous (horaire)."""
    __tablename__ = "meetings"
    __table_args__ = (UniqueConstraint("organization_id", "recording_id"),)
    recording_id: Mapped[int] = mapped_column(BigInteger)
    coach_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    share_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    invitees: Mapped[list] = mapped_column(JSONB, default=list)
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_items: Mapped[list] = mapped_column(JSONB, default=list)
    transcript: Mapped[list] = mapped_column(JSONB, default=list)
    shared_with_coachee: Mapped[bool] = mapped_column(Boolean, default=False)  # résumé et actions, jamais la transcription


class Cohort(PKMixin, TenantMixin, TimestampMixin, Base):
    """Groupe de coachés (promotion, atelier, dispositif financé)."""
    __tablename__ = "cohorts"
    name: Mapped[str] = mapped_column(String(200))
    program: Mapped[str | None] = mapped_column(String(200), nullable=True)   # ex. SOLO BOOST
    funder: Mapped[str | None] = mapped_column(String(200), nullable=True)    # ex. CTM / FSE+
    starts_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CohortMember(PKMixin, TenantMixin, Base):
    __tablename__ = "cohort_members"
    __table_args__ = (UniqueConstraint("cohort_id", "contact_id"),)
    cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), index=True)


class Synthesis(PKMixin, TenantMixin, TimestampMixin, Base):
    """Synthèse générée par Claude : parcours (un coaché), groupe ou rapport financeur (une cohorte)."""
    __tablename__ = "syntheses"
    kind: Mapped[str] = mapped_column(String(16))                    # parcours | groupe | financeur
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True, index=True)
    cohort_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | done | error
    content_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[dict] = mapped_column(JSONB, default=dict)          # réunions et périodes utilisées
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    shared_with_coachee: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- espace coaché
class Invitation(PKMixin, TenantMixin, TimestampMixin, Base):
    """Invitation d'un coaché à son espace : lien à usage unique (7 jours), seule l'empreinte est stockée."""
    __tablename__ = "invitations"
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Task(PKMixin, TenantMixin, TimestampMixin, Base):
    """Action de parcours : fixée par le coach (ou reprise d'une séance Fathom), cochée par le coaché."""
    __tablename__ = "tasks"
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    visible_to_coachee: Mapped[bool] = mapped_column(Boolean, default=True)
    source_meeting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


# ---------------------------------------------------------------- facturation
class BillingInvoice(PKMixin, TenantMixin, TimestampMixin, Base):
    """Copie locale des factures Stripe (écrite par la plateforme, lue par l'organisme sous RLS)."""
    __tablename__ = "billing_invoices"
    stripe_invoice_id: Mapped[str] = mapped_column(String(64), unique=True)
    number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24))              # draft | open | paid | uncollectible | void
    currency: Mapped[str] = mapped_column(String(3))
    amount_due: Mapped[int] = mapped_column(Integer)
    amount_paid: Mapped[int] = mapped_column(Integer)
    hosted_invoice_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    invoice_pdf: Mapped[str | None] = mapped_column(String(500), nullable=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformSetting(Base):
    """Réglages de plateforme modifiables par la super-administration (ex. TVA optionnelle)."""
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Promotion(PKMixin, TimestampMixin, Base):
    """Code promotionnel administrable, adossé à un coupon et à un code Stripe."""
    __tablename__ = "promotions"
    code: Mapped[str] = mapped_column(String(40), unique=True)
    label: Mapped[str] = mapped_column(String(120))
    percent_off: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    amount_off_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    duration: Mapped[str] = mapped_column(String(16))
    duration_in_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plans: Mapped[list] = mapped_column(JSONB, default=list)
    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_time_only: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    stripe_coupon_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_promotion_code_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    times_redeemed: Mapped[int] = mapped_column(Integer, default=0)       # utilisations du code Stripe actuel
    redeemed_before: Mapped[int] = mapped_column(Integer, default=0)      # utilisations des versions précédentes
    revision: Mapped[int] = mapped_column(Integer, default=1)             # incrémentée à chaque ajustement


class StripeEvent(Base):
    """Événements Stripe déjà traités : chaque webhook n'a d'effet qu'une fois."""
    __tablename__ = "stripe_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(80))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Tables soumises à la RLS — la migration et les tests d'étanchéité s'appuient sur cette liste.
TENANT_TABLES = ("memberships", "contacts", "audit_logs", "google_accounts", "coach_schedules",
                 "availability_rules", "booking_types", "appointments", "fathom_accounts", "meetings", "cohorts",
                 "cohort_members", "syntheses", "invitations", "tasks", "billing_invoices")
