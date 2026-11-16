import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, downloadFile, formatDate, formatDateTime, openFile } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface Result {
  gross: number; payg: number | null; net: number | null; super: number | null; qualifying_earnings: number | null; issues: string[]; review_ready: boolean;
  annual_closing: number; personal_closing: number; applied_rates: Record<string, number>;
}
interface Row {
  worker_id: string; name: string; has_profile?: boolean; entry: Record<string, number | boolean | string | null>; manual?: Record<string, number>; adjust?: Record<string, unknown>;
  hours_reviewed?: boolean; super_reviewed?: boolean; result: Result | null; error?: string | null; sources?: { timesheets?: unknown[]; leave?: unknown[]; unpaid_leave?: unknown[] };
}
interface Totals { gross: string; payg: string | null; net: string | null; super: string | null; rows: number; rows_with_issues: number }
interface RunBrief { id: string; week_start: string; week_end: string; label: string; status: string; status_label: string; payday: string | null; revision: number; totals: Totals | null; payslips_released: boolean; evidence_kinds: string[] }
interface Run extends RunBrief {
  can_edit: boolean; statement: string; rows: Row[]; ready: boolean; stale: string[]; stale_note?: string | null; hash?: string; employer?: { name: string; abn: string };
  history: { event: string; by: string | null; at: string; reason?: string }[]; evidence: { id: number; kind: string; reference: string; completed_on: string; note: string | null; by: string | null; corrects: number | null; correction_reason: string | null }[];
  evidence_note: string; corrections: { leave_id: string; worker: string; hours: string; text: string }[]; totals: Totals;
}
interface Profile {
  worker_id: string; name: string; position: string | null; has_profile: boolean; revision: number; hourly_rate: number | null; annual_salary: number | null; employment_type: string | null;
  ordinary_weekly_hours: number | null; conditions_reviewed: boolean | null; tax_mode: string | null; tax_declaration_reviewed: boolean | null; annual_weeks: number | null; personal_weeks: number | null;
  leave_loading_percent: number | null; casual_rates: { base_rate: number; loading_percent: number } | null; opening_annual: number | null; opening_personal: number | null; base_rate: number | null;
  bank: { is_set: boolean; hint: string | null }; can_edit: boolean;
}

const money = (v: number | string | null | undefined): ReactNode => (v === null || v === undefined ? <span className="muted">Not worked out</span> : `$${Number(v).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const HOURS: [string, string][] = [["ordinary", "ordinary"], ["ot1", "overtime 1"], ["ot2", "overtime 2"], ["saturday", "Saturday"], ["sunday", "Sunday"], ["night", "night"], ["holiday_work", "public holiday worked"],
  ["annual", "annual leave"], ["personal", "personal leave"], ["public_holiday", "public holiday"]];
const hoursText = (entry: Row["entry"]) => HOURS.filter(([k]) => Number(entry[k] ?? 0) > 0).map(([k, label]) => `${Number(entry[k])} ${label}`).join(", ") || "No hours";
const EVIDENCE: Record<string, string> = { payment: "Wages paid from the bank", stp: "Reported to the Tax Office (STP)", super: "Super paid to the funds" };

export function TestBanner() {
  return <Notice tone="warn"><strong>Test workbench.</strong> It works pay out and keeps a record. The portal does not pay anyone, lodge anything with the Tax Office or a super fund, or send payslips. The tax table and pay rules have not been checked against the law.</Notice>;
}

// ---------- runs ----------

export function PayRuns() {
  const navigate = useNavigate();
  const list = useLoad(() => api<{ items: RunBrief[]; can_edit: boolean }>("/api/payroll/runs"), []);
  const [week, setWeek] = useState("");
  const [extra, setExtra] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function start(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const run = await api<RunBrief>("/api/payroll/runs", { method: "POST", body: { week_start: week, extra } });
      navigate(`/app/pay/runs/${run.id}`);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  if (list.error) return <LoadError message={list.error} onRetry={list.reload} />;
  if (!list.data) return <Loading />;
  return (
    <>
      <div className="page-head"><div><h1>Pay runs</h1><p className="muted">One run for each pay week, built from approved timesheets and approved leave.</p></div></div>
      <TestBanner />
      {list.data.can_edit && (
        <form className="card card-pad row" style={{ gap: 12, alignItems: "flex-end", flexWrap: "wrap" }} onSubmit={start}>
          <label className="field">First day of the pay week<input type="date" value={week} onChange={(e) => setWeek(e.target.value)} required /></label>
          <label className="check"><input type="checkbox" checked={extra} onChange={(e) => setExtra(e.target.checked)} /> An extra run for a week that already has one</label>
          <button type="submit" className="btn primary">Start pay run</button>
          {error && <Notice tone="bad">{error}</Notice>}
        </form>
      )}
      <section className="card">
        {list.data.items.length === 0 ? <div className="empty">No pay runs yet.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Week</th><th scope="col">Run</th><th scope="col">Status</th><th scope="col">Gross</th><th scope="col">Net</th><th scope="col">Recorded outside the portal</th></tr></thead>
            <tbody>{list.data.items.map((r) => (
              <tr key={r.id}>
                <td><Link to={`/app/pay/runs/${r.id}`}>{formatDate(r.week_start)} to {formatDate(r.week_end)}</Link></td>
                <td>{r.label}</td><td><Chip tone={r.status === "finalised" ? "ok" : "warn"}>{r.status_label}</Chip></td>
                <td>{r.totals ? money(r.totals.gross) : "—"}</td><td>{r.totals ? money(r.totals.net) : "—"}</td>
                <td>{r.evidence_kinds.length ? r.evidence_kinds.map((k) => EVIDENCE[k]).join("; ") : <span className="muted">Nothing recorded</span>}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
    </>
  );
}

function ManualHours({ row, onClose, onSave }: { row: Row; onClose: () => void; onSave: (manual: Record<string, number>) => void }) {
  const [v, setV] = useState<Record<string, string>>(Object.fromEntries(HOURS.map(([k]) => [k, row.manual?.[k] ? String(row.manual[k]) : ""])));
  return (
    <Modal title={`Hours entered by hand: ${row.name}`} onClose={onClose}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); onSave(Object.fromEntries(Object.entries(v).filter(([, x]) => x !== "" && Number(x) > 0).map(([k, x]) => [k, Number(x)]))); }}>
        <p className="muted small">These are added to the hours that came from timesheets and leave. Use them for someone with no timesheet (a salaried person), or for a public holiday not worked.</p>
        <div className="grid-3">{HOURS.map(([k, label]) => <label key={k} className="field">{label.charAt(0).toUpperCase() + label.slice(1)}<input type="number" min={0} max={168} step="0.01" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></label>)}</div>
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary">Use these hours</button></div>
      </form>
    </Modal>
  );
}

const ADJUST: [string, string][] = [["taxable_allowances", "Taxable allowances ($)"], ["reimbursements", "Reimbursements, not taxed ($)"], ["deductions", "Deductions after tax ($)"], ["extra_withholding", "Extra tax to withhold ($)"],
  ["manual_payg", "Tax worked out by hand ($)"], ["qualifying_earnings_override", "Earnings super is paid on, decided by hand ($)"], ["lafha", "Living-away-from-home allowance ($)"]];

function Adjustments({ row, onClose, onSave }: { row: Row; onClose: () => void; onSave: (adjust: Record<string, unknown>) => void }) {
  const a = row.adjust ?? {};
  const [v, setV] = useState<Record<string, string>>({ ...Object.fromEntries(ADJUST.map(([k]) => [k, a[k] === undefined || a[k] === null ? "" : String(a[k])])), payg_reference: String(a.payg_reference ?? ""),
    lafha_taxable: a.lafha_taxable === undefined ? "" : String(a.lafha_taxable), lafha_superable: a.lafha_superable === undefined ? "" : String(a.lafha_superable) });
  const set = (key: string) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });
  function submit(event: FormEvent) {
    event.preventDefault();
    const out: Record<string, unknown> = Object.fromEntries(ADJUST.filter(([k]) => v[k] !== "").map(([k]) => [k, Number(v[k])]));
    if (v.payg_reference.trim()) out.payg_reference = v.payg_reference.trim();
    if (v.lafha_taxable) out.lafha_taxable = v.lafha_taxable === "true";
    if (v.lafha_superable) out.lafha_superable = v.lafha_superable === "true";
    onSave(out);
  }
  return (
    <Modal title={`Adjustments: ${row.name}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p className="muted small">Leave a box empty when it does not apply. Empty is not the same as zero: an empty "by hand" figure means the workbench works it out, or says why it cannot.</p>
        <div className="grid-2">{ADJUST.map(([k, label]) => <label key={k} className="field">{label}<input type="number" min={0} step="0.01" value={v[k]} onChange={set(k)} /></label>)}
          <label className="field">How the hand-worked tax was arrived at<input value={v.payg_reference} onChange={set("payg_reference")} maxLength={200} /></label>
          <label className="field">Is the living-away allowance taxed?<select value={v.lafha_taxable} onChange={set("lafha_taxable")}><option value="">Not decided</option><option value="true">Yes</option><option value="false">No</option></select></label>
          <label className="field">Is super paid on it?<select value={v.lafha_superable} onChange={set("lafha_superable")}><option value="">Not decided</option><option value="true">Yes</option><option value="false">No</option></select></label>
        </div>
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary">Use these adjustments</button></div>
      </form>
    </Modal>
  );
}

export function PayRunPage() {
  const { runId = "" } = useParams();
  const { me } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const load = useLoad(() => api<Run>(`/api/payroll/runs/${runId}`), [runId]);
  const workers = useLoad(() => api<{ items: Profile[] }>("/api/payroll/profiles"), []);
  const [draft, setDraft] = useState<Record<string, { manual: Record<string, number>; adjust: Record<string, unknown>; hours_reviewed: boolean; super_reviewed: boolean }> | null>(null);
  const [adjustFor, setAdjustFor] = useState<Row | null>(null);
  const [payday, setPayday] = useState<string | null>(null);
  const [manualFor, setManualFor] = useState<Row | null>(null);
  const [adding, setAdding] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "bad" | "info"; text: string } | null>(null);
  const [asking, setAsking] = useState<"reopen" | "evidence" | null>(null);
  const run = load.data;
  if (load.error) return <LoadError message={load.error} onRetry={load.reload} />;
  if (!run) return <Loading />;
  const isDraft = run.status === "draft";
  const rowState = (r: Row) => draft?.[r.worker_id] ?? { manual: r.manual ?? {}, adjust: r.adjust ?? {}, hours_reviewed: Boolean(r.hours_reviewed), super_reviewed: Boolean(r.super_reviewed) };
  const dirty = draft !== null || payday !== null;
  const change = (r: Row, patch: Partial<ReturnType<typeof rowState>>) => setDraft({ ...(draft ?? {}), [r.worker_id]: { ...rowState(r), ...patch } });

  async function act(fn: () => Promise<unknown>, done: string) {
    setMsg(null);
    try { await fn(); setDraft(null); setPayday(null); setMsg({ tone: "ok", text: done }); load.reload(); } catch (e) { setMsg({ tone: "bad", text: errorMessage(e) }); }
  }
  const save = (extraRows: Record<string, unknown> = {}) => act(() => api(`/api/payroll/runs/${run.id}`, { method: "PUT", body: { expected_revision: run.revision, payday: payday ?? run.payday,
    rows: { ...Object.fromEntries(run.rows.map((r) => [r.worker_id, rowState(r)])), ...extraRows } } }), "Saved.");
  const notOnRun = (workers.data?.items ?? []).filter((w) => !run.rows.some((r) => r.worker_id === w.worker_id));

  return (
    <>
      <p className="small"><Link to="/app/pay/runs">Pay runs</Link> / {formatDate(run.week_start)}</p>
      <div className="page-head">
        <div><h1>{run.label}: {formatDate(run.week_start)} to {formatDate(run.week_end)}</h1><p className="muted"><Chip tone={isDraft ? "warn" : "ok"}>{run.status_label}</Chip> {run.payday ? `Payday ${formatDate(run.payday)}` : "No payday set"}{run.hash ? ` · reference ${run.hash.slice(0, 16)}` : ""}</p></div>
        {run.can_edit && isDraft && (
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            {run.label === "Weekly run" && <button type="button" className="btn" disabled={dirty} title={dirty ? "Save first" : undefined} onClick={() => void act(() => api(`/api/payroll/runs/${run.id}/import`, { method: "POST", body: { expected_revision: run.revision } }), "Approved hours and leave brought in. Check any row whose hours changed.")}>Bring in approved hours again</button>}
            <button type="button" className="btn" disabled={!dirty} onClick={() => void save()}>Save</button>
            <button type="button" className="btn primary" disabled={dirty || !run.ready} title={dirty ? "Save first" : !run.ready ? "Resolve every review item first" : undefined} onClick={() => void act(() => api(`/api/payroll/runs/${run.id}/finalise`, { method: "POST", body: { expected_revision: run.revision } }), "Finalised. The figures are now fixed.")}>Finalise</button>
          </div>
        )}
      </div>
      <TestBanner />
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {run.stale_note && <Notice tone="warn">{run.stale_note}</Notice>}
      {run.corrections.map((c) => <Notice key={c.leave_id} tone="bad">{c.worker}: {c.hours} hours of leave. {c.text}</Notice>)}

      <section className="card">
        {isDraft && run.can_edit && (
          <div className="toolbar">
            <label className="field">Payday<input type="date" value={payday ?? run.payday ?? ""} min={run.week_start} onChange={(e) => setPayday(e.target.value)} /></label>
            <label className="field">Add someone with no timesheet
              <select value={adding} onChange={(e) => setAdding(e.target.value)}><option value="">Choose…</option>{notOnRun.map((w) => <option key={w.worker_id} value={w.worker_id}>{w.name}</option>)}</select>
            </label>
            <button type="button" className="btn" disabled={!adding} onClick={() => { void save({ [adding]: { manual: {}, adjust: {}, hours_reviewed: false, super_reviewed: false } }); setAdding(""); }}>Add to this run</button>
          </div>
        )}
        {run.rows.length === 0 ? <div className="empty">Nobody is on this run yet. {run.label === "Weekly run" ? "Approve timesheets for the week, then bring the hours in." : "Add the people this extra run is for."}</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Worker</th><th scope="col">Hours</th><th scope="col">Gross</th><th scope="col">Tax</th><th scope="col">Net</th><th scope="col">Super</th><th scope="col">{isDraft ? "To check" : "Payslip"}</th></tr></thead>
            <tbody>{run.rows.map((r) => {
              const s = rowState(r);
              return (
                <tr key={r.worker_id}>
                  <td>{r.name}{isDraft && r.has_profile === false && <div><Chip tone="bad">No pay settings</Chip></div>}{run.stale.includes(r.worker_id) && <div><Chip tone="warn">Hours changed</Chip></div>}</td>
                  <td>{hoursText(r.entry)}{Object.keys(s.manual).length > 0 && <div className="muted small">includes hours entered by hand</div>}
                    {isDraft && run.can_edit && <div className="row" style={{ gap: 10 }}><button type="button" className="link-btn small" aria-label={`Enter hours by hand for ${r.name}`} onClick={() => setManualFor(r)}>Enter hours by hand</button>
                      <button type="button" className="link-btn small" aria-label={`Adjustments for ${r.name}`} onClick={() => setAdjustFor(r)}>Adjustments{Object.keys(s.adjust).length ? ` (${Object.keys(s.adjust).length})` : ""}</button></div>}</td>
                  <td>{r.result ? money(r.result.gross) : "—"}</td><td>{r.result ? money(r.result.payg) : "—"}</td><td>{r.result ? money(r.result.net) : "—"}</td><td>{r.result ? money(r.result.super) : "—"}</td>
                  <td>
                    {isDraft ? (
                      <div className="stack" style={{ gap: 4 }}>
                        {r.error && <Notice tone="bad">{r.error}</Notice>}
                        {(r.result?.issues ?? []).filter((i) => !i.startsWith("Confirm the hours") && !i.startsWith("Confirm super eligibility has")).map((i) => <span key={i} className="small" style={{ color: "var(--bad)" }}>{i}</span>)}
                        {run.can_edit && <label className="check small"><input type="checkbox" checked={s.hours_reviewed} onChange={(e) => change(r, { hours_reviewed: e.target.checked })} aria-label={`Hours checked for ${r.name}`} /> Hours checked</label>}
                        {run.can_edit && <label className="check small"><input type="checkbox" checked={s.super_reviewed} onChange={(e) => change(r, { super_reviewed: e.target.checked })} aria-label={`Super checked for ${r.name}`} /> Super checked</label>}
                        {r.result?.review_ready && <Chip tone="ok">Ready</Chip>}
                      </div>
                    ) : <button type="button" className="btn small" aria-label={`Payslip for ${r.name}`} onClick={() => void openFile(`/api/payroll/runs/${run.id}/payslips/${r.worker_id}/pdf`, "payslip.pdf", false).catch((e) => setMsg({ tone: "bad", text: errorMessage(e) }))}>Open payslip</button>}
                  </td>
                </tr>
              );
            })}
              <tr className="total-row"><th scope="row">Total</th><td>{run.totals.rows} {run.totals.rows === 1 ? "person" : "people"}</td><td>{money(run.totals.gross)}</td><td>{money(run.totals.payg)}</td><td>{money(run.totals.net)}</td><td>{money(run.totals.super)}</td>
                <td>{isDraft && run.totals.rows_with_issues > 0 ? `${run.totals.rows_with_issues} not ready` : ""}</td></tr>
            </tbody>
          </table></div>
        )}
      </section>

      {!isDraft && (
        <>
          <section className="card card-pad stack" aria-label="After finalising">
            <h2 style={{ margin: 0 }}>After finalising</h2>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="btn" onClick={() => void downloadFile(`/api/payroll/runs/${run.id}/report.csv`, "pay-register.csv").catch((e) => setMsg({ tone: "bad", text: errorMessage(e) }))}>Download the pay register</button>
              {run.can_edit && <button type="button" className="btn" onClick={() => void act(() => api(`/api/payroll/runs/${run.id}/payslips/release`, { method: "POST", body: { release: !run.payslips_released } }), run.payslips_released ? "Payslips hidden from workers." : "Each worker can now see their own payslip in the portal. No email or text was sent.")}>{run.payslips_released ? "Hide payslips from workers" : "Let workers see their payslips"}</button>}
              {run.can_edit && <button type="button" className="btn" onClick={() => setAsking("evidence")}>Record something done outside the portal</button>}
              {run.can_edit && !run.evidence.some((e) => e.kind === "payment") && <button type="button" className="btn" onClick={() => setAsking("reopen")}>Reopen</button>}
            </div>
            <p className="muted small" style={{ margin: 0 }}>Payslips are {run.payslips_released ? "visible to each worker under My payslips" : "not visible to workers yet"}. Paying wages, reporting to the Tax Office and paying super are done outside the portal; record them here so there is a trail.{run.evidence.some((e) => e.kind === "payment") ? " A payment is recorded, so this run can no longer be reopened; make any correction in an extra run." : ""}</p>
            {run.evidence.length > 0 && (
              <div className="table-wrap"><table>
                <thead><tr><th scope="col">What</th><th scope="col">Reference</th><th scope="col">Date</th><th scope="col">Recorded by</th></tr></thead>
                <tbody>{run.evidence.map((e) => <tr key={e.id}><td>{EVIDENCE[e.kind]}{e.corrects && <div className="muted small">Corrects entry {e.corrects}: {e.correction_reason}</div>}</td><td>{e.reference}{e.note && <div className="muted small">{e.note}</div>}</td><td>{formatDate(e.completed_on)}</td><td>{e.by}</td></tr>)}</tbody>
              </table></div>
            )}
            {run.evidence.length > 0 && <p className="muted small" style={{ margin: 0 }}>{run.evidence_note}</p>}
          </section>
        </>
      )}
      {run.history.length > 0 && <p className="muted small">History: {run.history.map((h) => `${h.event} by ${h.by ?? "someone"} ${formatDateTime(h.at, tz)}${h.reason ? ` (${h.reason})` : ""}`).join("; ")}</p>}
      {manualFor && <ManualHours row={{ ...manualFor, manual: rowState(manualFor).manual }} onClose={() => setManualFor(null)} onSave={(manual) => { change(manualFor, { manual }); setManualFor(null); }} />}
      {adjustFor && <Adjustments row={{ ...adjustFor, adjust: rowState(adjustFor).adjust }} onClose={() => setAdjustFor(null)} onSave={(adjust) => { change(adjustFor, { adjust }); setAdjustFor(null); }} />}
      {asking === "reopen" && <ReasonModal title="Reopen this run" label="Why? The finalised figures are released and must be finalised again." onClose={() => setAsking(null)}
        onSubmit={(reason) => act(() => api(`/api/payroll/runs/${run.id}/reopen`, { method: "POST", body: { expected_revision: run.revision, reason } }), "Reopened.").then(() => setAsking(null))} />}
      {asking === "evidence" && <EvidenceModal run={run} onClose={() => setAsking(null)} onDone={() => { setAsking(null); setMsg({ tone: "ok", text: "Recorded." }); load.reload(); }} />}
    </>
  );
}

function ReasonModal({ title, label, onClose, onSubmit }: { title: string; label: string; onClose: () => void; onSubmit: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  return (
    <Modal title={title} onClose={onClose}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); onSubmit(reason); }}>
        <label className="field">{label}<input value={reason} onChange={(e) => setReason(e.target.value)} required minLength={5} maxLength={600} autoFocus /></label>
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary">{title}</button></div>
      </form>
    </Modal>
  );
}

function EvidenceModal({ run, onClose, onDone }: { run: Run; onClose: () => void; onDone: () => void }) {
  const [kind, setKind] = useState("payment");
  const [reference, setReference] = useState("");
  const [date, setDate] = useState(run.payday ?? "");
  const [confirm, setConfirm] = useState(false);
  const [correction, setCorrection] = useState("");
  const [error, setError] = useState<string | null>(null);
  const already = run.evidence.some((e) => e.kind === kind);
  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api(`/api/payroll/runs/${run.id}/completion/${kind}`, { method: "POST", body: { reference, completed_on: date, confirm, correction_reason: correction || null } });
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Modal title="Record something done outside the portal" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">What was done<select value={kind} onChange={(e) => setKind(e.target.value)}>{Object.entries(EVIDENCE).map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select></label>
        <label className="field">Receipt or reference<input value={reference} onChange={(e) => setReference(e.target.value)} required minLength={3} maxLength={200} /></label>
        <label className="field">Date it was done<input type="date" value={date} min={run.week_start} onChange={(e) => setDate(e.target.value)} required /></label>
        {already && <label className="field">This is already recorded. Why is it being corrected? (the earlier entry is kept)<input value={correction} onChange={(e) => setCorrection(e.target.value)} required maxLength={600} /></label>}
        <label className="check"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} required /> I confirm this was done outside the portal</label>
        {kind === "payment" && <p className="muted small">Once a payment is recorded the run can no longer be reopened. A later correction is made in an extra run.</p>}
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary">Record</button></div>
      </form>
    </Modal>
  );
}

// ---------- pay rules ----------

interface Settings { revision: number; rules: Record<string, number | string | null | unknown[]>; using_defaults: boolean; employer_name: string | null; employer_abn: string | null; week_start_day: number; can_edit: boolean; defaults_note: string; history: { revision: number; changed_by: string | null; changed_at: string }[] }
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const RULE_FIELDS: [string, string][] = [["daily_ordinary_hours", "Ordinary hours in a day"], ["ot1_hours", "Hours at the first overtime rate"], ["ot1_multiplier", "First overtime rate (times ordinary)"],
  ["ot2_multiplier", "Second overtime rate (times ordinary)"], ["night_multiplier", "Night rate (times ordinary)"], ["holiday_work_multiplier", "Public holiday worked (times ordinary)"],
  ["super_percent", "Super (% of qualifying earnings)"], ["meal_allowance", "Meal allowance ($)"]];

export function PayRules() {
  const { me } = useAuth();
  const load = useLoad(() => api<Settings>("/api/payroll/settings"), []);
  const [v, setV] = useState<Record<string, string> | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const s = load.data;
  if (load.error) return <LoadError message={load.error} onRetry={load.reload} />;
  if (!s) return <Loading />;
  const values = v ?? { ...Object.fromEntries(Object.entries(s.rules).filter(([, x]) => !Array.isArray(x)).map(([k, x]) => [k, x === null ? "" : String(x)])), employer_name: s.employer_name ?? "", employer_abn: s.employer_abn ?? "", week_start_day: String(s.week_start_day) };
  const set = (key: string) => (e: { target: { value: string } }) => setV({ ...values, [key]: e.target.value });
  async function submit(event: FormEvent) {
    event.preventDefault();
    setMsg(null);
    const num = (k: string) => Number(values[k]);
    const rules = { ...Object.fromEntries(RULE_FIELDS.map(([k]) => [k, num(k)])), night_start: values.night_start, night_end: values.night_end, meal_tax_treatment: values.meal_tax_treatment || null,
      saturday_multiplier: values.saturday_multiplier ? num("saturday_multiplier") : null, sunday_multiplier: values.sunday_multiplier ? num("sunday_multiplier") : null, earning_rules: s?.rules.earning_rules ?? [] };
    try {
      await api("/api/payroll/settings", { method: "PUT", body: { expected_revision: s?.revision, rules, employer_name: values.employer_name || null, employer_abn: values.employer_abn || null, week_start_day: Number(values.week_start_day) } });
      setV(null);
      setMsg({ tone: "ok", text: "Saved. The earlier version is kept in the history." });
      load.reload();
    } catch (e) {
      setMsg({ tone: "bad", text: errorMessage(e) });
    }
  }
  return (
    <>
      <div className="page-head"><div><h1>Pay rules</h1><p className="muted">The figures the workbench uses to turn hours into pay.</p></div></div>
      <TestBanner />
      <Notice tone="info">{s.defaults_note}{s.using_defaults ? " Nothing has been saved yet, so the starting figures are in use." : ""}</Notice>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <form className="card card-pad stack" onSubmit={submit}>
        <fieldset className="stack" style={{ gap: 8 }} disabled={!s.can_edit}>
          <legend>Employer</legend>
          <div className="grid-2">
            <label className="field">Legal employer name<input value={values.employer_name} onChange={set("employer_name")} maxLength={200} /></label>
            <label className="field">ABN<input value={values.employer_abn} onChange={set("employer_abn")} maxLength={20} inputMode="numeric" /></label>
            <label className="field">The pay week starts on<select value={values.week_start_day} onChange={set("week_start_day")}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></label>
          </div>
        </fieldset>
        <fieldset className="stack" style={{ gap: 8 }} disabled={!s.can_edit}>
          <legend>Hours and rates</legend>
          <div className="grid-3">
            {RULE_FIELDS.map(([k, label]) => <label key={k} className="field">{label}<input type="number" step="any" min={0} value={values[k] ?? ""} onChange={set(k)} required /></label>)}
            <label className="field">Saturday rate (times ordinary; empty = first overtime rate)<input type="number" step="any" min={1} value={values.saturday_multiplier ?? ""} onChange={set("saturday_multiplier")} /></label>
            <label className="field">Sunday rate (times ordinary; empty = second overtime rate)<input type="number" step="any" min={1} value={values.sunday_multiplier ?? ""} onChange={set("sunday_multiplier")} /></label>
            <label className="field">Night starts<input type="time" value={values.night_start ?? ""} onChange={set("night_start")} required /></label>
            <label className="field">Night ends<input type="time" value={values.night_end ?? ""} onChange={set("night_end")} required /></label>
            <label className="field">Meal allowance is<select value={values.meal_tax_treatment ?? ""} onChange={set("meal_tax_treatment")}><option value="taxable">Taxed</option><option value="exempt">Not taxed</option><option value="">Not decided (tax cannot be worked out when a meal is paid)</option></select></label>
          </div>
        </fieldset>
        {s.can_edit && <div className="row" style={{ justifyContent: "flex-end" }}><button type="submit" className="btn primary" disabled={v === null}>Save pay rules</button></div>}
      </form>
      {s.history.length > 0 && <p className="muted small">Earlier versions: {s.history.map((h) => `version ${h.revision}, replaced by ${h.changed_by ?? "someone"} on ${formatDateTime(h.changed_at, me?.org_timezone ?? "Australia/Hobart")}`).join("; ")}</p>}
    </>
  );
}

// ---------- employee pay settings ----------

export function PayProfiles() {
  const list = useLoad(() => api<{ items: Profile[]; can_edit: boolean }>("/api/payroll/profiles"), []);
  const [editing, setEditing] = useState<Profile | null>(null);
  if (list.error) return <LoadError message={list.error} onRetry={list.reload} />;
  if (!list.data) return <Loading />;
  const TYPES: Record<string, string> = { full_time: "Full time", part_time: "Part time", casual: "Casual", contractor: "Contractor" };
  return (
    <>
      <div className="page-head"><div><h1>Employee pay settings</h1><p className="muted">How each worker is paid. Bank details are stored encrypted and shown only when you ask, which is recorded.</p></div></div>
      <TestBanner />
      <section className="card">
        {list.data.items.length === 0 ? <div className="empty">No active workers.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Worker</th><th scope="col">Employment</th><th scope="col">Base rate</th><th scope="col">Reviewed</th><th scope="col">Bank details</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{list.data.items.map((p) => (
              <tr key={p.worker_id}>
                <td>{p.name}{p.position && <div className="muted small">{p.position}</div>}</td>
                <td>{p.has_profile ? TYPES[p.employment_type ?? ""] ?? <Chip tone="bad">Not set</Chip> : <Chip tone="neutral">No pay settings yet</Chip>}</td>
                <td>{p.base_rate !== null ? `$${p.base_rate.toFixed(2)} an hour` : "—"}</td>
                <td>{p.has_profile ? <>{p.conditions_reviewed ? <Chip tone="ok">Conditions</Chip> : <Chip tone="warn">Conditions not reviewed</Chip>} {p.tax_declaration_reviewed ? <Chip tone="ok">Tax</Chip> : <Chip tone="warn">Tax not reviewed</Chip>}</> : "—"}</td>
                <td>{p.bank.is_set ? p.bank.hint : <span className="muted">Not stored</span>}</td>
                <td style={{ textAlign: "right" }}><button type="button" className="btn small" aria-label={`Pay settings for ${p.name}`} onClick={() => setEditing(p)}>{list.data?.can_edit ? "Edit" : "View"}</button></td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
      {editing && <ProfileModal profile={editing} onClose={() => { setEditing(null); list.reload(); }} />}
    </>
  );
}

function ProfileModal({ profile, onClose }: { profile: Profile; onClose: () => void }) {
  const s = (x: number | null | undefined, fallback = "") => (x === null || x === undefined ? fallback : String(x));
  const [v, setV] = useState({ hourly_rate: s(profile.hourly_rate), annual_salary: s(profile.annual_salary), employment_type: profile.employment_type ?? "", ordinary_weekly_hours: s(profile.ordinary_weekly_hours, "38"),
    tax_mode: profile.tax_mode ?? "", annual_weeks: s(profile.annual_weeks, "4"), personal_weeks: s(profile.personal_weeks, "2"), leave_loading_percent: s(profile.leave_loading_percent, "0"),
    opening_annual: s(profile.opening_annual, "0"), opening_personal: s(profile.opening_personal, "0"), casual_base: s(profile.casual_rates?.base_rate), casual_loading: s(profile.casual_rates?.loading_percent, "25"),
    conditions_reviewed: Boolean(profile.conditions_reviewed), tax_declaration_reviewed: Boolean(profile.tax_declaration_reviewed) });
  const [bank, setBank] = useState({ account_name: "", bsb: "", account_number: "" });
  const [bankState, setBankState] = useState(profile.bank);
  const [revealed, setRevealed] = useState<{ account_name: string; bsb: string; account_number: string } | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [revision, setRevision] = useState(profile.revision);
  const set = (key: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });
  const num = (x: string) => (x.trim() === "" ? null : Number(x));
  async function submit(event: FormEvent) {
    event.preventDefault();
    setMsg(null);
    try {
      const saved = await api<Profile>(`/api/payroll/profiles/${profile.worker_id}`, { method: "PUT", body: { expected_revision: revision, hourly_rate: num(v.hourly_rate), annual_salary: num(v.annual_salary),
        employment_type: v.employment_type || null, pay_basis: num(v.annual_salary) ? "salary" : "hourly", ordinary_weekly_hours: num(v.ordinary_weekly_hours), tax_mode: v.tax_mode || null,
        annual_weeks: num(v.annual_weeks) ?? 0, personal_weeks: num(v.personal_weeks) ?? 0, leave_loading_percent: num(v.leave_loading_percent) ?? 0, opening_annual: num(v.opening_annual) ?? 0, opening_personal: num(v.opening_personal) ?? 0,
        conditions_reviewed: v.conditions_reviewed, tax_declaration_reviewed: v.tax_declaration_reviewed,
        casual_rates: v.employment_type === "casual" && num(v.casual_base) ? { base_rate: num(v.casual_base), loading_percent: num(v.casual_loading) ?? 0 } : null } });
      setRevision(saved.revision);
      setMsg({ tone: "ok", text: "Saved." });
    } catch (e) {
      setMsg({ tone: "bad", text: errorMessage(e) });
    }
  }
  async function saveBank(event: FormEvent) {
    event.preventDefault();
    try {
      setBankState(await api(`/api/payroll/profiles/${profile.worker_id}/bank`, { method: "PUT", body: bank }));
      setBank({ account_name: "", bsb: "", account_number: "" });
      setRevealed(null);
      setMsg({ tone: "ok", text: "Bank details stored encrypted." });
    } catch (e) {
      setMsg({ tone: "bad", text: errorMessage(e) });
    }
  }
  const off = !profile.can_edit;
  return (
    <Modal title={`Pay settings: ${profile.name}`} onClose={onClose}>
      <div className="stack">
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
        <form className="stack" onSubmit={submit}>
          <fieldset className="stack" style={{ gap: 8 }} disabled={off}>
            <legend>Pay</legend>
            <div className="grid-3">
              <label className="field">Employment<select value={v.employment_type} onChange={set("employment_type")}><option value="">Not set</option><option value="full_time">Full time</option><option value="part_time">Part time</option><option value="casual">Casual</option><option value="contractor">Contractor</option></select></label>
              <label className="field">Hourly rate ($)<input type="number" min={0} step="0.0001" value={v.hourly_rate} onChange={set("hourly_rate")} /></label>
              <label className="field">or annual salary ($)<input type="number" min={0} step="0.01" value={v.annual_salary} onChange={set("annual_salary")} /></label>
              <label className="field">Ordinary hours a week<input type="number" min={0} max={80} step="0.01" value={v.ordinary_weekly_hours} onChange={set("ordinary_weekly_hours")} /></label>
              {v.employment_type === "casual" && <label className="field">Casual base rate ($)<input type="number" min={0} step="0.0001" value={v.casual_base} onChange={set("casual_base")} /></label>}
              {v.employment_type === "casual" && <label className="field">Casual loading (%)<input type="number" min={0} max={100} step="0.01" value={v.casual_loading} onChange={set("casual_loading")} /></label>}
            </div>
            <label className="check"><input type="checkbox" checked={v.conditions_reviewed} onChange={(e) => setV({ ...v, conditions_reviewed: e.target.checked })} /> These pay conditions have been reviewed for this person</label>
          </fieldset>
          <fieldset className="stack" style={{ gap: 8 }} disabled={off}>
            <legend>Tax and leave</legend>
            <div className="grid-3">
              <label className="field">Tax scale<select value={v.tax_mode} onChange={set("tax_mode")}><option value="">Not set</option><option value="resident_threshold">Resident, claims the tax-free threshold</option><option value="resident_no_threshold">Resident, no tax-free threshold</option><option value="manual">Worked out by hand each pay</option></select></label>
              <label className="field">Annual leave (weeks a year)<input type="number" min={0} max={10} step="0.01" value={v.annual_weeks} onChange={set("annual_weeks")} /></label>
              <label className="field">Personal leave (weeks a year)<input type="number" min={0} max={10} step="0.01" value={v.personal_weeks} onChange={set("personal_weeks")} /></label>
              <label className="field">Leave loading (%)<input type="number" min={0} max={100} step="0.01" value={v.leave_loading_percent} onChange={set("leave_loading_percent")} /></label>
              <label className="field">Annual leave owed at the start (hours)<input type="number" step="0.01" value={v.opening_annual} onChange={set("opening_annual")} /></label>
              <label className="field">Personal leave owed at the start (hours)<input type="number" step="0.01" value={v.opening_personal} onChange={set("opening_personal")} /></label>
            </div>
            <label className="check"><input type="checkbox" checked={v.tax_declaration_reviewed} onChange={(e) => setV({ ...v, tax_declaration_reviewed: e.target.checked })} /> The tax declaration has been reviewed (HELP debts, variations and special payments need tax worked out by hand)</label>
          </fieldset>
          {!off && <div className="row" style={{ justifyContent: "flex-end" }}><button type="submit" className="btn primary">Save pay settings</button></div>}
        </form>
        <fieldset className="stack" style={{ gap: 8 }}>
          <legend>Bank details</legend>
          <p className="muted small" style={{ margin: 0 }}>{bankState.is_set ? `${bankState.hint}. ` : "Not stored. "}The portal never pays from these details; they are kept for your records.</p>
          {revealed && <Notice tone="info">{revealed.account_name} · BSB {revealed.bsb} · account {revealed.account_number}</Notice>}
          {!off && bankState.is_set && !revealed && <button type="button" className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => void api<typeof revealed>(`/api/payroll/profiles/${profile.worker_id}/bank/reveal`, { method: "POST" }).then(setRevealed).catch((e) => setMsg({ tone: "bad", text: errorMessage(e) }))}>Show bank details (this is recorded)</button>}
          {!off && (
            <form className="grid-3" onSubmit={saveBank}>
              <label className="field">Account name<input value={bank.account_name} onChange={(e) => setBank({ ...bank, account_name: e.target.value })} required minLength={2} maxLength={60} autoComplete="off" /></label>
              <label className="field">BSB<input value={bank.bsb} onChange={(e) => setBank({ ...bank, bsb: e.target.value })} required pattern="\d{3}-?\d{3}" inputMode="numeric" autoComplete="off" /></label>
              <label className="field">Account number<input value={bank.account_number} onChange={(e) => setBank({ ...bank, account_number: e.target.value })} required pattern="\d{4,9}" inputMode="numeric" autoComplete="off" /></label>
              <button type="submit" className="btn small" style={{ alignSelf: "end" }}>{bankState.is_set ? "Replace bank details" : "Store bank details"}</button>
            </form>
          )}
        </fieldset>
        <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" className="btn" onClick={onClose}>Close</button></div>
      </div>
    </Modal>
  );
}

// ---------- a worker's own payslips ----------

export function MyPayslips() {
  const mine = useLoad(() => api<{ linked: boolean; items: { run_id: string; week_start: string; payday: string | null; label: string; gross: string; net: string }[]; note?: string }>("/api/me/payroll/payslips"), []);
  const [error, setError] = useState<string | null>(null);
  const data = mine.data;
  return (
    <>
      <div className="page-head"><div><h1>My payslips</h1><p className="muted">Your own payslips, once the office has released them.</p></div></div>
      {error && <Notice tone="bad">{error}</Notice>}
      {mine.error ? <LoadError message={mine.error} onRetry={mine.reload} /> : !data ? <Loading /> : !data.linked ? (
        <Notice tone="info">Your login is not linked to a worker record, so there are no payslips here.</Notice>
      ) : (
        <section className="card">
          {data.note && <p className="card-pad muted small" style={{ margin: 0 }}>{data.note}</p>}
          {data.items.length === 0 ? <div className="empty">No payslips yet.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th scope="col">Week starting</th><th scope="col">Paid</th><th scope="col">Gross</th><th scope="col">Net</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{data.items.map((p) => (
                <tr key={p.run_id}><td>{formatDate(p.week_start)}<div className="muted small">{p.label}</div></td><td>{p.payday ? formatDate(p.payday) : "—"}</td><td>{money(p.gross)}</td><td>{money(p.net)}</td>
                  <td style={{ textAlign: "right" }}><button type="button" className="btn small" onClick={() => void openFile(`/api/me/payroll/payslips/${p.run_id}/pdf`, "payslip.pdf", false).catch((e) => setError(errorMessage(e)))}>Open</button></td></tr>))}</tbody>
            </table></div>
          )}
        </section>
      )}
    </>
  );
}
