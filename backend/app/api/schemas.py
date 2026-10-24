import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.security import Role


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    organization_id: uuid.UUID | None = None


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role: Role


class TokenOut(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"  # noqa: S105 (type OAuth2, pas un secret)
    organization_id: uuid.UUID | None = None
    role: Role | None = None
    memberships: list[MembershipOut] = []
    mfa_required: bool = False         # étape 2 : envoyer le code à /auth/login/mfa avec `mfa_token`
    mfa_token: str | None = None
    select_token: str | None = None    # plusieurs organismes : choisir via /auth/select-org (sans tout ressaisir)
    mfa_setup_required: bool = False   # jeton restreint : activer la double authentification


class MfaLoginIn(BaseModel):
    mfa_token: str = Field(max_length=2048)
    code: str = Field(min_length=6, max_length=16)
    organization_id: uuid.UUID | None = None


class SelectOrgIn(BaseModel):
    select_token: str = Field(max_length=2048)
    organization_id: uuid.UUID


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=16)


class MfaDisableIn(BaseModel):
    password: str = Field(max_length=128)
    code: str = Field(min_length=6, max_length=16)


class MemberCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=128)
    role: Role


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    full_name: str
    role: Role


class ContactIn(BaseModel):
    kind: str = Field(default="coachee", pattern=r"^(coachee|prescriber|funder)$")
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, max_length=40)
    pipeline_stage: str = Field(default="prospect", pattern=r"^(prospect|devis|contractualise|en_cours|termine)$")
    notes: str | None = None


class ContactOut(ContactIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    organization_id: uuid.UUID
    created_at: datetime
