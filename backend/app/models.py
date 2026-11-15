"""Stage 1 data model: tenants, identity, roles, permissions, sessions, audit.

Conventions (spec section 3):
- IDs are opaque strings on the wire.
- Every tenant-owned row carries org_id; queries always filter on it.
- Instants are timezone-aware UTC.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Organisation(Base):
    """The tenant boundary."""

    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Australia/Hobart")
    staff_login_url: Mapped[str | None] = mapped_column(String(500))
    public_portal_url: Mapped[str | None] = mapped_column(String(500))
    # The account holder: the protected top-level admin. Set once at first-run setup.
    owner_user_id: Mapped[str | None] = mapped_column(String(36))
    # Payroll owner (spec section 14). Defaults to the account holder at setup; never implied by role.
    payroll_owner_user_id: Mapped[str | None] = mapped_column(String(36))
    logo_file_id: Mapped[str | None] = mapped_column(String(36))
    # Contact and report details: trading_name, abn, address_line1/2, suburb, state, postcode,
    # country, contact_name/email/phone, emergency_contact_phone, after_hours_contact_name/phone, website_url
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Company(Base):
    """Business division inside an organisation (for example Paneltec and Viatec)."""

    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_companies_org_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Role(Base):
    """Role catalogue. System roles are seeded per organisation and are read-only."""

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("org_id", "role_id", name="uq_roles_org_role_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    role_id: Mapped[str] = mapped_column(String(80))  # slug, stable within the organisation
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Which phone role key this role maps to for module defaults (worker/supervisor/contractor/admin).
    legacy_role: Mapped[str] = mapped_column(String(40), default="worker")
    # {resource: {action: bool}}
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Base):
    """A login identity. A worker record is separate and linked by worker_id (stage 3)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(200))
    role_id: Mapped[str] = mapped_column(String(80))  # Role.role_id within the same organisation
    role_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    workspace_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    worker_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="invited")  # invited | active | disabled
    password_hash: Mapped[str | None] = mapped_column(String(500))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_password_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserPermissionOverride(Base):
    """Explicit per-user allow/deny cells. These win over role defaults."""

    __tablename__ = "user_permission_overrides"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    reasons: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    applied_preset_key: Mapped[str | None] = mapped_column(String(80))
    updated_by: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PermissionPreset(Base):
    """A named bundle of permission cells an administrator can apply to a user."""

    __tablename__ = "permission_presets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str | None] = mapped_column(String(60))
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserSession(Base):
    """A signed-in session. The bearer token is stored only as a SHA-256 hash."""

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user_active", "user_id", "revoked_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    remember_me: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(60))


class AuthToken(Base):
    """Single-use invitation and password-reset links. Only the hash is stored."""

    __tablename__ = "auth_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # invite | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Who changed what, and when. Never holds passwords, tokens or other secrets."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_target", "org_id", "target_type", "target_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OutboxMessage(Base):
    """Central record of every outbound message. Nothing is delivered in stage 1:
    messages are recorded as 'held' so the later outbox stage can pick them up."""

    __tablename__ = "outbox_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # email | sms
    purpose: Mapped[str] = mapped_column(String(60))
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="held")  # held | queued | sending | sent | failed | cancelled
    hold_reason: Mapped[str | None] = mapped_column(String(120))
    related_type: Mapped[str | None] = mapped_column(String(40))
    related_id: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StoredFile(Base):
    """Metadata for stored bytes. The bytes live on disk under FILES_DIR, addressed by SHA-256.

    File identity (id) is separate from the original and display names.
    """

    __tablename__ = "stored_files"
    __table_args__ = (Index("ix_stored_files_org_sha", "org_id", "sha256"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(120))
    original_name: Mapped[str] = mapped_column(String(300))
    purpose: Mapped[str] = mapped_column(String(40))  # logo | document | attachment | ...
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
