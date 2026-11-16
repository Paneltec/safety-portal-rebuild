import { useState, type FormEvent } from "react";
import { api, formatDate } from "../api";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface Shift { start: string; finish: string; next_day: boolean; break_minutes: number; break_start: string | null; site_id: string | null; site?: string | null; category: string; note: string | null; public_holiday?: boolean; replacement_day_shift?: boolean }
interface Entry {
  id: string; worker_id: string; worker: string; work_date: string; kind: string; kind_label: string; shifts: Shift[]; hours: Record<string, number>; paid_hours: string; notes: string | null;
  status: string; status_label: string; source: string; revision: number; reject_reason: string | null; reviewed_by: string | null; locked: boolean;
}
interface MyWeek { linked: boolean; week_start: string; week_end: string; today: string; days: { date: string; entry: Entry | null; editable: boolean; why_not: string | null }[]; total_paid_hours: string; ready_to_send: number }
interface OfficeWeek { week_start: string; week_end: string; items: Entry[]; workers: { worker_id: string; worker: string; paid_hours: string; days: number; is_own: boolean }[]; can_edit: boolean }
interface Counts { changed: number; unchanged: number; not_found: number; own_skipped: number }

const TONE: Record<string, "ok" | "warn" | "bad" | "neutral" | "info"> = { draft: "neutral", submitted: "warn", approved: "ok", rejected: "bad", locked: "info" };
const BUCKETS: [string, string][] = [["ordinary", "ordinary"], ["ot1", "overtime (first rate)"], ["ot2", "overtime (second rate)"], ["night", "night"], ["saturday", "Saturday"], ["sunday", "Sunday"], ["holiday_work", "public holiday"]];
const addDays = (iso: string, n: number) => new Date(new Date(`${iso}T12:00:00Z`).getTime() + n * 86400000).toISOString().slice(0, 10);
const weekday = (iso: string) => new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-AU", { weekday: "long", timeZone: "UTC" });
const emptyShift = (): Shift => ({ start: "07:00", finish: "15:30", next_day: false, break_minutes: 30, break_start: null, site_id: null, category: "site", note: null });

export function hoursLine(e: Entry): string {
  const parts = BUCKETS.filter(([k]) => (e.hours[k] ?? 0) > 0).map(([k, label]) => `${e.hours[k]} ${label}`);
  return parts.join(", ") || (e.kind === "work" ? "No hours" : e.kind_label);
}
const times = (e: Entry) => e.shifts.map((s) => `${s.start}–${s.finish}${s.next_day ? " (next day)" : ""}${s.public_holiday ? " · public holiday" : ""}`).join(", ") || e.kind_label;

function WeekNav({ start, end, onMove }: { start: string; end: string; onMove: (to: string) => void }) {
  return (
    <div className="row" style={{ gap: 8, alignItems: "center" }}>
      <button type="button" className="btn small" onClick={() => onMove(addDays(start, -7))}>Week before</button>
      <strong>{formatDate(start)} to {formatDate(end)}</strong>
      <button type="button" className="btn small" onClick={() => onMove(addDays(start, 7))}>Week after</button>
    </div>
  );
}

function DayEditor({ date, entry, office, workerId, onClose, onSaved }: { date: string; entry: Entry | null; office: boolean; workerId?: string; onClose: () => void; onSaved: () => void }) {
  const sites = useLoad(() => api<{ items: { id: string; label: string }[] }>("/api/forms/pickers/sites").catch(() => ({ items: [] })), []);
  const [kind, setKind] = useState(entry?.kind ?? "work");
  const [shifts, setShifts] = useState<Shift[]>(entry ? entry.shifts : [emptyShift()]);
  const [notes, setNotes] = useState(entry?.notes ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const patch = (i: number, change: Partial<Shift>) => setShifts(shifts.map((s, j) => (j === i ? { ...s, ...change } : s)));
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body = { kind, notes: notes || null, expected_revision: entry?.revision ?? null,
      shifts: kind === "work" ? shifts.map((s) => ({ start: s.start, finish: s.finish, next_day: s.next_day, break_minutes: Number(s.break_minutes) || 0, break_start: s.break_start || null, site_id: s.site_id || null,
        category: s.category, note: s.note || null, ...(office ? { public_holiday: Boolean(s.public_holiday), replacement_day_shift: Boolean(s.replacement_day_shift) } : {}) })) : [] };
    try {
      if (!office) await api(`/api/me/payroll/timesheets/${date}`, { method: "PUT", body });
      else if (entry) await api(`/api/payroll/timesheets/${entry.id}`, { method: "PATCH", body });
      else await api("/api/payroll/timesheets", { method: "POST", body: { ...body, worker_id: workerId, work_date: date } });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  return (
    <Modal title={`${weekday(date)} ${formatDate(date)}${entry && office ? `: ${entry.worker}` : ""}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {entry?.status === "rejected" && entry.reject_reason && <Notice tone="warn">Sent back by the office: {entry.reject_reason}</Notice>}
        <label className="field">This day
          <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="work">I worked</option><option value="rdo">Rostered day off</option><option value="no_work">Did not work</option></select>
        </label>
        {kind === "work" && shifts.map((s, i) => (
          <fieldset key={i} className="stack" style={{ gap: 8 }}>
            <legend>Shift {i + 1}</legend>
            <div className="grid-3">
              <label className="field">Start<input type="time" value={s.start} onChange={(e) => patch(i, { start: e.target.value })} required /></label>
              <label className="field">Finish<input type="time" value={s.finish} onChange={(e) => patch(i, { finish: e.target.value })} required /></label>
              <label className="field">Unpaid break (minutes)<input type="number" min={0} max={720} value={s.break_minutes} onChange={(e) => patch(i, { break_minutes: Number(e.target.value) })} /></label>
              <label className="field">Break started at (optional)<input type="time" value={s.break_start ?? ""} onChange={(e) => patch(i, { break_start: e.target.value || null })} /></label>
              <label className="field">Where
                <select value={s.site_id ?? ""} onChange={(e) => patch(i, { site_id: e.target.value || null })}><option value="">Not a listed site</option>{(sites.data?.items ?? []).map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}</select>
              </label>
              <label className="field">Kind of work
                <select value={s.category} onChange={(e) => patch(i, { category: e.target.value })}><option value="site">On site</option><option value="yard">Yard / Workshop</option><option value="travel">Travel</option><option value="training">Training</option><option value="office">Office</option></select>
              </label>
            </div>
            <label className="check"><input type="checkbox" checked={s.next_day} onChange={(e) => patch(i, { next_day: e.target.checked })} /> Finished after midnight (the next day)</label>
            {office && <label className="check"><input type="checkbox" checked={Boolean(s.public_holiday)} onChange={(e) => patch(i, { public_holiday: e.target.checked })} /> Worked on a public holiday</label>}
            {office && <label className="check"><input type="checkbox" checked={Boolean(s.replacement_day_shift)} onChange={(e) => patch(i, { replacement_day_shift: e.target.checked })} /> A night shift worked in place of the usual day shift</label>}
            {shifts.length > 1 && <button type="button" className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => setShifts(shifts.filter((_, j) => j !== i))}>Remove this shift</button>}
          </fieldset>
        ))}
        {kind === "work" && shifts.length < 10 && <button type="button" className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => setShifts([...shifts, { ...emptyShift(), start: "", finish: "", break_minutes: 0 }])}>Add another shift</button>}
        <label className="field">Notes (optional)<input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} /></label>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>Save day</button>
        </div>
      </form>
    </Modal>
  );
}

/** A worker's own week: fill in each day, then send the week to the office. */
export function MyTimesheet() {
  const [week, setWeek] = useState("");
  const mine = useLoad(() => api<MyWeek>(`/api/me/payroll/timesheets${week ? `?week_start=${week}` : ""}`), [week]);
  const [editing, setEditing] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const data = mine.data;
  async function send() {
    if (!data) return;
    try {
      const r = await api<{ submitted: number; skipped_empty: number }>("/api/me/payroll/submit", { method: "POST", body: { week_start: data.week_start } });
      setMsg({ tone: "ok", text: `${r.submitted} day${r.submitted === 1 ? "" : "s"} sent to the office.${r.skipped_empty ? ` ${r.skipped_empty} empty day${r.skipped_empty === 1 ? " was" : "s were"} left out.` : ""}` });
      mine.reload();
    } catch (e) {
      setMsg({ tone: "bad", text: errorMessage(e) });
    }
  }
  const day = data?.days.find((d) => d.date === editing);
  return (
    <>
      <div className="page-head">
        <div><h1>My timesheet</h1><p className="muted">Fill in each day you worked, then send the week to the office. Hours are worked out for you from the times.</p></div>
        {data?.linked && data.ready_to_send > 0 && <button type="button" className="btn primary" onClick={() => void send()}>Send {data.ready_to_send} day{data.ready_to_send === 1 ? "" : "s"} to the office</button>}
      </div>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {mine.error ? <LoadError message={mine.error} onRetry={mine.reload} /> : !data ? <Loading /> : !data.linked ? (
        <Notice tone="info">Your login is not linked to a worker record, so there is no timesheet here. Ask the office to link it.</Notice>
      ) : (
        <section className="card">
          <div className="toolbar" style={{ alignItems: "center" }}><WeekNav start={data.week_start} end={data.week_end} onMove={setWeek} /><span className="spacer" /><strong>{Number(data.total_paid_hours)} paid hours this week</strong></div>
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Day</th><th scope="col">Times</th><th scope="col">Hours</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{data.days.map((d) => (
              <tr key={d.date}>
                <td>{weekday(d.date)}<div className="muted small">{formatDate(d.date)}</div></td>
                <td>{d.entry ? times(d.entry) : <span className="muted">Nothing entered</span>}</td>
                <td>{d.entry ? <>{Number(d.entry.paid_hours)}<div className="muted small">{hoursLine(d.entry)}</div></> : "—"}</td>
                <td>{d.entry ? <><Chip tone={TONE[d.entry.status] ?? "neutral"}>{d.entry.status_label}</Chip>{d.entry.status === "rejected" && <div className="small">{d.entry.reject_reason}</div>}</> : "—"}</td>
                <td style={{ textAlign: "right" }}>{d.editable ? <button type="button" className="btn small" aria-label={`${d.entry ? "Change" : "Fill in"} ${weekday(d.date)}`} onClick={() => setEditing(d.date)}>{d.entry ? "Change" : "Fill in"}</button> : <span className="muted small">{d.why_not}</span>}</td>
              </tr>))}</tbody>
          </table></div>
        </section>
      )}
      {editing && day && <DayEditor date={editing} entry={day.entry} office={false} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setMsg(null); mine.reload(); }} />}
    </>
  );
}

function AccessPanel() {
  const access = useLoad(() => api<{ users: { id: string; name: string; level: string; is_owner: boolean }[]; note: string }>("/api/payroll/workbench/access/list").catch(() => null), []);
  const [error, setError] = useState<string | null>(null);
  if (!access.data) return null;  // only the payroll owner sees this
  async function set(id: string, level: string) {
    setError(null);
    try { await api(`/api/users/${id}/payroll-access`, { method: "PUT", body: { level } }); access.reload(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <section className="card card-pad stack" aria-label="Payroll access">
      <h2 style={{ margin: 0 }}>Who can open payroll</h2>
      <p className="muted small" style={{ margin: 0 }}>You are the payroll owner. {access.data.note}</p>
      {error && <Notice tone="bad">{error}</Notice>}
      {access.data.users.map((u) => (
        <div key={u.id} className="row" style={{ gap: 12, alignItems: "center" }}>
          <span style={{ flex: 1 }}>{u.name}</span>
          {u.is_owner ? <Chip tone="info">Payroll owner</Chip> : (
            <select aria-label={`Payroll access for ${u.name}`} value={u.level} onChange={(e) => void set(u.id, e.target.value)}>
              <option value="none">No access</option><option value="view">Can look</option><option value="edit">Can look and change</option>
            </select>
          )}
        </div>
      ))}
    </section>
  );
}

function PickDay({ weekStart, onClose, onPicked }: { weekStart: string; onClose: () => void; onPicked: (c: { workerId: string; date: string }) => void }) {
  const workers = useLoad(() => api<{ items: { id: string; name: string }[] }>("/api/workers?limit=200"), []);
  const [workerId, setWorkerId] = useState("");
  const [date, setDate] = useState(weekStart);
  return (
    <Modal title="Enter a day for a worker" onClose={onClose}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); onPicked({ workerId, date }); }}>
        <label className="field">Worker<select value={workerId} onChange={(e) => setWorkerId(e.target.value)} required><option value="">Choose…</option>{(workers.data?.items ?? []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
        <label className="field">Date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
        <p className="muted small">Use this when a worker cannot enter their own day. It still needs approving.</p>
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary">Next</button>
        </div>
      </form>
    </Modal>
  );
}

/** The office review: every worker's days for a week. Only people with payroll access get here. */
export function Timesheets() {
  const [week, setWeek] = useState("");
  const list = useLoad(() => api<OfficeWeek>(`/api/payroll/timesheets${week ? `?week_start=${week}` : ""}`), [week]);
  const [editing, setEditing] = useState<Entry | null>(null);
  const [sendingBack, setSendingBack] = useState<Entry | null>(null);
  const [picking, setPicking] = useState(false);
  const [creating, setCreating] = useState<{ workerId: string; date: string } | null>(null);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "bad" | "info"; text: string } | null>(null);
  const data = list.data;
  if (list.error) return <LoadError message={list.error} onRetry={list.reload} />;
  if (!data) return <Loading />;
  const own = new Set(data.workers.filter((w) => w.is_own).map((w) => w.worker_id));
  const waiting = data.items.filter((e) => e.status === "submitted" && !own.has(e.worker_id));

  async function review(path: string, body: Record<string, unknown>, verb: string) {
    try {
      const c = await api<Counts>(`/api/payroll/timesheets/${path}`, { method: "POST", body });
      setMsg({ tone: "ok", text: `${c.changed} day${c.changed === 1 ? "" : "s"} ${verb}.${c.own_skipped ? ` ${c.own_skipped} of your own left for someone else.` : ""}${c.unchanged ? ` ${c.unchanged} already done.` : ""}` });
      list.reload();
    } catch (e) {
      setMsg({ tone: "bad", text: errorMessage(e) });
    }
  }
  async function prefill() {
    try {
      const r = await api<{ created: number; already_there: number; could_not_use: number }>("/api/payroll/timesheets/prefill", { method: "POST", body: { week_start: data?.week_start } });
      setMsg({ tone: "info", text: `${r.created} draft${r.created === 1 ? "" : "s"} started from site sign-ons. ${r.already_there} day${r.already_there === 1 ? "" : "s"} already had a timesheet and were left alone.${r.could_not_use ? ` ${r.could_not_use} could not be used (overlapping sign-ons).` : ""} Workers still check and send their own drafts.` });
      list.reload();
    } catch (e) {
      setMsg({ tone: "bad", text: errorMessage(e) });
    }
  }
  return (
    <>
      <div className="page-head">
        <div><h1>Timesheets</h1><p className="muted">Check the hours workers have sent in. Approved days feed the pay workbench. Nobody approves their own days.</p></div>
        {data.can_edit && (
          <div className="row" style={{ gap: 8 }}>
            <button type="button" className="btn" onClick={() => setPicking(true)}>Enter a day for a worker</button>
            <button type="button" className="btn" onClick={() => void prefill()}>Start drafts from site sign-ons</button>
            {waiting.length > 0 && <button type="button" className="btn primary" onClick={() => void review("approve", { ids: waiting.map((e) => e.id) }, "approved")}>Approve all {waiting.length} waiting</button>}
          </div>
        )}
      </div>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <AccessPanel />
      <section className="card">
        <div className="toolbar" style={{ alignItems: "center" }}><WeekNav start={data.week_start} end={data.week_end} onMove={setWeek} /><span className="spacer" />
          <span className="muted small">{data.workers.map((w) => `${w.worker} ${Number(w.paid_hours)} h`).join(" · ")}</span></div>
        {data.items.length === 0 ? <div className="empty">No timesheets for this week yet.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Worker</th><th scope="col">Day</th><th scope="col">Times</th><th scope="col">Hours</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{data.items.map((e) => (
              <tr key={e.id}>
                <td>{e.worker}{e.source === "prefill" && <div className="muted small">Started from site sign-ons</div>}{e.source === "office" && <div className="muted small">Entered by the office</div>}</td>
                <td>{weekday(e.work_date).slice(0, 3)} {formatDate(e.work_date)}</td>
                <td>{times(e)}{e.notes && <div className="muted small">{e.notes}</div>}</td>
                <td>{Number(e.paid_hours)}<div className="muted small">{hoursLine(e)}</div></td>
                <td><Chip tone={TONE[e.status] ?? "neutral"}>{e.status_label}</Chip>{e.status === "rejected" && <div className="small">{e.reject_reason}</div>}{e.status === "approved" && e.reviewed_by && <div className="muted small">by {e.reviewed_by}</div>}</td>
                <td className="row" style={{ gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
                  {data.can_edit && !e.locked && own.has(e.worker_id) && e.status === "submitted" && <span className="muted small">Someone else must approve your own days</span>}
                  {data.can_edit && e.status === "submitted" && !own.has(e.worker_id) && <button type="button" className="btn small primary" aria-label={`Approve ${e.worker} ${formatDate(e.work_date)}`} onClick={() => void review("approve", { ids: [e.id] }, "approved")}>Approve</button>}
                  {data.can_edit && (e.status === "submitted" || e.status === "approved") && !own.has(e.worker_id) && <button type="button" className="btn small" aria-label={`Send back ${e.worker} ${formatDate(e.work_date)}`} onClick={() => { setReason(""); setSendingBack(e); }}>Send back</button>}
                  {data.can_edit && !e.locked && <button type="button" className="btn small" aria-label={`Correct ${e.worker} ${formatDate(e.work_date)}`} onClick={() => setEditing(e)}>Correct</button>}
                </td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
      {editing && <DayEditor date={editing.work_date} entry={editing} office onClose={() => setEditing(null)} onSaved={() => { setEditing(null); list.reload(); }} />}
      {picking && <PickDay weekStart={data.week_start} onClose={() => setPicking(false)} onPicked={(c) => { setPicking(false); setCreating(c); }} />}
      {creating && <DayEditor date={creating.date} entry={null} office workerId={creating.workerId} onClose={() => setCreating(null)} onSaved={() => { setCreating(null); list.reload(); }} />}
      {sendingBack && (
        <Modal title={`Send back ${sendingBack.worker}, ${formatDate(sendingBack.work_date)}`} onClose={() => setSendingBack(null)}>
          <form className="stack" onSubmit={(ev) => { ev.preventDefault(); void review("reject", { ids: [sendingBack.id], reason }, "sent back").then(() => setSendingBack(null)); }}>
            <label className="field">What needs fixing? The worker will see this.<input value={reason} onChange={(e) => setReason(e.target.value)} required maxLength={600} autoFocus /></label>
            <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
              <button type="button" className="btn" onClick={() => setSendingBack(null)}>Cancel</button>
              <button type="submit" className="btn primary">Send back</button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
