import { useState, type FormEvent } from "react";
import { api, formatDate, formatDateTime, openFile } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface Entry { id: string; at: string; who: string; action: string; target_type: string; target_id: string | null; detail: Record<string, unknown>; request_id: string | null }
interface Log { items: Entry[]; total: number; limit: number; offset: number; target_types: string[]; hidden: string[]; note: string }

function readable(text: string): string {
  const out = text.replace(/[._]/g, " ");
  return out.charAt(0).toUpperCase() + out.slice(1);
}

function bytes(n: number | null): string {
  if (n == null) return "";
  return n < 1024 ? `${n} bytes` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Who changed what, and when. Read-only. */
export function ActivityLog() {
  const { me } = useAuth();
  const [type, setType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<Entry | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const query = new URLSearchParams();
  if (type) query.set("target_type", type);
  if (from) query.set("date_from", from);
  if (to) query.set("date_to", to);
  const filters = query.toString();
  const load = useLoad(() => api<Log>(`/api/audit-log?${filters}${filters ? "&" : ""}limit=50&offset=${offset}`), [filters, offset]);
  const d = load.data;
  const zone = me?.org_timezone ?? "Australia/Hobart";
  return (
    <>
      <div className="page-head">
        <div><h1>Activity log</h1><p className="muted">Every change the portal has recorded: who did it, to what, and when. Nothing here can be edited or removed.</p></div>
        <button type="button" className="btn" onClick={() => void openFile(`/api/audit-log/export.csv${filters ? `?${filters}` : ""}`, "activity-log.csv", true).catch((e) => setMsg(errorMessage(e)))}>Download as a spreadsheet file</button>
      </div>
      {msg && <Notice tone="bad">{msg}</Notice>}
      {d && d.hidden.length > 0 && <Notice tone="info">Not shown to you: {d.hidden.join(" and ")}.</Notice>}
      <section className="card">
        <div className="toolbar">
          <label className="field">Kind of record<select value={type} onChange={(e) => { setType(e.target.value); setOffset(0); }}><option value="">All</option>{(d?.target_types ?? []).map((t) => <option key={t} value={t}>{readable(t)}</option>)}</select></label>
          <label className="field">From<input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setOffset(0); }} /></label>
          <label className="field">To<input type="date" value={to} onChange={(e) => { setTo(e.target.value); setOffset(0); }} /></label>
          {(type || from || to) && <button type="button" className="btn" onClick={() => { setType(""); setFrom(""); setTo(""); setOffset(0); }}>Clear filters</button>}
        </div>
        {load.error ? <LoadError message={load.error} onRetry={load.reload} /> : !d ? <Loading /> : d.items.length === 0 ? (
          <p className="muted card-pad" style={{ margin: 0 }}>{type || from || to ? "Nothing recorded matches these filters." : "Nothing has been recorded yet."}</p>
        ) : (
          <>
            <div className="table-wrap"><table>
              <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th><th scope="col">Kind of record</th><th scope="col"><span className="sr-only">Details</span></th></tr></thead>
              <tbody>{d.items.map((e) => (
                <tr key={e.id}><td>{formatDateTime(e.at, zone)}</td><td>{e.who}</td><td>{readable(e.action)}</td><td>{readable(e.target_type)}</td>
                  <td style={{ textAlign: "right" }}><button type="button" className="btn small" aria-label={`Details of ${readable(e.action)} at ${formatDateTime(e.at, zone)}`} onClick={() => setOpen(e)}>Details</button></td></tr>))}</tbody>
            </table></div>
            <div className="row card-pad" style={{ justifyContent: "space-between" }}>
              <span className="small muted">{d.offset + 1} to {d.offset + d.items.length} of {d.total}</span>
              <span className="row">
                <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Newer</button>
                <button type="button" className="btn small" disabled={d.offset + d.items.length >= d.total} onClick={() => setOffset(offset + 50)}>Older</button>
              </span>
            </div>
          </>
        )}
      </section>
      {open && (
        <Modal title={readable(open.action)} onClose={() => setOpen(null)}>
          <dl className="definitions">
            <div><dt>When</dt><dd>{formatDateTime(open.at, zone)}</dd></div>
            <div><dt>Who</dt><dd>{open.who}</dd></div>
            <div><dt>Record</dt><dd>{readable(open.target_type)}{open.target_id ? ` · ${open.target_id}` : ""}</dd></div>
            {Object.entries(open.detail).map(([k, v]) => <div key={k}><dt>{readable(k)}</dt><dd style={{ overflowWrap: "anywhere" }}>{typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}</dd></div>)}
            {open.request_id && <div><dt>Request reference</dt><dd>{open.request_id}</dd></div>}
          </dl>
        </Modal>
      )}
    </>
  );
}

interface Register { key: string; label: string; allowed: boolean; dated_by: string }
interface Pack { id: string; title: string; registers: { key: string; label: string; records: number | null }[]; date_from: string | null; date_to: string | null; status: string; sha256: string | null; size_bytes: number | null; file_available: boolean; created_by: string; created_at: string }

/** Packs of records to hand to an auditor. */
export function AuditPacks() {
  const { me } = useAuth();
  const list = useLoad(() => api<{ items: Pack[]; can_create: boolean }>("/api/audit-exports"), []);
  const registers = useLoad(() => api<{ items: Register[]; max_records_per_register: number; not_included: string }>("/api/audit-exports/registers"), []);
  const [making, setMaking] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const zone = me?.org_timezone ?? "Australia/Hobart";
  async function remove(pack: Pack) {
    setMsg(null);
    try { await api(`/api/audit-exports/${pack.id}`, { method: "DELETE" }); setMsg({ tone: "ok", text: "Removed from this list. A record that it was made is kept." }); list.reload(); } catch (e) { setMsg({ tone: "bad", text: errorMessage(e) }); }
  }
  return (
    <>
      <div className="page-head">
        <div><h1>Audit packs</h1><p className="muted">A pack is a ZIP file of records for an auditor, with a list of what is inside and a fingerprint for each file. It is kept here to download; nothing is emailed.</p></div>
        {list.data?.can_create && <button type="button" className="btn primary" onClick={() => setMaking(true)}>Make a pack</button>}
      </div>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {registers.data && <Notice tone="info">{registers.data.not_included}</Notice>}
      <section className="card">
        {list.error ? <LoadError message={list.error} onRetry={list.reload} /> : !list.data ? <Loading /> : list.data.items.length === 0 ? (
          <p className="muted card-pad" style={{ margin: 0 }}>No packs have been made yet.</p>
        ) : (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Pack</th><th scope="col">What is inside</th><th scope="col">Period</th><th scope="col">Made</th><th scope="col">File</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{list.data.items.map((p) => (
              <tr key={p.id}>
                <td><strong>{p.title}</strong></td>
                <td>{p.registers.map((r) => `${r.label} (${r.records ?? "?"})`).join(", ")}</td>
                <td>{p.date_from || p.date_to ? `${p.date_from ? formatDate(p.date_from) : "start"} to ${p.date_to ? formatDate(p.date_to) : "now"}` : "All dates"}</td>
                <td>{formatDateTime(p.created_at, zone)}<div className="small muted">by {p.created_by}</div></td>
                <td>{p.file_available ? <>{bytes(p.size_bytes)}<div className="small muted" title={p.sha256 ?? ""}>Fingerprint {p.sha256?.slice(0, 12)}…</div></> : <Chip tone="bad">File missing</Chip>}</td>
                <td style={{ textAlign: "right" }}><span className="row" style={{ justifyContent: "flex-end" }}>
                  {p.file_available && <button type="button" className="btn small" aria-label={`Download ${p.title}`} onClick={() => void openFile(`/api/audit-exports/${p.id}/download`, `${p.title}.zip`, true).catch((e) => setMsg({ tone: "bad", text: errorMessage(e) }))}>Download</button>}
                  {list.data?.can_create && <button type="button" className="btn small" aria-label={`Remove ${p.title}`} onClick={() => void remove(p)}>Remove</button>}
                </span></td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
      {making && registers.data && <MakePack registers={registers.data.items} max={registers.data.max_records_per_register} onClose={() => setMaking(false)} onMade={() => { setMaking(false); setMsg({ tone: "ok", text: "Pack made. Download it from the list." }); list.reload(); }} />}
    </>
  );
}

function MakePack({ registers, max, onClose, onMade }: { registers: Register[]; max: number; onClose: () => void; onMade: () => void }) {
  const [title, setTitle] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (chosen.length === 0) return setError("Choose at least one register to include.");
    setBusy(true);
    setError(null);
    try {
      await api("/api/audit-exports", { method: "POST", body: { title, registers: chosen, date_from: from || null, date_to: to || null } });
      onMade();
    } catch (e) { setError(errorMessage(e)); setBusy(false); }
  }
  return (
    <Modal title="Make an audit pack" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">Name of the pack<input value={title} onChange={(e) => setTitle(e.target.value)} required minLength={3} maxLength={200} placeholder="For example: Client audit, November" /></label>
        <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend style={{ fontWeight: 600, marginBottom: 6 }}>Include</legend>
          {registers.map((r) => (
            <label key={r.key} className="row" style={{ alignItems: "flex-start", opacity: r.allowed ? 1 : 0.6 }}>
              <input type="checkbox" disabled={!r.allowed} checked={chosen.includes(r.key)} onChange={(e) => setChosen(e.target.checked ? [...chosen, r.key] : chosen.filter((k) => k !== r.key))} />
              <span>{r.label}<br /><span className="small muted">{r.allowed ? `Dated by ${r.dated_by}.` : "You cannot see this register for the whole organisation."}</span></span>
            </label>))}
        </fieldset>
        <div className="row">
          <label className="field">From (optional)<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label className="field">To (optional)<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        </div>
        <p className="small muted" style={{ margin: 0 }}>At most {max.toLocaleString()} records per register. If there are more, the portal asks for a shorter period instead of leaving some out.</p>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Making the pack…" : "Make the pack"}</button>
        </div>
      </form>
    </Modal>
  );
}
