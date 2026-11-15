import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatDate, formatDateTime, type FormTemplate, type Job, type Site, type Worker } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";
import { startSubmission } from "./Forms";

type Msg = { tone: "ok" | "bad"; text: string } | null;

export function JobStatus({ job }: { job: Job }) {
  const tone = job.status === "issued" ? "warn" : job.status === "accepted" ? "info" : job.status === "completed" ? "ok" : job.status === "declined" ? "bad" : "dashed";
  return <Chip tone={tone} icon={tone !== "dashed"}>{job.status_label}</Chip>;
}

const DELIVERY: Record<string, string> = { held: "Text not sent (SMS not connected)", none: "No mobile number recorded", queued: "Text queued", sent: "Text sent", failed: "Text failed" };
const SKIP: Record<string, string> = { already_has_job: "already has a job that day", inactive: "is inactive or archived", not_found: "was not found" };

function localToday(timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone }).format(new Date());
}

/** Office: issue and follow the day's jobs. */
export function DailyJobs() {
  const { me } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const [day, setDay] = useState(() => localToday(tz));
  const jobs = useLoad(() => api<{ date: string; items: Job[]; counts: Record<string, number> }>(`/api/daily-jobs?date=${day}`), [day]);
  const [issuing, setIssuing] = useState(false);
  const [message, setMessage] = useState<Msg>(null);
  const past = day < localToday(tz);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Daily jobs</h1>
          <p className="muted">Tell workers where they are going. Each worker has one job a day; they accept it on their phone under My work.</p>
        </div>
        {!past && <button type="button" className="btn primary" onClick={() => setIssuing(true)}>Issue a job</button>}
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <section className="card">
        <div className="toolbar">
          <label className="field">Day<input type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} /></label>
          {day !== localToday(tz) && <button type="button" className="btn" onClick={() => setDay(localToday(tz))}>Today</button>}
          <span className="spacer" />
          {jobs.data && (
            <span className="chips" style={{ alignSelf: "center" }}>
              <Chip tone="warn">{jobs.data.counts.issued} waiting</Chip>
              <Chip tone="info">{jobs.data.counts.accepted} accepted</Chip>
              <Chip tone="ok">{jobs.data.counts.completed} done</Chip>
              {jobs.data.counts.declined > 0 && <Chip tone="bad">{jobs.data.counts.declined} declined</Chip>}
            </span>
          )}
        </div>
        {jobs.error ? <div className="card-pad"><LoadError message={jobs.error} onRetry={jobs.reload} /></div> : !jobs.data ? <Loading /> : jobs.data.items.length === 0 ? (
          <div className="empty">No jobs issued for {formatDate(day)}.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Worker</th><th scope="col">Job</th><th scope="col">Start</th><th scope="col">Status</th><th scope="col">Told by text?</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {jobs.data.items.map((j) => (
                  <tr key={j.id}>
                    <td>{j.worker_name}</td>
                    <td>
                      {j.site_name}
                      <div className="small muted">{[j.address, j.customer, j.truck].filter(Boolean).join(" · ")}</div>
                      {j.notes && <div className="small">{j.notes}</div>}
                      {j.forms && j.forms.length > 0 && <div className="small muted">Forms: {j.forms.map((f) => f.name).join(", ")}</div>}
                    </td>
                    <td className="num">{j.start_time ?? "—"}</td>
                    <td><JobStatus job={j} />{j.decline_reason && <div className="small">Reason: {j.decline_reason}</div>}</td>
                    <td className="small">{DELIVERY[j.delivery] ?? j.delivery}</td>
                    <td className="right">
                      {(j.status === "issued" || j.status === "accepted") && (
                        <button type="button" className="btn small" onClick={async () => {
                          if (!window.confirm(`Cancel the job for ${j.worker_name}?`)) return;
                          try { await api(`/api/daily-jobs/${j.id}/cancel`, { method: "POST" }); setMessage({ tone: "ok", text: "Job cancelled." }); jobs.reload(); } catch (e) { setMessage({ tone: "bad", text: errorMessage(e) }); }
                        }}>Cancel job</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {issuing && <IssueJob day={day} onClose={() => setIssuing(false)} onIssued={(text) => { setIssuing(false); setMessage({ tone: "ok", text }); jobs.reload(); }} />}
    </>
  );
}

interface BulkResult {
  trial_run: boolean;
  created: { worker_name: string; replaces_existing?: boolean }[];
  skipped: { name: string | null; reason: string }[];
}

function IssueJob({ day, onClose, onIssued }: { day: string; onClose: () => void; onIssued: (text: string) => void }) {
  const workers = useLoad(() => api<{ items: Worker[] }>("/api/workers?limit=200"), []);
  const sites = useLoad(() => api<{ items: Site[] }>("/api/sites?limit=200"), []);
  const forms = useLoad(() => api<{ templates: FormTemplate[] }>("/api/forms/templates").catch(() => ({ templates: [] })), []);
  const [chosen, setChosen] = useState<string[]>([]);
  const [siteId, setSiteId] = useState("");
  const [siteName, setSiteName] = useState("");
  const [start, setStart] = useState("");
  const [truck, setTruck] = useState("");
  const [customer, setCustomer] = useState("");
  const [notes, setNotes] = useState("");
  const [formIds, setFormIds] = useState<string[]>([]);
  const [override, setOverride] = useState(false);
  const [preview, setPreview] = useState<BulkResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const published = (forms.data?.templates ?? []).filter((t) => t.status === "published");

  async function send(trial: boolean) {
    if (chosen.length === 0) { setError("Tick at least one worker."); return; }
    if (!siteId && !siteName.trim()) { setError("Choose a site, or type where the job is."); return; }
    setBusy(true);
    setError(null);
    try {
      const result = await api<BulkResult>("/api/daily-jobs/bulk-create", {
        method: "POST",
        body: { worker_ids: chosen, date: day, site_id: siteId || null, site_name: siteId ? null : siteName, start_time: start || null, truck: truck || null,
          customer: customer || null, notes: notes || null, form_template_ids: formIds, override, trial_run: trial },
      });
      if (trial) setPreview(result);
      else onIssued(`${result.created.length} job${result.created.length === 1 ? "" : "s"} issued${result.skipped.length ? `; ${result.skipped.length} skipped` : ""}.`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Issue a job for ${formatDate(day)}`} onClose={onClose}>
      <form className="form" onSubmit={(e: FormEvent) => { e.preventDefault(); void send(true); }}>
        {error && <Notice tone="bad">{error}</Notice>}
        <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend style={{ fontWeight: 600, padding: 0 }}>Who</legend>
          {!workers.data ? <Loading /> : workers.data.items.length === 0 ? <Notice tone="info">Add workers first, under Workers.</Notice> : (
            <div className="grid-2" style={{ maxHeight: 180, overflowY: "auto" }}>
              {workers.data.items.map((w) => (
                <label className="check" key={w.id}>
                  <input type="checkbox" checked={chosen.includes(w.id)} onChange={(e) => { setPreview(null); setChosen(e.target.checked ? [...chosen, w.id] : chosen.filter((x) => x !== w.id)); }} /> {w.name}
                </label>
              ))}
            </div>
          )}
        </fieldset>
        <div className="grid-2">
          <label className="field">
            Site
            <select value={siteId} onChange={(e) => { setSiteId(e.target.value); setPreview(null); }}>
              <option value="">Not in the list (type it)</option>
              {(sites.data?.items ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          {!siteId && <label className="field">Where is the job?<input value={siteName} onChange={(e) => setSiteName(e.target.value)} /></label>}
          <label className="field">Start time (optional)<input type="time" value={start} onChange={(e) => setStart(e.target.value)} /></label>
          <label className="field">Truck or plant (optional)<input value={truck} onChange={(e) => setTruck(e.target.value)} /></label>
          <label className="field">Customer (optional)<input value={customer} onChange={(e) => setCustomer(e.target.value)} /></label>
        </div>
        <label className="field">Instructions (optional)<textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
        {published.length > 0 && (
          <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend style={{ fontWeight: 600, padding: 0 }}>Forms to fill in for this job (optional)</legend>
            <div className="grid-2">
              {published.map((t) => (
                <label className="check" key={t.id}><input type="checkbox" checked={formIds.includes(t.id)} onChange={(e) => setFormIds(e.target.checked ? [...formIds, t.id] : formIds.filter((x) => x !== t.id))} /> {t.name}</label>
              ))}
            </div>
          </fieldset>
        )}
        <label className="check"><input type="checkbox" checked={override} onChange={(e) => { setOverride(e.target.checked); setPreview(null); }} /> Replace their current job if they already have one that day</label>
        {preview && (
          <Notice tone={preview.created.length ? "info" : "warn"}>
            <strong>Check before issuing.</strong> {preview.created.length === 0 ? "Nobody would get this job." : `Will go to: ${preview.created.map((c) => c.worker_name + (c.replaces_existing ? " (replaces their current job)" : "")).join(", ")}.`}
            {preview.skipped.length > 0 && <> Skipped: {preview.skipped.map((s) => `${s.name ?? "A worker"} ${SKIP[s.reason] ?? s.reason}`).join("; ")}.</>}
          </Notice>
        )}
        <p className="small muted">A text message is prepared for each worker with a mobile number, but nothing is sent until SMS is connected. They see the job in the portal under My work.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          {preview && preview.created.length > 0
            ? <button type="button" className="btn primary" disabled={busy} onClick={() => void send(false)}>{busy ? "Issuing…" : `Issue ${preview.created.length} job${preview.created.length === 1 ? "" : "s"}`}</button>
            : <button type="submit" className="btn primary" disabled={busy}>{busy ? "Checking…" : "Check"}</button>}
        </div>
      </form>
    </Modal>
  );
}

/** Worker: today's job, with accept, decline and done. */
export function MyWork() {
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const mine = useLoad(() => api<{ upcoming: Job[]; recent: Job[]; status: string }>("/api/daily-jobs/mine"), []);
  const [message, setMessage] = useState<Msg>(null);
  const [declining, setDeclining] = useState<Job | null>(null);
  const today = localToday(tz);

  async function act(job: Job, action: "accept" | "complete", done: string) {
    setMessage(null);
    try {
      await api(`/api/daily-jobs/${job.id}/${action}`, { method: "POST" });
      setMessage({ tone: "ok", text: done });
      mine.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  if (mine.error) return <LoadError message={mine.error} onRetry={mine.reload} />;
  if (!mine.data) return <Loading />;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>My work</h1>
          <p className="muted">The jobs the office has given you.</p>
        </div>
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {mine.data.status === "no_worker_record" ? (
        <Notice tone="info">Your login is not linked to a worker record yet, so jobs cannot be issued to you. Ask the office to link it.</Notice>
      ) : mine.data.upcoming.length === 0 ? (
        <section className="card"><div className="empty">You have no job waiting. When the office issues one it appears here.</div></section>
      ) : (
        <div className="stack">
          {mine.data.upcoming.map((job) => (
            <section key={job.id} className="job-card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{job.date === today ? "Today" : formatDate(job.date)}{job.start_time ? ` · start ${job.start_time}` : ""}</strong>
                <JobStatus job={job} />
              </div>
              <h2 style={{ margin: 0 }}>{job.site_name}</h2>
              <dl className="kv">
                {job.address && <><dt>Address</dt><dd>{job.address}</dd></>}
                {job.customer && <><dt>Customer</dt><dd>{job.customer}</dd></>}
                {job.truck && <><dt>Truck or plant</dt><dd>{job.truck}</dd></>}
                {job.staff.length > 0 && <><dt>Crew</dt><dd>{job.staff.join(", ")}</dd></>}
                {job.notes && <><dt>Instructions</dt><dd style={{ whiteSpace: "pre-wrap" }}>{job.notes}</dd></>}
                <dt>Issued by</dt><dd>{job.issued_by ?? "The office"} · {formatDateTime(job.issued_at, tz)}</dd>
              </dl>
              {job.forms && job.forms.length > 0 && can("forms", "edit") && (
                <div className="stack" style={{ gap: 6 }}>
                  <strong>Forms for this job</strong>
                  <div className="row">
                    {job.forms.map((f) => (
                      <button key={f.id} type="button" className="btn" disabled={!f.available} onClick={async () => {
                        try { navigate(`/app/forms/submissions/${await startSubmission(f.id)}`); } catch (e) { setMessage({ tone: "bad", text: errorMessage(e) }); }
                      }}>{f.name}</button>
                    ))}
                  </div>
                </div>
              )}
              <div className="row">
                {job.status === "issued" && <button type="button" className="btn primary big-btn" onClick={() => void act(job, "accept", "Job accepted.")}>Accept job</button>}
                {job.status === "accepted" && <button type="button" className="btn primary big-btn" onClick={() => void act(job, "complete", "Job marked as done.")}>Mark as done</button>}
                <button type="button" className="btn" onClick={() => setDeclining(job)}>{job.status === "accepted" ? "I can no longer do this job" : "I cannot do this job"}</button>
              </div>
            </section>
          ))}
        </div>
      )}
      {mine.data.recent.length > 0 && (
        <section className="card" style={{ marginTop: 16 }}>
          <div className="card-head"><h2>Earlier jobs</h2></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Day</th><th scope="col">Job</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {mine.data.recent.map((j) => (
                  <tr key={j.id}><td className="num">{formatDate(j.date)}</td><td>{j.site_name}</td><td><JobStatus job={j} /></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {declining && <Decline job={declining} onClose={() => setDeclining(null)} onDone={() => { setDeclining(null); setMessage({ tone: "ok", text: "Recorded. The office will see this on the Daily jobs page." }); mine.reload(); }} />}
    </>
  );
}

function Decline({ job, onClose, onDone }: { job: Job; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api(`/api/daily-jobs/${job.id}/decline`, { method: "POST", body: { reason } });
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Modal title={`Cannot do: ${job.site_name}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">Why not?<input required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <p className="small muted">The office sees this on the Daily jobs page. Call them as well if it is urgent: nothing is sent automatically.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Back</button>
          <button type="submit" className="btn primary">Tell the office</button>
        </div>
      </form>
    </Modal>
  );
}
