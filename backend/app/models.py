"""Stage 1 data model: tenants, identity, roles, permissions, sessions, audit.

Conventions (spec section 3):
- IDs are opaque strings on the wire.
- Every tenant-owned row carries org_id; queries always filter on it.
- Instants are timezone-aware UTC.
"""
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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


class DocumentFolder(Base):
    """A folder in the document library. Folders nest through parent_folder_id."""

    __tablename__ = "document_folders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    parent_folder_id: Mapped[str | None] = mapped_column(ForeignKey("document_folders.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    color_key: Mapped[str | None] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)  # generated folders cannot be renamed or deleted
    shared_reference: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base):
    """A library document. `stored_file_id` points at the current revision's bytes."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    folder_id: Mapped[str] = mapped_column(ForeignKey("document_folders.id"), index=True)
    filename: Mapped[str] = mapped_column(String(200))  # display name; the file's identity is its id
    stored_file_id: Mapped[str] = mapped_column(ForeignKey("stored_files.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    uploaded_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # bytes declared unrecoverable
    gone_reason: Mapped[str | None] = mapped_column(String(300))


class DocumentRevision(Base):
    """Every set of bytes a document has had. Earlier revisions are kept, never overwritten."""

    __tablename__ = "document_revisions"
    __table_args__ = (UniqueConstraint("document_id", "revision", name="uq_document_revisions_doc_rev"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    stored_file_id: Mapped[str] = mapped_column(ForeignKey("stored_files.id"))
    filename: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FormTemplate(Base):
    """A form definition. `schema` is the working draft; published versions are immutable snapshots."""

    __tablename__ = "form_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="general")
    description: Mapped[str] = mapped_column(Text, default="")
    schema: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published | archived
    published_version: Mapped[int] = mapped_column(Integer, default=0)
    has_unpublished_changes: Mapped[bool] = mapped_column(Boolean, default=True)
    certification_gate_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    required_certifications: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FormTemplateVersion(Base):
    """An immutable published snapshot. Submissions point here, so later edits never change old records."""

    __tablename__ = "form_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_form_template_versions"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("form_templates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    schema: Mapped[list[Any]] = mapped_column(JSONB)
    published_by: Mapped[str | None] = mapped_column(String(36))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FormSubmission(Base):
    """One completed (or in-progress) form. A client-supplied id makes offline retries idempotent."""

    __tablename__ = "form_submissions"
    __table_args__ = (
        UniqueConstraint("org_id", "submitted_by", "client_submission_id", name="uq_form_submissions_client_id"),
        Index("ix_form_submissions_template", "template_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("form_templates.id"))
    template_version_id: Mapped[str] = mapped_column(ForeignKey("form_template_versions.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | submitted
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    submitted_by: Mapped[str] = mapped_column(String(36), index=True)
    client_submission_id: Mapped[str | None] = mapped_column(String(64))
    launched_via: Mapped[str | None] = mapped_column(String(40))
    source_scan_token: Mapped[str | None] = mapped_column(String(200))
    source_asset_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FormSubmissionFile(Base):
    """A photo, attachment or signature uploaded for one field of one submission."""

    __tablename__ = "form_submission_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("form_submissions.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))  # photo | attachment | signature
    stored_file_id: Mapped[str] = mapped_column(ForeignKey("stored_files.id"))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
