import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, downloadFile, formatDateTime, formatDuration, type Attendance, type Site } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

type Msg = { tone: "ok" | "bad"; text: string } | null;
type View = "on_site" | "today" | "all";

export function Visitors() {
  const { me, can } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const [view, setView] = useState<View>("on_site");
  const [siteId, setSiteId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [message, setMessage] = useState<Msg>(null);
  const [correcting, setCorrecting] = useState<Attendance | null>(null);
  const canEdit = can("sites_visitors", "edit");
  const canDelete = can("sites_visitors", "delete");

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // "Today" is the organisation's local day.
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date());
  const params = new URLSearchParams();
  if (view === "on_site") params.set("on_site", "true");
  if (view === "today") { params.set("date_from", today); params.set("date_to", today); }
  if (view === "all") { if (from) params.set("date_from", from); if (to) params.set("date_to", to); }
  if (siteId) params.set("site_id", siteId);
  if (query) params.set("q", query);
  const listParams = new URLSearchParams(params);
  if (archived) listParams.set("include_archived", "true");

  const sites = useLoad(() => (can("sites", "view") ? api<{ items: Site[] }>("/api/sites?status=all&limit=200") : Promise.resolve({ items: [] })), []);
  const counts = useLoad(() => api<{ on_site: number; today: number; total: number }>("/api/admin/visitors/category-counts"), []);
  const visitors = useLoad(() => api<{ items: Attendance[]; truncated: boolean; limit: number }>(`/api/admin/visitors?${listParams}`), [listParams.toString()]);

  function refresh() {
    visitors.reload();
    counts.reload();
  }

  async function act(path: string, method: string, done: string) {
    setMessage(null);
    try {
      await api(path, { method });
      setMessage({ tone: "ok", text: done });
      refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  const tiles: { key: View; label: string; value: number | undefined; tone: string }[] = [
    { key: "on_site", label: "On site now", value: counts.data?.on_site, tone: "warn" },
    { key: "today", label: "Signed in today", value: counts.data?.today, tone: "ok" },
    { key: "all", label: "All visits", value: counts.data?.total, tone: "dashed" },
  ];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Visitors</h1>
          <p className="muted">People without a portal login who signed in at a site with its QR code.</p>
        </div>
        <button type="button" className="btn" onClick={() => void downloadFile(`/api/admin/visitors/export.csv?${params}`, "visitors.csv").catch((e) => setMessage({ tone: "bad", text: errorMessage(e) }))}>Download (CSV)</button>
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <div className="tiles" role="group" aria-label="Choose a view">
        {tiles.map((tile) => (
          <button key={tile.key} type="button" className={`tile ${tile.tone}`} aria-pressed={view === tile.key} onClick={() => setView(tile.key)}>
            <span className="tile-num">{tile.value ?? "–"}</span>
            <span>{tile.label}</span>
          </button>
        ))}
      </div>

      <section className="card">
        <div className="toolbar">
          <label className="field" style={{ flex: "1 1 200px" }}>Search name, company or host<input type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          {sites.data && sites.data.items.length > 0 && (
            <label className="field">
              Site
              <select value={siteId} onChange={(e) => setSiteId(e.target.value)}>
                <option value="">All sites</option>
                {sites.data.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
          )}
          {view === "all" && (
            <>
              <label className="field">From<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
              <label className="field">To<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
              <label className="check" style={{ alignSelf: "flex-end", minHeight: 44 }}><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Include archived</label>
            </>
          )}
        </div>
        {visitors.error ? (
          <div className="card-pad"><LoadError message={visitors.error} onRetry={visitors.reload} /></div>
        ) : !visitors.data ? (
          <Loading />
        ) : visitors.data.items.length === 0 ? (
          <div className="empty">{view === "on_site" ? "No visitors are on site." : view === "today" ? "No visitors have signed in today." : "No visits match these filters."}</div>
        ) : (
          <>
            {visitors.data.truncated && <div className="card-pad" style={{ paddingTop: 0 }}><Notice tone="info">Showing the newest {visitors.data.limit}. Narrow the dates to see older visits.</Notice></div>}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Visitor</th><th scope="col">Site</th><th scope="col">Here to see</th><th scope="col">Signed in</th><th scope="col">Signed out</th>
                    {(canEdit || canDelete) && <th scope="col"><span className="sr-only">Actions</span></th>}
                  </tr>
                </thead>
                <tbody>
                  {visitors.data.items.map((v) => (
                    <tr key={v.id}>
                      <td>
                        {v.name}
                        <div className="small muted">{[v.company, v.phone, v.purpose].filter(Boolean).join(" · ")}</div>
                        {v.answers.length > 0 && <div className="small muted">{v.answers.map((a) => `${a.label} ${a.value === true ? "Yes" : a.value === false ? "No" : a.value}`).join(" · ")}</div>}
                      </td>
                      <td>{can("sites", "view") ? <Link to={`/app/sites/${v.site_id}`}>{v.site_name}</Link> : v.site_name}</td>
                      <td>{v.host ?? "—"}</td>
                      <td className="num">{formatDateTime(v.signed_in_at, tz)}</td>
                      <td>
                        {v.on_site ? (
                          <span className="chips"><Chip tone="info">On site · {formatDuration(v.minutes)}</Chip>{v.stale && <Chip tone="warn">Probably forgot to sign out</Chip>}</span>
                        ) : (
                          <span className="num">{formatDateTime(v.signed_out_at, tz)}{v.sign_out_source === "office" ? <span className="muted small"> · by the office</span> : null}</span>
                        )}
                        {v.archived_at && <Chip tone="dashed" icon={false}>Archived</Chip>}
                      </td>
                      {(canEdit || canDelete) && (
                        <td>
                          <span className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                            {canEdit && v.on_site && <button type="button" className="btn small" onClick={() => void act(`/api/admin/visitors/${v.id}/force-signout`, "POST", `${v.name} signed out.`)}>Sign visitor out</button>}
                            {canEdit && <button type="button" className="btn small" onClick={() => setCorrecting(v)}>Correct</button>}
                            {canDelete && !v.on_site && (v.archived_at
                              ? <button type="button" className="btn small" onClick={() => void act(`/api/admin/visitors/${v.id}/unarchive`, "POST", "Returned from the archive.")}>Unarchive</button>
                              : <button type="button" className="btn small" onClick={() => void act(`/api/admin/visitors/${v.id}/archive`, "POST", "Archived.")}>Archive</button>)}
                            {canDelete && (
                              <button type="button" className="btn small danger" onClick={() => { if (window.confirm(`Delete the visit by ${v.name}? Use this only for an entry made by mistake.`)) void act(`/api/admin/visitors/${v.id}`, "DELETE", "Visit deleted."); }}>Delete</button>
                            )}
                          </span>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
      {correcting && <Correct visit={correcting} onClose={() => setCorrecting(null)} onSaved={() => { setCorrecting(null); setMessage({ tone: "ok", text: "Correction saved. The earlier details are kept in the audit trail." }); refresh(); }} />}
    </>
  );
}

function Correct({ visit, onClose, onSaved }: { visit: Attendance; onClose: () => void; onSaved: () => void }) {
  const [v, setV] = useState({ name: visit.name, company: visit.company ?? "", phone: visit.phone ?? "", purpose: visit.purpose ?? "", host: visit.host ?? "" });
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api(`/api/admin/visitors/${visit.id}`, { method: "PATCH", body: { ...v, reason } });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Modal title={`Correct the visit by ${visit.name}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">Name<input required minLength={2} value={v.name} onChange={set("name")} /></label>
        <div className="grid-2">
          <label className="field">Company<input value={v.company} onChange={set("company")} /></label>
          <label className="field">Phone<input value={v.phone} onChange={set("phone")} /></label>
          <label className="field">Reason for visit<input value={v.purpose} onChange={set("purpose")} /></label>
          <label className="field">Here to see<input value={v.host} onChange={set("host")} /></label>
        </div>
        <label className="field">Why is this being corrected?<input required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary">Save correction</button>
        </div>
      </form>
    </Modal>
  );
}
