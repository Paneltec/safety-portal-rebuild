"""Backups and the system check (stage 9c).

These act on the whole installation, not one organisation, so they are limited to the installation
owner: the account holder of the first organisation set up on this server. An administrator of any
other organisation on the same server is refused. The portal never serves a backup archive for
download: archives stay in the server's backup folder, where the person who runs the server
collects them.
"""
import logging
import os
import platform
import shutil
from datetime import timedelta
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import backup
from ..config import get_settings
from ..crypto import key_status
from ..db import _session_factory, get_db, get_engine
from ..deps import Caller, get_caller, require_admin
from ..files import file_exists, files_root
from ..models import BackupSnapshot, IntegrationConfig, Organisation, StoredFile, User, new_id, utcnow
from ..services import audit, iso

log = logging.getLogger("portal.system")
router = APIRouter(tags=["system"])

STALE_AFTER = timedelta(hours=2)  # a "creating" row older than this was interrupted
FRESH, AGEING = timedelta(hours=26), timedelta(days=7)


def installation_owner_id(db: Session) -> str | None:
    first = db.scalars(select(Organisation).order_by(Organisation.created_at, Organisation.id).limit(1)).first()
    return first.owner_user_id if first is not None else None


def require_installation_owner(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)) -> Caller:
    if caller.user.id != installation_owner_id(db):
        raise HTTPException(403, "Only the account holder of this installation can do this")
    return caller


OWNER = Depends(require_installation_owner)


def _snap_out(row: BackupSnapshot, names: dict[str, str]) -> dict[str, Any]:
    present = bool(row.filename) and (backup.backup_root() / row.filename).is_file() if row.status == "created" else False
    return {"id": row.id, "status": row.status, "filename": row.filename, "size_bytes": int(row.size_bytes) if row.size_bytes is not None else None, "sha256": row.sha256,
            "summary": row.summary or {}, "error": row.error, "in_folder": present,
            "verify": {"state": row.verify_state, "at": iso(row.verify_at), **(row.verify_result or {})},
            "rehearsal": {"state": row.rehearsal_state, "at": iso(row.rehearsal_at), **(row.rehearsal_result or {})},
            "created_by": names.get(row.created_by or "", "A scheduled command" if row.created_by is None else ""), "created_at": iso(row.created_at), "finished_at": iso(row.finished_at)}


def _names(db: Session, rows: list[BackupSnapshot]) -> dict[str, str]:
    ids = {r.created_by for r in rows if r.created_by}
    return {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}


def _expire_stale(db: Session) -> None:
    for row in db.scalars(select(BackupSnapshot).where(BackupSnapshot.status == "creating", BackupSnapshot.created_at < utcnow() - STALE_AFTER)).all():
        row.status, row.error, row.finished_at = "failed", "Interrupted: the server stopped or restarted before the archive was finished.", utcnow()
    db.commit()


def _snapshot(db: Session, snapshot_id: str) -> BackupSnapshot:
    row = db.get(BackupSnapshot, snapshot_id)
    if row is None or row.status == "removed":
        raise HTTPException(404, "Backup not found")
    return row


def _run_create(snapshot_id: str) -> None:
    """Runs after the answer has been sent. Whatever happens, the row ends as created or failed."""
    with _session_factory()() as db:
        row = db.get(BackupSnapshot, snapshot_id)
        if row is None:
            return
        try:
            made = backup.create_archive(get_engine(), files_root(), backup.backup_root(), row.id, row.created_at)
            row.status, row.filename, row.size_bytes, row.sha256, row.summary = "created", made["filename"], made["size_bytes"], made["sha256"], made["summary"]
        except Exception as exc:  # noqa: BLE001 - recorded for the operator; never left as "creating"
            log.exception("backup %s failed", snapshot_id)
            row.status, row.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        row.finished_at = utcnow()
        db.commit()


@router.get("/api/backup/summary")
def backup_summary(caller: Caller = OWNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    _expire_stale(db)
    settings = get_settings()
    newest = db.scalars(select(BackupSnapshot).where(BackupSnapshot.status == "created").order_by(BackupSnapshot.created_at.desc()).limit(1)).first()
    verified = db.scalars(select(BackupSnapshot).where(BackupSnapshot.status == "created", BackupSnapshot.verify_state == "verified").order_by(BackupSnapshot.created_at.desc()).limit(1)).first()
    age = utcnow() - newest.created_at if newest else None
    state = "none" if newest is None else "fresh" if age <= FRESH else "ageing" if age <= AGEING else "old"
    root = backup.backup_root()
    usage = shutil.disk_usage(root)
    return {
        "state": state, "newest": iso(newest.created_at) if newest else None, "newest_verified": iso(verified.created_at) if verified else None,
        "newest_is_verified": bool(newest and newest.verify_state == "verified"),
        "folder": str(root), "folder_free_bytes": usage.free, "keep": settings.backup_keep,
        "rehearsal_available": bool(settings.rehearsal_database_url.strip()),
        "schedule": "The portal does not make backups by itself. Run `python -m app.backup snapshot` on a timer (see docs/BACKUP_RESTORE.md), or press the button.",
        "key_note": backup.KEY_NOTE,
        "not_built": "Sending archives to a second place (another NAS or cloud storage) and a NAS agent are not built. Copy the backup folder elsewhere with the NAS's own tools.",
    }


@router.get("/api/backup/snapshots")
def list_snapshots(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), caller: Caller = OWNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    _expire_stale(db)
    stmt = select(BackupSnapshot).where(BackupSnapshot.status != "removed")
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(BackupSnapshot.created_at.desc(), BackupSnapshot.id).limit(limit).offset(offset)).all())
    names = _names(db, rows)
    return {"items": [_snap_out(r, names) for r in rows], "total": int(total), "limit": limit, "offset": offset}


@router.post("/api/backup/snapshots", status_code=202)
def create_snapshot(request: Request, tasks: BackgroundTasks, caller: Caller = OWNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Start a backup. The answer comes back at once; the archive appears in the list as Created when it is finished."""
    _expire_stale(db)
    if db.scalar(select(func.count()).select_from(BackupSnapshot).where(BackupSnapshot.status == "creating")):
        raise HTTPException(409, "A backup is already being made. Wait for it to finish.")
    root = backup.backup_root()
    if not os.access(root, os.W_OK):
        raise HTTPException(503, "The backup folder cannot be written to. Check that it is connected and that the portal may write there.")
    need = sum(int(n or 0) for (n,) in db.execute(select(func.sum(StoredFile.size_bytes))).all()) + 50 * 1024 * 1024
    if shutil.disk_usage(root).free < need:
        raise HTTPException(507, "There is not enough free space in the backup folder for another archive. Free some space or tidy up old archives first.")
    row = BackupSnapshot(id=new_id(), status="creating", created_by=caller.user.id, created_at=utcnow())
    db.add(row)
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="backup.started", target_type="backup", target_id=row.id)
    db.commit()
    tasks.add_task(_run_create, row.id)
    return _snap_out(row, {caller.user.id: caller.user.name})


@router.post("/api/backup/snapshots/{snapshot_id}/verify")
def verify_snapshot(snapshot_id: str, request: Request, caller: Caller = OWNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Re-read the archive and check it against what was recorded. Changes nothing but the result shown here."""
    row = _snapshot(db, snapshot_id)
    if row.status != "created":
        raise HTTPException(409, "Only a finished backup can be verified.")
    result = backup.verify_archive(backup.backup_root() / (row.filename or "missing"), expect_id=row.id, expect_sha=row.sha256,
                                   expect_size=int(row.size_bytes) if row.size_bytes is not None else None)
    row.verify_state, row.verify_at = result.pop("state"), utcnow()
    row.verify_result = result
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="backup.verified", target_type="backup", target_id=row.id, detail={"result": row.verify_state})
    db.commit()
    return _snap_out(row, _names(db, [row]))


@router.post("/api/backup/snapshots/{snapshot_id}/rehearse")
def rehearse_snapshot(snapshot_id: str, request: Request, caller: Caller = OWNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Restore the archive into the scratch database and a temporary folder, compare, and clear both. The portal's own data is never touched."""
    row = _snapshot(db, snapshot_id)
    if row.status != "created":
        raise HTTPException(409, "Only a finished backup can be rehearsed.")
    if row.verify_state != "verified":
        raise HTTPException(409, "Verify this backup first. A rehearsal only runs on an archive that has passed its checks.")
    try:
        result = backup.rehearse(backup.backup_root() / (row.filename or "missing"))
        row.rehearsal_state = result.pop("state")
    except backup.BackupError as exc:
        row.rehearsal_state, result = "unavailable", {"reason": str(exc), "checks": []}
    except Exception as exc:  # noqa: BLE001 - a failed rehearsal is a result to show, not a crash
        log.exception("rehearsal of %s failed", snapshot_id)
        row.rehearsal_state, result = "failed", {"reason": f"The restore stopped part way: {type(exc).__name__}. The scratch database was cleared.", "checks": []}
    row.rehearsal_at, row.rehearsal_result = utcnow(), result
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="backup.rehearsed", target_type="backup", target_id=row.id, detail={"result": row.rehearsal_state})
    db.commit()
    return _snap_out(row, _names(db, [row]))


def _tidy_plan(db: Session) -> tuple[list[BackupSnapshot], list[BackupSnapshot]]:
    """Keep the newest N finished archives, and always the newest verified one. Failed attempts hold no archive and are left alone."""
    rows = list(db.scalars(select(BackupSnapshot).where(BackupSnapshot.status == "created").order_by(BackupSnapshot.created_at.desc(), BackupSnapshot.id)).all())
    keep_n = max(1, get_settings().backup_keep)
    keep = rows[:keep_n]
    best = next((r for r in rows if r.verify_state == "verified"), None)
    if best is not None and best not in keep:
        keep.append(best)
    return keep, [r for r in rows if r not in keep]


@router.get("/api/backup/retention/preview")
def tidy_preview(caller: Caller = OWNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    keep, drop = _tidy_plan(db)
    return {"keep": [r.id for r in keep], "remove": [{"id": r.id, "filename": r.filename, "created_at": iso(r.created_at), "size_bytes": int(r.size_bytes or 0)} for r in drop],
            "frees_bytes": sum(int(r.size_bytes or 0) for r in drop), "rule": f"Keeps the newest {max(1, get_settings().backup_keep)} archives and the newest verified one. Nothing is removed until you ask."}


@router.post("/api/backup/retention/run")
def tidy_run(request: Request, caller: Caller = OWNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    _, drop = _tidy_plan(db)
    root, freed = backup.backup_root(), 0
    for row in drop:
        path = root / (row.filename or "missing")
        if path.is_file() and path.parent == root:
            freed += path.stat().st_size
            path.unlink()
        row.status, row.removed_at = "removed", utcnow()
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="backup.tidied", target_type="backup", target_id=None, detail={"removed": [r.filename for r in drop]})
    db.commit()
    return {"removed": len(drop), "freed_bytes": freed}


@router.post("/api/backup/restore")
def restore_refused(request: Request, caller: Caller = OWNER, db: Session = Depends(get_db)) -> None:
    """There is no restore over the running portal. Recorded each time it is asked for."""
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="backup.restore_refused", target_type="backup", target_id=None)
    db.commit()
    raise HTTPException(409, "Restoring over the running portal is not offered, because it would overwrite current records. Rehearse a restore here, or restore into a new empty "
                             "database with the command in docs/BACKUP_RESTORE.md.")


# ---------- system check ----------

def _check(label: str, fn: Callable[[], tuple[str, str]]) -> dict[str, Any]:
    try:
        state, detail = fn()
    except Exception as exc:  # noqa: BLE001 - one check failing must not hide the others
        state, detail = "unknown", f"This could not be checked ({type(exc).__name__})."
    return {"label": label, "state": state, "detail": detail}


@router.get("/api/system/status")
def system_status(caller: Caller = OWNER, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Each check is worked out on its own: one that fails or cannot be checked does not blank the rest. No secrets are shown."""
    settings = get_settings()

    def database() -> tuple[str, str]:
        db.execute(text("SELECT 1"))
        return "ok", "The database answers."

    def migrations() -> tuple[str, str]:
        current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        try:
            from pathlib import Path

            from alembic.config import Config
            from alembic.script import ScriptDirectory
            base = Path(__file__).resolve().parents[2]
            cfg = Config(str(base / "alembic.ini"))
            cfg.set_main_option("script_location", str(base / "migrations"))
            head = ScriptDirectory.from_config(cfg).get_current_head()
        except Exception:  # noqa: BLE001
            return "unknown", f"The database is at version {current}. The expected version could not be read on this server."
        return ("ok", f"The database is at version {current}, which is what this version of the portal expects.") if current == head else \
            ("bad", f"The database is at version {current} but this version of the portal expects {head}. Restart the portal so it can update the database.")

    def storage() -> tuple[str, str]:
        root = files_root()
        if not os.access(root, os.W_OK):
            return "bad", "The file storage folder cannot be written to. Uploads will fail."
        free = shutil.disk_usage(root).free
        return ("warn" if free < 2 * 1024 ** 3 else "ok"), f"File storage is writable with {free / 1024 ** 3:.1f} GB free."

    def key() -> tuple[str, str]:
        status = key_status()
        return ("ok", "An encryption key is set. Keep a copy of it somewhere separate from the backups.") if status["configured"] else ("bad", str(status["message"]))

    def backups() -> tuple[str, str]:
        summary = backup_summary(caller=caller, db=db)
        if summary["state"] == "none":
            return "bad", "No backup has been made on this server."
        text_ = f"The newest backup was made {summary['newest'][:10]}" + (" and has been verified." if summary["newest_is_verified"] else " and has not been verified.")
        return ("ok" if summary["state"] == "fresh" and summary["newest_is_verified"] else "warn" if summary["state"] != "old" else "bad"), text_

    def safe_mode() -> tuple[str, str]:
        return ("ok", "Sending is locked off on the server: no email or text can leave, whatever is switched on in the portal.") if settings.comms_safe_mode.strip().lower() != "off" else \
            ("warn", "The server lock is off, so each organisation's own safe mode switch decides whether messages are sent.")

    def public_address() -> tuple[str, str]:
        return ("ok", f"Links in invitations and on QR signs use {settings.public_base_url}.") if settings.public_base_url.strip() else \
            ("warn", "No public address is set, so links use whatever address the person who creates them is using. Set PUBLIC_BASE_URL before printing QR signs.")

    checks = [_check("Database", database), _check("Database version", migrations), _check("File storage", storage), _check("Encryption key", key), _check("Backups", backups),
              _check("Message sending lock", safe_mode), _check("Public address", public_address)]
    connections = [{"kind": c.kind, "mode": c.mode, "last_test_at": iso(c.last_test_at), "last_test_ok": c.last_test_ok}
                   for c in db.scalars(select(IntegrationConfig).where(IntegrationConfig.org_id == caller.org_id).order_by(IntegrationConfig.kind)).all()]
    worst = "bad" if any(c["state"] == "bad" for c in checks) else "warn" if any(c["state"] in ("warn", "unknown") for c in checks) else "ok"
    return {"overall": worst, "checks": checks, "connections": connections,
            "version": {"portal": settings.app_version, "build": settings.git_commit or None, "environment": settings.app_env, "python": platform.python_version()},
            "counts": {"organisations": int(db.scalar(select(func.count()).select_from(Organisation)) or 0), "logins": int(db.scalar(select(func.count()).select_from(User)) or 0)},
            "not_built": "Installing updates from inside the portal, and checks of Docker or GitHub, are not built. Updates are done on the server (see README)."}


@router.get("/api/system/missing-files")
def missing_files(limit: int = Query(200, ge=1, le=1000), caller: Caller = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """File records in your organisation whose stored bytes are gone or the wrong size. Read-only: nothing is repaired or removed."""
    rows = db.scalars(select(StoredFile).where(StoredFile.org_id == caller.org_id, StoredFile.deleted_at.is_(None)).order_by(StoredFile.created_at.desc())).all()
    missing = [r for r in rows if not file_exists(r)]
    return {"checked": len(rows), "missing": len(missing),
            "items": [{"id": r.id, "name": r.original_name, "used_for": r.purpose, "size_bytes": r.size_bytes, "uploaded_at": iso(r.created_at)} for r in missing[:limit]],
            "note": "A missing file has to be uploaded again where it is used, or restored from a backup. This list changes nothing."}
