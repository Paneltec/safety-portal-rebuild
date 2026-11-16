"""Navixy: the tracker list that feeds the fleet register (stage 6a).

Rules (integration document, section III):
  * a tracker is matched to an asset on its Navixy id, never on its label;
  * the sign-in key Navixy issues can expire. When Navixy says so, sign in again ONCE with the
    stored details, store the new key encrypted and retry; never loop;
  * an outage changes nothing: no asset is removed, archived or marked as missing because a call failed;
  * a tracker that is genuinely no longer listed is flagged on its asset for a person to deal with;
  * missing readings stay missing. They are never turned into zero;
  * this adapter only reads. It has no ability to control a vehicle.

Grouping uses Navixy TAGS (not Navixy's separate "groups"); screens say so.

The simulator returns invented trackers. Assets it creates carry source "simulator", so they can
never be confused with, or adopted as, real Navixy trackers.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import fleet, integrations
from .config import get_settings
from .crypto import decrypt_json, encrypt_json
from .integrations import ProviderError
from .models import Asset, ImportRun, IntegrationConfig, utcnow
from .services import iso

INVALID_HASH_CODES = {3, 4}  # "wrong hash" and "session ended"
MAX_TRACKERS = 5000


@dataclass
class Fetched:
    trackers: list[dict[str, Any]]
    tags: list[dict[str, str]]
    warnings: list[str] = field(default_factory=list)


class _Session:
    """Calls to one Navixy account, with one sign-in refresh at most."""

    def __init__(self, config: IntegrationConfig):
        self.config = config
        self.settings = integrations.effective_settings(integrations.CATALOGUE["navixy"], config)
        self.secrets = decrypt_json(config.secrets)
        self.refreshed = False

    def _post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        hidden = [self.secrets.get("password", ""), self.secrets.get("session_hash", "")]
        timeout = get_settings().provider_timeout_seconds
        try:
            with integrations._client() as client:
                response = client.post(f"{self.settings['api_base_url']}/v2/{path}", json=body)
        except httpx.TimeoutException:
            raise ProviderError(f"Navixy did not answer within {timeout:g} seconds.") from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"Navixy could not be reached ({integrations._redact(type(exc).__name__, hidden)}).") from None
        if response.status_code == 429:
            raise ProviderError("Navixy says it is being called too often (HTTP 429). Wait a few minutes and try again.")
        try:
            return response.status_code, response.json()
        except ValueError:
            raise ProviderError(f"Navixy answered, but not in the expected format (HTTP {response.status_code}). Check the address is the API address for your region.") from None

    def _sign_in(self) -> None:
        _, data = self._post("user/auth", {"login": self.settings.get("email"), "password": self.secrets.get("password")})
        if not isinstance(data, dict) or data.get("success") is not True or not data.get("hash"):
            raise ProviderError("Navixy did not accept the stored login. Check the email and password under Connections.")
        self.secrets["session_hash"] = str(data["hash"])
        self.config.secrets = encrypt_json(self.secrets)

    def call(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.secrets.get("session_hash"):
            self._sign_in()
            self.refreshed = True
        for _ in range(2):
            status, data = self._post(path, {**(body or {}), "hash": self.secrets["session_hash"]})
            if isinstance(data, dict) and data.get("success") is True:
                return data
            code = (data.get("status") or {}).get("code") if isinstance(data, dict) and isinstance(data.get("status"), dict) else None
            if code in INVALID_HASH_CODES and not self.refreshed:
                self._sign_in()
                self.refreshed = True
                continue
            if code in INVALID_HASH_CODES:
                raise ProviderError("Navixy would not accept a fresh sign-in. Check the login under Connections.")
            raise ProviderError(f"Navixy returned an error for {path} (HTTP {status}{f', code {code}' if code is not None else ''}).")
        raise ProviderError("Navixy could not be read.")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) and number >= 0 else None


def _instant(value: Any) -> str | None:
    """Navixy reports times as 'YYYY-MM-DD HH:MM:SS' in UTC. Anything else is left out rather than guessed."""
    if not value:
        return None
    try:
        return iso(datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc))
    except ValueError:
        return None


def _simulated() -> Fetched:
    tags = [{"id": "sim-1", "name": "Sim Civil"}, {"id": "sim-2", "name": "Sim Traffic"}]
    rows = [("9001", "Sim Ute 01", ["sim-1"], 48210.0, None), ("9002", "Sim Tipper 02", ["sim-1"], 131904.5, 6120.0), ("9003", "Sim Excavator 03", ["sim-1"], None, 2840.5),
            ("9004", "Sim Arrow Board Trailer 04", ["sim-2"], None, None), ("9005", "Sim Traffic Ute 05", ["sim-2"], 76455.0, None)]
    names = {t["id"]: t["name"] for t in tags}
    now = iso(utcnow())
    return Fetched([{"source_id": i, "name": n, "model": "simulated tracker", "tag_ids": t, "tag_names": [names[x] for x in t],
                     "state": {"lat": None, "lng": None, "speed_kmh": None, "last_seen_at": now, "connection": "simulated"}, "odometer_km": o, "engine_hours": h}
                    for i, n, t, o, h in rows], tags)


def fetch(config: IntegrationConfig) -> Fetched:
    """Every tracker on the account with its tags, last position and meters where Navixy provides them."""
    if config.mode == "simulator":
        return _simulated()
    session = _Session(config)
    listing = session.call("tracker/list").get("list")
    if not isinstance(listing, list):
        raise ProviderError("Navixy answered, but not with a list of trackers.")
    if len(listing) > MAX_TRACKERS:
        raise ProviderError(f"Navixy listed more than {MAX_TRACKERS} trackers. The sync stopped rather than guess.")
    warnings: list[str] = []
    tags: list[dict[str, str]] = []
    try:
        tags = [{"id": str(t["id"]), "name": str(t.get("name") or t["id"])} for t in session.call("tag/list").get("list") or [] if isinstance(t, dict) and t.get("id") is not None]
    except ProviderError:
        warnings.append("Navixy tags could not be read, so vehicles are not grouped.")
    names = {t["id"]: t["name"] for t in tags}
    trackers = []
    for raw in listing:
        if not isinstance(raw, dict) or raw.get("id") is None or not str(raw.get("label") or "").strip():
            continue
        tag_ids = [str(b["tag_id"]) for b in raw.get("tag_bindings") or [] if isinstance(b, dict) and b.get("tag_id") is not None]
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        trackers.append({"source_id": str(raw["id"]), "name": str(raw["label"]).strip()[:200], "model": str(source.get("model") or "")[:80] or None,
                         "tag_ids": tag_ids, "tag_names": [names[t] for t in tag_ids if t in names], "state": None, "odometer_km": None, "engine_hours": None})
    ids = [int(t["source_id"]) for t in trackers if t["source_id"].isdigit()]
    if ids:
        try:
            states = session.call("tracker/get_states", {"trackers": ids}).get("states") or {}
            for t in trackers:
                s = states.get(t["source_id"]) if isinstance(states, dict) else None
                if isinstance(s, dict):
                    gps = s.get("gps") if isinstance(s.get("gps"), dict) else {}
                    loc = gps.get("location") if isinstance(gps.get("location"), dict) else {}
                    t["state"] = {"lat": _number_signed(loc.get("lat")), "lng": _number_signed(loc.get("lng")), "speed_kmh": _number(gps.get("speed")),
                                  "last_seen_at": _instant(s.get("last_update") or gps.get("updated")), "connection": str(s.get("connection_status") or "")[:30] or None}
        except ProviderError:
            warnings.append("Navixy positions could not be read. Locations are unchanged.")
        for meter, key in (("odometer", "odometer_km"), ("engine_hours", "engine_hours")):
            try:
                values = session.call("tracker/counter/value/list", {"trackers": ids, "type": meter}).get("value") or {}
                for t in trackers:
                    t[key] = _number(values.get(t["source_id"])) if isinstance(values, dict) else None
            except ProviderError:
                warnings.append(f"Navixy {'odometer' if meter == 'odometer' else 'hour meter'} readings are not available on this account. Meters are unchanged.")
    return Fetched(trackers, tags, warnings)


def _number_signed(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if -180 <= number <= 180 else None


def source_name(config: IntegrationConfig) -> str:
    return "simulator" if config.mode == "simulator" else "navixy"


def selected_tags(config: IntegrationConfig) -> list[str]:
    return [str(t) for t in (config.settings or {}).get("selected_tags") or []]


def run(db: Session, org_id: str, config: IntegrationConfig, *, actor_id: str | None, dry_run: bool, today) -> tuple[ImportRun, dict[str, Any]]:
    """Bring the tracker list into the fleet register. A preview changes nothing."""
    source = source_name(config)
    run_row = ImportRun(org_id=org_id, provider="navixy", resource="assets", mode=config.mode, dry_run=dry_run, status="failed", started_by=actor_id, counts={}, companies=[])
    db.add(run_row)
    detail: dict[str, Any] = {"created": [], "updated": [], "not_listed": [], "warnings": [], "tags": []}
    try:
        fetched = fetch(config)
    except ProviderError as exc:
        run_row.error, run_row.finished_at = str(exc)[:400], utcnow()
        return run_row, detail
    wanted = set(selected_tags(config))
    in_scope = [t for t in fetched.trackers if not wanted or wanted & set(t["tag_ids"])]
    existing = {a.source_id: a for a in db.scalars(select(Asset).where(Asset.org_id == org_id, Asset.source_system == source)).all()}
    counts = {"listed": len(fetched.trackers), "in_scope": len(in_scope), "created": 0, "updated": 0, "unchanged": 0, "kept_local": 0, "removed_here": 0, "not_listed": 0, "meter_review": 0}
    seen: set[str] = set()
    for t in in_scope:
        seen.add(t["source_id"])
        asset = existing.get(t["source_id"])
        if asset is not None and asset.deleted_at is not None:
            counts["removed_here"] += 1  # deleted in the portal on purpose: not brought back
            continue
        data = {"model": t["model"], "tag_ids": t["tag_ids"]}
        if asset is None:
            counts["created"] += 1
            detail["created"].append({"name": t["name"], "tags": t["tag_names"]})
            if dry_run:
                continue
            asset = Asset(org_id=org_id, kind="vehicle", name=t["name"], status="active", source_system=source, source_id=t["source_id"], source_data=data,
                          source_groups=t["tag_names"], tracker_state=t["state"], scan_token=fleet.new_scan_token(), created_by=actor_id, locally_edited=[])
            db.add(asset)
            db.flush()
        else:
            changed = []
            if asset.name != t["name"]:
                if "name" in (asset.locally_edited or []):
                    counts["kept_local"] += 1
                else:
                    changed.append("name")
            if list(asset.source_groups or []) != t["tag_names"]:
                changed.append("tags")
            if asset.source_missing:
                changed.append("listed again")
            if changed:
                counts["updated"] += 1
                detail["updated"].append({"name": asset.name, "changes": changed})
            else:
                counts["unchanged"] += 1
            if dry_run:
                continue
            if "name" in changed:
                asset.name = t["name"]
            asset.source_groups, asset.source_data, asset.source_missing = t["tag_names"], data, False
            if t["state"] is not None:
                asset.tracker_state = t["state"]
        if t["odometer_km"] is not None or t["engine_hours"] is not None:
            result = fleet.apply_meter(db, asset, odometer_km=t["odometer_km"], engine_hours=t["engine_hours"], source=source, reading_date=today, recorded_by=None)
            if not result["applied"]:
                counts["meter_review"] += 1
    for source_id, asset in existing.items():
        if source_id not in seen and asset.deleted_at is None and not asset.source_missing:
            counts["not_listed"] += 1
            detail["not_listed"].append({"name": asset.name})
            if not dry_run:
                asset.source_missing = True
    detail["warnings"], detail["tags"] = fetched.warnings, fetched.tags
    run_row.counts, run_row.status, run_row.finished_at = counts, "previewed" if dry_run else "committed", utcnow()
    if not dry_run:
        config.last_sync_at = utcnow()
        config.capabilities = {**(config.capabilities or {}), "tags": fetched.tags}
    return run_row, detail
