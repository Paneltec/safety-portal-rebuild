"""Provider connections: the catalogue, saved configuration, and the adapter for each provider.

Every adapter follows one contract (integration document, section II):
  * settings are validated; secrets are encrypted and never returned;
  * `test` is non-destructive, has a bounded timeout, maps errors to plain words and never puts a
    secret in a message or a log;
  * a simulator is available for every provider and is always labelled as one. A simulated result
    is never recorded or shown as a live connection.

"Connected" only ever means "the last test with these credentials succeeded". It says nothing
about which datasets the account may read, whether a sync has run, or whether a message was
delivered; those are reported separately.
"""
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .crypto import decrypt_json, encrypt_json
from .models import IntegrationConfig, utcnow

# Tests replace this with an httpx.MockTransport. None means the real network.
transport: httpx.BaseTransport | None = None


@dataclass
class Field:
    name: str
    label: str
    kind: str = "text"  # text | url | email | number | bool | list
    secret: bool = False
    required: bool = False
    default: Any = None
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None


@dataclass
class Kind:
    key: str
    label: str
    purpose: str
    fields: list[Field]
    channel: str | None = None  # the outbound channel this provider delivers, if any
    notes: list[str] = field(default_factory=list)


CATALOGUE: dict[str, Kind] = {k.key: k for k in [
    Kind("simpro", "Simpro", "Workers, sites, jobs and suppliers are imported from Simpro.", [
        Field("api_base_url", "Your Simpro address", "url", required=True, help="For example https://yourcompany.simprosuite.com"),
        Field("api_token", "API token", secret=True, required=True),
        Field("company_ids", "Company numbers to import", "list", help="The Simpro company ids, for example 0 and 2. Leave empty to check the token only."),
    ]),
    Kind("navixy", "Navixy", "Vehicle and plant locations, and meter readings.", [
        Field("api_base_url", "Navixy API address for your region", "url", required=True,
              help="Use the address for your account's region, for example https://api.eu.navixy.com or https://api.us.navixy.com"),
        Field("email", "Login email", "email", required=True),
        Field("password", "Password", secret=True, required=True),
        Field("account_id", "Account number (optional)"),
        Field("poll_seconds", "Seconds between location updates", "number", default=30, minimum=10, maximum=600),
        Field("auto_poll", "Update locations automatically", "bool", default=False),
    ], notes=["A sign-in key issued by Navixy is stored encrypted and refreshed when it expires.",
              "The portal only reads from Navixy. It cannot control a vehicle."]),
    Kind("smartfill", "SmartFill (FMT Data)", "Fuel transactions and tank levels.", [
        Field("api_url", "API address", "url", default="https://fmtdata.com/API/api.php"),
        Field("api_key", "Client reference", required=True, help="Issued by FMT Data. This is not your SmartFill website login."),
        Field("api_secret", "Client secret", secret=True, required=True),
    ], notes=["SmartFill limits how often it can be called (about 6 a minute). Testing counts as one call."]),
    Kind("fuelcheck", "FuelCheck Tasmania", "Daily diesel prices, used once a week to set the diesel rate for fuel reports.", [],
         notes=["A public price source: there is nothing to sign in to.",
                "Testing reads the price list once. A week's rate is only fixed when you ask for it on the Fuel page."]),
    Kind("microsoft365", "Microsoft 365", "Sends the portal's emails from one of your mailboxes.", [
        Field("tenant_id", "Directory (tenant) ID", required=True),
        Field("client_id", "Application (client) ID", required=True),
        Field("client_secret", "Client secret", secret=True, required=True),
        Field("sender_email", "Send from this mailbox", "email", required=True),
        Field("reply_to", "Replies go to (optional)", "email"),
    ], channel="email", notes=["Testing asks Microsoft for an access key only. It does not send an email."]),
    Kind("textmagic", "TextMagic", "Sends the portal's text messages.", [
        Field("username", "TextMagic username", required=True),
        Field("api_key", "API key", secret=True, required=True),
        Field("default_sender_id", "Sender name or number (optional)"),
        Field("daily_budget_aud", "Most to spend on texts in a day ($)", "number", default=10, minimum=0, maximum=1000),
        Field("estimated_cost_per_sms_aud", "Estimated cost of one text ($)", "number", default=0.1, minimum=0, maximum=5,
              help="Used with the daily limit to decide when to stop sending. Check your TextMagic plan for the real price."),
    ], channel="sms"),
]}

MODES = ("off", "simulator", "live")


@dataclass
class CheckResult:
    ok: bool
    message: str
    capabilities: dict[str, Any] = field(default_factory=dict)
    simulated: bool = False
    new_secrets: dict[str, str] = field(default_factory=dict)


class ProviderError(Exception):
    """A failure with a message that is safe to show: no secrets, no raw provider payloads."""


# ---------- configuration ----------

def get_config(db: Session, org_id: str, kind: str) -> IntegrationConfig | None:
    return db.scalars(select(IntegrationConfig).where(IntegrationConfig.org_id == org_id, IntegrationConfig.kind == kind)).first()


def effective_settings(kind: Kind, config: IntegrationConfig | None) -> dict[str, Any]:
    saved = (config.settings if config else None) or {}
    return {f.name: saved.get(f.name, f.default) for f in kind.fields if not f.secret}


def missing_required(kind: Kind, config: IntegrationConfig | None) -> list[str]:
    settings = effective_settings(kind, config)
    held = set((config.secrets_set if config else None) or [])
    out = []
    for f in kind.fields:
        if not f.required:
            continue
        if (f.secret and f.name not in held) or (not f.secret and settings.get(f.name) in (None, "", [])):
            out.append(f.label)
    return out


def status_of(kind: Kind, config: IntegrationConfig | None) -> dict[str, str]:
    """One word for the badge, and a sentence that says exactly what it means."""
    if config is None or (config.mode == "off" and not config.secrets_set and not config.settings):
        return {"state": "not_configured", "label": "Not set up", "detail": "Nothing has been entered for this connection."}
    missing = missing_required(kind, config)
    if config.mode == "off":
        return {"state": "off", "label": "Switched off", "detail": "Details are saved but the connection is switched off."}
    if config.mode == "simulator":
        return {"state": "simulator", "label": "Simulator", "detail": "A built-in simulator answers instead of the real system. Nothing leaves this server."}
    if missing:
        return {"state": "incomplete", "label": "Details missing", "detail": "Still needed: " + ", ".join(missing) + "."}
    if config.last_test_ok is True and config.last_test_mode == "live":
        return {"state": "connected", "label": "Connected", "detail": "The last test with these details succeeded."}
    if config.last_test_ok is False and config.last_test_mode == "live":
        return {"state": "error", "label": "Not working", "detail": config.last_test_message or "The last test failed."}
    return {"state": "untested", "label": "Not tested yet", "detail": "Details are saved. Press Test connection to check them."}


def _clean_value(f: Field, value: Any) -> Any:
    if value is None or value == "":
        return None
    if f.kind == "bool":
        if not isinstance(value, bool):
            raise HTTPException(422, f"{f.label}: choose yes or no")
        return value
    if f.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(422, f"{f.label}: enter a number")
        if (f.minimum is not None and value < f.minimum) or (f.maximum is not None and value > f.maximum):
            raise HTTPException(422, f"{f.label}: must be between {f.minimum:g} and {f.maximum:g}")
        return value
    if f.kind == "list":
        if not isinstance(value, list):
            raise HTTPException(422, f"{f.label}: give a list")
        items = list(dict.fromkeys(str(v).strip() for v in value if str(v).strip()))
        if len(items) > 50 or any(len(i) > 40 for i in items):
            raise HTTPException(422, f"{f.label}: too many or too long")
        return items
    if not isinstance(value, str):
        raise HTTPException(422, f"{f.label}: enter text")
    value = value.strip()
    if len(value) > 500:
        raise HTTPException(422, f"{f.label}: too long")
    if f.kind == "url":
        check_url(value, f.label)
        value = value.rstrip("/")
    if f.kind == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise HTTPException(422, f"{f.label}: enter an email address")
    return value


def check_url(value: str, label: str = "Address") -> None:
    """Only public https addresses. The server will call this address, so it must not be able to
    reach the portal's own network."""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise HTTPException(422, f"{label}: enter a full https:// address")
    if host in ("localhost",) or host.endswith((".local", ".internal", ".lan")) or "." not in host:
        raise HTTPException(422, f"{label}: this must be the provider's public address")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if not ip.is_global:
        raise HTTPException(422, f"{label}: this must be the provider's public address")


def is_mask(value: Any) -> bool:
    """A value made only of mask characters is the display placeholder, never a new secret."""
    return isinstance(value, str) and value.strip() != "" and set(value.strip()) <= {"•", "*", "●"}


def save_config(db: Session, org_id: str, kind: Kind, *, settings: dict[str, Any] | None, secrets: dict[str, Any] | None,
                clear_secrets: list[str] | None, mode: str | None, user_id: str | None) -> tuple[IntegrationConfig, list[str]]:
    """Apply a change. A secret that is omitted, empty or a mask is kept; `clear_secrets` removes one on purpose."""
    config = get_config(db, org_id, kind.key)
    if config is None:
        config = IntegrationConfig(org_id=org_id, kind=kind.key, settings={}, secrets_set=[])
        db.add(config)
    by_name = {f.name: f for f in kind.fields}
    changed: list[str] = []
    if settings is not None:
        current = dict(config.settings or {})
        for name, value in settings.items():
            f = by_name.get(name)
            if f is None or f.secret:
                raise HTTPException(422, f"Unknown setting: {name}")
            cleaned = _clean_value(f, value)
            if cleaned is None:
                if name in current:
                    current.pop(name)
                    changed.append(name)
            elif current.get(name) != cleaned:
                current[name] = cleaned
                changed.append(name)
        config.settings = current
    new_secrets = {n: v for n, v in (secrets or {}).items() if v not in (None, "") and not is_mask(v)}
    to_clear = list(clear_secrets or [])
    for name in [*new_secrets, *to_clear]:
        f = by_name.get(name)
        if f is None or not f.secret:
            raise HTTPException(422, f"Unknown secret: {name}")
    if new_secrets or to_clear:
        # Decrypt first: if the key is missing or wrong this stops here, before anything is written.
        store = decrypt_json(config.secrets)
        for name, value in new_secrets.items():
            if not isinstance(value, str) or len(value) > 4000:
                raise HTTPException(422, f"{by_name[name].label}: enter text")
            store[name] = value.strip()
            changed.append(name)
        for name in to_clear:
            if store.pop(name, None) is not None:
                changed.append(name)
        if "password" in new_secrets or "password" in to_clear:
            store.pop("session_hash", None)  # a sign-in key belongs to the old password
        config.secrets = encrypt_json(store) if store else None
        config.secrets_set = sorted(k for k in store if k in by_name)
    if mode is not None:
        if mode not in MODES:
            raise HTTPException(422, "mode must be off, simulator or live")
        if config.mode != mode:
            config.mode = mode
            changed.append("mode")
    if changed:
        # Whatever was proven before no longer describes these details.
        config.last_test_at = config.last_test_ok = config.last_test_mode = config.last_test_message = None
        config.capabilities = {}
        config.updated_by = user_id
    return config, changed


# ---------- calling providers ----------

def _client() -> httpx.Client:
    return httpx.Client(timeout=get_settings().provider_timeout_seconds, transport=transport, follow_redirects=False)


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[hidden]")
    return re.sub(r"\s+", " ", text)[:200]


def call(method: str, url: str, *, secrets: list[str], provider: str, **kwargs: Any) -> httpx.Response:
    """One bounded request. Raises ProviderError with words that are safe to show."""
    timeout = get_settings().provider_timeout_seconds
    try:
        with _client() as client:
            response = client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise ProviderError(f"{provider} did not answer within {timeout:g} seconds.") from None
    except httpx.HTTPError as exc:
        raise ProviderError(f"{provider} could not be reached ({_redact(type(exc).__name__, secrets)}). Check the address and that the server has internet access.") from None
    if response.status_code in (401, 403):
        raise ProviderError(f"{provider} rejected these details (HTTP {response.status_code}). Check they are correct and still active.")
    if response.status_code == 429:
        raise ProviderError(f"{provider} says it is being called too often (HTTP 429). Wait a few minutes and try again.")
    if response.status_code >= 400:
        raise ProviderError(f"{provider} returned an error (HTTP {response.status_code}).")
    return response


def _json(response: httpx.Response, provider: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise ProviderError(f"{provider} answered, but not in the expected format. Check the address is the API address.") from None


def _test_simpro(s: dict[str, Any], x: dict[str, str]) -> CheckResult:
    data = _json(call("GET", f"{s['api_base_url']}/api/v1.0/companies/", provider="Simpro", secrets=[x["api_token"]],
                      headers={"Authorization": f"Bearer {x['api_token']}", "Accept": "application/json"}), "Simpro")
    if not isinstance(data, list):
        raise ProviderError("Simpro answered, but not with a list of companies.")
    found = {str(c.get("ID")): str(c.get("Name") or "") for c in data if isinstance(c, dict)}
    wanted = [str(c) for c in (s.get("company_ids") or [])]
    missing = [c for c in wanted if c not in found]
    caps = {"companies_found": [{"id": k, "name": v} for k, v in found.items()], "companies_missing": missing}
    if missing:
        return CheckResult(False, f"The token works, but Simpro has no company numbered {', '.join(missing)}. It has: {', '.join(sorted(found)) or 'none'}.", caps)
    return CheckResult(True, f"Signed in to Simpro. {len(found)} compan{'y' if len(found) == 1 else 'ies'} visible. Importing has not been checked by this test.", caps)


def _test_navixy(s: dict[str, Any], x: dict[str, str]) -> CheckResult:
    data = _json(call("POST", f"{s['api_base_url']}/v2/user/auth", provider="Navixy", secrets=[x["password"]],
                      json={"login": s["email"], "password": x["password"]}), "Navixy")
    if not isinstance(data, dict) or data.get("success") is not True or not data.get("hash"):
        raise ProviderError("Navixy did not accept this login. Check the email, the password and that the address is for your region.")
    return CheckResult(True, "Signed in to Navixy. Vehicle lists have not been checked by this test.", {"signed_in": True},
                      new_secrets={"session_hash": str(data["hash"])})


def _test_smartfill(s: dict[str, Any], x: dict[str, str]) -> CheckResult:
    body = {"jsonrpc": "2.0", "method": "Tank:Level", "id": 1,
            "parameters": {"clientReference": s["api_key"], "clientSecret": x["api_secret"]}}  # "parameters", as SmartFill expects
    data = _json(call("POST", s.get("api_url") or "https://fmtdata.com/API/api.php", provider="SmartFill", secrets=[x["api_secret"]], json=body), "SmartFill")
    if not isinstance(data, dict) or data.get("error"):
        raise ProviderError("SmartFill did not accept the client reference and secret.")
    return CheckResult(True, "SmartFill accepted the client reference and secret. Importing transactions has not been checked by this test.", {"tank_level": True})


def _test_fuelcheck(s: dict[str, Any], x: dict[str, str]) -> CheckResult:
    from . import fuel
    data = _json(call("POST", fuel.FUELCHECK_URL, provider="FuelCheck", secrets=[], json=fuel.FUELCHECK_BODY), "FuelCheck")
    entries = data.get("AveragePrices") if isinstance(data, dict) else None
    days = {str(e.get("Captured"))[:10] for e in entries if isinstance(e, dict) and e.get("Code") == "DL" and e.get("Period") == "Month"} if isinstance(entries, list) else set()
    if not days:
        raise ProviderError("FuelCheck answered, but with no daily diesel prices in the expected format.")
    return CheckResult(True, f"FuelCheck answered with daily diesel prices for {len(days)} days.", {"days": len(days)})


def m365_token(s: dict[str, Any], x: dict[str, str]) -> str:
    data = _json(call("POST", f"https://login.microsoftonline.com/{s['tenant_id']}/oauth2/v2.0/token", provider="Microsoft 365",
                      secrets=[x["client_secret"]],
                      data={"client_id": s["client_id"], "client_secret": x["client_secret"], "grant_type": "client_credentials",
                            "scope": "https://graph.microsoft.com/.default"}), "Microsoft 365")
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise ProviderError("Microsoft 365 did not issue an access key for these details.")
    return str(token)


def _test_m365(s: dict[str, Any], x: dict[str, str]) -> CheckResult:
    m365_token(s, x)
    return CheckResult(True, f"Microsoft 365 issued an access key. No email was sent, so sending from {s['sender_email']} has not been proven.",
                      {"token": True, "send_tested": False})


def _tm_headers(s: dict[str, Any], x: dict[str, str]) -> dict[str, str]:
    return {"X-TM-Username": str(s["username"]).strip(), "X-TM-Key": x["api_key"].strip(), "Accept": "application/json"}


def _test_textmagic(s: dict[str, Any], x: dict[str, str]) -> CheckResult:
    data = _json(call("GET", "https://rest.textmagic.com/api/v2/user", provider="TextMagic", secrets=[x["api_key"]], headers=_tm_headers(s, x)), "TextMagic")
    caps: dict[str, Any] = {"account": True}
    if isinstance(data, dict) and isinstance(data.get("balance"), (int, float)):
        caps["balance"] = data["balance"]
    return CheckResult(True, "Signed in to TextMagic. No text was sent.", caps)


_TESTS = {"simpro": _test_simpro, "navixy": _test_navixy, "smartfill": _test_smartfill, "fuelcheck": _test_fuelcheck, "microsoft365": _test_m365, "textmagic": _test_textmagic}


def run_test(db: Session, config: IntegrationConfig, kind: Kind) -> CheckResult:
    """Test a saved connection and record the outcome. Never sends a message or changes provider data."""
    if config.mode == "off":
        raise HTTPException(409, "This connection is switched off. Choose Simulator or Live first.")
    if config.mode == "simulator":
        result = CheckResult(True, "Simulator answered. No real system was contacted, so this does not show your details are correct.", simulated=True)
    else:
        missing = missing_required(kind, config)
        if missing:
            raise HTTPException(422, "Still needed before testing: " + ", ".join(missing) + ".")
        secrets = decrypt_json(config.secrets)
        try:
            result = _TESTS[kind.key](effective_settings(kind, config), secrets)
        except ProviderError as exc:
            result = CheckResult(False, str(exc))
        if result.new_secrets:
            secrets.update(result.new_secrets)
            config.secrets = encrypt_json(secrets)
    config.last_test_at = utcnow()
    config.last_test_ok = result.ok
    config.last_test_mode = config.mode
    config.last_test_message = result.message[:400]
    config.capabilities = result.capabilities
    return result


# ---------- sending ----------

@dataclass
class SendResult:
    outcome: str  # sent | simulated | failed
    detail: str
    provider_message_id: str | None = None


def send(db: Session, org_id: str, channel: str, *, recipient: str, subject: str, body: str) -> SendResult | None:
    """Hand one message to the provider for this channel. None when no provider is ready to send.

    Called only by the comms boundary, which has already applied safe mode and the other checks.
    """
    kind = next((k for k in CATALOGUE.values() if k.channel == channel), None)
    config = get_config(db, org_id, kind.key) if kind else None
    if kind is None or config is None or config.mode == "off":
        return None
    if config.mode == "simulator":
        return SendResult("simulated", f"{kind.label} simulator: nothing left this server.")
    if missing_required(kind, config) or config.last_test_ok is not True or config.last_test_mode != "live":
        return None
    settings = effective_settings(kind, config)
    secrets = decrypt_json(config.secrets)
    try:
        if kind.key == "microsoft365":
            token = m365_token(settings, secrets)
            message: dict[str, Any] = {"subject": subject, "body": {"contentType": "Text", "content": body},
                                       "toRecipients": [{"emailAddress": {"address": recipient}}]}
            if settings.get("reply_to"):
                message["replyTo"] = [{"emailAddress": {"address": settings["reply_to"]}}]
            call("POST", f"https://graph.microsoft.com/v1.0/users/{settings['sender_email']}/sendMail", provider="Microsoft 365",
                 secrets=[secrets["client_secret"], token], headers={"Authorization": f"Bearer {token}"},
                 json={"message": message, "saveToSentItems": True})
            return SendResult("sent", "Microsoft 365 accepted the email for sending. That is not proof it reached the inbox.")
        payload: dict[str, Any] = {"text": body, "phones": re.sub(r"[^\d]", "", recipient)}
        if settings.get("default_sender_id"):
            payload["from"] = settings["default_sender_id"]
        data = _json(call("POST", "https://rest.textmagic.com/api/v2/messages", provider="TextMagic", secrets=[secrets["api_key"]],
                          headers=_tm_headers(settings, secrets), json=payload), "TextMagic")
        return SendResult("sent", "TextMagic accepted the text for sending.", str(data.get("id")) if isinstance(data, dict) and data.get("id") else None)
    except ProviderError as exc:
        return SendResult("failed", str(exc))
