import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, openFile, type Cert, type CertKind } from "../api";
import { useAuth } from "../auth";
import { CertChip, Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";
import { ExpiryCell } from "./WorkerDetail";

interface Register {
  items: Cert[];
  total: number;
  limit: number;
  offset: number;
  summary: { valid: number; expiring: number; expired: number; unknown: number };
  window_days: number;
}

interface Settings {
  expiry_window_days: number;
  expected_kinds: string[];
  gate_enabled: boolean;
  encryption: { configured: boolean; message: string | null } | null;
}

const PAGE_SIZE = 25;
const TILES: { key: keyof Register["summary"]; label: string; tone: string }[] = [
  { key: "expired", label: "Expired", tone: "bad" },
  { key: "expiring", label: "Expiring soon", tone: "warn" },
  { key: "unknown", label: "Expiry not recorded", tone: "dashed" },
  { key: "valid", label: "Valid", tone: "ok" },
];

export function Certifications() {
  const { me, can } = useAuth();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [inactive, setInactive] = useState(false);
  const [offset, setOffset] = useState(0);
  const [panel, setPanel] = useState<"settings" | "kinds" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const team = can("workers", "team_view");

  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const kinds = useLoad(() => api<{ items: CertKind[] }>("/api/certification-kinds?include_inactive=true"), []);
  const register = useLoad(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (query) params.set("q", query);
    if (status) params.set("status", status);
    if (kind) params.set("kind", kind);
    if (inactive) params.set("include_inactive_workers", "true");
    return api<Register>(`/api/workers/certifications/all?${params}`);
  }, [query, status, kind, inactive, offset]);
  const page = register.data;
  const filtered = Boolean(query || status || kind || inactive);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Certifications</h1>
          <p className="muted">
            {team ? "Current certificates across the roster." : "Your own certificates."}
            {page ? ` “Expiring soon” means within ${page.window_days} days.` : ""} Add or renew a certificate from the worker's page.
          </p>
        </div>
        <div className="row">
          {can("certifications", "edit") && <button type="button" className="btn" onClick={() => setPanel("kinds")}>Certificate types</button>}
          {me?.role_id === "admin" && <button type="button" className="btn" onClick={() => setPanel("settings")}>Settings</button>}
        </div>
      </div>

      {message && <Notice tone="ok">{message}</Notice>}

      {page && (
        <div className="tiles" role="group" aria-label="Filter by status">
          {TILES.map((tile) => (
            <button key={tile.key} type="button" className={`tile ${tile.tone}`} aria-pressed={status === tile.key}
              onClick={() => { setStatus(status === tile.key ? "" : tile.key); setOffset(0); }}>
              <span className="tile-num">{page.summary[tile.key]}</span>
              <span>{tile.label}</span>
            </button>
          ))}
        </div>
      )}

      <section className="card">
        <div className="toolbar">
          {team && (
            <label className="field" style={{ flex: "1 1 220px" }}>
              Search worker, type or number
              <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
            </label>
          )}
          <label className="field">
            Type
            <select value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0); }}>
              <option value="">All</option>
              {(kinds.data?.items ?? []).map((k) => <option key={k.slug} value={k.slug}>{k.label}{k.active ? "" : " (retired)"}</option>)}
            </select>
          </label>
          {team && (
            <label className="check" style={{ alignSelf: "flex-end", minHeight: 44 }}>
              <input type="checkbox" checked={inactive} onChange={(e) => { setInactive(e.target.checked); setOffset(0); }} /> Include inactive and archived workers
            </label>
          )}
          {filtered && (
            <button type="button" className="btn" onClick={() => { setSearch(""); setStatus(""); setKind(""); setInactive(false); setOffset(0); }}>Clear filters</button>
          )}
        </div>
        {status && (
          <div className="card-pad" style={{ paddingTop: 0, paddingBottom: 8 }}>
            <span className="small">Showing: <CertChip status={status} label={TILES.find((t) => t.key === status)?.label ?? status} /></span>
          </div>
        )}

        {register.error ? (
          <div className="card-pad"><LoadError message={register.error} onRetry={register.reload} /></div>
        ) : !page ? (
          <Loading />
        ) : page.items.length === 0 ? (
          <div className="empty">{filtered ? "No certificates match these filters." : "No certificates recorded yet. Add them from each worker's page."}</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Worker</th>
                    <th scope="col">Certificate</th>
                    <th scope="col">Number</th>
                    <th scope="col">Expires</th>
                    <th scope="col">Status</th>
                    <th scope="col">File</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <Link to={`/app/workers/${c.worker.id}`}>{c.worker.name}</Link>
                        {c.worker.status !== "active" ? <span className="muted small"> · {c.worker.status}</span> : null}
                      </td>
                      <td>{c.kind.label}</td>
                      <td>{c.number ?? "—"}</td>
                      <td><ExpiryCell cert={c} /></td>
                      <td>{c.archived_at ? <Chip tone="dashed" icon={false}>Archived</Chip> : <CertChip status={c.status} label={c.status_label} />}</td>
                      <td>
                        {c.evidence && c.evidence_url
                          ? <button type="button" className="link-btn" onClick={() => void openFile(c.evidence_url!, c.evidence!.name, false).catch(() => undefined)}>View</button>
                          : <span className="muted">None</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
              <span className="muted small" role="status">
                Showing {page.offset + 1}–{page.offset + page.items.length} of {page.total}{filtered ? " matching" : ""} {register.loading ? "· updating…" : ""}
              </span>
              <span className="spacer" />
              <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
              <button type="button" className="btn small" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
            </div>
          </>
        )}
      </section>

      {panel === "settings" && kinds.data && (
        <SettingsModal kinds={kinds.data.items.filter((k) => k.active)} onClose={() => setPanel(null)}
          onSaved={() => { setPanel(null); setMessage("Certification settings saved."); register.reload(); }} />
      )}
      {panel === "kinds" && <KindsModal onClose={() => { setPanel(null); kinds.reload(); }} />}
    </>
  );
}

function SettingsModal({ kinds, onClose, onSaved }: { kinds: CertKind[]; onClose: () => void; onSaved: () => void }) {
  const settings = useLoad(() => api<Settings>("/api/certifications/settings"), []);
  const [days, setDays] = useState<number | null>(null);
  const [expected, setExpected] = useState<string[] | null>(null);
  const [gate, setGate] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const s = settings.data;
  if (settings.error) return <Modal title="Certification settings" onClose={onClose}><Notice tone="bad">{settings.error}</Notice></Modal>;
  if (!s) return <Modal title="Certification settings" onClose={onClose}><Loading /></Modal>;
  const cur = { days: days ?? s.expiry_window_days, expected: expected ?? s.expected_kinds, gate: gate ?? s.gate_enabled };

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api("/api/certifications/settings", { method: "PUT", body: { expiry_window_days: cur.days, expected_kinds: cur.expected, gate_enabled: cur.gate } });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Modal title="Certification settings" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        {s.encryption && !s.encryption.configured && <Notice tone="warn">Protected worker details cannot be saved yet: {s.encryption.message}</Notice>}
        <label className="field">
          Warn this many days before expiry
          <input type="number" min={1} max={365} required value={cur.days} onChange={(e) => setDays(Number(e.target.value))} />
        </label>
        <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend style={{ fontWeight: 600, padding: 0 }}>Certificates every active worker is expected to hold</legend>
          <span className="small muted">A worker without one of these shows as “Missing”.</span>
          <div className="grid-2">
            {kinds.map((k) => (
              <label className="check" key={k.slug}>
                <input type="checkbox" checked={cur.expected.includes(k.slug)}
                  onChange={(e) => setExpected(e.target.checked ? [...cur.expected, k.slug] : cur.expected.filter((x) => x !== k.slug))} /> {k.label}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="check">
          <input type="checkbox" checked={cur.gate} onChange={(e) => setGate(e.target.checked)} /> Allow forms to require certificates
        </label>
        <p className="small muted">
          This is the organisation-wide switch. A form is only restricted when this is on <strong>and</strong> the form's own “require certificates” switch is on.
          People without the certificates listed on that form, or whose login is not linked to a worker record, will not be able to fill it in.
        </p>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary">Save settings</button>
        </div>
      </form>
    </Modal>
  );
}

function KindsModal({ onClose }: { onClose: () => void }) {
  const kinds = useLoad(() => api<{ items: CertKind[] }>("/api/certification-kinds?include_inactive=true"), []);
  const [label, setLabel] = useState("");
  const [aliases, setAliases] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function add(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api("/api/certification-kinds", { method: "POST", body: { label, aliases: aliases.split(",").map((a) => a.trim()).filter(Boolean) } });
      setLabel("");
      setAliases("");
      kinds.reload();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function toggle(kind: CertKind) {
    setError(null);
    try {
      await api(`/api/certification-kinds/${kind.id}`, { method: "PATCH", body: { active: !kind.active } });
      kinds.reload();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Modal title="Certificate types" onClose={onClose}>
      <div className="stack">
        {error && <Notice tone="bad">{error}</Notice>}
        <form className="form" onSubmit={add}>
          <div className="grid-2">
            <label className="field">New type<input required minLength={2} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Chainsaw operation" /></label>
            <label className="field">Other names (optional, comma separated)<input value={aliases} onChange={(e) => setAliases(e.target.value)} /></label>
          </div>
          <div><button type="submit" className="btn primary">Add type</button></div>
        </form>
        {kinds.error ? <Notice tone="bad">{kinds.error}</Notice> : !kinds.data ? <Loading /> : (
          <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
            <table>
              <thead><tr><th scope="col">Type</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {kinds.data.items.map((k) => (
                  <tr key={k.id}>
                    <td>{k.label}{k.aliases.length > 0 ? <div className="small muted">Also: {k.aliases.join(", ")}</div> : null}</td>
                    <td>{k.active ? <Chip tone="ok">In use</Chip> : <Chip tone="dashed" icon={false}>Retired</Chip>}</td>
                    <td><button type="button" className="btn small" onClick={() => void toggle(k)}>{k.active ? "Retire" : "Bring back"}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted">Retiring a type hides it from new entries. Certificates already recorded keep it.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" className="btn" onClick={onClose}>Done</button></div>
      </div>
    </Modal>
  );
}
