"""File storage. Bytes are written to FILES_DIR, addressed by organisation and SHA-256.

Rules (spec section 8):
- an upload only succeeds after the bytes are durably written and the metadata row exists
- hash, size and MIME type are recorded for every file
- missing bytes are reported as missing, never served as an empty file
"""
import hashlib
import os
import re
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .config import get_settings
from .models import StoredFile

CHUNK = 1024 * 1024

# Leading bytes for the image types accepted as logos and photos.
_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "application/pdf": (b"%PDF-",),
}


def files_root() -> Path:
    root = Path(get_settings().files_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path_for(org_id: str, sha256: str) -> Path:
    return files_root() / org_id / sha256[:2] / sha256


def sniff_mime(head: bytes, declared: str | None) -> str:
    """Trust the content over the browser's label for the types we can recognise."""
    for mime, signatures in _MAGIC.items():
        if any(head.startswith(sig) for sig in signatures):
            if mime == "image/webp" and head[8:12] != b"WEBP":
                continue
            return mime
    declared = (declared or "").split(";")[0].strip().lower()
    if declared in _MAGIC:
        # Claimed to be a recognisable type but the bytes disagree.
        return "application/octet-stream"
    return declared or "application/octet-stream"


def safe_name(name: str | None) -> str:
    name = os.path.basename((name or "file").replace("\\", "/")).strip() or "file"
    name = re.sub(r"[\x00-\x1f\x7f\"]", "", name)
    return name[:300] or "file"


def save_upload(
    db: Session,
    *,
    org_id: str,
    upload: UploadFile,
    purpose: str,
    created_by: str | None,
    max_bytes: int | None = None,
    allowed_mimes: set[str] | None = None,
) -> StoredFile:
    limit = max_bytes or get_settings().max_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    size = 0
    head = b""
    root = files_root()
    fd, tmp_name = tempfile.mkstemp(dir=root, prefix=".upload-")
    try:
        with os.fdopen(fd, "wb") as tmp:
            while chunk := upload.file.read(CHUNK):
                if not head:
                    head = chunk[:16]
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File is too large. The limit is {limit // (1024 * 1024) or 1} MB.")
                digest.update(chunk)
                tmp.write(chunk)
            tmp.flush()
            os.fsync(tmp.fileno())
        if size == 0:
            raise HTTPException(400, "The file is empty")
        mime = sniff_mime(head, upload.content_type)
        if allowed_mimes is not None and mime not in allowed_mimes:
            raise HTTPException(415, "This file type is not accepted here")
        sha = digest.hexdigest()
        final = _path_for(org_id, sha)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_name, final)  # atomic; identical content simply lands on the same path
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    row = StoredFile(org_id=org_id, sha256=sha, size_bytes=size, mime_type=mime,
                     original_name=safe_name(upload.filename), purpose=purpose, created_by=created_by)
    db.add(row)
    db.flush()
    return row


def save_bytes(db: Session, *, org_id: str, data: bytes, name: str, mime_type: str, purpose: str, created_by: str | None) -> StoredFile:
    """Store bytes the portal itself produced (an export, a generated archive). Same durability rules as an upload."""
    if not data:
        raise HTTPException(500, "Nothing was produced to store")
    sha = hashlib.sha256(data).hexdigest()
    root = files_root()
    fd, tmp_name = tempfile.mkstemp(dir=root, prefix=".made-")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        final = _path_for(org_id, sha)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_name, final)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    row = StoredFile(org_id=org_id, sha256=sha, size_bytes=len(data), mime_type=mime_type, original_name=safe_name(name), purpose=purpose, created_by=created_by)
    db.add(row)
    db.flush()
    return row


def file_exists(row: StoredFile) -> bool:
    path = _path_for(row.org_id, row.sha256)
    return path.is_file() and path.stat().st_size == row.size_bytes


def file_response(row: StoredFile, *, download_name: str | None = None, inline: bool = False, cache: bool = False) -> FileResponse:
    """Stream stored bytes. Callers must have authorised the owning record first."""
    if not file_exists(row):
        raise HTTPException(410, "The stored file is missing. It needs to be uploaded again.")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=86400" if cache else "no-store"}
    return FileResponse(
        _path_for(row.org_id, row.sha256),
        media_type=row.mime_type,
        filename=safe_name(download_name or row.original_name),
        content_disposition_type="inline" if inline else "attachment",
        headers=headers,
    )


def read_bytes(row: StoredFile | None, *, max_bytes: int = 15 * 1024 * 1024) -> bytes | None:
    """Stored bytes for embedding (for example in a PDF). None when missing or too large to embed."""
    if row is None or not file_exists(row) or row.size_bytes > max_bytes:
        return None
    return _path_for(row.org_id, row.sha256).read_bytes()


def file_out(row: StoredFile) -> dict:
    return {
        "id": row.id,
        "name": row.original_name,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "missing": not file_exists(row),
    }
