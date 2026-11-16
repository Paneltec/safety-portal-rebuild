"""Performance check on a synthetic dataset.

Builds a throwaway database (default name portal_perf), fills it with made-up records, times the
screens' own API calls, and writes docs/PERFORMANCE.md. Nothing real is read or contacted.

    cd backend
    PERF_DATABASE_URL=postgresql+psycopg://portal:<password>@localhost:5432/portal_perf \
        .venv/bin/python scripts/perf_check.py

The database named in PERF_DATABASE_URL is dropped and recreated. Its name must end in "_perf".
"""
import datetime as dt
import os
import platform
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

URL = os.environ.get("PERF_DATABASE_URL", "postgresql+psycopg://portal:portal@localhost:5432/portal_perf")
if not URL.rsplit("/", 1)[-1].endswith("_perf"):
    sys.exit("Refused: the database name must end in _perf. This script drops and recreates it.")

os.environ.update({"APP_ENV": "test", "DATABASE_URL": URL, "SETUP_KEY": "perf", "COMMS_SAFE_MODE": "on", "FILES_DIR": tempfile.mkdtemp(prefix="perf-files-"),
                   "BACKUP_DIR": tempfile.mkdtemp(prefix="perf-backups-"), "REHEARSAL_DATABASE_URL": ""})
from cryptography.fernet import Fernet  # noqa: E402

os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, insert, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

SIZES = {"workers": 500, "certifications": 1500, "assets": 300, "service_schedules": 300, "fuel_fills": 100_000, "form_submissions": 20_000, "hazards": 5_000,
         "sites": 50, "site_sign_ins": 25_000, "activity_entries": 50_000}
FIRST = ["Alex", "Sam", "Jess", "Tom", "Mia", "Lee", "Kim", "Pat", "Ray", "Dee", "Jo", "Max", "Ned", "Ivy", "Gus", "Eve", "Hal", "Liv", "Abe", "Una"]
LAST = ["Smith", "Jones", "Tran", "Okafor", "Marlow", "Keating", "Rowe", "Fontaine", "Nguyen", "Baker", "Walsh", "Singh", "Clarke", "Young", "Reid", "Hart", "Cole", "Dunn", "Frost", "Lane",
        "Mason", "Pike", "Quinn", "Shaw", "Todd"]
rng = random.Random(20261116)  # fixed, so two runs build the same data


def rebuild_database() -> None:
    admin = create_engine(URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    name = URL.rsplit("/", 1)[-1]
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")


def bulk(db: Session, model, rows: list[dict]) -> None:
    for start in range(0, len(rows), 5000):
        db.execute(insert(model), rows[start:start + 5000])


def seed() -> dict:
    from app import models as m
    from app.security import hash_password
    from app.services import create_organisation

    now = m.utcnow()
    today = now.date()
    uid = m.new_id
    with Session(create_engine(URL), expire_on_commit=False) as db:
        org = create_organisation(db, "Synthetic Civil")
        owner = m.User(org_id=org.id, email="owner@perf.example", name="Perf Owner", role_id="admin", status="active", password_hash=hash_password("Perf-Check-2026"))
        db.add(owner)
        db.flush()
        org.owner_user_id = org.payroll_owner_user_id = owner.id

        workers = [{"id": uid(), "org_id": org.id, "first_name": rng.choice(FIRST), "last_name": f"{rng.choice(LAST)}{'' if i < 25 else i}", "position": rng.choice(["Operator", "Labourer", "Leading hand"]),
                    "active": i % 20 != 0} for i in range(SIZES["workers"])]
        bulk(db, m.Worker, workers)
        kinds = ["first_aid", "white_card", "cpr"]
        bulk(db, m.Certification, [{"id": uid(), "org_id": org.id, "worker_id": w["id"], "kind_slug": kinds[k], "expiry_date": today + dt.timedelta(days=rng.randint(-60, 700)),
                                   "no_expiry": False, "created_by": owner.id} for w in workers for k in range(3)])
        sites = [{"id": uid(), "org_id": org.id, "name": f"{rng.choice(LAST)} St stage {i}", "scan_token": uid().replace("-", "")[:24], "active": True} for i in range(SIZES["sites"])]
        bulk(db, m.Site, sites)
        assets = [{"id": uid(), "org_id": org.id, "kind": "vehicle" if i % 3 else "plant", "name": f"Unit {i:03d}", "registration": f"P{i:03d}RF", "scan_token": uid().replace("-", "")[:24],
                   "status": "active", "odometer_km": 40000 + i * 137, "fuel_type": "diesel"} for i in range(SIZES["assets"])]
        bulk(db, m.Asset, assets)
        bulk(db, m.AssetSchedule, [{"id": uid(), "org_id": org.id, "asset_id": a["id"], "name": "Service", "interval_days": 180, "warn_days": 14,
                                   "baseline_date": today - dt.timedelta(days=rng.randint(10, 260)), "active": True} for a in assets[:SIZES["service_schedules"]]])
        batch = m.FuelBatch(org_id=org.id, via="csv", status="imported")
        db.add(batch)
        db.flush()
        fills = []
        for i in range(SIZES["fuel_fills"]):
            when = now - dt.timedelta(minutes=rng.randint(0, 730 * 24 * 60))
            asset = rng.choice(assets)
            fills.append({"id": uid(), "org_id": org.id, "source_system": "csv", "source_id": f"PERF-{i}", "batch_id": batch.id, "occurred_at": when, "local_date": when.date(),
                          "local_time": when.strftime("%H:%M"), "litres": round(rng.uniform(20, 180), 2), "fuel_type": "Diesel", "is_diesel": True, "registration": asset["registration"],
                          "asset_id": asset["id"], "attribution": "direct"})
        bulk(db, m.FuelTransaction, fills)
        template = m.FormTemplate(org_id=org.id, name="Daily check", category="general", status="published", published_version=1, created_by=owner.id,
                                  schema=[{"id": "note", "type": "text", "label": "Note"}])
        db.add(template)
        db.flush()
        version = m.FormTemplateVersion(org_id=org.id, template_id=template.id, version=1, name="Daily check", category="general", schema=template.schema)
        db.add(version)
        db.flush()
        bulk(db, m.FormSubmission, [{"id": uid(), "org_id": org.id, "template_id": template.id, "template_version_id": version.id, "status": "submitted", "values": {"note": f"Entry {i}"},
                                    "submitted_by": owner.id, "submitted_at": now - dt.timedelta(minutes=rng.randint(0, 500000))} for i in range(SIZES["form_submissions"])])
        bulk(db, m.SafetyRecord, [{"id": uid(), "org_id": org.id, "kind": "hazard", "title": f"{rng.choice(['Open trench', 'Loose plate', 'Blocked exit', 'Oil spill'])} {i}",
                                  "status": rng.choice(["open", "open", "in_progress", "closed"]), "record_date": today - dt.timedelta(days=rng.randint(0, 700)), "schema": [],
                                  "values": {"severity": "High"}, "created_by": owner.id} for i in range(SIZES["hazards"])])
        bulk(db, m.SiteAttendance, [{"id": uid(), "org_id": org.id, "site_id": rng.choice(sites)["id"], "kind": "visitor", "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                                    "signed_in_at": now - dt.timedelta(minutes=rng.randint(60, 500000)), "signed_out_at": now} for _ in range(SIZES["site_sign_ins"])])
        bulk(db, m.AuditLog, [{"id": uid(), "org_id": org.id, "actor_user_id": owner.id, "action": rng.choice(["site.created", "worker.updated", "asset.updated", "form_submission.submitted"]),
                              "target_type": "site", "target_id": uid(), "detail": {"n": i}, "created_at": now - dt.timedelta(minutes=rng.randint(0, 500000))}
                             for i in range(SIZES["activity_entries"])])
        db.commit()
        db.execute(text("ANALYZE"))
        db.commit()
        return {"template_id": template.id, "from": (today - dt.timedelta(days=30)).isoformat(), "to": today.isoformat(), "year_from": (today - dt.timedelta(days=365)).isoformat()}


def timed(fn, runs: int = 5) -> tuple[float, float, int, int]:
    """Median and slowest in milliseconds over `runs`, the status and the answer's size. The first call is a warm-up and is not counted."""
    fn()
    samples, last = [], None
    for _ in range(runs):
        start = time.perf_counter()
        last = fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), max(samples), last.status_code, len(last.content)


def main() -> None:
    print("building the database…", flush=True)
    rebuild_database()
    started = time.perf_counter()
    ids = seed()
    seed_seconds = time.perf_counter() - started
    from app.main import app

    client = TestClient(app)
    token = client.post("/api/auth/login", json={"email": "owner@perf.example", "password": "Perf-Check-2026"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    get = lambda url: (lambda: client.get(url, headers=h))  # noqa: E731
    calls = [
        ("Home: dashboard figures", get("/api/dashboard/metrics")),
        ("Search for \"smith\" across registers", get("/api/search?q=smith")),
        ("Workers: first page of 50", get("/api/workers?limit=50")),
        ("Workers: search \"okafor\"", get("/api/workers?q=okafor&limit=50")),
        ("Certifications register: first page", get("/api/workers/certifications/all?limit=50")),
        ("Certifications: expiry counts", get("/api/certifications/expiry-count")),
        ("Fleet: first page of 50", get("/api/assets?page_size=50")),
        ("Service: what is due", get("/api/assets/service/inbox")),
        ("Fuel: fills, first page of 50", get("/api/fleet/fuel/transactions?page_size=50")),
        ("Fuel: fills, page 1,000 of 50", get("/api/fleet/fuel/transactions?page_size=50&page=1000")),
        ("Fuel: report by vehicle, last 30 days", get(f"/api/fleet/fuel/reports?group=vehicle&date_from={ids['from']}&date_to={ids['to']}")),
        ("Fuel: report by vehicle, last 12 months", get(f"/api/fleet/fuel/reports?group=vehicle&date_from={ids['year_from']}&date_to={ids['to']}")),
        ("Fuel: summary tiles", get("/api/fleet/fuel/stats")),
        ("Forms: submissions, first page of 50", get(f"/api/forms/templates/{ids['template_id']}/submissions?limit=50")),
        ("Hazards: first page of 50", get("/api/hazards?limit=50")),
        ("Hazards: search \"trench\"", get("/api/hazards?q=trench&limit=50")),
        ("Visitors: list", get("/api/admin/visitors")),
        ("Activity log: first page of 50", get("/api/audit-log?limit=50")),
        ("Activity log: file for the last 30 days", get(f"/api/audit-log/export.csv?date_from={ids['from']}&date_to={ids['to']}")),
    ]
    rows = []
    for label, fn in calls:
        median, worst, status, size = timed(fn)
        rows.append((label, median, worst, status, size))
        print(f"{median:8.0f} ms  {status}  {label}", flush=True)

    start = time.perf_counter()
    pack = client.post("/api/audit-exports", json={"title": "Perf pack", "registers": ["hazard", "site_attendance", "form_submissions"], "date_from": ids["from"], "date_to": ids["to"]}, headers=h)
    rows.append(("Audit pack: hazards, site log and forms for 30 days (once)", (time.perf_counter() - start) * 1000, None, pack.status_code, len(pack.content)))
    too_many = client.post("/api/audit-exports", json={"title": "Perf pack", "registers": ["site_attendance", "form_submissions"]}, headers=h)

    start = time.perf_counter()
    made = client.post("/api/backup/snapshots", headers=h)  # the test client runs the background work before returning
    backup_ms = (time.perf_counter() - start) * 1000
    snap = client.get("/api/backup/snapshots", headers=h).json()["items"][0]
    start = time.perf_counter()
    verified = client.post(f"/api/backup/snapshots/{snap['id']}/verify", headers=h).json()
    verify_ms = (time.perf_counter() - start) * 1000

    import subprocess
    pg = subprocess.run(["psql", "--version"], capture_output=True, text=True).stdout.strip() or "PostgreSQL (version not read)"
    cpu = next((line.split(":", 1)[1].strip() for line in open("/proc/cpuinfo") if line.startswith("model name")), platform.processor())
    mem = next((int(line.split()[1]) // 1024 // 1024 for line in open("/proc/meminfo") if line.startswith("MemTotal")), 0)
    slow = [r for r in rows if r[1] > 1000] + ([("Making a backup", backup_ms)] if backup_ms > 1000 else [])
    out = [
        "# Performance check", "",
        f"Measured {dt.date.today().isoformat()} by `backend/scripts/perf_check.py` on a synthetic dataset. Run the script again to refresh this page; nothing here is typed by hand.", "",
        "## What this is and is not", "",
        "- One person at a time: each call was made five times in a row and the median is shown. It says nothing about many people using the portal at once.",
        "- The API and the database ran on the same machine, called in-process, so network time and the web server are not included.",
        "- The machine below is a small cloud build machine, not the NAS the portal will run on. Expect different numbers there, and measure again once it is installed.",
        "- Stored files (photos, PDFs) were not part of the dataset, so backup time and size here cover records only.", "",
        "## Machine", "",
        f"- {cpu}, {os.cpu_count()} cores, {mem} GB memory",
        f"- {pg}; Python {platform.python_version()}; {platform.system()} {platform.release()}", "",
        "## Dataset", "",
        "| Records | Number |", "|---|---|",
        *[f"| {k.replace('_', ' ').capitalize()} | {v:,} |" for k, v in SIZES.items()],
        "", f"Building it took {seed_seconds:.0f} seconds.", "",
        "## Timings", "",
        "| Screen or action | Median | Slowest of 5 | Answer size |", "|---|---|---|---|",
        *[f"| {label} | {median:,.0f} ms | {'' if worst is None else f'{worst:,.0f} ms'} | {size / 1024:,.0f} KB{'' if status in (200, 201) else f' (status {status})'} |" for label, median, worst, status, size in rows],
        f"| Backup: make an archive of all records | {backup_ms:,.0f} ms | | {int(snap['size_bytes'] or 0) / 1024 / 1024:,.1f} MB archive, {snap['summary'].get('rows', 0):,} records |",
        f"| Backup: verify that archive | {verify_ms:,.0f} ms | | {verified['verify']['state']} |",
        "", "## Limits that were exercised", "",
        f"- An audit pack covering all {SIZES['site_sign_ins']:,} site sign-ins and all {SIZES['form_submissions']:,} form submissions with no date range was "
        f"{'refused (status 422) with a request to shorten the period, as designed' if too_many.status_code == 422 else f'answered with status {too_many.status_code}'}.",
        "- The activity log file is limited to 20,000 entries per file; the 30-day file above is within that.",
        "- Register lists are paged by the server (50 per page here). Three answers are not paged: the visitors list returns at most 500 rows, and the service list and fuel report "
        "return one row per schedule or vehicle.", "",
        "## Reading the numbers", "",
        ("Nothing took longer than one second." if not slow else "Longer than one second on this machine: " + "; ".join(f"{r[0]} ({r[1]:,.0f} ms)" for r in slow) + ". "
         "A backup runs in the background after the button is pressed, so the screen does not wait for it."),
        "No tuning was done as a result of this run. Anything listed as slow is a known item, not a fixed one.", "",
    ]
    (BACKEND.parent / "docs" / "PERFORMANCE.md").write_text("\n".join(out), encoding="utf-8")
    from app.db import get_engine
    get_engine().dispose()
    admin = create_engine(URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:  # leave nothing behind
        conn.execute(text(f'DROP DATABASE IF EXISTS "{URL.rsplit("/", 1)[-1]}" WITH (FORCE)'))
    admin.dispose()
    print(f"backup {backup_ms:.0f} ms (status {made.status_code}), verify {verify_ms:.0f} ms -> docs/PERFORMANCE.md")


if __name__ == "__main__":
    main()
