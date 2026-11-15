"""Connections, safe mode, the outbox and notifications (stage 5a)."""
import json
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import integrations
from app.config import get_settings
from app.models import AuditLog, IntegrationConfig, OutboxAttempt, OutboxMessage, UserPermissionOverride, utcnow

I = "/api/integrations"
SM = "/api/admin/comms-safe-mode"
OB = "/api/email/outbox"
N = "/api/notifications"
SECRET = "s3cr3t-value-ABC123"


@pytest.fixture
def provider(monkeypatch):
    """Stand in for the internet. Records every request; answers from a table of handlers."""
    calls: list[httpx.Request] = []
    routes: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        for prefix, answer in routes.items():
            if str(request.url).startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer(request) if callable(answer) else answer
        return httpx.Response(404, json={"error": "no route"})

    monkeypatch.setattr(integrations, "transport", httpx.MockTransport(handler))
    return type("Provider", (), {"calls": calls, "routes": routes})


def grant_safe_mode(world, db, key="admin1"):
    """Safe mode can only be switched by someone given that right explicitly."""
    db.add(UserPermissionOverride(user_id=world.id(key), org_id=world.users[key].org_id, overrides={"comms_safe_mode": {"edit": True, "view": True}}, reasons={}))
    db.commit()


def save(client, headers, kind, **body):
    return client.put(f"{I}/{kind}", json=body, headers=headers)


M365 = {"settings": {"tenant_id": "tenant-1", "client_id": "client-1", "sender_email": "safety@one.example"}, "secrets": {"client_secret": SECRET}, "mode": "live"}
TM = {"settings": {"username": "paneltec"}, "secrets": {"api_key": SECRET}, "mode": "live"}


# ---------- connections ----------

def test_catalogue_and_permissions(world, client):
    admin = world.auth("admin1")
    listing = client.get(I, headers=admin).json()
    assert [i["kind"] for i in listing["items"]] == ["simpro", "navixy", "smartfill", "microsoft365", "textmagic"]
    assert all(i["status"]["state"] == "not_configured" and i["mode"] == "off" for i in listing["items"])
    assert listing["encryption"]["configured"] is True and listing["safe_mode"] == "on"
    assert client.get(I, headers=world.auth("auditor1")).status_code == 200
    assert client.get(I, headers=world.auth("worker1")).status_code == 403
    assert save(client, world.auth("auditor1"), "simpro", mode="simulator").status_code == 403
    assert client.get(f"{I}/nope", headers=admin).status_code == 404
    assert client.get(I).status_code == 401


def test_secrets_are_encrypted_never_returned_and_kept_unless_cleared(world, client, db):
    admin = world.auth("admin1")
    saved = save(client, admin, "simpro", settings={"api_base_url": "https://acme.simprosuite.com/", "company_ids": ["0", " 2 ", "0"]}, secrets={"api_token": SECRET}, mode="live")
    assert saved.status_code == 200, saved.text
    body = saved.json()
    fields = {f["name"]: f for f in body["fields"]}
    assert fields["api_token"] == {**fields["api_token"], "is_set": True} and "value" not in fields["api_token"]
    assert fields["api_base_url"]["value"] == "https://acme.simprosuite.com" and fields["company_ids"]["value"] == ["0", "2"]
    assert SECRET not in saved.text and body["status"]["state"] == "untested"
    row = db.scalars(select(IntegrationConfig)).one()
    assert SECRET not in (row.secrets or "") and row.secrets_set == ["api_token"] and SECRET not in json.dumps(row.settings)
    assert SECRET not in json.dumps([a.detail for a in db.scalars(select(AuditLog)).all()])
    assert SECRET not in client.get(I, headers=admin).text and SECRET not in client.get(f"{I}/simpro", headers=world.auth("auditor1")).text

    # Reopening and saving with the secret omitted, empty or as dots keeps it.
    for unchanged in ({}, {"api_token": ""}, {"api_token": "••••••••"}, {"api_token": None}):
        again = save(client, admin, "simpro", settings={"company_ids": ["0"]}, secrets=unchanged).json()
        assert {f["name"]: f for f in again["fields"]}["api_token"]["is_set"] is True
    db.expire_all()
    from app.crypto import decrypt_json
    assert decrypt_json(db.scalars(select(IntegrationConfig)).one().secrets) == {"api_token": SECRET}
    # Clearing is explicit.
    cleared = save(client, admin, "simpro", clear_secrets=["api_token"]).json()
    assert {f["name"]: f for f in cleared["fields"]}["api_token"]["is_set"] is False and cleared["status"]["state"] == "incomplete"
    assert "API token" in cleared["status"]["detail"]

    # Another organisation has its own, separate configuration.
    assert client.get(f"{I}/simpro", headers=world.auth("admin2")).json()["status"]["state"] == "not_configured"


def test_validation_of_settings_and_addresses(world, client):
    admin = world.auth("admin1")
    for bad in ("http://acme.simprosuite.com", "https://localhost", "https://10.0.0.5", "https://127.0.0.1/x", "https://nas.local", "https://user:pw@acme.example", "acme"):
        assert save(client, admin, "simpro", settings={"api_base_url": bad}).status_code == 422, bad
    assert save(client, admin, "navixy", settings={"poll_seconds": 5}).status_code == 422
    assert save(client, admin, "navixy", settings={"poll_seconds": "30"}).status_code == 422
    assert save(client, admin, "navixy", settings={"email": "not-an-email"}).status_code == 422
    assert save(client, admin, "navixy", settings={"nope": 1}).status_code == 422
    assert save(client, admin, "navixy", secrets={"email": "x"}).status_code == 422  # not a secret field
    assert save(client, admin, "navixy", mode="always").status_code == 422
    defaults = {f["name"]: f.get("value") for f in client.get(f"{I}/navixy", headers=admin).json()["fields"]}
    assert defaults["poll_seconds"] == 30 and defaults["api_base_url"] is None  # no region is assumed


def test_saving_secrets_fails_closed_without_the_encryption_key(world, client, db, monkeypatch):
    admin = world.auth("admin1")
    monkeypatch.setattr(get_settings(), "encryption_key", "")
    refused = save(client, admin, "textmagic", settings={"username": "paneltec"}, secrets={"api_key": SECRET})
    assert refused.status_code == 503 and "ENCRYPTION_KEY is not set" in refused.json()["detail"]
    assert db.scalars(select(IntegrationConfig)).first() is None  # nothing was written, in plain text or otherwise
    assert client.get(I, headers=admin).json()["encryption"]["configured"] is False
    assert save(client, admin, "textmagic", settings={"username": "paneltec"}).status_code == 200  # non-secret settings still save


def test_live_tests_use_the_documented_requests_and_map_errors(world, client, provider):
    admin = world.auth("admin1")
    # Simpro: bearer token, companies listed, selected company must exist.
    provider.routes["https://acme.simprosuite.com/api/v1.0/companies/"] = httpx.Response(200, json=[{"ID": 0, "Name": "Paneltec"}, {"ID": 2, "Name": "Viatec"}])
    save(client, admin, "simpro", settings={"api_base_url": "https://acme.simprosuite.com", "company_ids": ["0", "7"]}, secrets={"api_token": SECRET}, mode="live")
    partial = client.post(f"{I}/simpro/test-connection", headers=admin).json()
    assert partial["ok"] is False and "no company numbered 7" in partial["message"] and partial["connection"]["status"]["state"] == "error"
    assert provider.calls[-1].headers["authorization"] == f"Bearer {SECRET}"
    save(client, admin, "simpro", settings={"company_ids": ["0", "2"]})
    good = client.post(f"{I}/simpro/test-connection", headers=admin).json()
    assert good["ok"] is True and good["simulated"] is False and good["connection"]["status"]["state"] == "connected"
    assert good["connection"]["last_sync_at"] is None and "has not been checked" in good["message"]  # tested is not the same as imported

    # Navixy: login and password to the chosen region; the sign-in key is stored encrypted, not returned.
    provider.routes["https://api.eu.navixy.com/v2/user/auth"] = httpx.Response(200, json={"success": True, "hash": "hash-123456"})
    save(client, admin, "navixy", settings={"api_base_url": "https://api.eu.navixy.com", "email": "fleet@one.example"}, secrets={"password": SECRET}, mode="live")
    nav = client.post(f"{I}/navixy/test-connection", headers=admin)
    assert nav.json()["ok"] is True and "hash-123456" not in nav.text
    assert json.loads(provider.calls[-1].content) == {"login": "fleet@one.example", "password": SECRET}

    # SmartFill: the envelope uses "parameters" and the client reference/secret.
    provider.routes["https://fmtdata.com/API/api.php"] = httpx.Response(200, json={"jsonrpc": "2.0", "result": {"data": []}, "id": 1})
    save(client, admin, "smartfill", settings={"api_key": "REF-1"}, secrets={"api_secret": SECRET}, mode="live")
    assert client.post(f"{I}/smartfill/test-connection", headers=admin).json()["ok"] is True
    sent = json.loads(provider.calls[-1].content)
    assert sent["method"] == "Tank:Level" and sent["parameters"] == {"clientReference": "REF-1", "clientSecret": SECRET} and "params" not in sent

    # Microsoft 365: a token only. No mail is sent by a test.
    provider.routes["https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"] = httpx.Response(200, json={"access_token": "tok-abcdef"})
    save(client, admin, "microsoft365", **M365)
    m365 = client.post(f"{I}/microsoft365/test-connection", headers=admin).json()
    assert m365["ok"] is True and "No email was sent" in m365["message"] and m365["connection"]["capabilities"] == {"token": True, "send_tested": False}
    assert not any("sendMail" in str(c.url) for c in provider.calls)

    # Errors are put in plain words and never echo a secret.
    provider.routes["https://rest.textmagic.com/api/v2/user"] = httpx.Response(401, json={"message": f"bad key {SECRET}"})
    save(client, admin, "textmagic", **TM)
    rejected = client.post(f"{I}/textmagic/test-connection", headers=admin)
    assert rejected.json()["ok"] is False and "rejected these details (HTTP 401)" in rejected.json()["message"] and SECRET not in rejected.text
    provider.routes["https://rest.textmagic.com/api/v2/user"] = httpx.ReadTimeout("slow")
    assert "did not answer within" in client.post(f"{I}/textmagic/test-connection", headers=admin).json()["message"]
    provider.routes["https://rest.textmagic.com/api/v2/user"] = httpx.ConnectError(f"dns failure for key {SECRET}")
    unreachable = client.post(f"{I}/textmagic/test-connection", headers=admin)
    assert "could not be reached" in unreachable.json()["message"] and SECRET not in unreachable.text
    provider.routes["https://rest.textmagic.com/api/v2/user"] = httpx.Response(200, text="<html>login</html>")
    assert "not in the expected format" in client.post(f"{I}/textmagic/test-connection", headers=admin).json()["message"]

    # Changing the details discards the old proof.
    assert save(client, admin, "simpro", secrets={"api_token": "another-token-999"}).json()["status"]["state"] == "untested"
    # Disconnect wipes everything.
    assert client.delete(f"{I}/simpro", headers=admin).json()["status"]["state"] == "not_configured"


def test_simulator_is_always_labelled_and_never_counts_as_connected(world, client, provider):
    admin = world.auth("admin1")
    assert client.post(f"{I}/simpro/test-connection", headers=admin).status_code == 409  # nothing saved
    save(client, admin, "simpro", mode="simulator")
    result = client.post(f"{I}/simpro/test-connection", headers=admin).json()
    assert result["ok"] is True and result["simulated"] is True and "No real system was contacted" in result["message"]
    assert result["connection"]["status"]["state"] == "simulator" and result["connection"]["last_test"]["simulated"] is True
    assert provider.calls == []
    # Switching to live does not inherit the simulator's success.
    live = save(client, admin, "simpro", settings={"api_base_url": "https://acme.simprosuite.com"}, secrets={"api_token": SECRET}, mode="live").json()
    assert live["status"]["state"] == "untested"
    save(client, admin, "simpro", mode="off")
    assert client.post(f"{I}/simpro/test-connection", headers=admin).status_code == 409


# ---------- safe mode and the send boundary ----------

def _issue_job(world, client, mobile="0400 000 111"):
    tom = world.add_worker("Tom", "Okafor", link="worker1", mobile=mobile)
    job = client.post("/api/daily-jobs", json={"worker_id": tom.id, "site_name": "Depot"}, headers=world.auth("admin1"))
    assert job.status_code == 201, job.text
    return job.json()


def test_safe_mode_is_on_by_default_and_only_an_explicit_grant_can_change_it(world, client, db, monkeypatch):
    admin = world.auth("admin1")
    status = client.get(f"{SM}/status", headers=world.auth("worker1")).json()
    assert status == {"effective": "on", "env_locked": False, "env_value": "off", "org_value": "on", "blocked_count": 0, "can_change": False}
    assert client.patch(SM, json={"mode": "off"}, headers=admin).status_code == 403  # even an admin, without the explicit grant
    assert client.get(f"{SM}/who-can-toggle", headers=admin).json()["items"] == []
    grant_safe_mode(world, db)
    assert [u["name"] for u in client.get(f"{SM}/who-can-toggle", headers=admin).json()["items"]] == ["Alex Rowe"]
    assert client.patch(SM, json={"mode": "maybe"}, headers=admin).status_code == 422
    off = client.patch(SM, json={"mode": "off"}, headers=admin).json()
    assert off["effective"] == "off" and off["org_value"] == "off"
    assert db.scalars(select(AuditLog).where(AuditLog.action == "comms.safe_mode_changed")).one().detail == {"from": "on", "to": "off"}
    assert client.get(f"{SM}/status", headers=world.auth("admin2")).json()["effective"] == "on"  # the other organisation is untouched

    # The server lock wins over the organisation's switch.
    monkeypatch.setattr(get_settings(), "comms_safe_mode", "on")
    locked = client.get(f"{SM}/status", headers=admin).json()
    assert locked["effective"] == "on" and locked["env_locked"] is True and locked["org_value"] == "off"
    refused = client.patch(SM, json={"mode": "off"}, headers=admin)
    assert refused.status_code == 409 and "locked on by the server" in refused.json()["detail"]
    assert client.patch(SM, json={"mode": "on"}, headers=admin).json()["org_value"] == "on"


def test_messages_are_held_in_safe_mode_then_sent_once_when_everything_allows(world, client, db, provider):
    admin = world.auth("admin1")
    provider.routes["https://rest.textmagic.com/api/v2/user"] = httpx.Response(200, json={"balance": 42.5})
    provider.routes["https://rest.textmagic.com/api/v2/messages"] = httpx.Response(201, json={"id": 98765})
    save(client, admin, "textmagic", **TM)
    assert client.post(f"{I}/textmagic/test-connection", headers=admin).json()["ok"] is True

    job = _issue_job(world, client)
    assert job["delivery"] == "held"  # connected and tested, but safe mode is on
    held = client.get(OB, headers=admin).json()
    assert held["safe_mode"] == "on" and held["counts"]["held"] == 1
    message = held["items"][0]
    assert (message["hold_code"], message["can_retry"], message["purpose_label"]) == ("safe_mode", True, "Daily job")
    assert not any("/messages" in str(c.url) for c in provider.calls)
    assert client.get(f"{SM}/status", headers=admin).json()["blocked_count"] == 1
    # Retrying while safe mode is on still sends nothing.
    assert client.post(f"{OB}/{message['id']}/retry", headers=admin).json()["status"] == "held"
    assert not any("/messages" in str(c.url) for c in provider.calls)

    grant_safe_mode(world, db)
    client.patch(SM, json={"mode": "off"}, headers=admin)
    sent = client.post(f"{OB}/{message['id']}/retry", headers=admin).json()
    assert sent["status"] == "sent" and sent["attempts"] == 1 and sent["can_retry"] is False
    assert [h["outcome"] for h in sent["history"]] == ["held", "held", "sent"]
    request = next(c for c in provider.calls if "/messages" in str(c.url))
    assert request.headers["x-tm-username"] == "paneltec" and request.headers["x-tm-key"] == SECRET
    payload = json.loads(request.content)
    assert payload["phones"] == "0400000111" and "Depot" in payload["text"] and "My work" in payload["text"]
    # A sent message is never sent twice, and the job reports the real state.
    assert client.post(f"{OB}/{message['id']}/retry", headers=admin).status_code == 409
    assert len([c for c in provider.calls if "/messages" in str(c.url)]) == 1
    assert client.get("/api/daily-jobs", headers=admin).json()["items"][0]["delivery"] == "sent"
    assert client.post(f"{OB}/{message['id']}/cancel", headers=admin).status_code == 409


def test_provider_states_decide_what_happens_when_safe_mode_is_off(world, client, db, provider):
    admin = world.auth("admin1")
    grant_safe_mode(world, db)
    client.patch(SM, json={"mode": "off"}, headers=admin)
    tom = world.add_worker("Tom", "Okafor", mobile="0400 000 111")
    mia = world.add_worker("Mia", "Tanaka", mobile="0400 000 222")
    ann = world.add_worker("Ann", "Lee", mobile="0400 000 333")

    def issue(worker):
        return client.post("/api/daily-jobs", json={"worker_id": worker.id, "site_name": "Depot"}, headers=admin).json()

    assert issue(tom)["delivery"] == "held"  # no provider saved at all
    assert client.get(OB, headers=admin).json()["items"][0]["hold_code"] == "not_connected"
    save(client, admin, "textmagic", **TM)  # saved but never tested: still not sent
    assert issue(mia)["delivery"] == "held"
    save(client, admin, "textmagic", mode="simulator")
    simulated = issue(ann)
    assert simulated["delivery"] == "simulated"  # never reported as sent
    assert provider.calls == []
    row = client.get(f"{OB}?status=simulated", headers=admin).json()["items"][0]
    assert row["status_label"] == "Simulated" and client.post(f"{OB}/{row['id']}/retry", headers=admin).status_code == 409

    # A failure is recorded with safe words and can be retried.
    provider.routes["https://rest.textmagic.com/api/v2/user"] = httpx.Response(200, json={})
    provider.routes["https://rest.textmagic.com/api/v2/messages"] = httpx.Response(500, text=f"boom {SECRET}")
    save(client, admin, "textmagic", mode="live")
    client.post(f"{I}/textmagic/test-connection", headers=admin)
    held_id = client.get(f"{OB}?status=held", headers=admin).json()["items"][0]["id"]
    failed = client.post(f"{OB}/{held_id}/retry", headers=admin).json()
    assert failed["status"] == "failed" and "HTTP 500" in failed["reason"] and SECRET not in json.dumps(failed)
    provider.routes["https://rest.textmagic.com/api/v2/messages"] = httpx.Response(201, json={"id": 1})
    assert client.post(f"{OB}/{held_id}/retry", headers=admin).json()["status"] == "sent"

    # The daily limit stops further texts.
    save(client, admin, "textmagic", settings={"daily_budget_aud": 0.1, "estimated_cost_per_sms_aud": 0.1})
    client.post(f"{I}/textmagic/test-connection", headers=admin)
    other_id = client.get(f"{OB}?status=held", headers=admin).json()["items"][0]["id"]
    capped = client.post(f"{OB}/{other_id}/retry", headers=admin).json()
    assert capped["status"] == "held" and capped["hold_code"] == "budget"


def test_one_time_links_are_emailed_when_allowed_but_never_stored(world, client, db, provider):
    admin = world.auth("admin1")
    provider.routes["https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"] = httpx.Response(200, json={"access_token": "tok-abcdef"})
    provider.routes["https://graph.microsoft.com/v1.0/users/safety@one.example/sendMail"] = httpx.Response(202)
    save(client, admin, "microsoft365", **M365)
    client.post(f"{I}/microsoft365/test-connection", headers=admin)
    user = client.post("/api/users", json={"name": "New Person", "email": "new@one.example", "role_id": "worker"}, headers=admin).json()

    held = client.post(f"/api/users/{user['id']}/invite", headers=admin).json()  # safe mode on
    assert held["delivery"] == "held" and held["channel"] == "manual" and "Safe mode is on" in held["delivery_note"] and "Pass this link" in held["delivery_note"]
    assert not any("sendMail" in str(c.url) for c in provider.calls)
    row = db.scalars(select(OutboxMessage).where(OutboxMessage.purpose == "invitation")).one()
    assert row.body_text is None  # the link is not kept anywhere
    assert client.post(f"{OB}/{row.id}/retry", headers=admin).status_code == 409  # so it cannot be re-sent; a new link is needed

    grant_safe_mode(world, db)
    client.patch(SM, json={"mode": "off"}, headers=admin)
    sent = client.post(f"/api/users/{user['id']}/invite", headers=admin).json()
    assert sent["delivery"] == "sent" and sent["channel"] == "email" and sent["delivery_note"].startswith("Emailed to new@one.example")
    mail = json.loads(next(c for c in provider.calls if "sendMail" in str(c.url)).content)["message"]
    assert mail["toRecipients"] == [{"emailAddress": {"address": "new@one.example"}}] and sent["link"] in mail["body"]["content"]
    db.expire_all()
    rows = db.scalars(select(OutboxMessage).where(OutboxMessage.purpose == "invitation").order_by(OutboxMessage.created_at)).all()
    assert [r.status for r in rows] == ["held", "sent"] and all(r.body_text is None for r in rows)
    token = sent["link"].split("token=")[1]
    assert token not in json.dumps([a.detail for a in db.scalars(select(OutboxAttempt)).all()])


def test_automatic_callers_never_send(world, db, provider):
    """A message prepared without a person's request (a background job) is always held."""
    from app import comms
    from app.models import Organisation

    org = db.get(Organisation, world.org1.id)
    org.details = {**(org.details or {}), "comms_safe_mode": "off"}
    db.add(IntegrationConfig(org_id=org.id, kind="textmagic", mode="simulator", settings={}, secrets_set=[]))
    db.commit()
    message = comms.deliver(db, None, org_id=org.id, channel="sms", purpose="test", recipient="0400000000", subject="x", body="hello",
                            store_body=True, related_type=None, related_id=None, created_by=None)
    assert (message.status, message.hold_code) == ("held", "no_request")
    # The same key twice is one message.
    again = [comms.deliver(db, None, org_id=org.id, channel="sms", purpose="test", recipient="0400000000", subject="x", body="hello",
                           store_body=True, related_type=None, related_id=None, created_by=None, dedupe_key="reminder:cert-1:30") for _ in range(2)]
    assert again[0].id == again[1].id


def test_outbox_visibility_cancel_and_delete(world, client, db):
    admin, hseq, auditor = world.auth("admin1"), world.auth("hseq1"), world.auth("auditor1")
    _issue_job(world, client)
    supplier = client.post("/api/suppliers", json={"name": "Acme", "contact_email": "office@acme.example"}, headers=hseq).json()
    client.post("/api/renewals", json={"entity_id": supplier["id"], "doc_types_requested": ["licence"]}, headers=hseq)
    assert client.get(OB, headers=admin).json()["total"] == 2 and client.get(OB, headers=auditor).json()["sees_all"] is True
    mine = client.get(OB, headers=hseq).json()  # no outbox permission: only what they created
    assert mine["sees_all"] is False and [m["purpose"] for m in mine["items"]] == ["renewal_request"]
    job_message = next(m for m in client.get(OB, headers=admin).json()["items"] if m["purpose"] == "daily_job")
    assert client.get(f"{OB}/{job_message['id']}", headers=hseq).status_code == 404
    assert client.get(f"{OB}/{job_message['id']}", headers=world.auth("admin2")).status_code == 404
    assert client.post(f"{OB}/{job_message['id']}/cancel", headers=auditor).status_code == 403
    detail = client.get(f"{OB}/{job_message['id']}", headers=auditor).json()
    assert "Depot" in detail["body_text"] and detail["history"][0]["outcome"] == "held"
    assert client.get(f"{OB}/{mine['items'][0]['id']}", headers=hseq).json()["body_stored"] is False

    assert client.post(f"{OB}/{job_message['id']}/cancel", headers=admin).json()["status"] == "cancelled"
    assert client.post(f"{OB}/{job_message['id']}/retry", headers=admin).status_code == 409
    assert client.delete(f"{OB}/{mine['items'][0]['id']}", headers=hseq).json() == {"ok": True}  # their own
    assert client.post(f"{OB}/bulk-delete", json={}, headers=admin).status_code == 422
    assert client.post(f"{OB}/bulk-delete", json={"status": "cancelled"}, headers=hseq).status_code == 403
    assert client.post(f"{OB}/bulk-delete", json={"status": "cancelled"}, headers=admin).json() == {"deleted": 1}
    assert client.get(OB, headers=admin).json()["total"] == 0
    assert client.post(f"{OB}/bulk-delete", json={"status": "queued"}, headers=admin).status_code == 422  # never by filter


# ---------- notifications ----------

def test_notifications_follow_what_each_person_may_see(world, client, db):
    admin, worker, hseq = world.auth("admin1"), world.auth("worker1"), world.auth("hseq1")
    tom = world.add_worker("Tom", "Okafor", link="worker1")
    mia = world.add_worker("Mia", "Tanaka")
    soon = (date.today() + timedelta(days=5)).isoformat()
    past = (date.today() - timedelta(days=5)).isoformat()
    client.post(f"/api/workers/{tom.id}/certifications", json={"kind": "first_aid", "expiry_date": soon}, headers=admin)
    client.post(f"/api/workers/{mia.id}/certifications", json={"kind": "white_card", "expiry_date": past}, headers=admin)
    job = client.post("/api/daily-jobs", json={"worker_id": tom.id, "site_name": "Depot"}, headers=admin).json()

    mine = client.get(N, headers=worker).json()
    assert [(i["category"], i["title"]) for i in mine["items"]] == [("certifications", "Your First Aid expires soon"), ("jobs", "You have a job today: Depot")]
    assert mine["unread_count"] == 2 and mine["per_category_unread"] == {"certifications": 1, "jobs": 1}
    assert "Mia" not in json.dumps(mine)

    office = client.get(N, headers=admin).json()
    titles = [i["title"] for i in office["items"]]
    assert "Mia Tanaka: White Card (Construction Induction) has expired" in titles and "Tom Okafor: First Aid expires soon" in titles
    assert next(i for i in office["items"] if "Mia" in i["title"])["severity"] == "bad"
    client.post(f"/api/daily-jobs/{job['id']}/decline", json={"reason": "Off sick"}, headers=worker)
    assert "Tom Okafor cannot do the job at Depot" in [i["title"] for i in client.get(N, headers=admin).json()["items"]]
    assert "Tom Okafor cannot do the job at Depot" not in [i["title"] for i in client.get(N, headers=hseq).json()["items"]]  # admins issue jobs
    assert client.get(N, headers=world.auth("admin2")).json() == {"items": [], "unread_count": 0, "per_category_unread": {}}
    assert client.get(N).status_code == 401

    # Read state is per person; marking is idempotent; someone else's item cannot be marked.
    cert_note = next(i for i in office["items"] if "Mia" in i["title"])
    assert client.post(f"{N}/{cert_note['id']}/read", headers=worker).status_code == 404
    assert client.post(f"{N}/{cert_note['id']}/read", headers=admin).json() == {"ok": True}
    assert client.post(f"{N}/{cert_note['id']}/read", headers=admin).json() == {"ok": True}
    after = client.get(N, headers=admin).json()
    assert next(i for i in after["items"] if i["id"] == cert_note["id"])["read"] is True and after["items"][-1]["id"] == cert_note["id"]
    assert next(i for i in client.get(N, headers=hseq).json()["items"] if i["id"] == cert_note["id"])["read"] is False
    assert client.post(f"{N}/mark-all-read", headers=admin).json()["marked"] >= 1
    assert client.get(N, headers=admin).json()["unread_count"] == 0
    # Once the certificate is renewed, the notification goes away by itself.
    cert_id = cert_note["id"].split(":")[1]
    client.post(f"/api/workers/certifications/{cert_id}/renew", json={"expiry_date": (date.today() + timedelta(days=400)).isoformat()}, headers=admin)
    assert cert_note["id"] not in [i["id"] for i in client.get(N, headers=admin).json()["items"]]
