"""Backups (create, verify, rehearse, tidy) and the system check (stage 9c)."""
import datetime as dt
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text

from app import backup
from app.config import get_settings
from app.db import get_engine
from app.files import files_root
from app.models import AuditLog, BackupSnapshot, StoredFile, Worker

from .test_safety import HAZARD, answers

B = "/api/backup"


@pytest.fixture
def owner(world, db):
    """Org One was set up first, so its account holder runs the installation."""
    world.org1.created_at = world.org1.created_at - dt.timedelta(days=1)
    world.db.commit()
    for path in Path(get_settings().backup_dir).glob("*"):
        path.unlink()
    return world.auth("owner1")


def seed(world, client):
    """A little of everything a restore must bring back: records, a protected field, and a stored file."""
    admin = world.auth("admin1")
    made = client.post("/api/workers", json={"first_name": "Mia", "last_name": "Tran", "tax_file_number": "123 456 782"}, headers=admin)
    assert made.status_code == 201, made.text
    mia = world.db.get(Worker, made.json()["id"])
    assert mia.protected
    client.post("/api/hazards", headers=admin, json={"fields": answers(**HAZARD)})
    client.post("/api/sites", json={"name": "Bridge Rd upgrade"}, headers=admin)
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626001000000050001a5f645400000000049454e44ae426082")
    logo = client.post("/api/org/logo/upload", files={"file": ("logo.png", io.BytesIO(png), "image/png")}, headers=admin)
    assert logo.status_code in (200, 201), logo.text
    return mia


def make(client, owner):
    started = client.post(f"{B}/snapshots", headers=owner)
    assert started.status_code == 202, started.text
    return client.get(f"{B}/snapshots", headers=owner).json()["items"][0]


def test_backups_belong_to_the_installation_owner_only(world, client, owner):
    for key in ("admin1", "hseq1", "worker1", "admin2"):
        headers = world.auth(key)
        assert client.get(f"{B}/summary", headers=headers).status_code == 403
        assert client.get(f"{B}/snapshots", headers=headers).status_code == 403
        assert client.post(f"{B}/snapshots", headers=headers).status_code == 403
        assert client.post(f"{B}/snapshots/x/verify", headers=headers).status_code == 403
        assert client.post(f"{B}/snapshots/x/rehearse", headers=headers).status_code == 403
        assert client.post(f"{B}/retention/run", headers=headers).status_code == 403
        assert client.get("/api/system/status", headers=headers).status_code == 403
    assert client.get(f"{B}/summary").status_code == 401
    body = client.get(f"{B}/summary", headers=owner).json()
    assert body["state"] == "none" and body["newest"] is None and body["rehearsal_available"] is True and "not in this archive" in body["key_note"]
    assert "does not make backups by itself" in body["schedule"]


def test_create_verify_and_rehearse_a_backup(world, client, db, owner):
    mia = seed(world, client)
    snap = make(client, owner)
    assert snap["status"] == "created" and snap["in_folder"] and snap["verify"]["state"] == "not_checked" and snap["rehearsal"]["state"] == "not_run"
    assert snap["summary"]["files"] == 1 and snap["summary"]["files_missing"] == 0 and snap["summary"]["rows"] > 10 and snap["summary"]["key_fingerprint"]
    path = Path(get_settings().backup_dir) / snap["filename"]
    archive = zipfile.ZipFile(path)
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["snapshot_id"] == snap["id"] and "backup_snapshots" not in manifest["tables"] and manifest["tables"]["workers"]["rows"] == 1
    everything = b"".join(archive.read(n) for n in archive.namelist())
    assert get_settings().encryption_key.encode() not in everything and b"123456782" not in everything and b"123 456 782" not in everything  # the key and the plain value stay out
    # Created is not verified: the two are separate facts.
    assert client.get(f"{B}/summary", headers=owner).json()["newest_is_verified"] is False
    assert client.post(f"{B}/snapshots/{snap['id']}/rehearse", headers=owner).status_code == 409  # verify first

    checked = client.post(f"{B}/snapshots/{snap['id']}/verify", headers=owner).json()
    assert checked["verify"]["state"] == "verified" and all(c["ok"] for c in checked["verify"]["checks"]) and len(checked["verify"]["checks"]) >= 6
    assert hashlib.sha256(path.read_bytes()).hexdigest() == snap["sha256"]  # verifying did not change the archive
    assert client.get(f"{B}/summary", headers=owner).json()["newest_is_verified"] is True

    live_before = db.execute(text("SELECT count(*) FROM audit_log")).scalar()
    rehearsed = client.post(f"{B}/snapshots/{snap['id']}/rehearse", headers=owner).json()
    assert rehearsed["rehearsal"]["state"] == "passed", rehearsed["rehearsal"]
    assert all(c["ok"] for c in rehearsed["rehearsal"]["checks"]) and rehearsed["rehearsal"]["rows"] == snap["summary"]["rows"] and rehearsed["rehearsal"]["files"] == 1
    # The portal's own data is untouched (one new activity entry for the rehearsal itself), and the scratch database is left empty.
    db.expire_all()
    assert db.get(Worker, mia.id) is not None and db.execute(text("SELECT count(*) FROM audit_log")).scalar() == live_before + 1
    scratch = create_engine(get_settings().rehearsal_database_url)
    with scratch.connect() as conn:
        assert inspect(conn).get_table_names() == []
    scratch.dispose()
    actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.target_id == snap["id"]).order_by(AuditLog.created_at)).all()]
    assert actions == ["backup.started", "backup.verified", "backup.rehearsed"]
    # Restoring over the running portal does not exist.
    refused = client.post(f"{B}/restore", headers=owner)
    assert refused.status_code == 409 and "not offered" in refused.json()["detail"]


def test_a_damaged_or_absent_archive_is_never_reported_as_good(world, client, db, owner):
    seed(world, client)
    snap = make(client, owner)
    path = Path(get_settings().backup_dir) / snap["filename"]
    original = path.read_bytes()

    # One byte changed in the middle: failed, not pending and not verified.
    damaged = bytearray(original)
    damaged[len(damaged) // 2] ^= 0xFF
    path.write_bytes(bytes(damaged))
    body = client.post(f"{B}/snapshots/{snap['id']}/verify", headers=owner).json()
    assert body["verify"]["state"] == "failed" and any(not c["ok"] for c in body["verify"]["checks"]) and body["verify"]["reason"]
    assert client.post(f"{B}/snapshots/{snap['id']}/rehearse", headers=owner).status_code == 409

    # A valid ZIP whose contents were swapped: the recorded fingerprint and the manifest both catch it.
    swapped = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(swapped, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            dst.writestr(name, data.replace(b"Mia", b"Mio") if name == "db/workers.jsonl" else data)
    path.write_bytes(swapped.getvalue())
    result = backup.verify_archive(path, expect_id=snap["id"], expect_sha=snap["sha256"], expect_size=snap["size_bytes"])
    failed = [c["check"] for c in result["checks"] if not c["ok"]]
    assert result["state"] == "failed" and any("Fingerprint" in c for c in failed) and any("tables" in c for c in failed)

    # Not a ZIP at all.
    path.write_bytes(b"not a zip")
    assert client.post(f"{B}/snapshots/{snap['id']}/verify", headers=owner).json()["verify"]["state"] == "failed"

    # Moved away: pending with the reason, and the archive list says it is not in the folder.
    path.unlink()
    body = client.post(f"{B}/snapshots/{snap['id']}/verify", headers=owner).json()
    assert body["verify"]["state"] == "pending" and "Nothing was checked" in body["verify"]["reason"] and body["in_folder"] is False
    # Put back unchanged: verified again. Verification never rewrote anything.
    path.write_bytes(original)
    assert client.post(f"{B}/snapshots/{snap['id']}/verify", headers=owner).json()["verify"]["state"] == "verified"
    assert path.read_bytes() == original
    assert client.post(f"{B}/snapshots/nope/verify", headers=owner).status_code == 404


def test_a_file_missing_from_storage_is_reported_not_hidden(world, client, db, owner):
    seed(world, client)
    stored = db.scalars(select(StoredFile)).one()
    (files_root() / stored.org_id / stored.sha256[:2] / stored.sha256).unlink()
    found = client.get("/api/system/missing-files", headers=world.auth("admin1")).json()
    assert found["missing"] == 1 and found["items"][0]["name"] == "logo.png" and found["checked"] == 1
    assert client.get("/api/system/missing-files", headers=world.auth("admin2")).json() == {**found, "checked": 0, "missing": 0, "items": []}
    assert client.get("/api/system/missing-files", headers=world.auth("worker1")).status_code == 403
    snap = make(client, owner)
    assert snap["status"] == "created" and snap["summary"]["files"] == 0 and snap["summary"]["files_missing"] == 1
    body = client.post(f"{B}/snapshots/{snap['id']}/verify", headers=owner).json()
    assert body["verify"]["state"] == "verified" and "already missing" in body["verify"]["notes"][0]
    rehearsed = client.post(f"{B}/snapshots/{snap['id']}/rehearse", headers=owner).json()["rehearsal"]
    assert rehearsed["state"] == "failed" and [c["check"] for c in rehearsed["checks"] if not c["ok"]] == ["No files were missing when the archive was made"]


def test_rehearsal_refuses_any_database_that_could_be_real(world, client, owner, monkeypatch):
    seed(world, client)
    snap = make(client, owner)
    client.post(f"{B}/snapshots/{snap['id']}/verify", headers=owner)
    live = get_settings().database_url
    for url, words in ((live, "portal's own database"), (live.rsplit("/", 1)[0] + "/portal_other", "must end in _rehearsal"), ("", "No scratch database")):
        monkeypatch.setattr(get_settings(), "rehearsal_database_url", url)
        body = client.post(f"{B}/snapshots/{snap['id']}/rehearse", headers=owner).json()
        assert body["rehearsal"]["state"] == "unavailable" and words in body["rehearsal"]["reason"], body["rehearsal"]
    # The live database still has its tables: nothing was dropped by a refused rehearsal.
    with get_engine().connect() as conn:
        assert "workers" in inspect(conn).get_table_names()
    # A restore from the command line refuses the portal's own database and a database that is not empty.
    path = Path(get_settings().backup_dir) / snap["filename"]
    assert backup._main(["restore", str(path), "--target-url", live, "--files-dir", "/tmp/unused-restore-dir"]) == 2
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.restore_into(path, get_engine(), Path("/tmp/unused-restore-dir"), wipe=False)


def test_tidy_up_keeps_the_newest_and_the_newest_verified(world, client, db, owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_keep", 2)
    seed(world, client)
    made = []
    for days in (4, 3, 2, 1):
        snap = make(client, owner)
        row = db.get(BackupSnapshot, snap["id"])
        row.created_at = row.created_at - dt.timedelta(days=days)
        db.commit()
        made.append(snap)
    client.post(f"{B}/snapshots/{made[0]['id']}/verify", headers=owner)  # the oldest is the only verified one
    preview = client.get(f"{B}/retention/preview", headers=owner).json()
    assert [r["id"] for r in preview["remove"]] == [made[1]["id"]] and set(preview["keep"]) == {made[3]["id"], made[2]["id"], made[0]["id"]}
    folder = Path(get_settings().backup_dir)
    assert len(list(folder.glob("*.zip"))) == 4  # a preview removes nothing
    assert client.post(f"{B}/retention/run", headers=owner).json()["removed"] == 1
    assert sorted(p.name for p in folder.glob("*.zip")) == sorted(m["filename"] for m in (made[0], made[2], made[3]))
    assert [s["id"] for s in client.get(f"{B}/snapshots", headers=owner).json()["items"]] == [made[3]["id"], made[2]["id"], made[0]["id"]]
    assert client.post(f"{B}/retention/run", headers=owner).json() == {"removed": 0, "freed_bytes": 0}
    # A second backup cannot start while one is being made, and an interrupted one is marked failed rather than left running.
    db.add(BackupSnapshot(status="creating"))
    db.commit()
    assert client.post(f"{B}/snapshots", headers=owner).status_code == 409
    stuck = db.scalars(select(BackupSnapshot).where(BackupSnapshot.status == "creating")).one()
    stuck.created_at = stuck.created_at - dt.timedelta(hours=3)
    db.commit()
    newest = client.get(f"{B}/snapshots", headers=owner).json()["items"]
    assert [s for s in newest if s["status"] == "failed"][0]["error"].startswith("Interrupted")


def test_system_check_reports_each_thing_separately(world, client, db, owner, monkeypatch):
    body = client.get("/api/system/status", headers=owner).json()
    checks = {c["label"]: c for c in body["checks"]}
    assert checks["Database"]["state"] == "ok" and checks["Database version"]["state"] == "ok" and checks["File storage"]["state"] == "ok" and checks["Encryption key"]["state"] == "ok"
    assert checks["Backups"]["state"] == "bad" and "No backup" in checks["Backups"]["detail"] and body["overall"] == "bad"
    assert body["version"]["portal"] == get_settings().app_version and body["counts"]["organisations"] == 2
    assert get_settings().encryption_key not in json.dumps(body) and "portal:portal" not in json.dumps(body)  # no secrets, no connection string
    # One check that cannot be worked out is reported as unknown and the rest still answer.
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: (_ for _ in ()).throw(OSError("boom")))
    body = client.get("/api/system/status", headers=owner).json()
    checks = {c["label"]: c for c in body["checks"]}
    assert checks["File storage"]["state"] == "unknown" and checks["Database"]["state"] == "ok" and "boom" not in json.dumps(body)


def test_the_server_commands_make_check_and_restore(world, client, db, owner, tmp_path, capsys):
    """The commands in docs/BACKUP_RESTORE.md: a timed backup, a check, and a restore into an empty database."""
    seed(world, client)
    assert backup._main(["snapshot"]) == 0
    assert capsys.readouterr().out.startswith("created: portal-backup-")
    snap = client.get(f"{B}/snapshots", headers=owner).json()["items"][0]
    assert snap["status"] == "created" and snap["created_by"] == "A scheduled command"
    path = Path(get_settings().backup_dir) / snap["filename"]
    assert backup._main(["verify", str(path)]) == 0 and "ok   All" in capsys.readouterr().out

    target = get_settings().rehearsal_database_url  # stands in for a new, empty database
    scratch = create_engine(target)
    with scratch.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    try:
        assert backup._main(["restore", str(path), "--target-url", target, "--files-dir", str(tmp_path / "files")]) == 0
        out = capsys.readouterr().out
        assert "FAIL" not in out and out.count("ok   ") >= 4
        with scratch.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM workers")).scalar() == 1 and conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert len([p for p in (tmp_path / "files").rglob("*") if p.is_file()]) == 1
        # Asked again, it refuses: the database is no longer empty, so nothing is overwritten.
        assert backup._main(["restore", str(path), "--target-url", target, "--files-dir", str(tmp_path / "files2")]) == 2
        assert "not empty" in capsys.readouterr().out
    finally:
        with scratch.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        scratch.dispose()
    assert backup._main([]) == 2
