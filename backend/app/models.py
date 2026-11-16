"""Stage 1 data model: tenants, identity, roles, permissions, sessions, audit.

Conventions (spec section 3):
- IDs are opaque strings on the wire.
- Every tenant-owned row carries org_id; queries always filter on it.
- Instants are timezone-aware UTC.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
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
    # One login per worker record.
    __table_args__ = (Index("uq_users_worker", "worker_id", unique=True, postgresql_where=text("worker_id IS NOT NULL")),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(200))
    role_id: Mapped[str] = mapped_column(String(80))  # Role.role_id within the same organisation
    role_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    workspace_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    worker_id: Mapped[str | None] = mapped_column(String(36))
    # Set for an external login (a contractor's representative): limits what they see to that supplier.
    supplier_id: Mapped[str | None] = mapped_column(String(36))
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
    """Central record of every outbound message, whether or not it was delivered.

    held       not sent: safe mode is on, the provider is not connected, or the message carries a
               one-time link that is passed on by hand
    queued     accepted for sending, waiting for an attempt
    sent       the provider accepted it (accepted for processing, not proof of delivery)
    simulated  handled by a labelled simulator; nothing left the server
    failed     the provider refused it or could not be reached
    cancelled  withdrawn before sending
    """

    __tablename__ = "outbox_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # email | sms
    purpose: Mapped[str] = mapped_column(String(60))
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="held")
    hold_reason: Mapped[str | None] = mapped_column(String(200))
    hold_code: Mapped[str | None] = mapped_column(String(30))  # safe_mode | not_connected | manual_link
    # Kept only for messages with no secret in them, so they can be retried. A message carrying a
    # one-time link never has its body stored.
    body_text: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    related_type: Mapped[str | None] = mapped_column(String(40))
    related_id: Mapped[str | None] = mapped_column(String(80))
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(400))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxAttempt(Base):
    """One try at delivering a message: what happened, with anything sensitive removed."""

    __tablename__ = "outbox_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("outbox_messages.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(20))  # sent | simulated | failed | held
    detail: Mapped[str | None] = mapped_column(String(400))
    actor_user_id: Mapped[str | None] = mapped_column(String(36))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IntegrationConfig(Base):
    """One provider connection for one organisation. Secrets are encrypted; the names of the
    secrets that are set are kept alongside so a listing never needs the key."""

    __tablename__ = "integration_configs"
    __table_args__ = (UniqueConstraint("org_id", "kind", name="uq_integration_configs"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    mode: Mapped[str] = mapped_column(String(12), default="off")  # off | simulator | live
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    secrets: Mapped[str | None] = mapped_column(Text)  # encrypted JSON
    secrets_set: Mapped[list[str]] = mapped_column(JSONB, default=list)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_mode: Mapped[str | None] = mapped_column(String(12))
    last_test_message: Mapped[str | None] = mapped_column(String(400))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NotificationRead(Base):
    """A person has seen one notification. Notifications themselves are worked out from live records."""

    __tablename__ = "notification_reads"
    __table_args__ = (UniqueConstraint("user_id", "notification_id", name="uq_notification_reads"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    notification_id: Mapped[str] = mapped_column(String(160))
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class FormAssignment(Base):
    """Who may fill in a form. A form with no rules is open to everyone who can fill in forms."""

    __tablename__ = "form_assignments"
    __table_args__ = (UniqueConstraint("template_id", "target_kind", "target_id", name="uq_form_assignments"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("form_templates.id", ondelete="CASCADE"), index=True)
    target_kind: Mapped[str] = mapped_column(String(20))  # role | user | company
    target_id: Mapped[str] = mapped_column(String(80))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SafetyRecord(Base):
    """One entry in a safety register (SWMS, pre-start, site diary, hazard, incident, inspection,
    risk assessment, SSRA). The schema it was captured on is stored with it, so it always renders
    as it was filled in, whether it came from the built-in form or a published form template."""

    __tablename__ = "safety_records"
    __table_args__ = (
        UniqueConstraint("org_id", "source_submission_id", name="uq_safety_records_source_submission"),
        Index("ix_safety_records_list", "org_id", "kind", "status", "record_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30))
    record_date: Mapped[date | None] = mapped_column(Date)
    workspace_id: Mapped[str | None] = mapped_column(String(36))  # site link, validated once sites exist (stage 4)
    schema: Mapped[list[Any]] = mapped_column(JSONB)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | form | phone | import
    source_submission_id: Mapped[str | None] = mapped_column(String(36))
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[str | None] = mapped_column(String(36))
    superseded_by_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str] = mapped_column(String(36), index=True)
    updated_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archive_batch_id: Mapped[str | None] = mapped_column(String(36))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SafetyRecordFile(Base):
    __tablename__ = "safety_record_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("safety_records.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    stored_file_id: Mapped[str] = mapped_column(ForeignKey("stored_files.id"))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SafetyRecordEvent(Base):
    """History of a record: created, edited, submitted, reviewed, status changes, archive. Never rewritten."""

    __tablename__ = "safety_record_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("safety_records.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    actor_user_id: Mapped[str | None] = mapped_column(String(36))
    note: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SafetyAssignment(Base):
    """Who a record (a SWMS) is assigned to. No rules means everyone who can view the register."""

    __tablename__ = "safety_assignments"
    __table_args__ = (UniqueConstraint("record_id", "target_kind", "target_id", name="uq_safety_assignments"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("safety_records.id", ondelete="CASCADE"), index=True)
    target_kind: Mapped[str] = mapped_column(String(20))  # role | user | company
    target_id: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SafetyAcknowledgement(Base):
    """A person's sign-off on one specific version of a SWMS. It never moves to a newer version."""

    __tablename__ = "safety_acknowledgements"
    __table_args__ = (UniqueConstraint("record_id", "user_id", name="uq_safety_acknowledgements"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("safety_records.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(36))
    signed_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Worker(Base):
    """The operational person record. Separate from a login (User); the two are linked explicitly."""

    __tablename__ = "workers"
    __table_args__ = (
        UniqueConstraint("org_id", "source_system", "source_company", "source_id", name="uq_workers_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100), default="")
    preferred_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    mobile: Mapped[str | None] = mapped_column(String(40))
    position: Mapped[str | None] = mapped_column(String(120))
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The login link lives on users.worker_id (one login per worker, enforced by a unique index).
    # External identity (for example a Simpro employee). Imports upsert on this key, never on a name.
    source_system: Mapped[str | None] = mapped_column(String(20))
    source_company: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(80))
    # Import bookkeeping: the last values received, and the fields changed by hand since (kept on re-import).
    source_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    locally_edited: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    photo_file_id: Mapped[str | None] = mapped_column(String(36))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # address, emergency contact, notes
    protected: Mapped[str | None] = mapped_column(Text)  # encrypted JSON: birth date, tax file number, USI
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CertificationKind(Base):
    """Vocabulary of certification types (white card, first aid, licences...)."""

    __tablename__ = "certification_kinds"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_certification_kinds"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    slug: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(160))
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # "induction" kinds are the columns of the inductions matrix; they are still certificates.
    category: Mapped[str] = mapped_column(String(20), default="certificate", server_default="certificate")


class Certification(Base):
    """One certificate held by a worker. A renewal is a new row that supersedes the old one,
    so earlier evidence is kept."""

    __tablename__ = "certifications"
    __table_args__ = (Index("ix_certifications_worker", "worker_id", "kind_slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"))
    kind_slug: Mapped[str] = mapped_column(String(60))
    number: Mapped[str | None] = mapped_column(String(80))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    no_expiry: Mapped[bool] = mapped_column(Boolean, default=False)  # explicitly does not expire
    evidence_file_id: Mapped[str | None] = mapped_column(String(36))
    notes: Mapped[str | None] = mapped_column(Text)
    supersedes_id: Mapped[str | None] = mapped_column(String(36))
    superseded_by_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Site(Base):
    """A place where work happens. Has a QR token for sign-on; imports upsert on the source key."""

    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("org_id", "source_system", "source_company", "source_id", name="uq_sites_source"),
        UniqueConstraint("scan_token", name="uq_sites_scan_token"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    suburb: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(40))
    postcode: Mapped[str | None] = mapped_column(String(12))
    description: Mapped[str | None] = mapped_column(Text)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(String(20), default="project")  # project | depot | other
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_long: Mapped[float | None] = mapped_column(Float)
    source_system: Mapped[str | None] = mapped_column(String(20))
    source_company: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(80))
    source_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    locally_edited: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    # The secret in the printed QR code. Rotating it retires every sign printed before.
    scan_token: Mapped[str] = mapped_column(String(64))
    signon_questions: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    visitor_declaration: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SiteAttendance(Base):
    """One visit to a site by a signed-in person (kind=worker) or a visitor without a login.

    A person can have at most one open visit per site, which is what keeps occupancy honest
    when a sign-on is sent twice.
    """

    __tablename__ = "site_attendance"
    __table_args__ = (
        Index("uq_site_attendance_open_worker", "site_id", "user_id", unique=True,
              postgresql_where=text("kind = 'worker' AND signed_out_at IS NULL AND deleted_at IS NULL")),
        Index("uq_site_attendance_client_ref", "site_id", "client_ref", unique=True,
              postgresql_where=text("client_ref IS NOT NULL")),
        Index("ix_site_attendance_site_time", "site_id", "signed_in_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    kind: Mapped[str] = mapped_column(String(10))  # worker | visitor
    user_id: Mapped[str | None] = mapped_column(String(36))
    worker_id: Mapped[str | None] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(200))  # snapshot at sign-in
    company: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    purpose: Mapped[str | None] = mapped_column(String(300))
    host: Mapped[str | None] = mapped_column(String(200))
    declaration_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    answers: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # [{question_id, label, value}]
    gps: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(20), default="qr")  # qr | app | office
    client_ref: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    signed_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    signed_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sign_out_source: Mapped[str | None] = mapped_column(String(20))  # self | office
    signed_out_by: Mapped[str | None] = mapped_column(String(36))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(String(36))


class Supplier(Base):
    """An external organisation: a supplier, and when `is_contractor` is set, one that does work
    for us and so must keep compliance documents current. One directory, not two rosters."""

    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("org_id", "source_system", "source_company", "source_id", name="uq_suppliers_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    abn: Mapped[str | None] = mapped_column(String(20))
    trade: Mapped[str | None] = mapped_column(String(120))
    contact_name: Mapped[str | None] = mapped_column(String(160))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(300))
    suburb: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(40))
    postcode: Mapped[str | None] = mapped_column(String(12))
    is_contractor: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Import bookkeeping, kept apart from local edits so a sync never overwrites reviewed details.
    source_system: Mapped[str | None] = mapped_column(String(20))
    source_company: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(80))
    source_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    locally_edited: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplierNote(Base):
    __tablename__ = "supplier_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplierTask(Base):
    __tablename__ = "supplier_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | in_progress | done
    priority: Mapped[str] = mapped_column(String(10), default="normal")  # low | normal | high
    assigned_to: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplierMember(Base):
    """A person at the supplier (contact, or one of their people who comes to site)."""

    __tablename__ = "supplier_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    is_primary_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ComplianceDocType(Base):
    """What a supplier can be asked for: public liability, workers compensation, licences..."""

    __tablename__ = "compliance_doc_types"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_compliance_doc_types"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    slug: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(String(400))
    required_for_contractors: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class SupplierDocument(Base):
    """A compliance document held for a supplier. A newer one supersedes the old; both are kept."""

    __tablename__ = "supplier_documents"
    __table_args__ = (Index("ix_supplier_documents_supplier", "supplier_id", "doc_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    doc_type: Mapped[str] = mapped_column(String(60))
    file_id: Mapped[str] = mapped_column(String(36))
    reference: Mapped[str | None] = mapped_column(String(80))  # policy or licence number
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    no_expiry: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="office")  # office | renewal
    renewal_id: Mapped[str | None] = mapped_column(String(36))
    superseded_by_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RenewalLink(Base):
    """A scoped request for documents. The token opens one upload page for one supplier or worker
    and nothing else. Only the hash of the token is stored."""

    __tablename__ = "renewal_links"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_renewal_links_token"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(10))  # supplier | worker
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    doc_types: Mapped[list[str]] = mapped_column(JSONB, default=list)
    token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | submitted | accepted | partly_accepted | rejected | revoked
    recipient_email: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outbox_message_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(36))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RenewalUpload(Base):
    """One file supplied through a renewal link, and the office's decision on it."""

    __tablename__ = "renewal_uploads"
    __table_args__ = (Index("ix_renewal_uploads_renewal", "renewal_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    renewal_id: Mapped[str] = mapped_column(ForeignKey("renewal_links.id"))
    doc_type: Mapped[str] = mapped_column(String(60))
    file_id: Mapped[str] = mapped_column(String(36))
    reference: Mapped[str | None] = mapped_column(String(80))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    round_no: Mapped[int] = mapped_column(Integer, default=1)  # goes up each time the link is reissued
    decision: Mapped[str] = mapped_column(String(10), default="pending")  # pending | accepted | rejected
    decision_note: Mapped[str | None] = mapped_column(String(400))
    result_id: Mapped[str | None] = mapped_column(String(36))  # the supplier document or certification created
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InductionExemption(Base):
    """A worker explicitly marked "not applicable" for one induction, so the blank is deliberate."""

    __tablename__ = "induction_exemptions"
    __table_args__ = (UniqueConstraint("worker_id", "kind_slug", name="uq_induction_exemptions"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"))
    kind_slug: Mapped[str] = mapped_column(String(60))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrainingRecord(Base):
    """A course a worker has completed (toolbox talk, in-house training, external course)."""

    __tablename__ = "training_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"), index=True)
    course: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(200))
    completed_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    evidence_file_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHrDocument(Base):
    """A private and confidential file on a worker. Only people with HR access can list or open it."""

    __tablename__ = "worker_hr_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"), index=True)
    file_id: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyJob(Base):
    """A job issued to one worker for one day. A worker has at most one active job per day."""

    __tablename__ = "daily_jobs"
    __table_args__ = (
        Index("uq_daily_jobs_active", "worker_id", "job_date", unique=True,
              postgresql_where=text("status IN ('issued', 'accepted') AND deleted_at IS NULL")),
        Index("ix_daily_jobs_date", "org_id", "job_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    job_date: Mapped[date] = mapped_column(Date)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"))
    worker_name: Mapped[str] = mapped_column(String(200))  # as it was when the job was issued
    site_id: Mapped[str | None] = mapped_column(String(36))
    site_name: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    customer: Mapped[str | None] = mapped_column(String(200))
    truck: Mapped[str | None] = mapped_column(String(120))
    start_time: Mapped[str | None] = mapped_column(String(5))  # HH:MM, local
    staff: Mapped[list[str]] = mapped_column(JSONB, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    form_template_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(12), default="issued")  # issued | accepted | declined | completed | cancelled
    decline_reason: Mapped[str | None] = mapped_column(String(400))
    batch_id: Mapped[str | None] = mapped_column(String(36))
    outbox_message_id: Mapped[str | None] = mapped_column(String(36))
    issued_by: Mapped[str | None] = mapped_column(String(36))
    issued_by_name: Mapped[str | None] = mapped_column(String(200))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRun(Base):
    """One run of an import from an outside system: what was fetched and what it changed."""

    __tablename__ = "import_runs"
    __table_args__ = (Index("ix_import_runs_org", "org_id", "provider", "resource", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    resource: Mapped[str] = mapped_column(String(30))  # workers | sites | suppliers
    mode: Mapped[str] = mapped_column(String(12))  # live | simulator
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12))  # previewed | committed | partial | failed
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    companies: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # per company: fetched, ok, error
    error: Mapped[str | None] = mapped_column(String(400))
    started_by: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportReviewItem(Base):
    """An incoming record the import would not decide by itself: it looks like someone already
    on file. A person links it to that record, creates it separately, or ignores it."""

    __tablename__ = "import_review_items"
    __table_args__ = (UniqueConstraint("org_id", "resource", "source_system", "source_company", "source_id", name="uq_import_review_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36))
    resource: Mapped[str] = mapped_column(String(30))
    source_system: Mapped[str] = mapped_column(String(20))
    source_company: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(80))
    incoming: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    candidate_id: Mapped[str | None] = mapped_column(String(36))
    match_on: Mapped[str | None] = mapped_column(String(20))  # email | name | abn
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | linked | created | ignored
    resolved_by: Mapped[str | None] = mapped_column(String(36))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------- stage 6: fleet, equipment, service and fuel ----------

class EquipmentCategory(Base):
    """A named group of non-vehicle equipment (ladders, lifting gear, test instruments)."""

    __tablename__ = "equipment_categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    seeded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Asset(Base):
    """A vehicle, an item of plant, a trailer or a piece of equipment.

    A tracker from Navixy is matched on (source_system, source_id) and never on its label. Distance
    (odometer_km) and running time (engine_hours) are separate measurements and are never mixed.
    """

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("org_id", "source_system", "source_id", name="uq_assets_source"),
        UniqueConstraint("scan_token", name="uq_assets_scan_token"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="vehicle")  # vehicle | plant | trailer | equipment | other
    sub_type: Mapped[str | None] = mapped_column(String(80))
    category_id: Mapped[str | None] = mapped_column(ForeignKey("equipment_categories.id"))
    name: Mapped[str] = mapped_column(String(200))
    registration: Mapped[str | None] = mapped_column(String(40))
    serial_number: Mapped[str | None] = mapped_column(String(80))
    make: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(80))
    year: Mapped[int | None] = mapped_column(Integer)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"))
    assigned_worker_id: Mapped[str | None] = mapped_column(ForeignKey("workers.id"))
    location_note: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | out_of_service | archived
    fuel_type: Mapped[str | None] = mapped_column(String(20))
    tank_capacity_l: Mapped[float | None] = mapped_column(Float)
    odometer_km: Mapped[float | None] = mapped_column(Float)
    engine_hours: Mapped[float | None] = mapped_column(Float)
    meter_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A provider reading lower than the one on file. It is shown for a person to look at, never applied silently.
    meter_review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_inspection_date: Mapped[date | None] = mapped_column(Date)
    next_inspection_date: Mapped[date | None] = mapped_column(Date)
    source_system: Mapped[str | None] = mapped_column(String(20))  # navixy | simulator | None (added by hand)
    source_id: Mapped[str | None] = mapped_column(String(80))
    source_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    source_groups: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    source_missing: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    locally_edited: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    tracker_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    scan_token: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    photo_file_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetDocument(Base):
    """A file kept with an asset: a photo, a manual, a test or calibration certificate."""

    __tablename__ = "asset_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="document")  # document | certificate | photo
    title: Mapped[str] = mapped_column(String(200))
    file_id: Mapped[str] = mapped_column(ForeignKey("stored_files.id"))
    issued_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetMeterReading(Base):
    """One recorded odometer and/or hour-meter reading. The trail behind the figures on the asset."""

    __tablename__ = "asset_meter_readings"
    __table_args__ = (Index("ix_asset_meter_readings_asset_date", "asset_id", "reading_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    reading_date: Mapped[date] = mapped_column(Date)
    odometer_km: Mapped[float | None] = mapped_column(Float)
    engine_hours: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20))  # manual | navixy | simulator | service | fuel | reset
    applied: Mapped[bool] = mapped_column(Boolean, default=True)  # False: kept as evidence but not used as the current reading
    note: Mapped[str | None] = mapped_column(String(400))
    recorded_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssetSchedule(Base):
    """A recurring service for one asset: every so many days, kilometres and/or running hours,
    whichever comes first. The starting point is the last linked service record, or the baseline
    entered here when there is none yet. Editing a schedule never changes a past service record."""

    __tablename__ = "asset_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    interval_days: Mapped[int | None] = mapped_column(Integer)
    interval_km: Mapped[float | None] = mapped_column(Float)
    interval_hours: Mapped[float | None] = mapped_column(Float)
    warn_days: Mapped[int] = mapped_column(Integer, default=14)
    warn_km: Mapped[float] = mapped_column(Float, default=500)
    warn_hours: Mapped[float] = mapped_column(Float, default=25)
    baseline_date: Mapped[date | None] = mapped_column(Date)
    baseline_km: Mapped[float | None] = mapped_column(Float)
    baseline_hours: Mapped[float | None] = mapped_column(Float)
    assigned_worker_id: Mapped[str | None] = mapped_column(ForeignKey("workers.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetServiceRecord(Base):
    """Work actually done on an asset: a service, repair or inspection, with the meters at the time."""

    __tablename__ = "asset_service_records"
    __table_args__ = (Index("ix_asset_service_records_asset_date", "asset_id", "performed_on"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    schedule_id: Mapped[str | None] = mapped_column(ForeignKey("asset_schedules.id"))
    schedule_name: Mapped[str | None] = mapped_column(String(160))  # as it was called when the work was done
    kind: Mapped[str] = mapped_column(String(20), default="service")  # service | repair | inspection | other
    performed_on: Mapped[date] = mapped_column(Date)
    odometer_km: Mapped[float | None] = mapped_column(Float)
    engine_hours: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    technician: Mapped[str | None] = mapped_column(String(160))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    checklist: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    file_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FuelCard(Base):
    """A fuel card, key or fob as the fuel system knows it, and what it is assigned to here."""

    __tablename__ = "fuel_cards"
    __table_args__ = (UniqueConstraint("org_id", "card_number", name="uq_fuel_cards"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    card_number: Mapped[str] = mapped_column(String(80))
    label: Mapped[str | None] = mapped_column(String(200))  # how the fuel system describes it
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    worker_id: Mapped[str | None] = mapped_column(ForeignKey("workers.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_by: Mapped[str | None] = mapped_column(String(36))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FuelBatch(Base):
    """One import of fuel transactions: where it came from and what it changed."""

    __tablename__ = "fuel_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    via: Mapped[str] = mapped_column(String(12))  # csv | api | simulator
    filename: Mapped[str | None] = mapped_column(String(300))
    sha256: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str | None] = mapped_column(String(64))  # how times without an offset were read
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12))  # previewed | committed | failed
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    errors: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FuelWeeklyPrice(Base):
    """The diesel rate for one Monday-to-Sunday week, fixed from the seven daily observations of the
    week before (business rule B04). Written once and never changed or deleted. A rate made from
    simulator data is kept apart (simulated=True) and only ever prices simulator fills."""

    __tablename__ = "fuel_weekly_prices"
    __table_args__ = (UniqueConstraint("org_id", "week_start", "simulated", name="uq_fuel_weekly_prices"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    week_end: Mapped[date] = mapped_column(Date)
    source_week_start: Mapped[date] = mapped_column(Date)
    source_week_end: Mapped[date] = mapped_column(Date)
    daily_cents: Mapped[list[Any]] = mapped_column(JSONB)  # [{"date": "YYYY-MM-DD", "price": "203.4"}] x 7
    rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))  # AUD per litre
    fuel_code: Mapped[str] = mapped_column(String(8), default="DL")
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    timezone: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(12))  # fuelcheck | manual | simulator
    source_url: Mapped[str | None] = mapped_column(String(300))
    source_note: Mapped[str | None] = mapped_column(String(400))
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(36))


class FuelTransaction(Base):
    """One fill. Identity is the provider's own transaction id within the organisation, so importing
    the same fill again (from the API or a spreadsheet export) updates it rather than adding a copy.

    The supplier's own price and total (source_*) are kept as evidence and are separate from the
    weekly reporting rate and cost (report_*), which are written once and never changed.
    """

    __tablename__ = "fuel_transactions"
    __table_args__ = (
        UniqueConstraint("org_id", "source_system", "source_id", name="uq_fuel_transactions_source"),
        Index("ix_fuel_transactions_org_date", "org_id", "local_date"),
        Index("ix_fuel_transactions_asset", "asset_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    source_system: Mapped[str] = mapped_column(String(20))  # smartfill | simulator
    source_id: Mapped[str] = mapped_column(String(120))
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("fuel_batches.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    local_date: Mapped[date] = mapped_column(Date)
    local_time: Mapped[str] = mapped_column(String(5))  # HH:MM in the organisation's timezone
    litres: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    fuel_type: Mapped[str | None] = mapped_column(String(60))
    is_diesel: Mapped[bool] = mapped_column(Boolean, default=True)
    source_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    source_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    odometer_km: Mapped[float | None] = mapped_column(Float)
    engine_hours: Mapped[float | None] = mapped_column(Float)
    card_number: Mapped[str | None] = mapped_column(String(80))
    driver_name: Mapped[str | None] = mapped_column(String(160))
    location: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(200))
    registration: Mapped[str | None] = mapped_column(String(40))
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    worker_id: Mapped[str | None] = mapped_column(ForeignKey("workers.id"))
    attribution: Mapped[str] = mapped_column(String(12), default="unlinked")  # direct | card | manual | unlinked
    attribution_note: Mapped[str | None] = mapped_column(String(400))
    attributed_by: Mapped[str | None] = mapped_column(String(36))
    attributed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    weekly_price_id: Mapped[str | None] = mapped_column(ForeignKey("fuel_weekly_prices.id"))
    report_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    report_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FuelAnomaly(Base):
    """One reason a fill needs a look. A fill can have several reasons; each reason exists once."""

    __tablename__ = "fuel_anomalies"
    __table_args__ = (UniqueConstraint("transaction_id", "rule", name="uq_fuel_anomalies"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("fuel_transactions.id"), index=True)
    rule: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10))  # low | medium | high
    evidence: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | resolved | dismissed
    note: Mapped[str | None] = mapped_column(String(600))
    acted_by: Mapped[str | None] = mapped_column(String(36))
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderCall(Base):
    """A call made to a rate-limited provider. Kept in the database so the limit holds across
    processes and restarts, and is shared by manual and scheduled runs."""

    __tablename__ = "provider_calls"
    __table_args__ = (Index("ix_provider_calls", "org_id", "provider", "called_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"))
    provider: Mapped[str] = mapped_column(String(30))
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------- stage 7: leave, timesheets and the payroll workbench ----------

class LeaveRequest(Base):
    """A request for time off. Decided by someone other than the person asking. Once a pay run
    has used it (pay_run_id set), cancelling it does not quietly undo the pay: it is flagged for
    payroll to correct."""

    __tablename__ = "leave_requests"
    __table_args__ = (Index("ix_leave_requests_worker", "worker_id", "start_date"), Index("ix_leave_requests_org_status", "org_id", "status"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"))
    leave_type: Mapped[str] = mapped_column(String(20))  # annual | personal | unpaid | long_service | other
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    days: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # [{"date": "YYYY-MM-DD", "hours": "7.60"}]
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="requested")  # requested | approved | rejected | cancelled
    source: Mapped[str] = mapped_column(String(12), default="portal")  # portal (the worker) | office (entered for them)
    requested_by: Mapped[str | None] = mapped_column(String(36))
    decided_by: Mapped[str | None] = mapped_column(String(36))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(600))
    cancelled_by: Mapped[str | None] = mapped_column(String(36))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(600))
    certificate_file_id: Mapped[str | None] = mapped_column(String(36))
    pay_run_id: Mapped[str | None] = mapped_column(String(36))
    needs_payroll_correction: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PayrollGrant(Base):
    """Named access to payroll for one administrator, given by the payroll owner. A role or a
    permission override can never grant payroll; only the owner or a row here can."""

    __tablename__ = "payroll_grants"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    level: Mapped[str] = mapped_column(String(8))  # view | edit
    granted_by: Mapped[str | None] = mapped_column(String(36))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TimesheetDay(Base):
    """One worker's hours for one date. There is exactly one record per worker and date.

    draft -> submitted -> approved or rejected; an approved day used by a finalised pay run is
    locked. `revision` goes up on every change so two people editing the same day cannot
    overwrite each other without noticing.
    """

    __tablename__ = "timesheet_days"
    __table_args__ = (UniqueConstraint("org_id", "worker_id", "work_date", name="uq_timesheet_days"), Index("ix_timesheet_days_org_date", "org_id", "work_date"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"))
    work_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(10), default="work")  # work | rdo | no_work
    shifts: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    hours: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # buckets worked out by the engine
    paid_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | submitted | approved | rejected | locked
    source: Mapped[str] = mapped_column(String(10), default="self")  # self | office | prefill
    revision: Mapped[int] = mapped_column(Integer, default=1)
    rule_version: Mapped[str | None] = mapped_column(String(40))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(36))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(String(600))
    pay_run_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
