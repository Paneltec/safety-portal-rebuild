import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, downloadFile, fetchBlobUrl, formatDateTime, formatDuration, openFile, type Attendance, type SignOnQuestion, type Site } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

type Msg = { tone: "ok" | "bad"; text: string } | null;

interface SitePage {
  items: Site[];
  total: number;
  limit: number;
  offset: number;
  counts: { active: number; inactive: number };
}

const KIND_LABELS: Record<string, string> = { project: "Project site", depot: "Depot or yard", other: "Other" };
const PAGE_SIZE = 25;

function OnSite({ site }: { site: Site }) {
  const { workers, visitors } = site.on_site;
  if (workers + visitors === 0) return <span className="muted">Nobody</span>;
  const parts = [];
  if (workers) parts.push(`${workers} worker${workers === 1 ? "" : "s"}`);
  if (visitors) parts.push(`${visitors} visitor${visitors === 1 ? "" : "s"}`);
  return <Chip tone="info">{parts.join(", ")}</Chip>;
}

export function Sites() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"active" | "inactive">("active");
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const sites = useLoad(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), status });
    if (query) params.set("q", query);
    return api<SitePage>(`/api/sites?${params}`);
  }, [query, status, offset]);
  const page = sites.data;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Sites</h1>
          <p className="muted">Places where work happens. Each site has its own QR code for signing in and out.</p>
        </div>
        {can("sites", "edit") && <button type="button" className="btn primary" onClick={() => setAdding(true)}>Add site</button>}
      </div>

      <section className="card">
        <div className="tabs" role="tablist" aria-label="Site status">
          {(["active", "inactive"] as const).map((key) => (
            <button key={key} type="button" role="tab" className="tab" aria-selected={status === key} onClick={() => { setStatus(key); setOffset(0); }}>
              {key === "active" ? "In use" : "Not in use"}{page ? ` (${page.counts[key]})` : ""}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <label className="field" style={{ flex: "1 1 240px" }}>
            Search name or address
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
          {query && <button type="button" className="btn" onClick={() => setSearch("")}>Clear search</button>}
        </div>
        {sites.error ? (
          <div className="card-pad"><LoadError message={sites.error} onRetry={sites.reload} /></div>
        ) : !page ? (
          <Loading />
        ) : page.items.length === 0 ? (
          <div className="empty">{query ? "No sites match this search." : status === "active" ? "No sites yet. Add the first one to get its sign-in QR code." : "No sites are marked as not in use."}</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th scope="col">Site</th><th scope="col">Address</th><th scope="col">Type</th><th scope="col">On site now</th></tr>
                </thead>
                <tbody>
                  {page.items.map((s) => (
                    <tr key={s.id}>
                      <td><Link to={`/app/sites/${s.id}`}>{s.name}</Link>{s.source ? <span className="muted small"> · {s.source.system}</span> : null}</td>
                      <td>{s.address_full || "—"}</td>
                      <td>{KIND_LABELS[s.kind]}</td>
                      <td><OnSite site={s} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
              <span className="muted small" role="status">Showing {page.offset + 1}–{page.offset + page.items.length} of {page.total}{query ? " matching" : ""}</span>
              <span className="spacer" />
              <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
              <button type="button" className="btn small" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
            </div>
          </>
        )}
      </section>
      {adding && <SiteForm onClose={() => setAdding(false)} onSaved={(s) => navigate(`/app/sites/${s.id}`)} />}
    </>
  );
}

function SiteForm({ site, onClose, onSaved }: { site?: Site; onClose: () => void; onSaved: (s: Site) => void }) {
  const [v, setV] = useState({
    name: site?.name ?? "", address: site?.address ?? "", suburb: site?.suburb ?? "", state: site?.state ?? "TAS",
    postcode: site?.postcode ?? "", kind: site?.kind ?? "project", description: site?.description ?? "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body = { name: v.name, address: v.address || null, suburb: v.suburb || null, state: v.state || null, postcode: v.postcode || null, kind: v.kind, description: v.description || null };
    try {
      onSaved(await api<Site>(site ? `/api/sites/${site.id}` : "/api/sites", { method: site ? "PATCH" : "POST", body }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={site ? `Edit ${site.name}` : "Add site"} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">Site name<input required minLength={2} value={v.name} onChange={set("name")} /></label>
        <label className="field">Street address (optional)<input value={v.address} onChange={set("address")} /></label>
        <div className="grid-2">
          <label className="field">Suburb<input value={v.suburb} onChange={set("suburb")} /></label>
          <label className="field">State<input value={v.state} onChange={set("state")} /></label>
          <label className="field">Postcode<input value={v.postcode} onChange={set("postcode")} /></label>
          <label className="field">
            Type
            <select value={v.kind} onChange={set("kind")}>
              {Object.entries(KIND_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
        </div>
        <label className="field">Office notes (optional, never shown to visitors)<textarea rows={2} value={v.description} onChange={set("description")} /></label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : site ? "Save" : "Add site"}</button>
        </div>
      </form>
    </Modal>
  );
}

export function SiteDetail() {
  const { siteId = "" } = useParams();
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const site = useLoad(() => api<Site>(`/api/sites/${siteId}`), [siteId]);
  const [message, setMessage] = useState<Msg>(null);
  const [editing, setEditing] = useState(false);
  const canEdit = can("sites", "edit");
  const tz = me?.org_timezone ?? "Australia/Hobart";

  if (site.error) return <LoadError message={site.error} onRetry={site.reload} />;
  const s = site.data;
  if (!s) return <Loading />;

  return (
    <>
      <p className="small"><Link to="/app/sites">← Sites</Link></p>
      <div className="page-head">
        <div>
          <h1>{s.name}</h1>
          <p className="muted row" style={{ gap: 8 }}>
            {s.active ? <Chip tone="ok">In use</Chip> : <Chip tone="dashed" icon={false}>Not in use</Chip>}
            <span>{KIND_LABELS[s.kind]}</span>
            {s.address_full && <span>· {s.address_full}</span>}
          </p>
        </div>
        {canEdit && (
          <div className="row">
            <button type="button" className="btn" onClick={async () => {
              try {
                site.setData(await api<Site>(`/api/sites/${s.id}`, { method: "PATCH", body: { active: !s.active } }));
                setMessage({ tone: "ok", text: s.active ? "Marked as not in use. Its QR code no longer works." : "Back in use. Its QR code works again." });
              } catch (e) { setMessage({ tone: "bad", text: errorMessage(e) }); }
            }}>{s.active ? "Mark not in use" : "Put back in use"}</button>
            <button type="button" className="btn primary" onClick={() => setEditing(true)}>Edit details</button>
          </div>
        )}
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {s.description && <section className="card card-pad"><h2>Office notes</h2><p style={{ whiteSpace: "pre-wrap" }}>{s.description}</p></section>}

      <OnSiteNow site={s} timezone={tz} onChanged={site.reload} />
      {s.sign_on_url && <QrCard site={s} onRotated={(next) => { site.setData(next); setMessage({ tone: "ok", text: "New QR code issued. Print and replace the sign: the old one no longer works." }); }} />}
      {canEdit && <QuestionsCard site={s} onSaved={(next) => { site.setData(next); setMessage({ tone: "ok", text: "Sign-in questions saved." }); }} />}
      <LogCard site={s} timezone={tz} />

      {can("sites", "delete") && (
        <section className="card card-pad stack">
          <h2>Delete this site</h2>
          <p className="muted small">For a site added by mistake. Anyone still signed in is signed out, the QR code stops working, and the sign-in history is kept. An admin can restore it.</p>
          <div>
            <button type="button" className="btn danger" onClick={async () => {
              if (!window.confirm(`Delete ${s.name}?`)) return;
              try {
                await api(`/api/sites/${s.id}`, { method: "DELETE" });
                navigate("/app/sites");
              } catch (e) { setMessage({ tone: "bad", text: errorMessage(e) }); }
            }}>Delete site</button>
          </div>
        </section>
      )}
      {editing && <SiteForm site={s} onClose={() => setEditing(false)} onSaved={(next) => { setEditing(false); site.setData(next); setMessage({ tone: "ok", text: "Details saved." }); }} />}
    </>
  );
}

function OnSiteNow({ site, timezone, onChanged }: { site: Site; timezone: string; onChanged: () => void }) {
  const { can } = useAuth();
  const rows = useLoad(() => api<{ items: Attendance[] }>(`/api/sites/${site.id}/active-signons`), [site.id]);
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="card">
      <div className="card-head">
        <h2>On site now</h2>
        <button type="button" className="btn small" onClick={rows.reload}>Refresh</button>
      </div>
      {error && <div className="card-pad" style={{ paddingBottom: 0 }}><Notice tone="bad">{error}</Notice></div>}
      {rows.error ? <div className="card-pad"><LoadError message={rows.error} onRetry={rows.reload} /></div> : !rows.data ? <Loading /> : rows.data.items.length === 0 ? (
        <div className="empty">Nobody is signed in at this site.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Signed in</th><th scope="col">Time on site</th>{can("sites", "edit") && <th scope="col"><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {rows.data.items.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}{r.company ? <span className="muted small"> · {r.company}</span> : null}</td>
                  <td>{r.kind === "worker" ? "Worker" : "Visitor"}</td>
                  <td className="num">{formatDateTime(r.signed_in_at, timezone)}</td>
                  <td>{formatDuration(r.minutes)} {r.stale && <Chip tone="warn">Probably forgot to sign out</Chip>}</td>
                  {can("sites", "edit") && (
                    <td className="right">
                      <button type="button" className="btn small" onClick={async () => {
                        setError(null);
                        try {
                          await api(`/api/sites/${site.id}/active-signons/${r.id}`, { method: "DELETE" });
                          rows.reload();
                          onChanged();
                        } catch (e) { setError(errorMessage(e)); }
                      }}>Sign out of site</button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function QrCard({ site, onRotated }: { site: Site; onRotated: (s: Site) => void }) {
  const [image, setImage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let made: string | null = null;
    let cancelled = false;
    setImage(null);
    fetchBlobUrl(`/api/sites/${site.id}/qr.png`).then((url) => { made = url; if (!cancelled) setImage(url); }).catch(() => setError("The QR code could not be loaded."));
    return () => { cancelled = true; if (made) URL.revokeObjectURL(made); };
  }, [site.id, site.sign_on_url]);

  return (
    <section className="card">
      <div className="card-head"><h2>Sign-in QR code</h2></div>
      <div className="card-pad qr-box" style={{ paddingTop: 4 }}>
        {image ? <img src={image} alt={`QR code for signing in at ${site.name}`} /> : <div className="logo-preview" style={{ width: 180, height: 180 }}>{error ?? "Loading…"}</div>}
        <div className="stack" style={{ flex: "1 1 260px" }}>
          <p>Print the sign and put it at the gate. Scanning it opens the sign-in page for this site only.{!site.active && <strong> This site is marked not in use, so the code does not work at the moment.</strong>}</p>
          {site.sign_on_address_configured === false && (
            <Notice tone="warn">
              The portal's public address has not been set on the server, so this code uses the address you are browsing on now.
              Before printing signs, set <code>PUBLIC_BASE_URL</code> in the server's .env file to the address phones will use, then restart.
            </Notice>
          )}
          <div className="code-box">{site.sign_on_url}</div>
          <div className="row">
            <button type="button" className="btn primary" onClick={() => void openFile(`/api/sites/${site.id}/qr-signage.pdf`, `sign-in-${site.name}.pdf`, false).catch((e) => setError(errorMessage(e)))}>Open printable sign (PDF)</button>
            <button type="button" className="btn" onClick={() => void navigator.clipboard?.writeText(site.sign_on_url ?? "")}>Copy link</button>
            <button type="button" className="btn" onClick={async () => {
              if (!window.confirm("Issue a new QR code? Every sign printed so far stops working and must be replaced.")) return;
              try { onRotated(await api<Site>(`/api/sites/${site.id}/rotate-token`, { method: "POST" })); } catch (e) { setError(errorMessage(e)); }
            }}>Issue a new code</button>
          </div>
          {error && image && <Notice tone="bad">{error}</Notice>}
        </div>
      </div>
    </section>
  );
}

function QuestionsCard({ site, onSaved }: { site: Site; onSaved: (s: Site) => void }) {
  const [questions, setQuestions] = useState<(SignOnQuestion & { choicesText?: string })[]>(() => (site.signon_questions ?? []).map((q) => ({ ...q, choicesText: (q.choices ?? []).join("\n") })));
  const [declaration, setDeclaration] = useState(site.visitor_declaration ?? "");
  const [error, setError] = useState<string | null>(null);
  const update = (index: number, patch: Partial<SignOnQuestion & { choicesText?: string }>) => setQuestions(questions.map((q, i) => (i === index ? { ...q, ...patch } : q)));

  async function save() {
    setError(null);
    try {
      onSaved(await api<Site>(`/api/sites/${site.id}`, {
        method: "PATCH",
        body: {
          visitor_declaration: declaration.trim() || null,
          signon_questions: questions.map((q) => ({
            id: q.id || null, type: q.type, label: q.label, required: q.required,
            choices: q.type === "choice" ? (q.choicesText ?? "").split("\n").map((c) => c.trim()).filter(Boolean) : null,
          })),
        },
      }));
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <section className="card">
      <div className="card-head"><h2>What people are asked at sign-in</h2></div>
      <div className="card-pad stack" style={{ paddingTop: 4 }}>
        {error && <Notice tone="bad">{error}</Notice>}
        {questions.length === 0 && <p className="muted">No extra questions. Workers just sign on; visitors give their name and accept the site rules.</p>}
        {questions.map((q, index) => (
          <div key={index} className="fields" style={{ alignItems: "end", padding: 10, border: "1px solid var(--line)", borderRadius: 6 }}>
            <label className="field" style={{ flex: "2 1 240px" }}>Question {index + 1}<input value={q.label} onChange={(e) => update(index, { label: e.target.value })} /></label>
            <label className="field">
              Answer type
              <select value={q.type} onChange={(e) => update(index, { type: e.target.value as SignOnQuestion["type"] })}>
                <option value="yesno">Yes or no</option>
                <option value="choice">Choose one</option>
                <option value="text">Short text</option>
              </select>
            </label>
            {q.type === "choice" && (
              <label className="field" style={{ flex: "1 1 200px" }}>Choices, one per line<textarea rows={3} value={q.choicesText ?? ""} onChange={(e) => update(index, { choicesText: e.target.value })} /></label>
            )}
            <label className="check"><input type="checkbox" checked={q.required} onChange={(e) => update(index, { required: e.target.checked })} /> Must be answered</label>
            <button type="button" className="btn small" onClick={() => setQuestions(questions.filter((_, i) => i !== index))}>Remove</button>
          </div>
        ))}
        <div><button type="button" className="btn" onClick={() => setQuestions([...questions, { id: "", type: "yesno", label: "", required: true }])}>Add a question</button></div>
        <label className="field">
          Site rules that visitors must accept
          <textarea rows={4} value={declaration} placeholder={site.default_visitor_declaration} onChange={(e) => setDeclaration(e.target.value)} />
          <span className="hint">Leave empty to use the standard wording shown in grey.</span>
        </label>
        <div><button type="button" className="btn primary" onClick={() => void save()}>Save sign-in questions</button></div>
      </div>
    </section>
  );
}

function LogCard({ site, timezone }: { site: Site; timezone: string }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [kind, setKind] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const params = new URLSearchParams();
  if (from) params.set("date_from", from);
  if (to) params.set("date_to", to);
  if (kind) params.set("kind", kind);
  const log = useLoad(() => (open ? api<{ items: Attendance[]; truncated: boolean; limit: number }>(`/api/sites/${site.id}/signon-log?${params}`) : Promise.resolve(null)), [site.id, open, from, to, kind]);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Sign-in history</h2>
        <button type="button" className="btn small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</button>
      </div>
      {open && (
        <>
          <div className="toolbar">
            <label className="field">From<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
            <label className="field">To<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
            <label className="field">
              Who
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="">Everyone</option>
                <option value="worker">Workers</option>
                <option value="visitor">Visitors</option>
              </select>
            </label>
            <button type="button" className="btn" onClick={() => void downloadFile(`/api/sites/${site.id}/signon-log/export?${params}`, `${site.name}-sign-ins.csv`, "POST").catch((e) => setError(errorMessage(e)))}>Download (CSV)</button>
          </div>
          {error && <div className="card-pad" style={{ paddingTop: 0 }}><Notice tone="bad">{error}</Notice></div>}
          {log.error ? <div className="card-pad"><LoadError message={log.error} onRetry={log.reload} /></div> : !log.data ? <Loading /> : log.data.items.length === 0 ? (
            <div className="empty">No sign-ins in this period.</div>
          ) : (
            <>
              {log.data.truncated && <div className="card-pad" style={{ paddingTop: 0 }}><Notice tone="info">Showing the newest {log.data.limit}. Narrow the dates to see older sign-ins.</Notice></div>}
              <div className="table-wrap">
                <table>
                  <thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Signed in</th><th scope="col">Signed out</th><th scope="col">Time on site</th></tr></thead>
                  <tbody>
                    {log.data.items.map((r) => (
                      <tr key={r.id}>
                        <td>{r.name}{r.company ? <span className="muted small"> · {r.company}</span> : null}</td>
                        <td>{r.kind === "worker" ? "Worker" : "Visitor"}</td>
                        <td className="num">{formatDateTime(r.signed_in_at, timezone)}</td>
                        <td className="num">{r.signed_out_at ? <>{formatDateTime(r.signed_out_at, timezone)}{r.sign_out_source === "office" ? <span className="muted small"> · by the office</span> : null}</> : <Chip tone="info">On site</Chip>}</td>
                        <td>{formatDuration(r.minutes)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
