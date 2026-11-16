import { useState, type FormEvent } from "react";
import { api, formatDate, openFile } from "../api";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface LeaveRow {
  id: string; worker_id: string; worker: string; leave_type: string; leave_type_label: string; start_date: string; end_date: string; hours: string; reason: string | null;
  status: string; status_label: string; source: string; requested_by: string | null; decided_by: string | null; decision_note: string | null; cancel_reason: string | null;
  has_certificate: boolean; used_by_payroll: boolean; needs_payroll_correction: boolean; is_own: boolean; can_decide: boolean; can_cancel: boolean;
}
const TONE: Record<string, "ok" | "warn" | "bad" | "neutral"> = { requested: "warn", approved: "ok", rejected: "bad", cancelled: "neutral" };
const TYPES: [string, string][] = [["annual", "Annual leave"], ["personal", "Personal (sick or carer's) leave"], ["unpaid", "Unpaid leave"], ["long_service", "Long service leave"], ["other", "Other leave"]];
const dates = (r: LeaveRow) => (r.start_date === r.end_date ? formatDate(r.start_date) : `${formatDate(r.start_date)} to ${formatDate(r.end_date)}`);

function Status({ r }: { r: LeaveRow }) {
  return (
    <>
      <Chip tone={TONE[r.status] ?? "neutral"}>{r.status_label}</Chip>
      {r.needs_payroll_correction && <> <Chip tone="bad">Payroll to correct</Chip></>}
      {r.status === "approved" && r.decided_by && <div className="muted small">by {r.decided_by}{r.decision_note ? `: ${r.decision_note}` : ""}</div>}
      {r.status === "rejected" && <div className="small">{r.decision_note}</div>}
      {r.status === "cancelled" && r.cancel_reason && <div className="muted small">{r.cancel_reason}</div>}
    </>
  );
}

function LeaveForm({ workerPicker, onClose, onSaved }: { workerPicker: boolean; onClose: () => void; onSaved: () => void }) {
  const workers = useLoad(() => (workerPicker ? api<{ items: { id: string; name: string }[] }>("/api/workers?status=active&limit=200") : Promise.resolve(null)), [workerPicker]);
  const [v, setV] = useState({ worker_id: "", leave_type: "annual", start_date: "", end_date: "", hours: "", reason: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body = { leave_type: v.leave_type, start_date: v.start_date, end_date: v.end_date || v.start_date, hours: v.hours === "" ? null : Number(v.hours), reason: v.reason || null };
    try {
      await api(workerPicker ? "/api/leave" : "/api/me/leave", { method: "POST", body: workerPicker ? { ...body, worker_id: v.worker_id } : body });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  return (
    <Modal title={workerPicker ? "Enter leave for a worker" : "Ask for leave"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {workerPicker && (
          <label className="field">Worker
            <select value={v.worker_id} onChange={set("worker_id")} required><option value="">Choose…</option>{(workers.data?.items ?? []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select>
          </label>
        )}
        <label className="field">Kind of leave<select value={v.leave_type} onChange={set("leave_type")}>{TYPES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select></label>
        <div className="grid-3">
          <label className="field">First day<input type="date" value={v.start_date} onChange={set("start_date")} required /></label>
          <label className="field">Last day<input type="date" value={v.end_date} min={v.start_date} onChange={set("end_date")} /></label>
          <label className="field">Hours (optional)<input type="number" min={0.25} step="0.25" value={v.hours} onChange={set("hours")} /></label>
        </div>
        <p className="muted small">Leave the hours empty for whole working days. Fill them in for part of a day, or for a weekend.</p>
        <label className="field">Reason or note (optional)<textarea rows={2} value={v.reason} onChange={set("reason")} maxLength={2000} /></label>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{workerPicker ? "Save request" : "Send request"}</button>
        </div>
      </form>
    </Modal>
  );
}

function ReasonModal({ title, label, required, action, onClose, onDone }: { title: string; label: string; required: boolean; action: (text: string) => Promise<unknown>; onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    try { await action(text); onDone(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <Modal title={title} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">{label}<input value={text} onChange={(e) => setText(e.target.value)} required={required} maxLength={600} autoFocus /></label>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Back</button>
          <button type="submit" className="btn primary">{title}</button>
        </div>
      </form>
    </Modal>
  );
}

/** The person's own leave: ask, see the answer, cancel, attach a certificate. */
export function MyLeave() {
  const mine = useLoad(() => api<{ linked: boolean; requests: LeaveRow[]; hours_per_day: string; balances_note?: string }>("/api/me/leave"), []);
  const [asking, setAsking] = useState(false);
  const [cancelling, setCancelling] = useState<LeaveRow | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const data = mine.data;
  async function attach(r: LeaveRow, file: File | undefined) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try { await api(`/api/me/leave/${r.id}/certificate`, { form }); setMsg({ tone: "ok", text: "Evidence attached." }); mine.reload(); } catch (e) { setMsg({ tone: "bad", text: errorMessage(e) }); }
  }
  return (
    <>
      <div className="page-head">
        <div><h1>My leave</h1><p className="muted">Ask for time off and see what has been decided.{data ? ` A working day counts as ${Number(data.hours_per_day)} hours.` : ""}</p></div>
        {data?.linked && <button type="button" className="btn primary" onClick={() => setAsking(true)}>Ask for leave</button>}
      </div>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {mine.error ? <LoadError message={mine.error} onRetry={mine.reload} /> : !data ? <Loading /> : !data.linked ? (
        <Notice tone="info">Your login is not linked to a worker record, so leave cannot be requested here. Ask the office to link it.</Notice>
      ) : (
        <section className="card">
          {data.requests.length === 0 ? <div className="empty">You have not asked for any leave yet.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th scope="col">Dates</th><th scope="col">Kind</th><th scope="col">Hours</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{data.requests.map((r) => (
                <tr key={r.id}>
                  <td>{dates(r)}</td><td>{r.leave_type_label}</td><td>{Number(r.hours)}</td><td><Status r={r} /></td>
                  <td className="row" style={{ gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
                    {r.has_certificate && <button type="button" className="btn small" onClick={() => void openFile(`/api/leave/${r.id}/certificate`, "evidence", false).catch((e) => setMsg({ tone: "bad", text: errorMessage(e) }))}>Open evidence</button>}
                    {r.can_cancel && <label className="btn small">{r.has_certificate ? "Replace evidence" : "Attach evidence"}<input type="file" className="sr-only" accept=".pdf,image/*" aria-label={`Attach evidence for ${dates(r)}`} onChange={(e) => void attach(r, e.target.files?.[0])} /></label>}
                    {r.can_cancel && <button type="button" className="btn small" aria-label={`Cancel leave ${dates(r)}`} onClick={() => setCancelling(r)}>Cancel</button>}
                  </td>
                </tr>))}</tbody>
            </table></div>
          )}
          {data.balances_note && <p className="card-pad muted small" style={{ margin: 0 }}>{data.balances_note}</p>}
        </section>
      )}
      {asking && <LeaveForm workerPicker={false} onClose={() => setAsking(false)} onSaved={() => { setAsking(false); setMsg({ tone: "ok", text: "Request sent. You will see the decision here." }); mine.reload(); }} />}
      {cancelling && <ReasonModal title="Cancel leave" label={cancelling.used_by_payroll ? "Why? (payroll has already used this leave and will need to correct it)" : "Why? (optional)"} required={cancelling.used_by_payroll}
        action={(reason) => api(`/api/me/leave/${cancelling.id}/cancel`, { method: "POST", body: { reason: reason || null } })} onClose={() => setCancelling(null)} onDone={() => { setCancelling(null); mine.reload(); }} />}
    </>
  );
}

/** The office view: decide requests, see who is away, enter leave for someone. */
export function Leave() {
  const [status, setStatus] = useState("requested");
  const list = useLoad(() => api<{ items: LeaveRow[]; can_edit: boolean }>(`/api/leave${status ? `?status=${status}` : ""}`), [status]);
  const summary = useLoad(() => api<{ waiting: number; on_leave_today: number; starting_within_14_days: number; needs_payroll_correction: number }>("/api/leave/summary"), []);
  const away = useLoad(() => api<{ items: { worker_id: string; worker: string; leave_type_label: string; until: string }[] }>("/api/leave/on-date"), []);
  const [adding, setAdding] = useState(false);
  const [acting, setActing] = useState<{ row: LeaveRow; action: "reject" | "cancel" } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = () => { list.reload(); summary.reload(); away.reload(); };
  const data = list.data;
  const s = summary.data;
  async function approve(r: LeaveRow) {
    setError(null);
    try { await api(`/api/leave/${r.id}/decision`, { method: "POST", body: { decision: "approve" } }); refresh(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <>
      <div className="page-head">
        <div><h1>Leave</h1><p className="muted">Requests for time off. Nobody can decide their own request, and no message is sent when one is decided.</p></div>
        {data?.can_edit && <button type="button" className="btn primary" onClick={() => setAdding(true)}>Enter leave for a worker</button>}
      </div>
      {s && (
        <div className="tiles">
          <div className="tile static"><span className="tile-label">Waiting for a decision</span><strong>{s.waiting}</strong></div>
          <div className="tile static"><span className="tile-label">Away today</span><strong>{s.on_leave_today}</strong><span className="muted small">{(away.data?.items ?? []).map((a) => a.worker).join(", ")}</span></div>
          <div className="tile static"><span className="tile-label">Starting in the next 14 days</span><strong>{s.starting_within_14_days}</strong></div>
          {s.needs_payroll_correction > 0 && <div className="tile static"><span className="tile-label">For payroll to correct</span><strong>{s.needs_payroll_correction}</strong><span className="muted small">cancelled after being paid</span></div>}
        </div>
      )}
      {error && <Notice tone="bad">{error}</Notice>}
      <section className="card">
        <div className="tabs" role="tablist" aria-label="Leave requests">
          {[["requested", "Waiting"], ["approved", "Approved"], ["rejected", "Not approved"], ["cancelled", "Cancelled"], ["", "All"]].map(([key, label]) => (
            <button key={key} type="button" role="tab" className="tab" aria-selected={status === key} onClick={() => setStatus(key)}>{label}</button>
          ))}
        </div>
        {list.error ? <div className="card-pad"><LoadError message={list.error} onRetry={list.reload} /></div> : !data ? <Loading /> : data.items.length === 0 ? <div className="empty">Nothing here.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Worker</th><th scope="col">Dates</th><th scope="col">Kind</th><th scope="col">Hours</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{data.items.map((r) => (
              <tr key={r.id}>
                <td>{r.worker}{r.source === "office" && <div className="muted small">Entered by {r.requested_by}</div>}{r.reason && <div className="muted small">{r.reason}</div>}</td>
                <td>{dates(r)}</td><td>{r.leave_type_label}</td><td>{Number(r.hours)}</td><td><Status r={r} /></td>
                <td className="row" style={{ gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
                  {r.has_certificate && data.can_edit && <button type="button" className="btn small" onClick={() => void openFile(`/api/leave/${r.id}/certificate`, "evidence", false).catch((e) => setError(errorMessage(e)))}>Evidence</button>}
                  {r.can_decide && <button type="button" className="btn small primary" aria-label={`Approve leave for ${r.worker}`} onClick={() => void approve(r)}>Approve</button>}
                  {r.can_decide && <button type="button" className="btn small" aria-label={`Do not approve leave for ${r.worker}`} onClick={() => setActing({ row: r, action: "reject" })}>Do not approve</button>}
                  {r.status === "requested" && r.is_own && data.can_edit && <span className="muted small">Someone else must decide your own request</span>}
                  {r.can_cancel && r.status === "approved" && data.can_edit && <button type="button" className="btn small" aria-label={`Cancel leave for ${r.worker}`} onClick={() => setActing({ row: r, action: "cancel" })}>Cancel</button>}
                </td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
      {adding && <LeaveForm workerPicker onClose={() => setAdding(false)} onSaved={() => { setAdding(false); refresh(); }} />}
      {acting && (acting.action === "reject"
        ? <ReasonModal title="Do not approve" label="Why? The person will see this." required action={(note) => api(`/api/leave/${acting.row.id}/decision`, { method: "POST", body: { decision: "reject", note } })} onClose={() => setActing(null)} onDone={() => { setActing(null); refresh(); }} />
        : <ReasonModal title="Cancel leave" label={acting.row.used_by_payroll ? "Why? (payroll has already used this leave and will need to correct it)" : "Why? (optional)"} required={acting.row.used_by_payroll} action={(reason) => api(`/api/leave/${acting.row.id}/cancel`, { method: "POST", body: { reason: reason || null } })} onClose={() => setActing(null)} onDone={() => { setActing(null); refresh(); }} />)}
    </>
  );
}
