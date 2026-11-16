"""Backups: create an archive, verify it, and rehearse a restore somewhere harmless.

One archive is a ZIP holding every table as JSON lines, every stored file, and a manifest that
lists each of them with its size and SHA-256. The three operations are deliberately separate:

  create    writes a new archive. It reads the database in one consistent view and changes nothing.
  verify    re-reads an archive and checks it against its own manifest and the size and hash
            recorded when it was made. It never changes the archive or the portal's data.
  rehearse  loads an archive into a scratch database and a temporary file folder, then exports
            that scratch copy again and checks it is identical. It never touches the portal's own
            database: the scratch database must be a different one whose name ends "_rehearsal".

There is no "restore over the running portal". A real restore goes into an empty database with the
command at the bottom of this file (see docs/BACKUP_RESTORE.md).

The encryption key is never put in an archive. Without the key that was in use when the archive
was made, protected details in it cannot be read. The manifest holds a fingerprint of the key so a
restore can tell whether the key it has is the right one.
"""
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Connection, Engine, make_url

from .config import get_settings
from .models import BackupSnapshot, Base

FORMAT = 1
CHUNK = 1024 * 1024
SKIP_TABLES = {BackupSnapshot.__tablename__}  # the catalogue describes this server's folder, not the data
KEY_NOTE = "The encryption key is not in this archive. Keep it somewhere separate. Without the same key, protected details cannot be read after a restore."


class BackupError(Exception):
    """Something the operator needs to read. The message is safe to show."""


def backup_root() -> Path:
    root = Path(get_settings().backup_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def key_fingerprint() -> str | None:
    key = get_settings().encryption_key.strip()
    return hashlib.sha256(b"portal-key-fingerprint:" + key.encode()).hexdigest()[:16] if key else None


def tables() -> list[Any]:
    return [t for t in Base.metadata.sorted_tables if t.name not in SKIP_TABLES]


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    return value


def _decode(column: Any, value: Any) -> Any:
    if value is None:
        return None
    kind = column.type.python_type
    if kind is datetime:
        return datetime.fromisoformat(value)
    if kind is date:
        return date.fromisoformat(value)
    if kind is time:
        return time.fromisoformat(value)
    if kind is Decimal:
        return Decimal(value)
    if kind is bytes:
        return base64.b64decode(value)
    return value


def _lines(conn: Connection, table: Any) -> Iterator[bytes]:
    """One table as JSON lines, in primary-key order, always written the same way so its hash is repeatable."""
    order = list(table.primary_key.columns) or list(table.columns)
    for row in conn.execution_options(yield_per=2000).execute(select(table).order_by(*order)):
        yield (json.dumps([_encode(v) for v in row], ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _export_table(conn: Connection, table: Any, sink: Any = None) -> dict[str, Any]:
    digest, rows, size = hashlib.sha256(), 0, 0
    for line in _lines(conn, table):
        digest.update(line)
        rows += 1
        size += len(line)
        if sink is not None:
            sink.write(line)
    return {"rows": rows, "bytes": size, "sha256": digest.hexdigest(), "columns": [c.name for c in table.columns]}


def _revision(conn: Connection) -> str | None:
    try:
        with conn.begin_nested():
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - a database made without migrations has no such table
        return None


def _file_path(files_root: Path, org_id: str, sha: str) -> Path:
    return files_root / org_id / sha[:2] / sha


def create_archive(engine: Engine, files_root: Path, dest_dir: Path, snapshot_id: str, made_at: datetime) -> dict[str, Any]:
    """Write one archive and return what was recorded about it. Reads only."""
    name = f"portal-backup-{made_at:%Y%m%d-%H%M%S}-{snapshot_id[:8]}.zip"
    partial = dest_dir / f".{name}.partial"
    manifest: dict[str, Any] = {"format": FORMAT, "snapshot_id": snapshot_id, "made_at_utc": made_at.isoformat(), "portal_version": get_settings().app_version,
                                "key_fingerprint": key_fingerprint(), "key_note": KEY_NOTE, "tables": {}, "files": [], "files_missing": []}
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
                manifest["database_revision"] = _revision(conn)
                for table in tables():
                    with archive.open(f"db/{table.name}.jsonl", "w", force_zip64=True) as sink:
                        manifest["tables"][table.name] = _export_table(conn, table, sink)
                stored = Base.metadata.tables["stored_files"]
                wanted = sorted({(r.org_id, r.sha256, int(r.size_bytes)) for r in conn.execute(select(stored.c.org_id, stored.c.sha256, stored.c.size_bytes))})
            for org_id, sha, size in wanted:
                path = _file_path(files_root, org_id, sha)
                if not path.is_file():
                    manifest["files_missing"].append({"org": org_id, "sha256": sha, "why": "not in file storage"})
                    continue
                digest, copied = hashlib.sha256(), 0
                info = zipfile.ZipInfo(f"files/{org_id}/{sha[:2]}/{sha}", date_time=made_at.timetuple()[:6])
                info.compress_type = zipfile.ZIP_STORED  # photos and PDFs are already compressed
                with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as sink:
                    while chunk := source.read(CHUNK):
                        digest.update(chunk)
                        copied += len(chunk)
                        sink.write(chunk)
                entry = {"org": org_id, "sha256": sha, "bytes": copied}
                if digest.hexdigest() != sha or copied != size:
                    entry["damaged"] = True  # kept as found, and reported: a backup must not quietly drop or "fix" a file
                manifest["files"].append(entry)
            archive.writestr("manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
        digest = hashlib.sha256()
        with partial.open("rb") as fh:
            while chunk := fh.read(CHUNK):
                digest.update(chunk)
        with partial.open("rb") as fh:
            os.fsync(fh.fileno())
        final = dest_dir / name
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    damaged = sum(1 for f in manifest["files"] if f.get("damaged"))
    return {"filename": name, "size_bytes": final.stat().st_size, "sha256": digest.hexdigest(),
            "summary": {"tables": len(manifest["tables"]), "rows": sum(t["rows"] for t in manifest["tables"].values()), "files": len(manifest["files"]),
                        "file_bytes": sum(f["bytes"] for f in manifest["files"]), "files_missing": len(manifest["files_missing"]), "files_damaged": damaged,
                        "database_revision": manifest["database_revision"], "key_fingerprint": manifest["key_fingerprint"], "portal_version": manifest["portal_version"]}}


def _sha_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: Path, *, expect_id: str | None = None, expect_sha: str | None = None, expect_size: int | None = None) -> dict[str, Any]:
    """Re-read an archive and check everything that can be checked without a database.

    Returns {"state": "verified" | "failed" | "pending", "checks": [...], "reason": ...}. "pending"
    means nothing could be checked (the archive is not here); it is never reported as success.
    """
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    if not path.is_file():
        return {"state": "pending", "checks": [], "reason": "The archive is not in this server's backup folder. It may have been moved or the folder may not be connected. Nothing was checked, "
                                                            "and this does not mean a copy elsewhere is damaged."}
    if expect_size is None or expect_sha is None:
        check("Size and fingerprint recorded when it was made", False, "No size or fingerprint was recorded for this archive, so its bytes cannot be compared.")
    else:
        check("Size matches what was recorded", path.stat().st_size == expect_size, f"{path.stat().st_size} bytes now, {expect_size} recorded")
        check("Fingerprint (SHA-256) matches what was recorded", _sha_of(path) == expect_sha)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if not check("Contains a manifest", "manifest.json" in names):
                return {"state": "failed", "checks": checks, "reason": "The archive has no manifest."}
            manifest = json.loads(archive.read("manifest.json"))
            if expect_id is not None:
                check("Manifest is for this archive", manifest.get("snapshot_id") == expect_id)
            check("Format is one this portal reads", manifest.get("format") == FORMAT, f"format {manifest.get('format')}")
            bad_tables = []
            for name, meta in manifest.get("tables", {}).items():
                entry = f"db/{name}.jsonl"
                if entry not in names:
                    bad_tables.append(f"{name}: missing")
                    continue
                digest, rows = hashlib.sha256(), 0
                with archive.open(entry) as fh:
                    for line in fh:
                        digest.update(line)
                        rows += 1
                if digest.hexdigest() != meta["sha256"] or rows != meta["rows"]:
                    bad_tables.append(f"{name}: contents differ from the manifest")
            check(f"All {len(manifest.get('tables', {}))} tables are present and unchanged", not bad_tables, "; ".join(bad_tables[:5]))
            bad_files = 0
            for meta in manifest.get("files", []):
                entry = f"files/{meta['org']}/{meta['sha256'][:2]}/{meta['sha256']}"
                if entry not in names:
                    bad_files += 1
                    continue
                digest = hashlib.sha256()
                with archive.open(entry) as fh:
                    while chunk := fh.read(CHUNK):
                        digest.update(chunk)
                if digest.hexdigest() != meta["sha256"] and not meta.get("damaged"):
                    bad_files += 1
            check(f"All {len(manifest.get('files', []))} stored files are present and unchanged", bad_files == 0, f"{bad_files} differ or are missing" if bad_files else "")
            extra = sorted(n for n in names if n != "manifest.json" and not n.startswith(("db/", "files/")))
            check("Nothing unexpected inside", not extra, ", ".join(extra[:5]))
            notes = []
            if manifest.get("files_missing"):
                notes.append(f"{len(manifest['files_missing'])} file(s) were already missing from file storage when the archive was made, so they are not in it.")
            if any(f.get("damaged") for f in manifest.get("files", [])):
                notes.append("Some files were already damaged in file storage when the archive was made. They are included as found.")
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError) as exc:
        check("Archive can be opened and read", False, type(exc).__name__)
        return {"state": "failed", "checks": checks, "reason": "The archive could not be read. It is damaged or is not a portal backup."}
    ok = all(c["ok"] for c in checks)
    return {"state": "verified" if ok else "failed", "checks": checks, "notes": notes, "reason": None if ok else "One or more checks did not pass. Do not rely on this archive."}


# ---------- restore (into an empty or scratch database only) ----------

def same_database(a: str, b: str) -> bool:
    x, y = make_url(a), make_url(b)
    return (x.host or "localhost", x.port or 5432, x.database) == (y.host or "localhost", y.port or 5432, y.database)


def scratch_engine() -> Engine:
    """The rehearsal database, after checking it cannot be the portal's own."""
    settings = get_settings()
    url = settings.rehearsal_database_url.strip()
    if not url:
        raise BackupError("No scratch database is set up for rehearsals. Set REHEARSAL_DATABASE_URL to an empty database whose name ends in _rehearsal, then restart.")
    if same_database(url, settings.database_url):
        raise BackupError("The rehearsal database is the portal's own database. A rehearsal never runs there.")
    if not (make_url(url).database or "").endswith("_rehearsal"):
        raise BackupError("The rehearsal database's name must end in _rehearsal, so a real database cannot be chosen by mistake.")
    engine = create_engine(url)
    try:
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        engine.dispose()
        raise BackupError("The scratch database could not be reached. Create it once on the database server (see docs/BACKUP_RESTORE.md), then try again.") from exc
    return engine


def restore_into(path: Path, engine: Engine, files_root: Path, *, wipe: bool) -> dict[str, Any]:
    """Load an archive into `engine` and `files_root`. With wipe=False the database must be empty."""
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != FORMAT:
            raise BackupError("This archive was made in a format this version of the portal does not read.")
        known = {t.name: t for t in tables()}
        unknown = sorted(set(manifest["tables"]) - set(known))
        if unknown:
            raise BackupError(f"The archive holds tables this version of the portal does not have ({', '.join(unknown[:5])}). Restore with the portal version that made it "
                              f"({manifest.get('portal_version')}), then upgrade.")
        for name, meta in manifest["tables"].items():
            if meta["columns"] != [c.name for c in known[name].columns]:
                raise BackupError(f"The table {name} has different columns in this version of the portal. Restore with the portal version that made the archive "
                                  f"({manifest.get('portal_version')}), then upgrade.")
        with engine.begin() as conn:
            if wipe:
                conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
            elif inspect(conn).get_table_names():
                raise BackupError("The target database is not empty. A restore only goes into an empty database, so nothing can be overwritten.")
            Base.metadata.create_all(conn)
            for table in tables():
                meta = manifest["tables"].get(table.name)
                if meta is None:
                    continue
                columns = [table.c[name] for name in meta["columns"]]
                batch: list[dict[str, Any]] = []
                with archive.open(f"db/{table.name}.jsonl") as fh:
                    for line in fh:
                        batch.append({c.name: _decode(c, v) for c, v in zip(columns, json.loads(line))})
                        if len(batch) >= 1000:
                            conn.execute(table.insert(), batch)
                            batch = []
                if batch:
                    conn.execute(table.insert(), batch)
            if manifest.get("database_revision"):
                conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
                conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": manifest["database_revision"]})
        for meta in manifest["files"]:
            target = _file_path(files_root, meta["org"], meta["sha256"])
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(f"files/{meta['org']}/{meta['sha256'][:2]}/{meta['sha256']}") as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, CHUNK)
    return manifest


def compare(manifest: dict[str, Any], engine: Engine, files_root: Path) -> list[dict[str, Any]]:
    """Export the restored copy again and check it is exactly what the archive said it held."""
    checks: list[dict[str, Any]] = []
    differ = []
    with engine.connect() as conn:
        for table in tables():
            meta = manifest["tables"].get(table.name)
            if meta is None:
                continue
            again = _export_table(conn, table)
            if again["rows"] != meta["rows"] or again["sha256"] != meta["sha256"]:
                differ.append(f"{table.name} ({again['rows']} rows, {meta['rows']} expected)")
        rows = sum(t["rows"] for t in manifest["tables"].values())
        checks.append({"check": f"All {len(manifest['tables'])} tables hold exactly the same {rows} rows as the archive", "ok": not differ, "detail": "; ".join(differ[:5])})
        stored = Base.metadata.tables["stored_files"]
        missing_in_archive = {(m["org"], m["sha256"]) for m in manifest.get("files_missing", [])}
        unlinked = damaged = 0
        for row in conn.execute(select(stored.c.org_id, stored.c.sha256, stored.c.size_bytes).distinct()):
            if (row.org_id, row.sha256) in missing_in_archive:
                continue
            path = _file_path(files_root, row.org_id, row.sha256)
            if not path.is_file():
                unlinked += 1
            elif path.stat().st_size != row.size_bytes or _sha_of(path) != row.sha256:
                damaged += 1
        checks.append({"check": "Every file record points at a restored file with the right contents", "ok": unlinked == 0 and damaged == 0,
                       "detail": f"{unlinked} missing, {damaged} with wrong contents" if unlinked or damaged else f"{len(manifest['files'])} files"})
        orphans = conn.execute(text("SELECT count(*) FROM users u LEFT JOIN organisations o ON o.id = u.org_id WHERE o.id IS NULL")).scalar() or 0
        checks.append({"check": "Every login belongs to an organisation in the restored copy", "ok": orphans == 0, "detail": ""})
    now = key_fingerprint()
    then = manifest.get("key_fingerprint")
    checks.append({"check": "The server's encryption key is the one the archive was made with", "ok": then is None or now == then,
                   "detail": "The archive was made with no key set, so it holds no protected details." if then is None else
                   "" if now == then else "The key is different. Protected details in this archive could not be read with the current key."})
    if manifest.get("files_missing"):
        checks.append({"check": "No files were missing when the archive was made", "ok": False, "detail": f"{len(manifest['files_missing'])} file(s) were already missing and are not in the archive."})
    return checks


def rehearse(path: Path) -> dict[str, Any]:
    """Restore into the scratch database and a temporary folder, compare, then clear both."""
    engine = scratch_engine()
    folder = Path(tempfile.mkdtemp(prefix="portal-rehearsal-"))
    try:
        manifest = restore_into(path, engine, folder, wipe=True)
        checks = compare(manifest, engine, folder)
        return {"state": "passed" if all(c["ok"] for c in checks) else "failed", "checks": checks,
                "tables": len(manifest["tables"]), "rows": sum(t["rows"] for t in manifest["tables"].values()), "files": len(manifest["files"])}
    finally:
        try:
            with engine.begin() as conn:  # leave nothing readable behind in the scratch database
                conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
        finally:
            engine.dispose()
            shutil.rmtree(folder, ignore_errors=True)


# ---------- command line: scheduled backups and real restores ----------

def _main(argv: list[str]) -> int:
    from .db import get_engine
    from .files import files_root
    from .models import new_id, utcnow

    usage = ("usage: python -m app.backup snapshot\n"
             "       python -m app.backup verify <archive.zip>\n"
             "       python -m app.backup restore <archive.zip> --target-url <database url> --files-dir <empty folder>")
    if len(argv) >= 1 and argv[0] == "snapshot":
        from sqlalchemy.orm import Session
        with Session(get_engine()) as db:
            row = BackupSnapshot(id=new_id(), status="creating", created_at=utcnow())
            db.add(row)
            db.commit()
            try:
                made = create_archive(get_engine(), files_root(), backup_root(), row.id, row.created_at)
                row.status, row.filename, row.size_bytes, row.sha256, row.summary, row.finished_at = "created", made["filename"], made["size_bytes"], made["sha256"], made["summary"], utcnow()
            except Exception as exc:  # noqa: BLE001
                row.status, row.error, row.finished_at = "failed", f"{type(exc).__name__}: {exc}"[:500], utcnow()
            db.commit()
            print(f"{row.status}: {row.filename or row.error}")
            return 0 if row.status == "created" else 1
    if len(argv) == 2 and argv[0] == "verify":
        result = verify_archive(Path(argv[1]))
        for c in result["checks"]:
            print(("ok   " if c["ok"] else "FAIL ") + c["check"] + (f" ({c['detail']})" if c["detail"] else ""))
        print(result["state"] + (f": {result['reason']}" if result.get("reason") else ""))
        print("Note: run from the command line, the size and fingerprint recorded in the portal are not compared. Use Verify in the portal for that.")
        return 0 if all(c["ok"] for c in result["checks"][1:]) and result["state"] != "pending" else 1
    if len(argv) == 6 and argv[0] == "restore" and argv[2] == "--target-url" and argv[4] == "--files-dir":
        target, folder = argv[3], Path(argv[5])
        if same_database(target, get_settings().database_url):
            print("Refused: the target is the database this portal is configured to use. Restore into a new, empty database and point the portal at it afterwards.")
            return 2
        if folder.exists() and any(folder.iterdir()):
            print("Refused: the files folder is not empty.")
            return 2
        folder.mkdir(parents=True, exist_ok=True)
        engine = create_engine(target)
        try:
            manifest = restore_into(Path(argv[1]), engine, folder, wipe=False)
            checks = compare(manifest, engine, folder)
        except BackupError as exc:
            print(f"Refused: {exc}")
            return 2
        for c in checks:
            print(("ok   " if c["ok"] else "FAIL ") + c["check"] + (f" ({c['detail']})" if c["detail"] else ""))
        return 0 if all(c["ok"] for c in checks) else 1
    print(usage)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
