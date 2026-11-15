import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Company, type Worker } from "../api";
import { useAuth } from "../auth";
import { Avatar, CertChip, Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface WorkerPage {
  items: Worker[];
  total: number;
  limit: number;
  offset: number;
  counts: { active: number; inactive: number; archived: number };
}

const PAGE_SIZE = 25;
type Status = "active" | "inactive" | "archived";
const TABS: { key: Status; label: string }[] = [
  { key: "active", label: "Active" },
  { key: "inactive", label: "Inactive" },
  { key: "archived", label: "Archived" },
];

export function WorkerStatus({ status }: { status: string }) {
  if (status === "active") return <Chip tone="ok">Active</Chip>;
  if (status === "inactive") return <Chip tone="neutral" icon={false}>Inactive</Chip>;
  if (status === "archived") return <Chip tone="dashed" icon={false}>Archived</Chip>;
  return <Chip tone="bad">Deleted</Chip>;
}

/** The roster's certificate summary for one person: only the states that need attention, else "All current". */
function CertSummary({ counts }: { counts: Worker["certifications"] }) {
  if (!counts) return null;
  const total = counts.valid + counts.expiring + counts.expired + counts.unknown;
  const parts: { status: string; label: string }[] = [];
  if (counts.expired) parts.push({ status: "expired", label: `${counts.expired} expired` });
  if (counts.missing) parts.push({ status: "missing", label: `${counts.missing} missing` });
  if (counts.expiring) parts.push({ status: "expiring", label: `${counts.expiring} expiring` });
  if (counts.unknown) parts.push({ status: "unknown", label: `${counts.unknown} no expiry date` });
  if (parts.length === 0) return total === 0 ? <span className="muted">None recorded</span> : <CertChip status="valid" label={`${total} current`} />;
  return (
    <span className="chips">
      {parts.map((p) => <CertChip key={p.status} status={p.status} label={p.label} />)}
    </span>
  );
}

export function Workers() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<Status>("active");
  const [linked, setLinked] = useState("");
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);
  const team = can("workers", "team_view");

  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const workers = useLoad(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), status });
    if (query) params.set("q", query);
    if (linked) params.set("linked", linked);
    return api<WorkerPage>(`/api/workers?${params}`);
  }, [query, status, linked, offset]);
  const page = workers.data;
  const filtered = Boolean(query || linked);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Workers</h1>
          <p className="muted">
            {team
              ? "Everyone who does work for the business. A worker record is separate from a login; link them on the worker's page."
              : "Your own worker record. The office keeps the full roster."}
          </p>
        </div>
        {can("workers", "edit") && (
          <button type="button" className="btn primary" onClick={() => setAdding(true)}>Add worker</button>
        )}
      </div>

      <section className="card">
        {team && (
          <div className="tabs" role="tablist" aria-label="Worker status">
            {TABS.map((tab) => (
              <button key={tab.key} type="button" role="tab" className="tab" aria-selected={status === tab.key}
                onClick={() => { setStatus(tab.key); setOffset(0); }}>
                {tab.label}{page ? ` (${page.counts[tab.key]})` : ""}
              </button>
            ))}
          </div>
        )}
        {team && (
          <div className="toolbar">
            <label className="field" style={{ flex: "1 1 240px" }}>
              Search name, email or position
              <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
            </label>
            <label className="field">
              Login
              <select value={linked} onChange={(e) => { setLinked(e.target.value); setOffset(0); }}>
                <option value="">All</option>
                <option value="yes">Has a login</option>
                <option value="no">No login</option>
              </select>
            </label>
            {filtered && (
              <button type="button" className="btn" onClick={() => { setSearch(""); setLinked(""); setOffset(0); }}>Clear filters</button>
            )}
          </div>
        )}

        {workers.error ? (
          <div className="card-pad"><LoadError message={workers.error} onRetry={workers.reload} /></div>
        ) : !page ? (
          <Loading />
        ) : page.items.length === 0 ? (
          <div className="empty">
            {!team
              ? "Your login is not linked to a worker record yet. Ask the office to link it."
              : filtered ? "No workers match these filters." : status === "active" ? "No workers yet. Add the first one to get started." : `No ${status} workers.`}
          </div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Position</th>
                    <th scope="col">Contact</th>
                    <th scope="col">Certificates</th>
                    <th scope="col">Login</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((w) => (
                    <tr key={w.id}>
                      <td>
                        <span className="person">
                          <Avatar url={w.photo_url} name={w.name} size={32} />
                          <span>
                            <Link to={`/app/workers/${w.id}`}>{w.name}</Link>
                            {w.preferred_name ? <span className="muted small"> “{w.preferred_name}”</span> : null}
                            {w.source ? <span className="muted small"> · {w.source.system}</span> : null}
                          </span>
                        </span>
                      </td>
                      <td>{w.position ?? "—"}</td>
                      <td className="small">{w.mobile ?? w.phone ?? w.email ?? "—"}</td>
                      <td><CertSummary counts={w.certifications} /></td>
                      <td>{w.has_login ? <Chip tone="ok">Linked</Chip> : <span className="muted">None</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
              <span className="muted small" role="status">
                Showing {page.offset + 1}–{page.offset + page.items.length} of {page.total}
                {filtered ? " matching" : ""} {workers.loading ? "· updating…" : ""}
              </span>
              <span className="spacer" />
              <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
              <button type="button" className="btn small" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
            </div>
          </>
        )}
      </section>

      {adding && <AddWorker onClose={() => setAdding(false)} onCreated={(w) => navigate(`/app/workers/${w.id}`)} />}
    </>
  );
}

function AddWorker({ onClose, onCreated }: { onClose: () => void; onCreated: (w: Worker) => void }) {
  const companies = useLoad(() => api<{ companies: Company[] }>("/api/org/companies"), []);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [position, setPosition] = useState("");
  const [email, setEmail] = useState("");
  const [mobile, setMobile] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await api<Worker>("/api/workers", {
        method: "POST",
        body: { first_name: first, last_name: last, position: position || null, email: email || null, mobile: mobile || null, company_id: companyId || null },
      }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title="Add worker" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="grid-2">
          <label className="field">First name<input required value={first} onChange={(e) => setFirst(e.target.value)} /></label>
          <label className="field">Last name<input value={last} onChange={(e) => setLast(e.target.value)} /></label>
        </div>
        <label className="field">Position (optional)<input value={position} onChange={(e) => setPosition(e.target.value)} /></label>
        <div className="grid-2">
          <label className="field">Email (optional)<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
          <label className="field">Mobile (optional)<input type="tel" value={mobile} onChange={(e) => setMobile(e.target.value)} /></label>
        </div>
        {companies.data && companies.data.companies.length > 0 && (
          <label className="field">
            Company (optional)
            <select value={companyId} onChange={(e) => setCompanyId(e.target.value)}>
              <option value="">Not set</option>
              {companies.data.companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        )}
        <p className="small muted">This adds the person to the roster only. You can create a login for them on the next screen.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Adding…" : "Add worker"}</button>
        </div>
      </form>
    </Modal>
  );
}
