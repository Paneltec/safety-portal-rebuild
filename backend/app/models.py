"""Stage 1 data model: tenants, identity, roles, permissions, sessions, audit.

Conventions (spec section 3):
- IDs are opaque strings on the wire.
- Every tenant-owned row carries org_id; queries always filter on it.
- Instants are timezone-aware UTC.
"""
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
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
