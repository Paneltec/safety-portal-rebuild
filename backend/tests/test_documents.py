import hashlib

from sqlalchemy import select

from app.files import _path_for
from app.models import Document, StoredFile

PDF = b"%PDF-1.4\n" + b"synthetic test document " * 20
PDF2 = b"%PDF-1.4\n" + b"second revision " * 30
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
BASE = "/api/document-library"


def make_folder(client, headers, name, parent=None):
    r = client.post(f"{BASE}/folders", headers=headers, json={"name": name, "parent_folder_id": parent})
    assert r.status_code == 201, r.text
    return r.json()


def upload(client, headers, folder_id, *files):
    return client.post(f"{BASE}/folders/{folder_id}/files", headers=headers,
                       files=[("files", (name, data, mime)) for name, data, mime in files])


def test_permissions_by_role(world, client):
    admin = world.auth("admin1")
    folder = make_folder(client, admin, "Policies")
    # Workers have no document access; supervisors may view but not edit; auditors view only.
    assert client.get(f"{BASE}/folders", headers=world.auth("worker1")).status_code == 403
    assert client.get(f"{BASE}/folders", headers=world.auth("super1")).status_code == 200
    assert client.post(f"{BASE}/folders", headers=world.auth("super1"), json={"name": "X"}).status_code == 403
    assert upload(client, world.auth("auditor1"), folder["id"], ("a.pdf", PDF, "application/pdf")).status_code == 403
    doc = upload(client, admin, folder["id"], ("a.pdf", PDF, "application/pdf")).json()["saved"][0]
    assert client.get(f"{BASE}/files/{doc['id']}/download", headers=world.auth("auditor1")).status_code == 200
    assert client.get(f"{BASE}/files/{doc['id']}/download", headers=world.auth("worker1")).status_code == 403
    assert client.get(f"{BASE}/files/{doc['id']}/download").status_code == 401
    assert client.delete(f"{BASE}/files/{doc['id']}", headers=world.auth("hseq1")).status_code == 403  # no delete right
    assert client.post(f"{BASE}/files/{doc['id']}/archive", headers=world.auth("hseq1")).status_code == 200


def test_folder_tree_rules(world, client):
    admin = world.auth("admin1")
    a = make_folder(client, admin, "  Site   Plans ")
    assert a["name"] == "Site Plans" and a["path"] == []
    assert client.post(f"{BASE}/folders", headers=admin, json={"name": "site plans"}).status_code == 409
    assert client.post(f"{BASE}/folders", headers=admin, json={"name": "X", "parent_folder_id": "missing"}).status_code == 400
    b = make_folder(client, admin, "Northbank", a["id"])
    c = make_folder(client, admin, "Stage 2", b["id"])
    assert [p["name"] for p in client.get(f"{BASE}/folders/{c['id']}", headers=admin).json()["path"]] == ["Site Plans", "Northbank"]
    assert [f["name"] for f in client.get(f"{BASE}/folders", headers=admin).json()] == ["Site Plans"]
    assert [f["name"] for f in client.get(f"{BASE}/folders/{a['id']}/subfolders", headers=admin).json()] == ["Northbank"]
    flat = client.get(f"{BASE}/folders/all", headers=admin).json()
    assert [f["full_path"] for f in flat] == ["Site Plans", "Site Plans / Northbank", "Site Plans / Northbank / Stage 2"]
    # No cycles, no self-parenting.
    assert client.patch(f"{BASE}/folders/{a['id']}", headers=admin, json={"parent_folder_id": a["id"]}).status_code == 400
    assert client.patch(f"{BASE}/folders/{a['id']}", headers=admin, json={"parent_folder_id": c["id"]}).status_code == 400
    assert client.patch(f"{BASE}/folders/{a['id']}", headers=admin, json={}).status_code == 400
    # Move Stage 2 to the top level and rename it.
    moved = client.patch(f"{BASE}/folders/{c['id']}", headers=admin, json={"parent_folder_id": None, "name": "Archive plans"}).json()
    assert moved["parent_folder_id"] is None and moved["path"] == []
    # A folder holding anything cannot be deleted; an empty one can.
    assert client.delete(f"{BASE}/folders/{a['id']}", headers=admin).status_code == 409
    assert client.delete(f"{BASE}/folders/{c['id']}", headers=admin).json() == {"ok": True}
    assert client.get(f"{BASE}/folders/{c['id']}", headers=admin).status_code == 404


def test_upload_download_roundtrip_and_rejections(world, client, db):
    admin = world.auth("admin1")
    folder = make_folder(client, admin, "Registers")
    r = upload(client, admin, folder["id"],
               ("Plan A.pdf", PDF, "application/pdf"),
               ("photo.png", PNG, "image/png"),
               ("run.exe", b"MZ....", "application/octet-stream"),
               ("page.html", b"<html></html>", "text/html"),
               ("fake.pdf", b"not really a pdf", "application/pdf"),
               ("empty.pdf", b"", "application/pdf"))
    assert r.status_code == 200
    body = r.json()
    assert [d["filename"] for d in body["saved"]] == ["Plan A.pdf", "photo.png"]
    assert {x["filename"] for x in body["rejected"]} == {"run.exe", "page.html", "fake.pdf", "empty.pdf"}
    doc = body["saved"][0]
    assert doc["sha256"] == hashlib.sha256(PDF).hexdigest() and doc["size_bytes"] == len(PDF) and doc["previewable"] is True
    # Bytes out equal bytes in. PDFs open inline unless a download is asked for.
    got = client.get(f"{BASE}/files/{doc['id']}/download", headers=admin)
    assert got.content == PDF and got.headers["content-disposition"].startswith("inline")
    forced = client.get(f"{BASE}/files/{doc['id']}/download?download=1", headers=admin)
    assert forced.headers["content-disposition"].startswith("attachment") and "Plan%20A.pdf" in forced.headers["content-disposition"]
    # A retry of the same upload does not create a duplicate.
    again = upload(client, admin, folder["id"], ("Plan A.pdf", PDF, "application/pdf")).json()
    assert again["saved"] == [] and "already" in again["rejected"][0]["reason"]
    listing = client.get(f"{BASE}/folders/{folder['id']}/files", headers=admin).json()
    assert listing["total"] == 2 and listing["items"][0]["uploaded_by_name"] == "Alex Rowe"
    assert db.scalar(select(Document.filename).where(Document.id == doc["id"])) == "Plan A.pdf"


def test_rename_move_expiry_tags_archive_delete(world, client):
    admin = world.auth("admin1")
    one, two = make_folder(client, admin, "One"), make_folder(client, admin, "Two")
    doc = upload(client, admin, one["id"], ("Insurance.pdf", PDF, "application/pdf")).json()["saved"][0]
    url = f"{BASE}/files/{doc['id']}"
    assert client.patch(url, headers=admin, json={}).status_code == 400
    assert client.patch(url, headers=admin, json={"filename": "Insurance.exe"}).status_code == 400
    assert client.patch(url, headers=admin, json={"expiry_date": "31/12/2026"}).status_code == 400
    assert client.patch(url, headers=admin, json={"folder_id": "missing"}).status_code == 404
    r = client.patch(url, headers=admin, json={"filename": "Public liability.pdf", "expiry_date": "2020-01-31",
                                               "folder_id": two["id"], "tags": ["Insurance", " insurance ", "2026"]}).json()
    assert r["filename"] == "Public liability.pdf" and r["folder_id"] == two["id"]
    assert r["expiry_date"] == "2020-01-31" and r["expired"] is True and r["tags"] == ["Insurance", "2026"]
    assert client.patch(url, headers=admin, json={"clear_expiry": True}).json()["expiry_date"] is None
    assert client.get(f"{BASE}/folders/{one['id']}/files", headers=admin).json()["total"] == 0
    # Search by name and by tag, scoped to a folder or the whole library.
    assert client.get(f"{BASE}/search?q=liability", headers=admin).json()["results"][0]["folder_path"] == "Two"
    assert client.get(f"{BASE}/search?q=2026", headers=admin).json()["count"] == 1
    assert client.get(f"{BASE}/search?q=liability&folder_id={one['id']}", headers=admin).json()["count"] == 0
    # Archive hides by default but keeps the file readable and restorable.
    assert client.post(f"{url}/archive", headers=admin).json()["archived_at"]
    assert client.get(f"{BASE}/folders/{two['id']}/files", headers=admin).json()["total"] == 0
    assert client.get(f"{BASE}/folders/{two['id']}/files?include_archived=true", headers=admin).json()["total"] == 1
    assert client.get(f"{url}/download", headers=admin).status_code == 200
    assert client.delete(f"{BASE}/folders/{two['id']}", headers=admin).status_code == 409
    assert client.post(f"{url}/restore", headers=admin).json()["archived_at"] is None
    assert client.delete(url, headers=admin).status_code == 204
    assert client.get(f"{url}/download", headers=admin).status_code == 404
    counts = client.get(f"{BASE}/counts", headers=admin).json()
    assert counts["files_active"] == 0 and counts["files_deleted"] == 1 and counts["folders_active"] == 2


def test_revisions_and_missing_file_repair(world, client, db):
    admin = world.auth("admin1")
    folder = make_folder(client, admin, "SWMS originals")
    doc = upload(client, admin, folder["id"], ("Trenching.pdf", PDF, "application/pdf")).json()["saved"][0]
    url = f"{BASE}/files/{doc['id']}"
    assert client.post(f"{url}/replace-binary", headers=admin, files={"file": ("new.png", PNG, "image/png")}).status_code == 400
    r = client.post(f"{url}/replace-binary", headers=admin, files={"file": ("Trenching v2.pdf", PDF2, "application/pdf")})
    assert r.status_code == 200 and r.json()["revision"] == 2 and r.json()["filename"] == "Trenching.pdf"
    detail = client.get(url, headers=admin).json()
    assert [rev["revision"] for rev in detail["revisions"]] == [2, 1]
    # The earlier revision's bytes are preserved.
    assert client.get(f"{url}/download", headers=admin).content == PDF2
    assert client.get(f"{url}/download?revision=1", headers=admin).content == PDF
    assert client.get(f"{url}/download?revision=9", headers=admin).status_code == 404
    # Present bytes cannot be declared lost.
    assert client.post(f"{url}/mark-gone", headers=admin, json={"reason": "testing"}).status_code == 400
    stored = db.scalars(select(StoredFile).where(StoredFile.sha256 == hashlib.sha256(PDF2).hexdigest())).one()
    _path_for(stored.org_id, stored.sha256).unlink()
    assert client.get(f"{url}/download", headers=admin).status_code == 410  # never an empty file
    assert client.get(url, headers=admin).json()["missing_binary"] is True
    assert client.get(f"{BASE}/counts", headers=admin).json()["files_missing_binary"] == 1
    assert client.get(f"{BASE}/counts/missing-binary", headers=admin).json()["files"][0]["id"] == doc["id"]
    gone = client.post(f"{url}/mark-gone", headers=admin, json={"reason": "Lost in old storage move"}).json()
    assert gone["gone_reason"] == "Lost in old storage move" and gone["missing_binary"] is False
    assert client.get(f"{BASE}/counts", headers=admin).json()["files_missing_binary"] == 0
    # Uploading a replacement repairs it as a new revision.
    fixed = client.post(f"{url}/replace-binary", headers=admin, files={"file": ("Trenching.pdf", PDF, "application/pdf")}).json()
    assert fixed["revision"] == 3 and fixed["gone_at"] is None and fixed["missing_binary"] is False
    assert client.get(f"{url}/download", headers=admin).content == PDF


def test_documents_are_invisible_across_organisations(world, client):
    admin1, admin2 = world.auth("admin1"), world.auth("admin2")
    folder = make_folder(client, admin1, "Private")
    doc = upload(client, admin1, folder["id"], ("secret.pdf", PDF, "application/pdf")).json()["saved"][0]
    other = make_folder(client, admin2, "Private")  # same name is fine in another organisation
    for method, path, kwargs in [
        ("get", f"{BASE}/folders/{folder['id']}", {}),
        ("get", f"{BASE}/folders/{folder['id']}/files", {}),
        ("patch", f"{BASE}/folders/{folder['id']}", {"json": {"name": "Hacked"}}),
        ("delete", f"{BASE}/folders/{folder['id']}", {}),
        ("get", f"{BASE}/files/{doc['id']}", {}),
        ("get", f"{BASE}/files/{doc['id']}/download", {}),
        ("patch", f"{BASE}/files/{doc['id']}", {"json": {"filename": "x.pdf"}}),
        ("post", f"{BASE}/files/{doc['id']}/archive", {}),
        ("delete", f"{BASE}/files/{doc['id']}", {}),
        ("post", f"{BASE}/files/{doc['id']}/replace-binary", {"files": {"file": ("s.pdf", PDF, "application/pdf")}}),
    ]:
        assert client.request(method, path, headers=admin2, **kwargs).status_code == 404, (method, path)
    assert upload(client, admin2, folder["id"], ("x.pdf", PDF, "application/pdf")).status_code == 404
    # A file cannot be moved into another organisation's folder.
    assert client.patch(f"{BASE}/files/{doc['id']}", headers=admin1, json={"folder_id": other["id"]}).status_code == 404
    assert client.get(f"{BASE}/search?q=secret", headers=admin2).json()["count"] == 0
    assert client.get(f"{BASE}/counts", headers=admin2).json()["files_active"] == 0
