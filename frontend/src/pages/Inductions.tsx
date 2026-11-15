import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, downloadFile, formatDate, openFile, type Matrix2, type MatrixCell, type Training, type Worker } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

type Msg = { tone: "ok" | "bad"; text: string } | null;

const WORDS: Record<string, string> = {
  valid: "Valid", expiring: "Expiring", expired: "Expired", unknown: "No expiry date", not_applicable: "Not applicable", blank: "",
};
const LEGEND: { key: string; label: string }[] = [
  { key: "expired", label: "Expired" }, { key: "expiring", label: "Expiring soon" }, { key: "unknown", label: "Held, expiry not recorded" },
  { key: "valid", label: "Valid" }, { key: "not_applicable", label: "Not applicable" }, { key: "blank", label: "Nothing recorded" },
];

function cellText(cell: MatrixCell): { word: string; detail: string } {
  if (cell.no_expiry) return { word: "Valid", detail: "Does not expire" };
  return { word: WORDS[cell.status], detail: cell.expiry_date ? formatDate(cell.expiry_date) : "" };
}

export function Inductions() {
  const { can } = useAuth();
  const [tab, setTab] = useState<"matrix" | "training">("matrix");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Inductions and training</h1>
          <p className="muted">Site inductions by worker, and the courses people have completed. An induction is also listed with the worker's certificates: it is the same record.</p>
        </div>
      </div>
      <div className="tabs" role="tablist" aria-label="Inductions or training" style={{ padding: 0, marginBottom: 12 }}>
        <button type="button" role="tab" className="tab" aria-selected={tab === "matrix"} onClick={() => setTab("matrix")}>Inductions matrix</button>
        <button type="button" role="tab" className="tab" aria-selected={tab === "training"} onClick={() => setTab("training")}>Completed training</button>
      </div>
      {tab === "matrix" ? <MatrixView /> : <TrainingView canAdd={can("reference_library", "edit")} />}
    </>
  );
}

function MatrixView() {
  const { can } = useAuth();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [inactive, setInactive] = useState(false);
  const [editing, setEditing] = useState<{ worker: { id: string; name: string }; column: { column_key: string; name: string }; cell: MatrixCell } | null>(null);
  const [types, setTypes] = useState(false);
  const [message, setMessage] = useState<Msg>(null);
  const team = can("inductions", "team_view");

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);
  const grid = useLoad(() => api<Matrix2>(`/api/workers/inductions/matrix?include_inactive=${inactive}${query ? `&q=${encodeURIComponent(query)}` : ""}`), [query, inactive]);
  const data = grid.data;

  return (
    <section className="card">
      <div className="toolbar">
        {team && <label className="field" style={{ flex: "1 1 220px" }}>Search worker<input type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></label>}
        {team && <label className="check" style={{ alignSelf: "flex-end", minHeight: 44 }}><input type="checkbox" checked={inactive} onChange={(e) => setInactive(e.target.checked)} /> Include inactive workers</label>}
        <span className="spacer" />
        {team && can("inductions", "edit") && <button type="button" className="btn" onClick={() => setTypes(true)}>Induction types</button>}
        <button type="button" className="btn" onClick={() => void downloadFile("/api/workers/inductions/export.csv", "inductions-matrix.csv").catch((e) => setMessage({ tone: "bad", text: errorMessage(e) }))}>Download (CSV)</button>
      </div>
      {message && <div className="card-pad" style={{ paddingTop: 0 }}><Notice tone={message.tone}>{message.text}</Notice></div>}
      <div className="card-pad chips" style={{ paddingTop: 0 }} aria-label="Key">
        {LEGEND.map((l) => (
          <span key={l.key} className={`cell-btn ${l.key}`} style={{ width: "auto", minHeight: 0, padding: "3px 8px", border: "1px solid var(--line)", borderRadius: 4 }}>
            {l.label}{data ? ` (${data.summary[l.key] ?? 0})` : ""}
          </span>
        ))}
      </div>
      {grid.error ? <div className="card-pad"><LoadError message={grid.error} onRetry={grid.reload} /></div> : !data ? <Loading /> : data.rows.length === 0 ? (
        <div className="empty">{team ? (query ? "No workers match this search." : "No workers yet. Add workers first, then record their inductions here.") : "Your login is not linked to a worker record yet, so there are no inductions to show."}</div>
      ) : data.columns.length === 0 ? (
        <div className="empty">No induction types are set up. Add one with “Induction types”.</div>
      ) : (
        <div className="matrix-wrap">
          <table className="grid-matrix">
            <thead>
              <tr>
                <th scope="col">Worker</th>
                {data.columns.map((c) => <th key={c.column_key} scope="col">{c.name}</th>)}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.worker.id}>
                  <th scope="row">
                    {team ? <Link to={`/app/workers/${row.worker.id}`}>{row.worker.name}</Link> : row.worker.name}
                    {!row.worker.active && <span className="muted small"> · inactive</span>}
                  </th>
                  {data.columns.map((c) => {
                    const cell = row.cells[c.column_key];
                    const text = cellText(cell);
                    return (
                      <td key={c.column_key}>
                        <button type="button" className={`cell-btn ${cell.status}`} disabled={!data.can_edit}
                          aria-label={`${row.worker.name}, ${c.name}: ${text.word || "nothing recorded"}${text.detail ? `, ${text.detail}` : ""}`}
                          onClick={() => setEditing({ worker: row.worker, column: c, cell })}>
                          <span className="cell-word">{text.word || (data.can_edit ? <span className="muted">Add</span> : "—")}</span>
                          {text.detail && <span>{text.detail}</span>}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data?.truncated && <div className="card-pad"><Notice tone="info">Showing the first 500 workers. Search to narrow the list.</Notice></div>}
      {editing && <CellEditor target={editing} onClose={() => setEditing(null)} onSaved={(text) => { setEditing(null); setMessage({ tone: "ok", text }); grid.reload(); }} />}
      {types && <TypesModal onClose={() => { setTypes(false); grid.reload(); }} />}
    </section>
  );
}

function CellEditor({ target, onClose, onSaved }: {
  target: { worker: { id: string; name: string }; column: { column_key: string; name: string }; cell: MatrixCell };
  onClose: () => void; onSaved: (text: string) => void;
}) {
  const { can } = useAuth();
  const { cell } = target;
  const [choice, setChoice] = useState<"date" | "never" | "na">(cell.no_expiry ? "never" : cell.status === "not_applicable" ? "na" : "date");
  const [expiry, setExpiry] = useState(cell.expiry_date ?? "");
  const [issue, setIssue] = useState(cell.issue_date ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const recorded = Boolean(cell.certification_id);

  async function put(body: Record<string, unknown>, done: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ cell: MatrixCell }>("/api/workers/inductions/cell", { method: "PUT", body: { worker_id: target.worker.id, column_key: target.column.column_key, ...body } });
      // A file chosen with the date is attached to the record just saved.
      const file = fileInput.current?.files?.[0];
      if (file && result.cell.certification_id) {
        const form = new FormData();
        form.append("file", file);
        await api(`/api/workers/${target.worker.id}/certifications/${result.cell.certification_id}/upload`, { form });
      }
      onSaved(done);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (choice === "date") {
      if (!expiry) { setError("Choose the expiry date, or pick another option."); return; }
      void put({ expiry_date: expiry, issue_date: issue || null }, "Induction recorded.");
    } else if (choice === "never") void put({ held_no_expiry: true, issue_date: issue || null }, "Induction recorded.");
    else void put({ not_held: true }, "Marked as not applicable.");
  }

  return (
    <Modal title={`${target.worker.name}: ${target.column.name}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0, gap: 4 }}>
          <legend className="sr-only">What to record</legend>
          <label className="check"><input type="radio" name="choice" checked={choice === "date"} onChange={() => setChoice("date")} /> Completed, expires on a date</label>
          <label className="check"><input type="radio" name="choice" checked={choice === "never"} onChange={() => setChoice("never")} /> Completed, does not expire</label>
          <label className="check"><input type="radio" name="choice" checked={choice === "na"} disabled={recorded} onChange={() => setChoice("na")} /> Not applicable to this worker</label>
        </fieldset>
        {recorded && <span className="small muted">To mark it not applicable, clear the recorded completion first.</span>}
        {choice !== "na" && (
          <>
            <div className="grid-2">
              {choice === "date" && <label className="field">Expiry date<input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} /></label>}
              <label className="field">Completed on (optional)<input type="date" value={issue} onChange={(e) => setIssue(e.target.value)} /></label>
            </div>
            {can("certifications", "edit") && (
              <label className="field">Certificate or card (optional, PDF or photo)<input ref={fileInput} type="file" accept="application/pdf,image/png,image/jpeg,image/webp" />
                {cell.has_evidence && <span className="hint">A file is already attached. Choosing another replaces it.</span>}
              </label>
            )}
          </>
        )}
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>
            {cell.status !== "blank" && (
              <button type="button" className="btn danger" disabled={busy} onClick={() => { if (window.confirm("Clear this cell? Use this for something entered by mistake.")) void put({ clear: true }, "Cell cleared."); }}>Clear</button>
            )}
          </span>
          <span className="row">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
          </span>
        </div>
      </form>
    </Modal>
  );
}

function TypesModal({ onClose }: { onClose: () => void }) {
  const { can } = useAuth();
  const types = useLoad(() => api<{ items: { id: string; name: string; active: boolean }[] }>("/api/inductions/types?include_inactive=true"), []);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<unknown>) {
    setError(null);
    try { await action(); types.reload(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <Modal title="Induction types" onClose={onClose}>
      <div className="stack">
        {error && <Notice tone="bad">{error}</Notice>}
        <form className="row" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api("/api/inductions/types", { method: "POST", body: { name } }); setName(""); }); }}>
          <label className="field" style={{ flex: 1 }}>New induction<input required minLength={2} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Hydro Tasmania induction" /></label>
          <button type="submit" className="btn primary" style={{ alignSelf: "flex-end" }}>Add</button>
        </form>
        {!types.data ? <Loading /> : (
          <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
            <table>
              <thead><tr><th scope="col">Induction</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {types.data.items.map((t) => (
                  <tr key={t.id}>
                    <td>{t.name}</td>
                    <td>{t.active ? <Chip tone="ok">In use</Chip> : <Chip tone="dashed" icon={false}>Retired</Chip>}</td>
                    <td className="right">
                      {can("inductions", "delete") && (t.active
                        ? <button type="button" className="btn small" onClick={() => void run(() => api(`/api/inductions/types/${t.id}`, { method: "DELETE" }))}>Retire</button>
                        : <button type="button" className="btn small" onClick={() => void run(() => api(`/api/inductions/types/${t.id}/restore`, { method: "POST" }))}>Bring back</button>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted">Retiring an induction removes its column. Completions already recorded stay on each worker's record.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" className="btn" onClick={onClose}>Done</button></div>
      </div>
    </Modal>
  );
}

function TrainingView({ canAdd }: { canAdd: boolean }) {
  const { can } = useAuth();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState<Msg>(null);
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);
  const list = useLoad(() => api<{ items: Training[]; total: number }>(`/api/completed-training?limit=200${query ? `&q=${encodeURIComponent(query)}` : ""}`), [query]);

  return (
    <section className="card">
      <div className="toolbar">
        <label className="field" style={{ flex: "1 1 220px" }}>Search course or worker<input type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
        <span className="spacer" />
        {canAdd && <button type="button" className="btn primary" onClick={() => setAdding(true)}>Record training</button>}
      </div>
      {message && <div className="card-pad" style={{ paddingTop: 0 }}><Notice tone={message.tone}>{message.text}</Notice></div>}
      {list.error ? <div className="card-pad"><LoadError message={list.error} onRetry={list.reload} /></div> : !list.data ? <Loading /> : list.data.items.length === 0 ? (
        <div className="empty">{query ? "No training matches this search." : "No training recorded yet."}</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Worker</th><th scope="col">Course</th><th scope="col">Provider</th><th scope="col">Completed</th><th scope="col">File</th>{can("reference_library", "delete") && <th scope="col"><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {list.data.items.map((t) => (
                <tr key={t.id}>
                  <td>{t.worker.name}</td>
                  <td>{t.course}{t.notes ? <div className="small muted">{t.notes}</div> : null}</td>
                  <td>{t.provider ?? "—"}</td>
                  <td className="num">{formatDate(t.completed_date)}</td>
                  <td>{t.evidence && t.evidence_url ? <button type="button" className="link-btn" onClick={() => void openFile(t.evidence_url!, t.evidence!.name, false).catch((e) => setMessage({ tone: "bad", text: errorMessage(e) }))}>{t.evidence.name}</button> : <span className="muted">None</span>}</td>
                  {can("reference_library", "delete") && (
                    <td className="right"><button type="button" className="btn small" onClick={async () => {
                      if (!window.confirm(`Remove “${t.course}” for ${t.worker.name}?`)) return;
                      try { await api(`/api/completed-training/${t.id}`, { method: "DELETE" }); list.reload(); } catch (e) { setMessage({ tone: "bad", text: errorMessage(e) }); }
                    }}>Remove</button></td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && <AddTraining onClose={() => setAdding(false)} onSaved={() => { setAdding(false); setMessage({ tone: "ok", text: "Training recorded." }); list.reload(); }} />}
    </section>
  );
}

function AddTraining({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const workers = useLoad(() => api<{ items: Worker[] }>("/api/workers?limit=200"), []);
  const fileInput = useRef<HTMLInputElement>(null);
  const [workerId, setWorkerId] = useState("");
  const [course, setCourse] = useState("");
  const [provider, setProvider] = useState("");
  const [when, setWhen] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const record = await api<Training>("/api/completed-training", { method: "POST", body: { worker_id: workerId, course, provider: provider || null, completed_date: when || null } });
      const file = fileInput.current?.files?.[0];
      if (file) {
        const form = new FormData();
        form.append("file", file);
        await api(`/api/completed-training/${record.id}/file`, { form });
      }
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title="Record training" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">
          Worker
          <select required value={workerId} onChange={(e) => setWorkerId(e.target.value)}>
            <option value="">Choose…</option>
            {(workers.data?.items ?? []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
        <label className="field">Course<input required minLength={2} value={course} onChange={(e) => setCourse(e.target.value)} /></label>
        <div className="grid-2">
          <label className="field">Provider (optional)<input value={provider} onChange={(e) => setProvider(e.target.value)} /></label>
          <label className="field">Completed on (optional)<input type="date" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
        </div>
        <label className="field">Certificate (optional, PDF or photo)<input ref={fileInput} type="file" accept="application/pdf,image/png,image/jpeg,image/webp" /></label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}
