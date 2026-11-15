"""Document library: folders, files, revisions, archive, search and missing-file repair."""
import os
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Caller, require_permission
from ..files import file_exists, file_response, save_upload
from ..models import Document, DocumentFolder, DocumentRevision, StoredFile, User, utcnow
from ..services import audit, iso

router = APIRouter(prefix="/api/document-library", tags=["document library"])

# Types that may be stored. Anything a browser could execute (HTML, SVG, scripts) is refused.
ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".doc", ".docx", ".xls", ".xlsx", ".csv",
    ".txt", ".ppt", ".pptx", ".rtf", ".odt", ".ods", ".msg", ".eml", ".zip", ".dwg", ".dxf",
}
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
MAX_FILES_PER_UPLOAD = 20
MAX_DEPTH = 8


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color_key: str | None = Field(default=None, max_length=20)
    parent_folder_id: str | None = Field(default=None, max_length=64)


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color_key: str | None = Field(default=None, max_length=20)
    sort_order: int | None = Field(default=None, ge=0, le=1000000)
    parent_folder_id: str | None = Field(default=None, max_length=64)
    shared_reference: bool | None = None


class FilePatch(BaseModel):
    filename: str | None = Field(default=None, min_length=1, max_length=200)
    expiry_date: str | None = Field(default=None, max_length=32)
    clear_expiry: bool = False
    folder_id: str | None = Field(default=None, min_length=1, max_length=64)
    tags: list[str] | None = Field(default=None, max_length=20)


class GoneIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


# ---------- helpers ----------

def _folder(db: Session, caller: Caller, folder_id: str, *, missing: str = "Folder not found", status: int = 404) -> DocumentFolder:
    folder = db.get(DocumentFolder, folder_id)
    if folder is None or folder.org_id != caller.org_id or folder.deleted_at is not None:
        raise HTTPException(status, missing)
    return folder


def _document(db: Session, caller: Caller, file_id: str, *, include_deleted: bool = False) -> Document:
    doc = db.get(Document, file_id)
    if doc is None or doc.org_id != caller.org_id or (doc.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "File not found")
    return doc


def _ancestors(db: Session, folder: DocumentFolder) -> list[DocumentFolder]:
    """Parents from the root down to (not including) this folder."""
    chain: list[DocumentFolder] = []
    current = folder
    while current.parent_folder_id and len(chain) < MAX_DEPTH + 2:
        parent = db.get(DocumentFolder, current.parent_folder_id)
        if parent is None:
            break
        chain.append(parent)
        current = parent
    return list(reversed(chain))


def _descendant_ids(db: Session, org_id: str, folder_id: str) -> set[str]:
    found: set[str] = set()
    frontier = [folder_id]
    while frontier:
        children = db.scalars(
            select(DocumentFolder.id).where(
                DocumentFolder.org_id == org_id, DocumentFolder.parent_folder_id.in_(frontier), DocumentFolder.deleted_at.is_(None)
            )
        ).all()
        frontier = [c for c in children if c not in found]
        found.update(frontier)
    return found


def _name_taken(db: Session, org_id: str, parent_id: str | None, name: str, *, except_id: str | None = None) -> bool:
    stmt = select(DocumentFolder.id).where(
        DocumentFolder.org_id == org_id, DocumentFolder.deleted_at.is_(None), func.lower(DocumentFolder.name) == name.lower(),
        DocumentFolder.parent_folder_id.is_(None) if parent_id is None else DocumentFolder.parent_folder_id == parent_id,
    )
    if except_id:
        stmt = stmt.where(DocumentFolder.id != except_id)
    return db.scalars(stmt).first() is not None


def _folder_out(db: Session, folder: DocumentFolder) -> dict[str, Any]:
    files = db.scalar(
        select(func.count()).select_from(Document).where(
            Document.folder_id == folder.id, Document.deleted_at.is_(None), Document.archived_at.is_(None)
        )
    )
    subfolders = db.scalar(
        select(func.count()).select_from(DocumentFolder).where(
            DocumentFolder.parent_folder_id == folder.id, DocumentFolder.deleted_at.is_(None)
        )
    )
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_folder_id": folder.parent_folder_id,
        "color_key": folder.color_key,
        "sort_order": folder.sort_order,
        "is_system": folder.is_system,
        "shared_reference": folder.shared_reference,
        "file_count": int(files or 0),
        "subfolder_count": int(subfolders or 0),
        "path": [{"id": f.id, "name": f.name} for f in _ancestors(db, folder)],
        "created_at": iso(folder.created_at),
        "updated_at": iso(folder.updated_at),
    }


def _doc_out(db: Session, doc: Document, *, names: dict[str, str] | None = None) -> dict[str, Any]:
    stored = db.get(StoredFile, doc.stored_file_id)
    missing = stored is None or not file_exists(stored)
    uploader = names.get(doc.uploaded_by or "") if names is not None else None
    if names is None and doc.uploaded_by:
        user = db.get(User, doc.uploaded_by)
        uploader = user.name if user else None
    today = date.today()
    return {
        "id": doc.id,
        "folder_id": doc.folder_id,
        "filename": doc.filename,
        "mime_type": stored.mime_type if stored else None,
        "size_bytes": stored.size_bytes if stored else None,
        "sha256": stored.sha256 if stored else None,
        "revision": doc.revision,
        "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
        "expired": bool(doc.expiry_date and doc.expiry_date < today),
        "tags": list(doc.tags or []),
        "uploaded_by": doc.uploaded_by,
        "uploaded_by_name": uploader,
        "created_at": iso(doc.created_at),
        "updated_at": iso(doc.updated_at),
        "archived_at": iso(doc.archived_at),
        "deleted_at": iso(doc.deleted_at),
        "missing_binary": missing and doc.gone_at is None,
        "gone_at": iso(doc.gone_at),
        "gone_reason": doc.gone_reason,
        "previewable": bool(stored and stored.mime_type in INLINE_TYPES and not missing),
    }


def _extension(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in tags:
        tag = " ".join(tag.split())[:40]
        if tag and tag.lower() not in {t.lower() for t in cleaned}:
            cleaned.append(tag)
    return cleaned


# ---------- folders ----------

@router.get("/folders")
def list_root_folders(
    caller: Caller = Depends(require_permission("documents", "view")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(DocumentFolder)
        .where(DocumentFolder.org_id == caller.org_id, DocumentFolder.parent_folder_id.is_(None), DocumentFolder.deleted_at.is_(None))
        .order_by(DocumentFolder.sort_order, func.lower(DocumentFolder.name), DocumentFolder.id)
    ).all()
    return [_folder_out(db, f) for f in rows]


@router.get("/folders/all")
def list_all_folders(
    caller: Caller = Depends(require_permission("documents", "view")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Flat list of every folder, with its path, for move pickers."""
    rows = db.scalars(
        select(DocumentFolder)
        .where(DocumentFolder.org_id == caller.org_id, DocumentFolder.deleted_at.is_(None))
        .order_by(func.lower(DocumentFolder.name), DocumentFolder.id)
    ).all()
    out = []
    for folder in rows:
        path = [f.name for f in _ancestors(db, folder)] + [folder.name]
        out.append({"id": folder.id, "name": folder.name, "parent_folder_id": folder.parent_folder_id,
                    "is_system": folder.is_system, "full_path": " / ".join(path)})
    return sorted(out, key=lambda f: f["full_path"].lower())


@router.post("/folders", status_code=201)
def create_folder(
    body: FolderIn,
    request: Request,
    caller: Caller = Depends(require_permission("documents", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(422, "Folder name cannot be empty")
    parent = None
    if body.parent_folder_id:
        parent = _folder(db, caller, body.parent_folder_id, missing="Parent folder not found", status=400)
        if len(_ancestors(db, parent)) + 1 >= MAX_DEPTH:
            raise HTTPException(400, f"Folders can be nested at most {MAX_DEPTH} levels deep")
    if _name_taken(db, caller.org_id, parent.id if parent else None, name):
        raise HTTPException(409, "A folder with this name already exists here")
    folder = DocumentFolder(org_id=caller.org_id, parent_folder_id=parent.id if parent else None, name=name,
                            color_key=body.color_key, created_by=caller.user.id)
    db.add(folder)
    db.flush()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="document_folder.created",
          target_type="document_folder", target_id=folder.id, detail={"name": name, "parent": folder.parent_folder_id})
    db.commit()
    return _folder_out(db, folder)


@router.get("/folders/{folder_id}")
def get_folder(
    folder_id: str, caller: Caller = Depends(require_permission("documents", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return _folder_out(db, _folder(db, caller, folder_id))


@router.patch("/folders/{folder_id}")
def patch_folder(
    folder_id: str,
    body: FolderPatch,
    request: Request,
    caller: Caller = Depends(require_permission("documents", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    folder = _folder(db, caller, folder_id)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "No editable fields supplied")
    changes: dict[str, Any] = {}
    new_parent_id = folder.parent_folder_id
    if "parent_folder_id" in patch and patch["parent_folder_id"] != folder.parent_folder_id:
        if folder.is_system:
            raise HTTPException(400, "Cannot move the system folder")
        new_parent_id = patch["parent_folder_id"]
        if new_parent_id is not None:
            if new_parent_id == folder.id:
                raise HTTPException(400, "A folder cannot be its own parent")
            parent = _folder(db, caller, new_parent_id, missing="Parent folder not found", status=400)
            if parent.id in _descendant_ids(db, caller.org_id, folder.id):
                raise HTTPException(400, "Reparent would create a cycle (target parent is a descendant)")
        changes["parent_folder_id"] = {"from": folder.parent_folder_id, "to": new_parent_id}
    new_name = folder.name
    if patch.get("name") is not None:
        new_name = " ".join(patch["name"].split())
        if new_name != folder.name:
            if folder.is_system:
                raise HTTPException(400, "Cannot rename the system folder")
            changes["name"] = {"from": folder.name, "to": new_name}
    if ("name" in changes or "parent_folder_id" in changes) and _name_taken(db, caller.org_id, new_parent_id, new_name, except_id=folder.id):
        raise HTTPException(409, "A folder with this name already exists here")
    folder.name, folder.parent_folder_id = new_name, new_parent_id
    for field in ("color_key", "sort_order", "shared_reference"):
        if field in patch and patch[field] is not None and patch[field] != getattr(folder, field):
            changes[field] = {"from": getattr(folder, field), "to": patch[field]}
            setattr(folder, field, patch[field])
    if changes:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="document_folder.updated",
              target_type="document_folder", target_id=folder.id, detail=changes)
        db.commit()
    return _folder_out(db, folder)


@router.delete("/folders/{folder_id}")
def delete_folder(
    folder_id: str,
    request: Request,
    caller: Caller = Depends(require_permission("documents", "delete")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    """Delete an empty folder. A folder that still holds files or subfolders is refused, so nothing vanishes by accident."""
    folder = _folder(db, caller, folder_id)
    if folder.is_system:
        raise HTTPException(400, "Cannot delete the system folder")
    out = _folder_out(db, folder)
    archived = db.scalar(
        select(func.count()).select_from(Document).where(Document.folder_id == folder.id, Document.deleted_at.is_(None))
    ) or 0
    if out["subfolder_count"] or archived:
        raise HTTPException(409, "This folder is not empty. Move or delete its files and subfolders first (including archived files).")
    folder.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="document_folder.deleted",
          target_type="document_folder", target_id=folder.id, detail={"name": folder.name})
    db.commit()
    return {"ok": True}


@router.get("/folders/{folder_id}/subfolders")
def list_subfolders(
    folder_id: str, caller: Caller = Depends(require_permission("documents", "view")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    folder = _folder(db, caller, folder_id)
    rows = db.scalars(
        select(DocumentFolder)
        .where(DocumentFolder.parent_folder_id == folder.id, DocumentFolder.deleted_at.is_(None))
        .order_by(DocumentFolder.sort_order, func.lower(DocumentFolder.name), DocumentFolder.id)
    ).all()
    return [_folder_out(db, f) for f in rows]


# ---------- files ----------

def _names(db: Session, docs: list[Document]) -> dict[str, str]:
    ids = {d.uploaded_by for d in docs if d.uploaded_by}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


@router.get("/folders/{folder_id}/files")
def list_files(
    folder_id: str,
    include_archived: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_permission("documents", "view")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    folder = _folder(db, caller, folder_id)
    stmt = select(Document).where(Document.folder_id == folder.id, Document.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(Document.archived_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    docs = list(db.scalars(stmt.order_by(func.lower(Document.filename), Document.id).limit(limit).offset(offset)).all())
    names = _names(db, docs)
    return {"items": [_doc_out(db, d, names=names) for d in docs], "total": int(total), "limit": limit, "offset": offset}


@router.post("/folders/{folder_id}/files")
def upload_files(
    folder_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    caller: Caller = Depends(require_permission("documents", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Store one or more files. Each file is accepted or rejected on its own, with a reason."""
    folder = _folder(db, caller, folder_id)
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(400, f"Upload at most {MAX_FILES_PER_UPLOAD} files at a time")
    saved: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for upload in files:
        name = os.path.basename((upload.filename or "").replace("\\", "/")).strip()[:200]
        if not name or _extension(name) not in ALLOWED_EXTENSIONS:
            rejected.append({"filename": name or "(no name)", "reason": "This file type is not accepted"})
            continue
        try:
            with db.begin_nested():
                stored = save_upload(db, org_id=caller.org_id, upload=upload, purpose="document", created_by=caller.user.id)
                if stored.mime_type == "application/octet-stream" and _extension(name) in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
                    raise HTTPException(415, "The file's content does not match its type")
                duplicate = db.scalars(
                    select(Document.id).join(StoredFile, StoredFile.id == Document.stored_file_id).where(
                        Document.folder_id == folder.id, Document.deleted_at.is_(None),
                        StoredFile.sha256 == stored.sha256, func.lower(Document.filename) == name.lower(),
                    )
                ).first()
                if duplicate:
                    raise HTTPException(409, "The same file is already in this folder")
                doc = Document(org_id=caller.org_id, folder_id=folder.id, filename=name, stored_file_id=stored.id,
                               uploaded_by=caller.user.id)
                db.add(doc)
                db.flush()
                db.add(DocumentRevision(org_id=caller.org_id, document_id=doc.id, revision=1, stored_file_id=stored.id,
                                        filename=name, created_by=caller.user.id, note="Uploaded"))
                audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="document.uploaded",
                      target_type="document", target_id=doc.id, detail={"filename": name, "sha256": stored.sha256, "folder_id": folder.id})
            saved.append(_doc_out(db, doc))
        except HTTPException as exc:
            rejected.append({"filename": name, "reason": str(exc.detail)})
    db.commit()
    return {"saved": saved, "rejected": rejected}


@router.get("/files/{file_id}")
def get_file(
    file_id: str, caller: Caller = Depends(require_permission("documents", "view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    doc = _document(db, caller, file_id)
    revisions = db.execute(
        select(DocumentRevision, StoredFile)
        .join(StoredFile, StoredFile.id == DocumentRevision.stored_file_id)
        .where(DocumentRevision.document_id == doc.id)
        .order_by(DocumentRevision.revision.desc())
    ).all()
    return {
        **_doc_out(db, doc),
        "revisions": [
            {"revision": r.revision, "filename": r.filename, "note": r.note, "size_bytes": s.size_bytes, "sha256": s.sha256,
             "created_by": r.created_by, "created_at": iso(r.created_at), "missing_binary": not file_exists(s)}
            for r, s in revisions
        ],
    }


@router.patch("/files/{file_id}")
def patch_file(
    file_id: str,
    body: FilePatch,
    request: Request,
    caller: Caller = Depends(require_permission("documents", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    doc = _document(db, caller, file_id)
    patch = body.model_dump(exclude_unset=True)
    if not patch or patch == {"clear_expiry": False}:
        raise HTTPException(400, "Nothing to update")
    changes: dict[str, Any] = {}
    if patch.get("filename") is not None:
        name = os.path.basename(patch["filename"].replace("\\", "/")).strip()
        if not name:
            raise HTTPException(400, "Filename cannot be empty")
        if _extension(name) != _extension(doc.filename):
            raise HTTPException(400, "Cannot change file extension")
        if name != doc.filename:
            changes["filename"] = {"from": doc.filename, "to": name}
            doc.filename = name
    if patch.get("clear_expiry"):
        if doc.expiry_date is not None:
            changes["expiry_date"] = {"from": doc.expiry_date.isoformat(), "to": None}
            doc.expiry_date = None
    elif patch.get("expiry_date") is not None:
        try:
            expiry = date.fromisoformat(patch["expiry_date"])
        except ValueError as exc:
            raise HTTPException(400, "expiry_date must be ISO-8601 (YYYY-MM-DD)") from exc
        if expiry != doc.expiry_date:
            changes["expiry_date"] = {"from": doc.expiry_date.isoformat() if doc.expiry_date else None, "to": expiry.isoformat()}
            doc.expiry_date = expiry
    if patch.get("folder_id") is not None and patch["folder_id"] != doc.folder_id:
        target = _folder(db, caller, patch["folder_id"], missing="Target folder not found")
        changes["folder_id"] = {"from": doc.folder_id, "to": target.id}
        doc.folder_id = target.id
    if patch.get("tags") is not None:
        tags = _clean_tags(patch["tags"])
        if tags != list(doc.tags or []):
            changes["tags"] = {"from": list(doc.tags or []), "to": tags}
            doc.tags = tags
    if changes:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="document.updated",
              target_type="document", target_id=doc.id, detail=changes)
        db.commit()
    return _doc_out(db, doc)


def _set_archived(db: Session, request: Request, caller: Caller, file_id: str, archived: bool) -> dict[str, Any]:
    doc = _document(db, caller, file_id)
    if (doc.archived_at is not None) != archived:
        doc.archived_at = utcnow() if archived else None
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id,
              action="document.archived" if archived else "document.restored", target_type="document", target_id=doc.id)
        db.commit()
    return _doc_out(db, doc)


@router.post("/files/{file_id}/archive")
def archive_file(
    file_id: str, request: Request, caller: Caller = Depends(require_permission("documents", "edit")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Archived files are hidden by default but stay readable and restorable."""
    return _set_archived(db, request, caller, file_id, True)


@router.post("/files/{file_id}/restore")
def restore_file(
    file_id: str, request: Request, caller: Caller = Depends(require_permission("documents", "edit")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return _set_archived(db, request, caller, file_id, False)


@router.delete("/files/{file_id}", status_code=204)
def delete_file(
    file_id: str, request: Request, caller: Caller = Depends(require_permission("documents", "delete")), db: Session = Depends(get_db)
) -> None:
    doc = _document(db, caller, file_id)
    doc.deleted_at = utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="document.deleted",
          target_type="document", target_id=doc.id, detail={"filename": doc.filename})
    db.commit()


@router.get("/files/{file_id}/download")
def download_file(
    file_id: str,
    download: int = Query(0, ge=0, le=1),
    revision: int | None = Query(None, ge=1),
    caller: Caller = Depends(require_permission("documents", "view")),
    db: Session = Depends(get_db),
):
    """Stream the current bytes, or an earlier revision. Only PDFs and images may open inline."""
    doc = _document(db, caller, file_id)
    stored_id, name = doc.stored_file_id, doc.filename
    if revision is not None and revision != doc.revision:
        row = db.scalars(
            select(DocumentRevision).where(DocumentRevision.document_id == doc.id, DocumentRevision.revision == revision)
        ).first()
        if row is None:
            raise HTTPException(404, "Revision not found")
        stored_id, name = row.stored_file_id, row.filename
    stored = db.get(StoredFile, stored_id)
    if stored is None:
        raise HTTPException(410, "The stored file is missing. It needs to be uploaded again.")
    inline = download == 0 and stored.mime_type in INLINE_TYPES
    return file_response(stored, download_name=name, inline=inline)


@router.post("/files/{file_id}/replace-binary")
def replace_binary(
    file_id: str,
    request: Request,
    file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("documents", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Upload new bytes as the next revision (also repairs a missing file). Earlier revisions are kept."""
    doc = _document(db, caller, file_id)
    name = os.path.basename((file.filename or "").replace("\\", "/")).strip()
    if _extension(name) != _extension(doc.filename):
        raise HTTPException(400, "The replacement must be the same type of file")
    stored = save_upload(db, org_id=caller.org_id, upload=file, purpose="document", created_by=caller.user.id)
    doc.revision += 1
    doc.stored_file_id = stored.id
    doc.gone_at, doc.gone_reason = None, None
    db.add(DocumentRevision(org_id=caller.org_id, document_id=doc.id, revision=doc.revision, stored_file_id=stored.id,
                            filename=doc.filename, created_by=caller.user.id, note="Replaced"))
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="document.binary_replaced",
          target_type="document", target_id=doc.id, detail={"revision": doc.revision, "sha256": stored.sha256})
    db.commit()
    return _doc_out(db, doc)


@router.post("/files/{file_id}/mark-gone")
def mark_gone(
    file_id: str,
    body: GoneIn,
    request: Request,
    caller: Caller = Depends(require_permission("documents", "edit")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Record that a file's bytes cannot be recovered. Only allowed when they really are missing."""
    doc = _document(db, caller, file_id)
    stored = db.get(StoredFile, doc.stored_file_id)
    if stored is not None and file_exists(stored):
        raise HTTPException(400, "This file's content is present, so it cannot be marked as lost")
    doc.gone_at, doc.gone_reason = utcnow(), body.reason.strip()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="document.marked_gone",
          target_type="document", target_id=doc.id, detail={"reason": doc.gone_reason})
    db.commit()
    return _doc_out(db, doc)


# ---------- counts and search ----------

def _missing(db: Session, org_id: str) -> list[Document]:
    docs = db.scalars(
        select(Document).where(Document.org_id == org_id, Document.deleted_at.is_(None), Document.gone_at.is_(None))
    ).all()
    out = []
    for doc in docs:
        stored = db.get(StoredFile, doc.stored_file_id)
        if stored is None or not file_exists(stored):
            out.append(doc)
    return out


@router.get("/counts")
def counts(caller: Caller = Depends(require_permission("documents", "view")), db: Session = Depends(get_db)) -> dict[str, int]:
    def count(model, *conditions) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(model.org_id == caller.org_id, *conditions)) or 0)

    return {
        "folders_active": count(DocumentFolder, DocumentFolder.deleted_at.is_(None)),
        "folders_deleted": count(DocumentFolder, DocumentFolder.deleted_at.is_not(None)),
        "files_active": count(Document, Document.deleted_at.is_(None), Document.archived_at.is_(None)),
        "files_archived": count(Document, Document.deleted_at.is_(None), Document.archived_at.is_not(None)),
        "files_deleted": count(Document, Document.deleted_at.is_not(None)),
        "files_missing_binary": len(_missing(db, caller.org_id)),
    }


@router.get("/counts/missing-binary")
def missing_binary(
    caller: Caller = Depends(require_permission("documents", "edit")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    docs = _missing(db, caller.org_id)
    return {"count": len(docs), "files": [_doc_out(db, d) for d in docs]}


@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=120),
    folder_id: str | None = None,
    recursive: bool = True,
    include_archived: bool = False,
    limit: int = Query(50, ge=1, le=200),
    caller: Caller = Depends(require_permission("documents", "view")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Find files by name or tag, within one folder (and its subfolders) or the whole library."""
    like = f"%{q.strip().lower()}%"
    stmt = select(Document).where(
        Document.org_id == caller.org_id, Document.deleted_at.is_(None),
        or_(func.lower(Document.filename).like(like), func.lower(cast(Document.tags, Text)).like(like)),
    )
    if not include_archived:
        stmt = stmt.where(Document.archived_at.is_(None))
    if folder_id:
        folder = _folder(db, caller, folder_id)
        ids = {folder.id} | (_descendant_ids(db, caller.org_id, folder.id) if recursive else set())
        stmt = stmt.where(Document.folder_id.in_(ids))
    docs = list(db.scalars(stmt.order_by(func.lower(Document.filename), Document.id).limit(limit)).all())
    names = _names(db, docs)
    results = []
    for doc in docs:
        folder = db.get(DocumentFolder, doc.folder_id)
        path = [f.name for f in _ancestors(db, folder)] + [folder.name] if folder else []
        results.append({**_doc_out(db, doc, names=names), "folder_path": " / ".join(path)})
    return {"query": q, "count": len(results), "results": results, "folder_id": folder_id, "recursive": bool(recursive)}
