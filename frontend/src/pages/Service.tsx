import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, formatDate } from "../api";
import { ReminderButton } from "../components/Reminders";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";
import type { Asset } from "./Fleet";

interface Part { basis: string; status: string; text: string }
interface Schedule {
  id: string; name: string; interval_days: number | null; interval_km: number | null; interval_hours: number | null; warn_days: number; warn_km: number; warn_hours: number;
  baseline_date: string | null; baseline_km: number | null; baseline_hours: number | null; assigned_worker_id: string | null; assigned_worker: string | null; notes: string | null;
  active: boolean; status: string; status_label: string; summary: string; parts: Part[];
  last_done: { date: string | null; odometer_km: number | null; engine_hours: number | null; from_record: boolean } | null;
}
interface ServiceRecord {
  id: string; kind: string; kind_label: string; performed_on: string; schedule_id: string | null; schedule_name: string | null; odometer_km: number | null; engine_hours: number | null;
  description: string | null; technician: string | null; cost: string | null; needs_attention: number; recorded_by: string | null;
}
interface Preset { key: string; name: string; interval_days?: number; interval_km?: number; interval_hours?: number; note: string }

const TONE: Record<string, "ok" | "warn" | "bad" | "neutral"> = { ok: "ok", due_soon: "warn", overdue: "bad", unknown: "neutral", off: "neutral" };
export function ServiceChip({ status, label }: { status: string; label: string }) {
  return <Chip tone={TONE[status] ?? "neutral"}>{label}</Chip>;
}

function every(s: Schedule): string {
  const bits = [];
  if (s.interval_km) bits.push(`${s.interval_km.toLocaleString()} km`);
  if (s.interval_hours) bits.push(`${s.interval_hours.toLocaleString()} hours`);
  if (s.interval_days) bits.push(`${s.interval_days} days`);
  return `Every ${bits.join(" or ")}${bits.length > 1 ? ", whichever comes first" : ""}`;
}

/** Schedules and service history, shown on an asset's page. */
export function ServicePanel({ asset, reload }: { asset: Asset; reload: () => void }) {
  const schedules = useLoad(() => api<{ items: Schedule[]; can_edit: boolean }>(`/api/assets/${asset.id}/schedules`), [asset.id, asset.meter_at]);
  const records = useLoad(() => api<{ items: ServiceRecord[]; total_cost: string | null; records_without_cost: number; can_edit: boolean; can_delete: boolean }>(`/api/assets/${asset.id}/records`), [asset.id]);
  const [editing, setEditing] = useState<Schedule | "new" | null>(null);
  const [recording, setRecording] = useState<{ schedule?: Schedule } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = () => { schedules.reload(); records.reload(); reload(); };
  const canEdit = schedules.data?.can_edit ?? false;

  return (
    <>
      <section className="card" aria-label="Service schedules">
        <div className="card-pad row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>Service schedules</h2>
          {canEdit && <button type="button" className="btn small" onClick={() => setEditing("new")}>Add schedule</button>}
        </div>
        {error && <div className="card-pad"><Notice tone="bad">{error}</Notice></div>}
        {schedules.error ? <div className="card-pad"><LoadError message={schedules.error} onRetry={schedules.reload} /></div> : !schedules.data ? <Loading /> : schedules.data.items.length === 0 ? (
          <div className="empty">No schedules. Add one to be told when a service is coming up.</div>
        ) : (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Service</th><th scope="col">How often</th><th scope="col">Last done</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{schedules.data.items.map((s) => (
              <tr key={s.id}>
                <td>{s.name}{s.assigned_worker && <div className="muted small">Assigned to {s.assigned_worker}</div>}</td>
                <td>{every(s)}</td>
                <td>{s.last_done?.date ? formatDate(s.last_done.date) : "—"}{s.last_done && !s.last_done.from_record && (s.last_done.date || s.last_done.odometer_km !== null || s.last_done.engine_hours !== null) && <div className="muted small">Starting point entered by hand</div>}</td>
                <td><ServiceChip status={s.status} label={s.status_label} /><div className="small">{s.summary}</div></td>
                <td className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                  {canEdit && s.active && <button type="button" className="btn small primary" aria-label={`Record ${s.name} as done`} onClick={() => setRecording({ schedule: s })}>Record as done</button>}
                  {canEdit && <button type="button" className="btn small" aria-label={`Edit ${s.name}`} onClick={() => setEditing(s)}>Edit</button>}
                </td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>

      <section className="card" aria-label="Service history">
        <div className="card-pad row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>Service history</h2>
          {canEdit && <button type="button" className="btn small" onClick={() => setRecording({})}>Record other work</button>}
        </div>
        {!records.data ? <Loading /> : records.data.items.length === 0 ? <div className="empty">No work recorded yet.</div> : (
          <>
            <div className="table-wrap"><table>
              <thead><tr><th scope="col">Date</th><th scope="col">Work</th><th scope="col">Meters</th><th scope="col">By</th><th scope="col">Cost</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{records.data.items.map((r) => (
                <tr key={r.id}>
                  <td>{formatDate(r.performed_on)}</td>
                  <td>{r.schedule_name ?? r.kind_label}{r.needs_attention > 0 && <> <Chip tone="warn">{r.needs_attention} to follow up</Chip></>}{r.description && <div className="muted small">{r.description}</div>}</td>
                  <td>{[r.odometer_km !== null ? `${r.odometer_km.toLocaleString()} km` : null, r.engine_hours !== null ? `${r.engine_hours.toLocaleString()} h` : null].filter(Boolean).join(", ") || "—"}</td>
                  <td>{r.technician ?? r.recorded_by ?? "—"}</td>
                  <td>{r.cost !== null ? `$${Number(r.cost).toLocaleString("en-AU", { minimumFractionDigits: 2 })}` : <span className="muted">Not recorded</span>}</td>
                  <td style={{ textAlign: "right" }}>{records.data?.can_delete && <button type="button" className="btn small" aria-label={`Remove the record of ${formatDate(r.performed_on)}`} onClick={() => { if (window.confirm("Remove this record? A schedule linked to it will count from the service before it.")) void api(`/api/assets/${asset.id}/records/${r.id}`, { method: "DELETE" }).then(refresh).catch((e) => setError(errorMessage(e))); }}>Remove</button>}</td>
                </tr>))}</tbody>
            </table></div>
            {records.data.total_cost !== null && <p className="card-pad muted small" style={{ margin: 0 }}>Recorded cost ${Number(records.data.total_cost).toLocaleString("en-AU", { minimumFractionDigits: 2 })}{records.data.records_without_cost > 0 ? `. ${records.data.records_without_cost} record${records.data.records_without_cost === 1 ? " has" : "s have"} no cost entered, so this is not the full cost.` : "."}</p>}
          </>
        )}
      </section>
      {editing && <ScheduleModal asset={asset} schedule={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />}
      {recording && <RecordModal asset={asset} schedule={recording.schedule} onClose={() => setRecording(null)} onSaved={() => { setRecording(null); refresh(); }} />}
    </>
  );
}

function ScheduleModal({ asset, schedule, onClose, onSaved }: { asset: Asset; schedule?: Schedule; onClose: () => void; onSaved: () => void }) {
  const presets = useLoad(() => api<{ items: Preset[]; note: string }>("/api/fleet/service-schedule-presets"), []);
  const str = (x: number | null | undefined) => (x === null || x === undefined ? "" : String(x));
  const [v, setV] = useState({
    name: schedule?.name ?? "", interval_days: str(schedule?.interval_days), interval_km: str(schedule?.interval_km), interval_hours: str(schedule?.interval_hours),
    baseline_date: schedule?.baseline_date ?? "", baseline_km: str(schedule?.baseline_km), baseline_hours: str(schedule?.baseline_hours), notes: schedule?.notes ?? "", active: schedule?.active ?? true,
  });
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });
  const number = (x: string) => (x.trim() === "" ? null : Number(x));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const body = { name: v.name, interval_days: number(v.interval_days), interval_km: number(v.interval_km), interval_hours: number(v.interval_hours), baseline_date: v.baseline_date || null,
      baseline_km: number(v.baseline_km), baseline_hours: number(v.baseline_hours), notes: v.notes || null, active: v.active,
      warn_days: schedule?.warn_days ?? 14, warn_km: schedule?.warn_km ?? 500, warn_hours: schedule?.warn_hours ?? 25, assigned_worker_id: schedule?.assigned_worker_id ?? null };
    try {
      await api(`/api/assets/${asset.id}/schedules${schedule ? `/${schedule.id}` : ""}`, { method: schedule ? "PUT" : "POST", body });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Modal title={schedule ? `Edit ${schedule.name}` : "Add a service schedule"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {!schedule && presets.data && (
          <label className="field">Start from a common interval (optional)
            <select defaultValue="" onChange={(e) => { const p = presets.data?.items.find((x) => x.key === e.target.value); if (p) setV({ ...v, name: p.name, interval_days: str(p.interval_days), interval_km: str(p.interval_km), interval_hours: str(p.interval_hours) }); }}>
              <option value="">Choose…</option>
              {presets.data.items.map((p) => <option key={p.key} value={p.key}>{p.name}: {p.note}</option>)}
            </select>
            <span className="muted small">{presets.data.note}</span>
          </label>
        )}
        <label className="field">Name<input value={v.name} onChange={set("name")} required maxLength={160} /></label>
        <fieldset className="stack" style={{ gap: 8 }}>
          <legend>How often (fill in at least one; it is due on whichever comes first)</legend>
          <div className="grid-3">
            <label className="field">Every (km)<input type="number" min={1} step="any" value={v.interval_km} onChange={set("interval_km")} /></label>
            <label className="field">Every (hours)<input type="number" min={1} step="any" value={v.interval_hours} onChange={set("interval_hours")} /></label>
            <label className="field">Every (days)<input type="number" min={1} value={v.interval_days} onChange={set("interval_days")} /></label>
          </div>
        </fieldset>
        <fieldset className="stack" style={{ gap: 8 }}>
          <legend>When it was last done (the starting point until a service is recorded here)</legend>
          <div className="grid-3">
            <label className="field">Odometer then (km)<input type="number" min={0} step="any" value={v.baseline_km} onChange={set("baseline_km")} /></label>
            <label className="field">Hour meter then<input type="number" min={0} step="any" value={v.baseline_hours} onChange={set("baseline_hours")} /></label>
            <label className="field">Date<input type="date" value={v.baseline_date} onChange={set("baseline_date")} /></label>
          </div>
        </fieldset>
        <label className="field">Notes<textarea rows={2} value={v.notes} onChange={set("notes")} maxLength={4000} /></label>
        {schedule && <label className="check"><input type="checkbox" checked={v.active} onChange={(e) => setV({ ...v, active: e.target.checked })} /> Switched on</label>}
        {schedule && <p className="muted small">Changing a schedule does not change the records of services already done.</p>}
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary">Save</button>
        </div>
      </form>
    </Modal>
  );
}

function RecordModal({ asset, schedule, onClose, onSaved }: { asset: Asset; schedule?: Schedule; onClose: () => void; onSaved: () => void }) {
  const today = new Date().toLocaleDateString("en-CA");
  const [v, setV] = useState({ kind: "service", performed_on: today, odometer_km: "", engine_hours: "", technician: "", cost: "", description: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const number = (x: string) => (x.trim() === "" ? null : Number(x));
    try {
      await api(`/api/assets/${asset.id}/records`, { method: "POST", body: { kind: schedule ? "service" : v.kind, performed_on: v.performed_on, schedule_id: schedule?.id ?? null,
        odometer_km: number(v.odometer_km), engine_hours: number(v.engine_hours), technician: v.technician || null, cost: number(v.cost), description: v.description || null } });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  return (
    <Modal title={schedule ? `Record ${schedule.name} as done` : "Record work"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {!schedule && <label className="field">Kind of work<select value={v.kind} onChange={set("kind")}><option value="service">Service</option><option value="repair">Repair</option><option value="inspection">Inspection</option><option value="other">Other</option></select></label>}
        <label className="field">Date done<input type="date" value={v.performed_on} max={today} onChange={set("performed_on")} required /></label>
        <div className="grid-2">
          <label className="field">Odometer at the time (km){asset.odometer_km !== null && <span className="muted small"> now {asset.odometer_km.toLocaleString()}</span>}<input type="number" min={0} step="any" value={v.odometer_km} onChange={set("odometer_km")} required={Boolean(schedule?.interval_km)} /></label>
          <label className="field">Hour meter at the time{asset.engine_hours !== null && <span className="muted small"> now {asset.engine_hours.toLocaleString()}</span>}<input type="number" min={0} step="any" value={v.engine_hours} onChange={set("engine_hours")} required={Boolean(schedule?.interval_hours)} /></label>
          <label className="field">Done by<input value={v.technician} onChange={set("technician")} maxLength={160} placeholder="Person or workshop" /></label>
          <label className="field">Cost ($, optional)<input type="number" min={0} step="0.01" value={v.cost} onChange={set("cost")} /></label>
        </div>
        <label className="field">What was done<textarea rows={3} value={v.description} onChange={set("description")} maxLength={8000} /></label>
        {schedule && <p className="muted small">The next {schedule.name} will be counted from this one.</p>}
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>Save record</button>
        </div>
      </form>
    </Modal>
  );
}

interface DueRow { schedule_id: string; schedule: string; asset_id: string; asset: string; simulated: boolean; status: string; status_label: string; summary: string; assigned_worker: string | null }

/** Everything across the fleet that is overdue, due soon, or cannot be worked out. */
export function ServiceDue() {
  const [status, setStatus] = useState("");
  const inbox = useLoad(() => api<{ items: DueRow[]; total: number; can_edit: boolean }>(`/api/assets/service/inbox${status ? `?status=${status}` : ""}`), [status]);
  const summary = useLoad(() => api<Record<string, number>>("/api/assets/service/summary"), []);
  return (
    <>
      <p className="small"><Link to="/app/fleet">Fleet and equipment</Link> / Service due</p>
      <div className="page-head">
        <div>
          <h1>Service due</h1>
          <p className="muted">{summary.data ? `${summary.data.overdue} overdue, ${summary.data.due_soon} due soon, ${summary.data.unknown} that cannot be worked out, ${summary.data.ok} up to date.` : "Services that need attention across the fleet."}</p>
        </div>
        {inbox.data?.can_edit && <ReminderButton url="/api/assets/service/scan-reminders" what="services" label="Service reminders" />}
      </div>
      <section className="card">
        <div className="toolbar">
          <label className="field">Show
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Everything needing attention</option><option value="overdue">Overdue</option><option value="due_soon">Due soon</option><option value="unknown">Cannot be worked out</option>
            </select>
          </label>
        </div>
        {inbox.error ? <div className="card-pad"><LoadError message={inbox.error} onRetry={inbox.reload} /></div> : !inbox.data ? <Loading /> : inbox.data.items.length === 0 ? (
          <div className="empty">Nothing needs attention{status ? " in this view" : ""}.</div>
        ) : (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Asset</th><th scope="col">Service</th><th scope="col">Status</th><th scope="col">Assigned to</th></tr></thead>
            <tbody>{inbox.data.items.map((r) => (
              <tr key={r.schedule_id}>
                <td><Link to={`/app/fleet/${r.asset_id}`}>{r.asset}</Link>{r.simulated && <> <Chip tone="warn">Simulator, not real</Chip></>}</td>
                <td>{r.schedule}</td>
                <td><ServiceChip status={r.status} label={r.status_label} /><div className="small">{r.summary}</div></td>
                <td>{r.assigned_worker ?? <span className="muted">No one</span>}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
    </>
  );
}
