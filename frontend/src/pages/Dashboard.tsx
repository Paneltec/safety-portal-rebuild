import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, formatDateTime } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Notice } from "../components/ui";
import { useLoad } from "../hooks";

interface Tile { section: string; key: string; label: string; tone: "bad" | "warn" | "neutral"; definition: string; path: string; count: number | null; available: boolean }
interface Metrics { today: string; timezone: string; tiles: Tile[]; attention: Tile[]; unavailable: string[]; recent_activity: { at: string; who: string; action: string; target_type: string }[] | null }

/** "worker.archived" -> "Worker archived". The stored action names are plain enough to read once tidied. */
function readable(action: string): string {
  const text = action.replace(/[._]/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** What needs attention and the figures behind it. Every number links to the screen that lists the same records. */
export function Dashboard() {
  const { me } = useAuth();
  const load = useLoad(() => api<Metrics>("/api/dashboard/metrics"), []);
  if (load.error) return <LoadError message={`The figures could not be loaded. ${load.error}`} onRetry={load.reload} />;
  if (!load.data) return <Loading label="Working out the figures…" />;
  const m = load.data;
  if (m.tiles.length === 0) return null;
  const sections = [...new Set(m.tiles.map((t) => t.section))];
  return (
    <>
      <section className="card" aria-label="Needs attention">
        <div className="card-head"><h2>Needs attention</h2><button type="button" className="btn small" onClick={load.reload}>Refresh</button></div>
        <div className="card-pad stack" style={{ paddingTop: 4 }}>
          {m.unavailable.length > 0 && <Notice tone="warn">{m.unavailable.length} figure{m.unavailable.length === 1 ? "" : "s"} could not be worked out just now and {m.unavailable.length === 1 ? "is" : "are"} shown as a dash below. A dash does not mean zero.</Notice>}
          {m.attention.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nothing needs attention right now.</p> : (
            <ul className="attention">
              {m.attention.map((t) => (
                <li key={t.key}>
                  <Link to={t.path}><span className={`attention-num ${t.tone}`}>{t.count}</span><span><strong>{t.label}</strong><br /><span className="small muted">{t.definition}</span></span></Link>
                </li>))}
            </ul>
          )}
        </div>
      </section>
      {sections.map((section) => (
        <section key={section} aria-label={section}>
          <h2 className="section-title">{section}</h2>
          <div className="tiles">
            {m.tiles.filter((t) => t.section === section).map((t) => (
              <Link key={t.key} to={t.path} className={`tile ${t.available && t.count ? t.tone : ""}`} title={t.definition} style={{ textDecoration: "none", color: "inherit" }}>
                <span className="tile-num">{t.available ? t.count : "—"}</span>
                <span className="tile-label">{t.label}</span>
                {!t.available && <span className="small muted">Not available just now</span>}
              </Link>))}
          </div>
        </section>))}
      <details className="card card-pad">
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>What each figure counts</summary>
        <dl className="definitions">
          {m.tiles.map((t) => <div key={t.key}><dt>{t.label}</dt><dd>{t.definition}</dd></div>)}
        </dl>
        <p className="small muted" style={{ marginBottom: 0 }}>Figures cover only what you are allowed to see, as at {m.today} ({m.timezone}). There is no overall compliance score: a single percentage would hide what is behind it.</p>
      </details>
      {m.recent_activity && (
        <section className="card" aria-label="Recent activity">
          <div className="card-head"><h2>Recent activity</h2><Link to="/app/settings/activity" className="small">See the full activity log</Link></div>
          {m.recent_activity.length === 0 ? <p className="muted card-pad" style={{ margin: 0 }}>Nothing recorded yet.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th></tr></thead>
              <tbody>{m.recent_activity.map((r, i) => <tr key={i}><td>{formatDateTime(r.at, me?.org_timezone ?? m.timezone)}</td><td>{r.who}</td><td>{readable(r.action)}</td></tr>)}</tbody>
            </table></div>
          )}
        </section>
      )}
    </>
  );
}

/** The search field in the top bar. */
export function SearchBox() {
  const navigate = useNavigate();
  const [text, setText] = useState("");
  function go(event: FormEvent) {
    event.preventDefault();
    if (text.trim().length >= 2) navigate(`/app/search?q=${encodeURIComponent(text.trim())}`);
  }
  return (
    <form className="searchbox" role="search" onSubmit={go}>
      <input type="search" aria-label="Search the portal" placeholder="Search names and titles" value={text} onChange={(e) => setText(e.target.value)} maxLength={80} />
      <button type="submit" className="btn" disabled={text.trim().length < 2}>Search</button>
    </form>
  );
}

interface Results { query: string; groups: { kind: string; label: string; more: boolean; items: { title: string; subtitle: string | null; path: string }[] }[]; total: number; searched: string[]; failed: string[]; note: string }

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const [text, setText] = useState(q);
  const load = useLoad(() => (q.length >= 2 ? api<Results>(`/api/search?q=${encodeURIComponent(q)}&limit=8`) : Promise.resolve(null)), [q]);
  const r = load.data;
  return (
    <>
      <div className="page-head"><div><h1>Search</h1><p className="muted">Finds workers, sites, suppliers, fleet, library files, safety records and users by name or title, in the registers you can see.</p></div></div>
      <form className="row" role="search" onSubmit={(e) => { e.preventDefault(); setParams(text.trim() ? { q: text.trim() } : {}); }}>
        <label className="field" style={{ flex: 1, minWidth: 200 }}>Search for<input type="search" value={text} onChange={(e) => setText(e.target.value)} maxLength={80} autoFocus /></label>
        <button type="submit" className="btn primary" style={{ alignSelf: "flex-end" }} disabled={text.trim().length < 2}>Search</button>
      </form>
      {q.length < 2 ? <p className="muted">Type at least two letters.</p> : load.error ? <LoadError message={load.error} onRetry={load.reload} /> : !r ? <Loading label="Searching…" /> : (
        <>
          {r.failed.length > 0 && <Notice tone="warn">These could not be searched just now: {r.failed.join(", ")}. The other results are complete.</Notice>}
          {r.total === 0 ? <Notice tone="info">Nothing found for “{r.query}” in: {r.searched.join(", ") || "any register"}. {r.note}</Notice> : (
            <>
              {r.groups.map((g) => (
                <section key={g.kind} className="card" aria-label={g.label}>
                  <div className="card-head"><h2>{g.label}</h2>{g.more && <Chip tone="neutral">First {g.items.length} shown. Open the register to see them all.</Chip>}</div>
                  <ul className="results">{g.items.map((i) => <li key={i.path}><Link to={i.path}><strong>{i.title}</strong>{i.subtitle && <span className="small muted"> · {i.subtitle}</span>}</Link></li>)}</ul>
                </section>))}
              <p className="small muted">{r.note}</p>
            </>
          )}
        </>
      )}
    </>
  );
}
