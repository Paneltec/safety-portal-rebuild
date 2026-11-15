from sqlalchemy import select

from app.models import FormSubmission

BASE = "/api/forms"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
FIELDS = [
    {"id": "rego", "type": "text", "label": "Rego", "required": True},
    {"id": "tyres", "type": "compliance", "label": "Tyres", "required": True},
    {"id": "photo", "type": "photo", "label": "Defect photo"},
    {"id": "sign", "type": "signature", "label": "Operator signature", "required": True},
]


def publish(client, headers, name="Daily pre-start", fields=FIELDS, category="pre_start"):
    t = client.post(f"{BASE}/templates", headers=headers, json={"name": name, "category": category, "fields": fields})
    assert t.status_code == 201, t.text
    p = client.post(f"{BASE}/templates/{t.json()['id']}/publish", headers=headers)
    assert p.status_code == 200, p.text
    return p.json()


def answers(**values):
    return [{"id": k, "value": v} for k, v in values.items()]


def add_file(client, headers, sub_id, field_id, name="p.png", data=PNG, mime="image/png"):
    return client.post(f"{BASE}/submissions/{sub_id}/files", headers=headers, data={"field_id": field_id}, files={"file": (name, data, mime)})


def test_template_management_needs_the_use_right_and_validates(world, client):
    admin, worker, supervisor = world.auth("admin1"), world.auth("worker1"), world.auth("super1")
    body = {"name": "Toolbox talk", "fields": [{"type": "text", "label": "Topic"}]}
    assert client.post(f"{BASE}/templates", headers=worker, json=body).status_code == 403
    assert client.post(f"{BASE}/templates", headers=supervisor, json=body).status_code == 403
    assert client.post(f"{BASE}/templates", headers=world.auth("hseq1"), json=body).status_code == 201
    assert client.post(f"{BASE}/templates", headers=admin, json={"name": "X", "category": "nope"}).status_code == 422
    assert client.post(f"{BASE}/templates", headers=admin, json={"name": "X", "fields": [{"type": "hologram", "label": "H"}]}).status_code == 422
    empty = client.post(f"{BASE}/templates", headers=admin, json={"name": "Empty"}).json()
    assert empty["status"] == "draft" and empty["published_version"] == 0
    assert client.post(f"{BASE}/templates/{empty['id']}/publish", headers=admin).status_code == 400
    types = client.get(f"{BASE}/field-types", headers=worker).json()
    assert len(types["field_types"]) == 20 and {"key": "pre_start", "label": "Daily pre-start"} in types["categories"]


def test_drafts_are_hidden_from_people_filling_in_forms(world, client):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    draft = client.post(f"{BASE}/templates", headers=admin, json={"name": "Draft form", "fields": FIELDS}).json()
    live = publish(client, admin)
    assert [t["name"] for t in client.get(f"{BASE}/templates", headers=worker).json()["templates"]] == ["Daily pre-start"]
    assert client.get(f"{BASE}/templates", headers=admin).json()["count"] == 2
    assert client.get(f"{BASE}/templates/{draft['id']}", headers=worker).status_code == 404
    assert client.get(f"{BASE}/templates/{draft['id']}/access-check", headers=worker).json()["reason"] == "not_published"
    assert client.get(f"{BASE}/templates/{live['id']}/access-check", headers=worker).json()["allowed"] is True
    assert client.get(f"{BASE}/templates/{live['id']}/access-check", headers=world.auth("auditor1")).json()["reason"] == "no_permission"
    assert client.post(f"{BASE}/templates/{draft['id']}/submissions", headers=worker, json={"fields": []}).status_code == 409
    # Unpublished edits to a live form are not shown to workers.
    client.patch(f"{BASE}/templates/{live['id']}", headers=admin, json={"fields": FIELDS + [{"id": "extra", "type": "text", "label": "Extra"}]})
    assert len(client.get(f"{BASE}/templates/{live['id']}", headers=worker).json()["fields"]) == 4
    assert client.get(f"{BASE}/templates/{live['id']}", headers=admin).json()["has_unpublished_changes"] is True


def test_staged_submission_with_files_then_submit(world, client, db):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    template = publish(client, admin)
    url = f"{BASE}/templates/{template['id']}/submissions"
    # A one-shot submit cannot succeed while a required signature is missing.
    r = client.post(url, headers=worker, json={"fields": answers(rego="PT-031", tyres={"result": "pass"})})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["field_id"] == "sign"
    assert db.scalar(select(FormSubmission.id)) is None  # nothing half-saved

    draft = client.post(url, headers=worker, json={"status": "draft", "fields": answers(rego="PT-031")}).json()
    assert draft["status"] == "draft" and draft["template_version"] == 1
    sid = draft["id"]
    # Files: only image types for photos and signatures; only on file fields; only by the author.
    assert add_file(client, worker, sid, "rego").status_code == 400
    assert add_file(client, worker, sid, "photo", "x.pdf", b"%PDF-1.4 x", "application/pdf").status_code == 415
    assert add_file(client, admin, sid, "photo").status_code == 403
    photo = add_file(client, worker, sid, "photo").json()
    sig = add_file(client, worker, sid, "sign", "sig.png").json()
    # A failed check needs a note; a file id from another field is refused.
    bad = client.patch(f"{BASE}/submissions/{sid}", headers=worker, json={"fields": answers(rego="PT-031", tyres={"result": "fail"})})
    assert bad.status_code == 422
    bad = client.post(f"{BASE}/submissions/{sid}/submit", headers=worker, json={"fields": answers(
        rego="PT-031", tyres={"result": "pass"}, sign={"file_id": photo["id"], "signed_name": "Tom"})})
    assert bad.status_code == 422
    saved = client.patch(f"{BASE}/submissions/{sid}", headers=worker, json={"expected_revision": 1, "fields": answers(
        rego="PT-031", tyres={"result": "fail", "note": "Nail in rear left"}, photo=[photo["id"]],
        sign={"file_id": sig["id"], "signed_name": "Tom Okafor"})})
    assert saved.status_code == 200 and saved.json()["revision"] == 2
    assert client.patch(f"{BASE}/submissions/{sid}", headers=worker, json={"expected_revision": 1, "fields": []}).status_code == 409
    done = client.post(f"{BASE}/submissions/{sid}/submit", headers=worker)
    assert done.status_code == 200 and done.json()["status"] == "submitted" and done.json()["submitted_at"]
    assert done.json()["values"]["tyres"] == {"result": "fail", "note": "Nail in rear left"}  # a fail is never turned into a pass
    # Submitted records are locked; a repeated submit is harmless.
    assert client.post(f"{BASE}/submissions/{sid}/submit", headers=worker).status_code == 200
    assert client.patch(f"{BASE}/submissions/{sid}", headers=worker, json={"fields": []}).status_code == 409
    assert add_file(client, worker, sid, "photo").status_code == 409
    assert client.delete(f"{BASE}/submissions/{sid}/files/{photo['id']}", headers=worker).status_code == 409
    # The stored photo comes back byte for byte, through the submission only.
    got = client.get(f"{BASE}/submissions/{sid}/files/{photo['id']}", headers=worker)
    assert got.status_code == 200 and got.content == PNG
    assert client.get(f"{BASE}/submissions/{sid}/files/{photo['id']}").status_code == 401
    detail = client.get(f"{BASE}/submissions/{sid}", headers=admin).json()
    assert {f["kind"] for f in detail["files"]} == {"photo", "signature"} and detail["submitted_by_name"] == "Tom Okafor"


def test_later_template_edits_do_not_change_old_submissions(world, client):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    template = publish(client, admin, fields=[{"id": "q1", "type": "text", "label": "Original question", "required": True}])
    first = client.post(f"{BASE}/templates/{template['id']}/submissions", headers=worker, json={"fields": answers(q1="First answer")}).json()
    client.patch(f"{BASE}/templates/{template['id']}", headers=admin, json={"name": "Renamed form", "fields": [
        {"id": "q1", "type": "text", "label": "Reworded question", "required": True},
        {"id": "q2", "type": "number", "label": "New number", "required": True}]})
    v2 = client.post(f"{BASE}/templates/{template['id']}/publish", headers=admin).json()
    assert v2["published_version"] == 2
    # Publishing again with no changes does not create a third version.
    assert client.post(f"{BASE}/templates/{template['id']}/publish", headers=admin).json()["published_version"] == 2
    old = client.get(f"{BASE}/submissions/{first['id']}", headers=worker).json()
    assert old["template_version"] == 1 and old["template_name"] == "Daily pre-start"
    assert [f["label"] for f in old["schema"]] == ["Original question"] and old["values"] == {"q1": "First answer"}
    # New submissions are checked against the new version.
    assert client.post(f"{BASE}/templates/{template['id']}/submissions", headers=worker, json={"fields": answers(q1="x")}).status_code == 422
    new = client.post(f"{BASE}/templates/{template['id']}/submissions", headers=worker, json={"fields": answers(q1="x", q2=3)}).json()
    assert new["template_version"] == 2
    # Deleting the template keeps its submissions readable.
    assert client.delete(f"{BASE}/templates/{template['id']}", headers=world.auth("hseq1")).status_code == 403
    assert client.delete(f"{BASE}/templates/{template['id']}", headers=admin).json() == {"ok": True, "submissions_kept": 2}
    assert client.get(f"{BASE}/submissions/{first['id']}", headers=worker).json()["values"] == {"q1": "First answer"}
    assert client.get(f"{BASE}/templates/{template['id']}/submissions", headers=admin).json()["total"] == 2


def test_offline_retry_does_not_duplicate(world, client, db):
    admin, worker = world.auth("admin1"), world.auth("worker1")
    template = publish(client, admin, fields=[{"id": "q1", "type": "text", "label": "Q", "required": True}])
    url = f"{BASE}/templates/{template['id']}/submissions"
    body = {"client_submission_id": "phone-abc-0001", "fields": answers(q1="Sent once"), "launched_via": "qr", "source_asset_id": "asset-7"}
    first = client.post(url, headers=worker, json=body)
    second = client.post(url, headers=worker, json=body)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["duplicate"] is False and second.json()["duplicate"] is True
    assert first.json()["id"] == second.json()["id"] and second.json()["launched_via"] == "qr"
    assert db.query(FormSubmission).count() == 1
    # The same client id from a different person is a different submission.
    other = client.post(url, headers=world.auth("super1"), json=body)
    assert other.json()["duplicate"] is False and db.query(FormSubmission).count() == 2


def test_own_versus_team_visibility(world, client):
    admin = world.auth("admin1")
    template = publish(client, admin, fields=[{"id": "q1", "type": "text", "label": "Q"}])
    url = f"{BASE}/templates/{template['id']}/submissions"
    mine = client.post(url, headers=world.auth("worker1"), json={"fields": answers(q1="worker")}).json()
    theirs = client.post(url, headers=world.auth("super1"), json={"fields": answers(q1="supervisor")}).json()
    draft = client.post(url, headers=world.auth("super1"), json={"status": "draft", "fields": []}).json()
    worker_list = client.get(url, headers=world.auth("worker1")).json()
    assert worker_list["scope"] == "own" and [s["id"] for s in worker_list["items"]] == [mine["id"]]
    assert client.get(f"{BASE}/submissions/{theirs['id']}", headers=world.auth("worker1")).status_code == 404
    team = client.get(url, headers=admin).json()
    # Team view shows everyone's submitted forms, but not other people's unfinished drafts.
    assert team["scope"] == "team" and {s["id"] for s in team["items"]} == {mine["id"], theirs["id"]}
    assert client.get(f"{url}?status=draft", headers=world.auth("super1")).json()["total"] == 1
    # Own draft can be discarded; a submitted record needs the delete right.
    assert client.delete(f"{BASE}/submissions/{draft['id']}", headers=world.auth("super1")).json() == {"ok": True}
    assert client.delete(f"{BASE}/submissions/{mine['id']}", headers=world.auth("worker1")).status_code == 403
    assert client.delete(f"{BASE}/submissions/{mine['id']}", headers=admin).json() == {"ok": True}
    assert client.get(f"{BASE}/submissions/{mine['id']}", headers=admin).status_code == 404


def test_export_import_definitions_only(world, client):
    admin1, admin2 = world.auth("admin1"), world.auth("admin2")
    template = publish(client, admin1)
    client.post(f"{BASE}/templates/{template['id']}/submissions", headers=world.auth("worker1"), json={"status": "draft", "fields": answers(rego="SECRET-ANSWER")})
    exported = client.get(f"{BASE}/templates/export", headers=admin1)
    assert exported.status_code == 200 and "SECRET-ANSWER" not in exported.text
    assert client.get(f"{BASE}/templates/export", headers=world.auth("worker1")).status_code == 403
    payload = exported.json()
    payload["templates"].append({"name": "Broken", "fields": [{"type": "nope", "label": "X"}]})
    result = client.post(f"{BASE}/templates/import", headers=admin2, json=payload).json()
    assert [c["name"] for c in result["created"]] == ["Daily pre-start"] and result["errors"][0]["name"] == "Broken"
    imported = client.get(f"{BASE}/templates/{result['created'][0]['id']}", headers=admin2).json()
    assert imported["status"] == "draft" and [f["id"] for f in imported["fields"]] == ["rego", "tyres", "photo", "sign"]
    # Importing again into the same organisation does not overwrite the existing form.
    again = client.post(f"{BASE}/templates/import", headers=admin2, json=exported.json()).json()
    assert again["created"][0]["name"] == "Daily pre-start (imported)"


def test_forms_are_invisible_across_organisations(world, client):
    admin1, admin2 = world.auth("admin1"), world.auth("admin2")
    template = publish(client, admin1, fields=[{"id": "q1", "type": "text", "label": "Q"}])
    sub = client.post(f"{BASE}/templates/{template['id']}/submissions", headers=world.auth("worker1"), json={"fields": answers(q1="private")}).json()
    for method, path, body in [
        ("get", f"{BASE}/templates/{template['id']}", None),
        ("patch", f"{BASE}/templates/{template['id']}", {"name": "Hacked"}),
        ("post", f"{BASE}/templates/{template['id']}/publish", None),
        ("delete", f"{BASE}/templates/{template['id']}", None),
        ("get", f"{BASE}/templates/{template['id']}/access-check", None),
        ("get", f"{BASE}/templates/{template['id']}/submissions", None),
        ("post", f"{BASE}/templates/{template['id']}/submissions", {"fields": []}),
        ("get", f"{BASE}/submissions/{sub['id']}", None),
        ("delete", f"{BASE}/submissions/{sub['id']}", None),
    ]:
        assert client.request(method, path, headers=admin2, json=body).status_code == 404, (method, path)
    assert client.get(f"{BASE}/templates", headers=admin2).json()["count"] == 0


def test_pickers_are_scoped_and_honest_about_what_is_not_built(world, client):
    worker = world.auth("worker1")
    people = client.get(f"{BASE}/pickers/workers?q=alex", headers=worker).json()
    assert people["available"] is True and [p["label"] for p in people["items"]] == ["Alex Rowe"]
    assert people["items"][0]["id"] == world.id("admin1")  # the other organisation's Alex Rowe is not offered
    for path in ("pickers/sites", "pickers/jobs", "fleet/vehicles"):
        result = client.get(f"{BASE}/{path}", headers=worker).json()
        assert result["available"] is False and result["items"] == [] and "built" in result["message"]
    assert client.get(f"{BASE}/pickers/workers").status_code == 401


def test_submission_pdf_matches_the_saved_record(world, client):
    from io import BytesIO

    from PIL import Image as PILImage

    admin, worker = world.auth("admin1"), world.auth("worker1")
    def picture(colour):
        buf = BytesIO()
        PILImage.new("RGB", (120, 60), colour).save(buf, "PNG")
        return buf.getvalue()

    real_png, photo_png, sig_png = picture((194, 65, 12)), picture((27, 75, 143)), picture((11, 93, 74))
    client.patch("/api/org", headers=admin, json={"suburb": "Launceston", "abn": "12 345 678 901", "public_portal_url": "https://portal.one.example"})
    client.post("/api/org/logo/upload", headers=admin, files={"file": ("logo.png", real_png, "image/png")})
    template = publish(client, admin, fields=FIELDS + [
        {"id": "notes", "type": "textarea", "label": "Notes"},
        {"id": "h", "type": "section_header", "label": "Follow up"},
        {"id": "acts", "type": "corrective_actions", "label": "Actions"}])
    sid = client.post(f"{BASE}/templates/{template['id']}/submissions", headers=worker, json={"status": "draft", "fields": []}).json()["id"]
    photo = add_file(client, worker, sid, "photo", "tyre.png", photo_png).json()
    sig = add_file(client, worker, sid, "sign", "sig.png", sig_png).json()
    long_text = "A long paragraph about the site conditions. " * 400  # runs over several pages
    done = client.post(f"{BASE}/submissions/{sid}/submit", headers=worker, json={"fields": answers(
        rego="PT-031", tyres={"result": "fail", "note": "Nail in rear left <b>tyre</b> & rim"}, photo=[photo["id"]],
        sign={"file_id": sig["id"], "signed_name": "Tom Okafor"}, notes=long_text,
        acts=[{"description": "Replace tyre", "responsible": "Workshop", "due_date": "2026-11-16"}])})
    assert done.status_code == 200, done.text

    assert client.get(f"{BASE}/submissions/{sid}/pdf").status_code == 401
    assert client.get(f"{BASE}/submissions/{sid}/pdf", headers=world.auth("admin2")).status_code == 404
    assert client.get(f"{BASE}/submissions/{sid}/pdf", headers=world.auth("super1")).status_code == 200  # team view
    r = client.get(f"{BASE}/submissions/{sid}/pdf", headers=worker)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF-")
    assert r.headers["content-disposition"].startswith("inline")

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(r.content))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) >= 3 and f"Page 1 of {len(reader.pages)}" in text
    for expected in ("Org One", "ABN 12 345 678 901", "Launceston", "Daily pre-start", "Submitted", "Tom Okafor", "PT-031",
                     "FAIL", "Nail in rear left <b>tyre</b> & rim", "Signed by Tom Okafor", "Follow up", "Replace tyre",
                     "due 16/11/2026", "https://portal.one.example"):
        assert expected in text, expected
    assert "Emergent" not in text and "preview" not in text.lower()
    # Both the photo and the signature are embedded, plus the logo on the first page.
    assert sum(len(page.images) for page in reader.pages) >= 3

    # A photo whose bytes are lost is declared in the PDF rather than breaking it.
    from sqlalchemy import select as sa_select

    from app.db import get_engine
    from app.files import _path_for
    from app.models import StoredFile
    from sqlalchemy.orm import Session as SASession

    with SASession(get_engine()) as s:
        stored = s.scalars(sa_select(StoredFile).where(StoredFile.original_name == "tyre.png")).one()
        _path_for(stored.org_id, stored.sha256).unlink()
    again = client.get(f"{BASE}/submissions/{sid}/pdf?download=1", headers=worker)
    assert again.status_code == 200 and again.headers["content-disposition"].startswith("attachment")
    assert "content missing from storage" in "\n".join(p.extract_text() for p in PdfReader(BytesIO(again.content)).pages)


def test_assignments_limit_who_sees_and_fills_a_form(world, client):
    admin, worker, supervisor = world.auth("admin1"), world.auth("worker1"), world.auth("super1")
    template = publish(client, admin, fields=[{"id": "q1", "type": "text", "label": "Q"}])
    tid = template["id"]
    url = f"{BASE}/templates/{tid}/assignments"
    # With no rules a published form is open to everyone who can fill in forms.
    assert client.get(url, headers=admin).json()["open_to_everyone"] is True
    assert client.get(f"{BASE}/templates", headers=worker).json()["count"] == 1
    assert client.put(url, headers=worker, json={"rules": []}).status_code == 403
    assert client.put(url, headers=admin, json={"rules": [{"kind": "planet", "target_id": "mars"}]}).status_code == 422
    # Targets must belong to this organisation.
    assert client.put(url, headers=admin, json={"rules": [{"kind": "user", "target_id": world.id("worker2")}]}).status_code == 404
    assert client.put(url, headers=admin, json={"rules": [{"kind": "role", "target_id": "nope"}]}).status_code == 404

    r = client.put(url, headers=admin, json={"rules": [{"kind": "role", "target_id": "supervisor"}]})
    assert r.status_code == 200 and r.json()["rules"] == [{"kind": "role", "target_id": "supervisor", "label": "Supervisor"}]
    # The worker no longer sees it and cannot use it, even with the id.
    assert client.get(f"{BASE}/templates", headers=worker).json()["count"] == 0
    assert client.get(f"{BASE}/templates/{tid}", headers=worker).status_code == 404
    assert client.get(f"{BASE}/templates/{tid}/access-check", headers=worker).json()["reason"] == "not_assigned"
    assert client.post(f"{BASE}/templates/{tid}/submissions", headers=worker, json={"fields": []}).status_code == 403
    assert client.post(f"{BASE}/templates/{tid}/submissions", headers=supervisor, json={"fields": []}).status_code == 201
    # Managers always keep access to manage and test the form.
    assert client.get(f"{BASE}/templates/{tid}", headers=admin).status_code == 200

    # Naming the worker and their company both work, and the preview explains why.
    companies = client.put("/api/org/companies", headers=admin, json={"companies": [{"name": "Paneltec Civil", "code": "PAN"}]}).json()["companies"]
    client.patch(f"/api/users/{world.id('worker1')}", headers=admin, json={"company_id": companies[0]["id"]})
    client.put(url, headers=admin, json={"rules": [{"kind": "user", "target_id": world.id("worker1")}, {"kind": "company", "target_id": companies[0]["id"]}]})
    assert client.get(f"{BASE}/templates", headers=worker).json()["count"] == 1
    preview = client.get(f"{BASE}/assignments/effective?user_id={world.id('worker1')}", headers=admin).json()
    assert preview["forms"][0]["visible"] is True
    assert sorted(preview["forms"][0]["reasons"]) == ["Assigned to Paneltec Civil", "Assigned to this person by name"]
    hidden = client.get(f"{BASE}/assignments/effective?user_id={world.id('super1')}", headers=admin).json()
    assert hidden["forms"][0]["visible"] is False and hidden["forms"][0]["reasons"] == []
    auditor = client.get(f"{BASE}/assignments/effective?user_id={world.id('auditor1')}", headers=admin).json()
    assert auditor["can_fill_in_forms"] is False and auditor["forms"][0]["blocked_by"]
    assert client.get(f"{BASE}/assignments/effective?user_id={world.id('worker2')}", headers=admin).status_code == 404
    assert client.get(f"{BASE}/assignments/effective?user_id={world.id('worker1')}", headers=worker).status_code == 403
    # Clearing the rules opens the form again.
    assert client.put(url, headers=admin, json={"rules": []}).json()["open_to_everyone"] is True
    assert client.get(f"{BASE}/templates", headers=supervisor).json()["count"] == 1
