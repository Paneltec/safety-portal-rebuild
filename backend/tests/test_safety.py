from io import BytesIO

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def answers(**values):
    return [{"id": k, "value": v} for k, v in values.items()]


HAZARD = dict(title="Open trench unfenced", hazard_type="Hazard", description="Trench beside footpath has no barrier", severity="High")
PRESTART = dict(date="2026-11-16", crew_lead="Jess Marlow", work_summary="Lay 40 m of pipe", hazards_discussed="Traffic, trench collapse",
                sign_ons=[{"name": "Tom Okafor", "role": "Operator", "time": "06:55"}])
SWMS = dict(title="Trenching and excavation", job_description="Excavate and lay sewer main", prepared_by="Sam Keating",
            activity_analysis=[{"job_step": "Excavate", "hazards": "Collapse", "initial_risk": "High", "controls": "Shoring", "residual_risk": "Low", "responsible": "Supervisor"}])


def test_every_register_serves_its_built_in_form(world, client):
    admin = world.auth("admin1")
    for prefix, workflow in [("swms", "review"), ("pre-starts", "review"), ("site-diary", "review"), ("hazards", "tracking"),
                             ("incidents", "tracking"), ("inspections", "review"), ("risk-assessments", "review"), ("ssra", "review")]:
        body = client.get(f"/api/{prefix}/schema", headers=admin).json()
        assert body["workflow"] == workflow and len(body["schema"]) >= 5, prefix
        assert client.get(f"/api/{prefix}/schema").status_code == 401
        assert client.get(f"/api/{prefix}", headers=admin).json() == {"items": [], "total": 0, "limit": 50, "offset": 0, "scope": "team"}
    assert "likelihood" in client.get("/api/risk-assessments/schema", headers=admin).json()["risk_rule"]


def test_hazard_tracking_scope_history_and_closing(world, client):
    worker, supervisor, admin = world.auth("worker1"), world.auth("super1"), world.auth("admin1")
    assert client.post("/api/hazards", headers=worker, json={"fields": answers(title="No detail")}).status_code == 422
    assert client.post("/api/hazards", headers=world.auth("auditor1"), json={"fields": answers(**HAZARD)}).status_code == 403
    mine = client.post("/api/hazards", headers=worker, json={"fields": answers(**HAZARD)})
    assert mine.status_code == 201 and mine.json()["status"] == "open" and mine.json()["title"] == "Open trench unfenced"
    hid = mine.json()["id"]
    theirs = client.post("/api/hazards", headers=supervisor, json={"fields": answers(**{**HAZARD, "title": "Loose plate", "hazard_type": "Near miss"})}).json()

    # A worker sees only their own reports; a supervisor (team view) sees everyone's.
    own = client.get("/api/hazards", headers=worker).json()
    assert own["scope"] == "own" and [r["id"] for r in own["items"]] == [hid]
    assert client.get(f"/api/hazards/{theirs['id']}", headers=worker).status_code == 404
    assert client.get("/api/hazards", headers=supervisor).json()["total"] == 2
    assert client.get("/api/hazards?category=Near miss", headers=supervisor).json()["total"] == 1
    assert client.get("/api/hazards?q=trench", headers=supervisor).json()["total"] == 1
    counts = client.get("/api/hazards/category-counts", headers=supervisor).json()
    assert counts == {"total": 2, "by_category": {"Hazard": 1, "Near miss": 1}, "by_status": {"open": 2}}
    assert client.get("/api/hazards/category-counts", headers=worker).json()["total"] == 1

    # Editing keeps the earlier answers in the history.
    edited = client.patch(f"/api/hazards/{hid}", headers=worker, json={"fields": answers(**{**HAZARD, "severity": "Critical"})}).json()
    assert edited["values"]["severity"] == "Critical" and edited["revision"] == 2
    assert client.patch(f"/api/hazards/{hid}", headers=worker, json={"expected_revision": 1, "fields": answers(**HAZARD)}).status_code == 409
    # Closing needs a resolution note and locks the record; the original report is untouched.
    assert client.post(f"/api/hazards/{hid}/status", headers=worker, json={"status": "closed"}).status_code == 422
    assert client.post(f"/api/hazards/{hid}/status", headers=worker, json={"status": "resolved"}).status_code == 422
    client.post(f"/api/hazards/{hid}/status", headers=supervisor, json={"status": "in_progress"})
    closed = client.post(f"/api/hazards/{hid}/status", headers=supervisor, json={"status": "closed", "note": "Barrier fence installed"}).json()
    assert closed["status"] == "closed" and closed["values"]["description"] == HAZARD["description"] and closed["can_edit"] is False
    assert client.patch(f"/api/hazards/{hid}", headers=worker, json={"fields": answers(**HAZARD)}).status_code == 409
    events = client.get(f"/api/hazards/{hid}/history", headers=worker).json()["events"]
    assert [e["action"] for e in events] == ["status_changed", "status_changed", "edited", "created"]
    assert events[0]["note"] == "Barrier fence installed" and events[2]["detail"]["before"] == {"severity": "High"}
    # Reopening is allowed and recorded.
    assert client.post(f"/api/hazards/{hid}/status", headers=supervisor, json={"status": "open"}).json()["can_edit"] is True
    # Deleting needs the delete right (admin), and is kept in the audit trail.
    assert client.delete(f"/api/hazards/{hid}", headers=supervisor).status_code == 403
    assert client.delete(f"/api/hazards/{hid}", headers=admin).json() == {"ok": True}
    assert client.get(f"/api/hazards/{hid}", headers=admin).status_code == 404


def test_incident_with_evidence_and_pdf(world, client):
    from PIL import Image as PILImage
    from pypdf import PdfReader

    worker = world.auth("worker1")
    buf = BytesIO()
    PILImage.new("RGB", (90, 60), (27, 75, 143)).save(buf, "PNG")
    incident = dict(title="Reversing near miss", category="Near miss", occurred_date="2026-11-14", occurred_time="14:20",
                    description="Tipper reversed toward spotter", immediate_actions="Stopped work, toolbox talk")
    created = client.post("/api/incidents", headers=worker, json={"fields": answers(**incident)})
    assert created.status_code == 201 and created.json()["record_date"] == "2026-11-14"
    iid = created.json()["id"]
    assert client.post(f"/api/incidents/{iid}/files", headers=worker, data={"field_id": "title"}, files={"file": ("p.png", buf.getvalue(), "image/png")}).status_code == 400
    photo = client.post(f"/api/incidents/{iid}/files", headers=worker, data={"field_id": "photos"}, files={"file": ("scene.png", buf.getvalue(), "image/png")})
    assert photo.status_code == 201
    saved = client.patch(f"/api/incidents/{iid}", headers=worker, json={"fields": answers(
        **incident, photos=[photo.json()["id"]],
        follow_up_actions=[{"description": "Fit reversing camera", "responsible": "Workshop", "due_date": "2026-11-20"}],
        persons_involved=[{"name": "Tom Okafor", "role": "Spotter", "injury_or_involvement": "None"}])})
    assert saved.status_code == 200 and saved.json()["files"][0]["name"] == "scene.png"
    got = client.get(f"/api/incidents/{iid}/files/{photo.json()['id']}", headers=worker)
    assert got.status_code == 200 and got.content == buf.getvalue()
    assert client.get(f"/api/incidents/{iid}/files/{photo.json()['id']}").status_code == 401
    assert client.get(f"/api/incidents/{iid}/files/{photo.json()['id']}", headers=world.auth("admin2")).status_code == 404
    pdf = client.get(f"/api/incidents/{iid}/pdf", headers=worker)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
    for expected in ("Reversing near miss", "Incidents", "Open", "Near miss", "14/11/2026", "Fit reversing camera", "Tom Okafor"):
        assert expected in text, expected
    assert client.get(f"/api/incidents/{iid}/pdf", headers=world.auth("admin2")).status_code == 404


def test_pre_start_review_cycle(world, client):
    worker, supervisor, hseq = world.auth("worker1"), world.auth("super1"), world.auth("hseq1")
    draft = client.post("/api/pre-starts", headers=worker, json={"fields": answers(date="2026-11-16", crew_lead="Jess Marlow")})
    assert draft.status_code == 201 and draft.json()["status"] == "draft" and draft.json()["title"] == "Pre-start · 16/11/2026 · Jess Marlow"
    pid = draft.json()["id"]
    missing = client.post(f"/api/pre-starts/{pid}/submit", headers=worker)
    assert missing.status_code == 422 and {e["field_id"] for e in missing.json()["detail"]["errors"]} == {"work_summary", "hazards_discussed", "sign_ons"}
    sent = client.post(f"/api/pre-starts/{pid}/submit", headers=worker, json={"fields": answers(**PRESTART)}).json()
    assert sent["status"] == "submitted" and sent["can_edit"] is False
    assert client.patch(f"/api/pre-starts/{pid}", headers=worker, json={"fields": answers(**PRESTART)}).status_code == 409
    # Workers and supervisors cannot review; the HSEQ lead and admin can (D-029).
    for who in (worker, supervisor):
        assert client.post(f"/api/pre-starts/{pid}/review", headers=who, json={"action": "approve"}).status_code == 403
    assert client.post(f"/api/pre-starts/{pid}/review", headers=hseq, json={"action": "request_changes"}).status_code == 422
    assert client.post(f"/api/pre-starts/{pid}/review", headers=hseq, json={"action": "bless"}).status_code == 422
    back = client.post(f"/api/pre-starts/{pid}/review", headers=hseq, json={"action": "request_changes", "note": "Add the excavator operator to sign-on"}).json()
    assert back["status"] == "changes_requested"
    fixed = {**PRESTART, "sign_ons": PRESTART["sign_ons"] + [{"name": "Priya Nand", "role": "Excavator operator", "time": "06:58"}]}
    again = client.post(f"/api/pre-starts/{pid}/submit", headers=worker, json={"fields": answers(**fixed)}).json()
    assert again["status"] == "submitted" and len(again["values"]["sign_ons"]) == 2
    approved = client.post(f"/api/pre-starts/{pid}/review", headers=hseq, json={"action": "approve"}).json()
    assert approved["status"] == "approved" and approved["can_review"] is False
    assert client.post(f"/api/pre-starts/{pid}/review", headers=hseq, json={"action": "approve"}).status_code == 409
    actions = [e["action"] for e in client.get(f"/api/pre-starts/{pid}/history", headers=worker).json()["events"]]
    assert actions == ["review_approve", "submitted", "review_request_changes", "submitted", "created"]
    # An author may discard their own draft, but not an approved record.
    scrap = client.post("/api/pre-starts", headers=worker, json={"fields": []}).json()
    assert client.delete(f"/api/pre-starts/{scrap['id']}", headers=worker).json() == {"ok": True}
    assert client.delete(f"/api/pre-starts/{pid}", headers=worker).status_code == 403


def test_inspection_failures_stay_visible(world, client):
    hseq = world.auth("hseq1")
    assert client.post("/api/inspections", headers=world.auth("worker1"), json={"fields": []}).status_code == 403  # view only
    draft = client.post("/api/inspections", headers=hseq, json={"fields": answers(inspection_type="Weekly site inspection", date="2026-11-16")}).json()
    iid = draft["id"]
    sig = client.post(f"/api/inspections/{iid}/files", headers=hseq, data={"field_id": "operator_signature"}, files={"file": ("s.png", PNG, "image/png")}).json()
    checks = {k: {"result": "pass"} for k in ("housekeeping", "signage", "ppe", "plant", "excavations", "traffic", "environment")}
    base = dict(inspection_type="Weekly site inspection", date="2026-11-16", operator_signature={"file_id": sig["id"], "signed_name": "Sam Keating"})
    # A failed item must say what is wrong; it can never be saved as a bare fail.
    bad = client.post(f"/api/inspections/{iid}/submit", headers=hseq, json={"fields": answers(**base, **{**checks, "excavations": {"result": "fail"}})})
    assert bad.status_code == 422 and bad.json()["detail"]["errors"][0]["field_id"] == "excavations"
    good = client.post(f"/api/inspections/{iid}/submit", headers=hseq, json={"fields": answers(
        **base, **{**checks, "excavations": {"result": "fail", "note": "No edge protection on trench 3"}},
        corrective_actions=[{"description": "Install edge protection", "responsible": "Site supervisor"}])})
    assert good.status_code == 200
    assert good.json()["failed_checks"] == [{"field_id": "excavations", "label": "Excavations and trenches", "note": "No edge protection on trench 3"}]
    assert client.get("/api/inspections", headers=hseq).json()["items"][0]["failed_check_count"] == 1


def test_risk_scores_show_their_rule_and_ssra_is_separate(world, client):
    hseq = world.auth("hseq1")
    rows = [{"hazard": "Trench collapse", "likelihood_1_5": "3", "consequence_1_5": "5", "controls": "Shoring",
             "residual_likelihood_1_5": "1", "residual_consequence_1_5": "5"},
            {"hazard": "Dust", "likelihood_1_5": "often", "consequence_1_5": "2", "controls": "Water cart"}]
    ra = client.post("/api/risk-assessments", headers=hseq, json={"status": "submitted", "fields": answers(
        title="Deep trenching", date="2026-11-16", hazards=rows, operator="Sam Keating")})
    assert ra.status_code == 201
    risk = ra.json()["risk"]
    assert "Score = likelihood" in risk["rule"]
    assert risk["rows"][0] == {"hazard": "Trench collapse", "initial": {"score": 15, "rating": "High"}, "residual": {"score": 5, "rating": "Medium"}}
    assert risk["rows"][1]["initial"] is None  # no score is invented from an unusable answer
    ssra = client.post("/api/ssra", headers=hseq, json={"fields": answers(title="Pipe lift", site="Northbank", date="2026-11-16", hazards=rows[:1])})
    assert ssra.status_code == 201
    # Each lives only in its own register.
    assert client.get("/api/risk-assessments", headers=hseq).json()["total"] == 1
    assert client.get("/api/ssra", headers=hseq).json()["total"] == 1
    assert client.get(f"/api/risk-assessments/{ssra.json()['id']}", headers=hseq).status_code == 404


def test_swms_review_assignment_acknowledgement_and_versions(world, client):
    hseq, admin, worker, supervisor = world.auth("hseq1"), world.auth("admin1"), world.auth("worker1"), world.auth("super1")
    assert client.post("/api/swms", headers=worker, json={"fields": answers(**SWMS)}).status_code == 403  # workers read only
    v1 = client.post("/api/swms", headers=hseq, json={"status": "submitted", "fields": answers(**SWMS)}).json()
    sid = v1["id"]
    # Not visible to a worker until approved.
    assert client.get("/api/swms", headers=worker).json()["total"] == 0
    assert client.get(f"/api/swms/{sid}", headers=worker).status_code == 404
    assert client.post(f"/api/swms/{sid}/acknowledge", headers=hseq, json={"signed_name": "Sam"}).status_code == 409
    assert client.post(f"/api/swms/{sid}/review", headers=admin, json={"action": "approve"}).json()["status"] == "approved"
    # Approved and unassigned: everyone who can view SWMS sees it, though an admin-side user wrote it.
    assert client.get("/api/swms", headers=worker).json()["total"] == 1
    # Assign to supervisors only: the worker loses sight of it, even with the id.
    assert client.put(f"/api/swms/{sid}/assignments", headers=worker, json={"rules": []}).status_code == 403
    assert client.put(f"/api/swms/{sid}/assignments", headers=hseq, json={"rules": [{"kind": "user", "target_id": world.id("worker2")}]}).status_code == 404
    client.put(f"/api/swms/{sid}/assignments", headers=hseq, json={"rules": [{"kind": "role", "target_id": "supervisor"}]})
    assert client.get("/api/swms", headers=worker).json()["total"] == 0
    assert client.get(f"/api/swms/{sid}", headers=worker).status_code == 404
    assert client.get(f"/api/swms/{sid}/pdf", headers=worker).status_code == 404
    client.put(f"/api/swms/{sid}/assignments", headers=hseq, json={"rules": [{"kind": "role", "target_id": "supervisor"}, {"kind": "user", "target_id": world.id("worker1")}]})
    seen = client.get(f"/api/swms/{sid}", headers=worker).json()
    assert seen["title"] == "Trenching and excavation" and seen["can_edit"] is False and seen["acknowledged_by_me_at"] is None

    # Acknowledge: once per person, on this version.
    ack = client.post(f"/api/swms/{sid}/acknowledge", headers=worker, json={"signed_name": "Tom Okafor"}).json()
    assert ack["acknowledged_by_me_at"] and ack["acknowledgement_count"] == 1
    client.post(f"/api/swms/{sid}/acknowledge", headers=worker, json={"signed_name": "Tom Okafor"})
    listing = client.get(f"/api/swms/{sid}/acknowledgements", headers=hseq).json()
    assert listing["count"] == 1 and listing["acknowledgements"][0]["user_name"] == "Tom Okafor" and listing["version_no"] == 1
    assert client.get(f"/api/swms/{sid}/acknowledgements", headers=worker).status_code == 403

    # New version: v1 stays in force until v2 is approved, then v1 is superseded and keeps its acknowledgement.
    v2 = client.post(f"/api/swms/{sid}/new-version", headers=hseq)
    assert v2.status_code == 201 and v2.json()["version_no"] == 2 and v2.json()["status"] == "draft" and v2.json()["supersedes_id"] == sid
    assert client.post(f"/api/swms/{sid}/new-version", headers=hseq).status_code == 409  # one in progress at a time
    v2id = v2.json()["id"]
    changed = {**SWMS, "scope": "Now includes dewatering"}
    client.post(f"/api/swms/{v2id}/submit", headers=hseq, json={"fields": answers(**changed)})
    assert client.get(f"/api/swms/{sid}", headers=worker).json()["superseded_by_id"] is None
    client.post(f"/api/swms/{v2id}/review", headers=admin, json={"action": "approve"})
    assert [r["id"] for r in client.get("/api/swms", headers=worker).json()["items"]] == [v2id]  # assignments carried to v2
    assert client.get(f"/api/swms/{v2id}", headers=worker).json()["acknowledged_by_me_at"] is None  # must be signed afresh
    old = client.get(f"/api/swms/{sid}", headers=hseq).json()
    assert old["superseded_by_id"] == v2id and old["acknowledgement_count"] == 1 and "scope" not in old["values"]
    assert client.post(f"/api/swms/{sid}/acknowledge", headers=supervisor, json={"signed_name": "Jess"}).status_code == 409
    chain = client.get(f"/api/swms/{v2id}/versions", headers=hseq).json()
    assert [v["version_no"] for v in chain["versions"]] == [2, 1]
    assert client.get("/api/swms", headers=hseq).json()["total"] == 1 and client.get("/api/swms?include_superseded=true", headers=hseq).json()["total"] == 2


def test_bulk_archive_previews_then_archives_and_restores(world, client):
    supervisor, hseq = world.auth("super1"), world.auth("hseq1")
    ids = [client.post("/api/hazards", headers=supervisor, json={"fields": answers(**{**HAZARD, "title": f"Hazard {i}"})}).json()["id"] for i in range(3)]
    assert client.post("/api/hazards/archive", headers=world.auth("worker1"), json={"ids": ids}).status_code == 403  # no archive right
    assert client.post("/api/hazards/archive", headers=hseq, json={}).status_code == 400
    preview = client.post("/api/hazards/archive", headers=hseq, json={"ids": ids[:2]}).json()
    assert preview["dry_run"] is True and preview["matched"] == 2 and preview["archived"] == 0
    assert client.get("/api/hazards", headers=hseq).json()["total"] == 3  # the preview changed nothing
    done = client.post("/api/hazards/archive", headers=hseq, json={"ids": ids[:2], "dry_run": False}).json()
    assert done["archived"] == 2 and done["batch_id"]
    assert client.get("/api/hazards", headers=hseq).json()["total"] == 1
    assert client.get("/api/hazards?include_archived=true", headers=hseq).json()["total"] == 3
    assert client.get(f"/api/hazards/{ids[0]}", headers=hseq).json()["archived_at"]  # still readable
    assert client.patch(f"/api/hazards/{ids[0]}", headers=supervisor, json={"fields": answers(**HAZARD)}).status_code == 409
    assert client.post(f"/api/hazards/unarchive-batch/{done['batch_id']}", headers=hseq).json() == {"restored": 2}
    assert client.post(f"/api/hazards/unarchive-batch/{done['batch_id']}", headers=hseq).status_code == 404
    assert client.post(f"/api/hazards/{ids[2]}/archive", headers=hseq).json()["archived_at"]
    assert client.post(f"/api/hazards/{ids[2]}/unarchive", headers=hseq).json()["archived_at"] is None


def test_form_submissions_project_into_their_register_exactly_once(world, client):
    admin, worker = world.auth("admin1"), world.auth("worker1")

    def publish(name, category):
        t = client.post("/api/forms/templates", headers=admin, json={"name": name, "category": category, "fields": [
            {"id": "date", "type": "date", "label": "Date", "required": True}, {"id": "note", "type": "text", "label": "Note"}]}).json()
        client.post(f"/api/forms/templates/{t['id']}/publish", headers=admin)
        return t["id"]

    prestart, hazard, general, ssra = publish("Plant pre-start", "pre_start"), publish("Quick hazard", "hazard"), publish("Toolbox talk", "general"), publish("SSRA form", "ssra")
    body = {"fields": answers(date="2026-11-12", note="All good"), "client_submission_id": "retry-0001"}
    first = client.post(f"/api/forms/templates/{prestart}/submissions", headers=worker, json=body).json()
    client.post(f"/api/forms/templates/{prestart}/submissions", headers=worker, json=body)  # offline retry
    client.post(f"/api/forms/submissions/{first['id']}/submit", headers=worker)  # repeated submit
    register = client.get("/api/pre-starts", headers=worker).json()
    assert register["total"] == 1
    record = client.get(f"/api/pre-starts/{register['items'][0]['id']}", headers=worker).json()
    assert record["source"] == "form" and record["source_submission_id"] == first["id"] and record["status"] == "submitted"
    assert record["record_date"] == "2026-11-12" and record["title"] == "Pre-start · 12/11/2026"
    assert [f["id"] for f in record["schema"]] == ["date", "note"] and record["values"]["note"] == "All good"
    # A draft does not project; submitting it does.
    draft = client.post(f"/api/forms/templates/{hazard}/submissions", headers=worker, json={"status": "draft", "fields": answers(date="2026-11-13")}).json()
    assert client.get("/api/hazards", headers=worker).json()["total"] == 0
    client.post(f"/api/forms/submissions/{draft['id']}/submit", headers=worker)
    hazards = client.get("/api/hazards", headers=worker).json()
    assert hazards["total"] == 1 and hazards["items"][0]["status"] == "open" and hazards["items"][0]["title"] == "Quick hazard"
    # General forms project nowhere; SSRA forms land only in the SSRA register.
    client.post(f"/api/forms/templates/{general}/submissions", headers=worker, json={"fields": answers(date="2026-11-13")})
    client.post(f"/api/forms/templates/{ssra}/submissions", headers=world.auth("hseq1"), json={"fields": answers(date="2026-11-13")})
    hseq = world.auth("hseq1")
    assert client.get("/api/ssra", headers=hseq).json()["total"] == 1 and client.get("/api/risk-assessments", headers=hseq).json()["total"] == 0
    totals = [client.get(f"/api/{p}", headers=admin).json()["total"] for p in ("swms", "pre-starts", "site-diary", "hazards", "incidents", "inspections", "risk-assessments", "ssra")]
    assert totals == [0, 1, 0, 1, 0, 0, 0, 1]


def test_registers_are_invisible_across_organisations(world, client):
    admin1, admin2 = world.auth("admin1"), world.auth("admin2")
    hazard = client.post("/api/hazards", headers=admin1, json={"fields": answers(**HAZARD)}).json()
    swms = client.post("/api/swms", headers=admin1, json={"status": "submitted", "fields": answers(**SWMS)}).json()
    client.post(f"/api/swms/{swms['id']}/review", headers=admin1, json={"action": "approve"})
    for method, path, body in [
        ("get", f"/api/hazards/{hazard['id']}", None), ("patch", f"/api/hazards/{hazard['id']}", {"fields": []}),
        ("post", f"/api/hazards/{hazard['id']}/status", {"status": "closed", "note": "x"}), ("delete", f"/api/hazards/{hazard['id']}", None),
        ("get", f"/api/hazards/{hazard['id']}/history", None), ("get", f"/api/hazards/{hazard['id']}/pdf", None),
        ("post", f"/api/hazards/{hazard['id']}/archive", None), ("get", f"/api/swms/{swms['id']}", None),
        ("post", f"/api/swms/{swms['id']}/acknowledge", {"signed_name": "X"}), ("post", f"/api/swms/{swms['id']}/new-version", None),
        ("get", f"/api/swms/{swms['id']}/assignments", None), ("put", f"/api/swms/{swms['id']}/assignments", {"rules": []}),
        ("get", f"/api/swms/{swms['id']}/versions", None), ("get", f"/api/swms/{swms['id']}/acknowledgements", None),
    ]:
        assert client.request(method, path, headers=admin2, json=body).status_code == 404, (method, path)
    assert client.get("/api/hazards", headers=admin2).json()["total"] == 0
    assert client.post("/api/hazards/archive", headers=admin2, json={"ids": [hazard["id"]], "dry_run": False}).json()["archived"] == 0
    assert client.get(f"/api/hazards/{hazard['id']}", headers=admin1).json()["archived_at"] is None
