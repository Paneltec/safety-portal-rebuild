import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, downloadFile, formatDate, formatDateTime } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface Reason { rule: string; label: string; severity: string; status: string; evidence: string[]; note: string | null; acted_by: string | null; acted_at: string | null }
interface Fill {
  id: string; source_id: string; simulated: boolean; local_date: string; local_time: string; litres: string; fuel_type: string | null; odometer_km: number | null;
  card_number: string | null; driver_name: string | null; location: string | null; description: string | null; registration: string | null; asset_id: string | null; asset: string | null;
  worker: string | null; attribution: string; attribution_label: string; attribution_note: string | null; pricing: string; pricing_label: string; report_rate: string | null;
  report_cost: string | null; source_total: string | null; reasons?: Reason[]; attributed_by?: string | null; week?: { week_start: string; rate: string; source: string } | null;
}
interface ReportRow { key: string | null; label: string; fills: number; litres: string; cost: string | null; unpriced_litres: string; unpriced_fills: number; complete: boolean; other_fuel_litres: string; supplier_total: string | null; last_fill: string | null }
interface Report {
  group: string; date_from: string; date_to: string; simulated: boolean; items: ReportRow[]; group_meaning: string; notes: string[]; cost_meaning: string; can_email: boolean;
  totals: { fills: number; litres: string; cost: string; complete: boolean; unpriced_litres: string; unpriced_fills: number; average_rate: string | null; supplier_total: string | null; supplier_fills: number };
}
interface Price { id: string; week_start: string; week_end: string; source_week_start: string; source_week_end: string; daily_cents: { date: string; price: string }[]; rate: string; source: string; source_label: string; source_note: string | null; simulated: boolean; locked_at: string; locked_by: string | null }
interface Weekly {
  today: string; this_week: { week_start: string; week_end: string; source_week_start: string; source_week_end: string; price: Price | null };
  pending_weeks: { week_start: string; week_end: string; fills: number; litres: string; source_week_start: string; source_week_end: string }[]; ledger: Price[]; simulated: boolean;
  fuelcheck_mode: string; source_page: string; rule: string; can_refresh: boolean; can_enter: boolean;
}
interface Batch { id: string; via: string; via_label: string; filename: string | null; timezone: string | null; dry_run: boolean; status: string; counts: Record<string, number>; errors: { row: number | null; error: string }[]; warnings: string[]; created_by: string | null; created_at: string; error?: string | null }
interface Counts { changed: number; unchanged: number; not_found: number }

const money = (x: string | null) => (x === null ? null : `$${Number(x).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const SimChip = () => <Chip tone="warn">Simulator, not real</Chip>;
const isoDaysAgo = (n: number) => new Date(Date.now() - n * 86400000).toLocaleDateString("en-CA");
const TABS = [["reports", "Reports"], ["check", "To check"], ["fills", "Fills"], ["cards", "Cards"], ["rate", "Weekly diesel rate"], ["import", "Import"]] as const;
type Tab = (typeof TABS)[number][0];

export function Fuel() {
  const [tab, setTab] = useState<Tab>("reports");  // reports by vehicle is always the first view
  const [stamp, setStamp] = useState(0);
  const bump = () => setStamp((n) => n + 1);
  const stats = useLoad(() => api<{ simulated: boolean; this_week: Report["totals"]; rate: string | null; fills_waiting_for_rate: number; open_anomalies: number }>("/api/fleet/fuel/stats"), [stamp]);
  const s = stats.data;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Fuel</h1>
          <p className="muted">Diesel fills, what they cost at the weekly rate, and anything that needs a second look.</p>
        </div>
      </div>
      {s?.simulated && <Notice tone="warn">The SmartFill simulator is on, so these figures are built from invented fills. Switch it off under Settings, Connections, to see real fuel.</Notice>}
      {s && (
        <div className="tiles">
          <div className="tile static"><span className="tile-label">This week</span><strong>{Number(s.this_week.litres).toLocaleString()} L</strong><span className="muted small">{s.this_week.fills} fill{s.this_week.fills === 1 ? "" : "s"}</span></div>
          <div className="tile static"><span className="tile-label">This week's cost</span><strong>{money(s.this_week.cost)}</strong><span className="muted small">{s.this_week.complete ? "At the weekly rate" : `Not complete: ${s.this_week.unpriced_litres} L not priced yet`}</span></div>
          <div className="tile static"><span className="tile-label">This week's diesel rate</span><strong>{s.rate ? `$${s.rate} a litre` : "Not set yet"}</strong><span className="muted small">{s.fills_waiting_for_rate ? `${s.fills_waiting_for_rate} fill${s.fills_waiting_for_rate === 1 ? "" : "s"} waiting for a rate` : "No fills waiting"}</span></div>
          <div className="tile static"><span className="tile-label">To check</span><strong>{s.open_anomalies}</strong><span className="muted small">fill{s.open_anomalies === 1 ? "" : "s"} flagged</span></div>
        </div>
      )}
      <section className="card">
        <div className="tabs" role="tablist" aria-label="Fuel">
          {TABS.map(([key, label]) => <button key={key} type="button" role="tab" className="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}{key === "check" && s?.open_anomalies ? ` (${s.open_anomalies})` : ""}</button>)}
        </div>
        {tab === "reports" && <Reports stamp={stamp} />}
        {tab === "check" && <ToCheck onChanged={bump} />}
        {tab === "fills" && <Fills onChanged={bump} />}
        {tab === "cards" && <Cards onChanged={bump} />}
        {tab === "rate" && <Rate onChanged={bump} />}
        {tab === "import" && <Import onChanged={bump} />}
      </section>
    </>
  );
}

// ---------- reports ----------

function Reports({ stamp }: { stamp: number }) {
  const [group, setGroup] = useState("vehicle");
  const [from, setFrom] = useState(isoDaysAgo(27));
  const [to, setTo] = useState(isoDaysAgo(0));
  const [emailing, setEmailing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const query = `group=${group}&date_from=${from}&date_to=${to}`;
  const report = useLoad(() => api<Report>(`/api/fleet/fuel/reports?${query}`), [query, stamp]);
  const r = report.data;
  const heading = { vehicle: "Vehicle", day: "Day", driver: "Driver", card: "Card" }[group] ?? "Group";
  return (
    <>
      <div className="toolbar">
        <label className="field">Group by
          <select value={group} onChange={(e) => setGroup(e.target.value)}><option value="vehicle">Vehicle</option><option value="day">Day</option><option value="driver">Driver</option><option value="card">Card</option></select>
        </label>
        <label className="field">From<input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="field">To<input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></label>
        <span className="spacer" />
        <button type="button" className="btn" onClick={() => void downloadFile(`/api/fleet/fuel/reports/export?${query}`, `fuel-by-${group}.csv`).catch((e) => setError(errorMessage(e)))}>Download as a spreadsheet</button>
        {r?.can_email && <button type="button" className="btn" onClick={() => setEmailing(true)}>Email this report</button>}
      </div>
      {error && <div className="card-pad"><Notice tone="bad">{error}</Notice></div>}
      {report.error ? <div className="card-pad"><LoadError message={report.error} onRetry={report.reload} /></div> : !r ? <Loading /> : (
        <>
          <p className="card-pad muted small" style={{ margin: 0 }}>{r.group_meaning}. {r.cost_meaning}</p>
          {r.notes.map((n) => <div key={n} className="card-pad" style={{ paddingTop: 0 }}><Notice tone="warn">{n}</Notice></div>)}
          {r.items.length === 0 ? <div className="empty">No fuel in this period.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th scope="col">{heading}</th><th scope="col">Fills</th><th scope="col">Diesel litres</th><th scope="col">Cost</th><th scope="col">Last fill</th></tr></thead>
              <tbody>
                {r.items.map((row) => (
                  <tr key={row.key ?? "none"}>
                    <td>{row.label}{row.key === null && group === "vehicle" && <span className="muted small"> · see To check</span>}</td>
                    <td>{row.fills}</td>
                    <td>{Number(row.litres).toLocaleString()}{Number(row.other_fuel_litres) > 0 && <span className="muted small"> + {row.other_fuel_litres} other fuel</span>}</td>
                    <td>{row.cost === null ? <span className="muted">No rate yet</span> : money(row.cost)}{!row.complete && row.cost !== null && <> <Chip tone="warn">{row.unpriced_litres} L not priced yet</Chip></>}</td>
                    <td>{row.last_fill ? formatDate(row.last_fill) : "—"}</td>
                  </tr>
                ))}
                <tr className="total-row">
                  <th scope="row">Total</th><td>{r.totals.fills}</td><td>{Number(r.totals.litres).toLocaleString()}</td>
                  <td>{money(r.totals.cost)}{!r.totals.complete && <> <Chip tone="warn">Not complete</Chip></>}</td><td />
                </tr>
              </tbody>
            </table></div>
          )}
          {r.totals.supplier_total !== null && <p className="card-pad muted small" style={{ margin: 0 }}>The supplier's own amounts were given for {r.totals.supplier_fills} of {r.totals.fills} fills and come to {money(r.totals.supplier_total)}. They are kept for reference and are not used for the cost above.</p>}
        </>
      )}
      {emailing && r && <EmailModal query={{ group, date_from: from, date_to: to }} onClose={() => setEmailing(false)} />}
    </>
  );
}

function EmailModal({ query, onClose }: { query: { group: string; date_from: string; date_to: string }; onClose: () => void }) {
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const [result, setResult] = useState<{ recipient: string; status: string; note: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ messages: { recipient: string; status: string; note: string }[] }>("/api/fleet/fuel/reports/email", { method: "POST", body: { ...query, to: to.split(/[,;\s]+/).filter(Boolean), note: note || null } });
      setResult(r.messages);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Email this report" onClose={onClose}>
      {result ? (
        <div className="stack">
          {result.map((m) => <Notice key={m.recipient} tone={m.status === "sent" ? "ok" : "info"}>{m.recipient}: {m.note}</Notice>)}
          <p className="muted small">Every message is listed in the <Link to="/app/settings/outbox">Outbox</Link>.</p>
          <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" className="btn" onClick={onClose}>Close</button></div>
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          <p className="muted small">The report for {formatDate(query.date_from)} to {formatDate(query.date_to)} goes as plain text. With safe mode on it is held in the Outbox and not sent.</p>
          <label className="field">Send to (email addresses, separated by commas)<input value={to} onChange={(e) => setTo(e.target.value)} required autoFocus /></label>
          <label className="field">Note to include (optional)<textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} /></label>
          {error && <Notice tone="bad">{error}</Notice>}
          <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn primary" disabled={busy}>Send</button>
          </div>
        </form>
      )}
    </Modal>
  );
}

// ---------- to check ----------

function Reasons({ reasons }: { reasons: Reason[] }) {
  return (
    <ul className="plain-list">
      {reasons.map((x) => (
        <li key={x.rule} style={{ opacity: x.status === "open" ? 1 : 0.7 }}>
          <Chip tone={x.status !== "open" ? "neutral" : x.severity === "high" ? "bad" : x.severity === "medium" ? "warn" : "info"}>{x.label}</Chip>{" "}
          <span className="small">{x.evidence.join("; ")}</span>
          {x.status !== "open" && <div className="muted small">{x.status === "dismissed" ? "Dismissed" : "Resolved"}{x.acted_by ? ` by ${x.acted_by}` : ""}{x.note ? `: ${x.note}` : ""}</div>}
        </li>
      ))}
    </ul>
  );
}

function ToCheck({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState("open");
  const [rule, setRule] = useState("");
  const [page, setPage] = useState(1);
  const [chosen, setChosen] = useState<string[]>([]);
  const [allMatching, setAllMatching] = useState(false);
  const [asking, setAsking] = useState<{ action: "resolve" | "dismiss" | "reopen" | "match"; ids: string[] } | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const query = `status=${status}${rule ? `&rule=${rule}` : ""}`;
  const inbox = useLoad(() => api<{ items: Fill[]; total: number; page_size: number; rules: { rule: string; label: string }[]; can_edit: boolean }>(`/api/fleet/fuel/anomalies?${query}&page=${page}`), [query, page]);
  const data = inbox.data;
  const reset = () => { setChosen([]); setAllMatching(false); };
  const pageIds = data?.items.map((f) => f.id) ?? [];

  async function selectAllMatching() {
    const r = await api<{ ids: string[]; truncated: boolean }>(`/api/fleet/fuel/anomalies/matching-ids?${query}`);
    setChosen(r.ids);
    setAllMatching(true);
    setOutcome(r.truncated ? "Only the first 500 matching fills were selected." : null);
  }
  function finished(counts: Counts, verb: string) {
    setAsking(null);
    reset();
    setOutcome(`${counts.changed} ${verb}${counts.unchanged ? `, ${counts.unchanged} already done` : ""}${counts.not_found ? `, ${counts.not_found} could not be found` : ""}.`);
    inbox.reload();
    onChanged();
  }
  return (
    <>
      <div className="toolbar">
        <label className="field">Show
          <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); reset(); }}><option value="open">To check</option><option value="resolved">Resolved</option><option value="dismissed">Dismissed</option><option value="all">All</option></select>
        </label>
        <label className="field">Reason
          <select value={rule} onChange={(e) => { setRule(e.target.value); setPage(1); reset(); }}><option value="">Any reason</option>{(data?.rules ?? []).map((x) => <option key={x.rule} value={x.rule}>{x.label}</option>)}</select>
        </label>
      </div>
      {outcome && <div className="card-pad" style={{ paddingTop: 0 }}><Notice tone="info">{outcome}</Notice></div>}
      {data?.can_edit && chosen.length > 0 && (
        <div className="toolbar" style={{ alignItems: "center", background: "var(--info-bg)" }} role="region" aria-label="Selected fills">
          <strong>{chosen.length} selected</strong>
          {!allMatching && data.total > pageIds.length && chosen.length === pageIds.length && <button type="button" className="btn small" onClick={() => void selectAllMatching()}>Select all {data.total} that match</button>}
          <span className="spacer" />
          {status !== "open" ? <button type="button" className="btn small" onClick={() => setAsking({ action: "reopen", ids: chosen })}>Reopen</button> : (
            <>
              <button type="button" className="btn small" onClick={() => setAsking({ action: "match", ids: chosen })}>Match to a vehicle</button>
              <button type="button" className="btn small" onClick={() => setAsking({ action: "resolve", ids: chosen })}>Resolve</button>
              <button type="button" className="btn small" onClick={() => setAsking({ action: "dismiss", ids: chosen })}>Dismiss</button>
            </>
          )}
          <button type="button" className="btn small" onClick={reset}>Clear</button>
        </div>
      )}
      {inbox.error ? <div className="card-pad"><LoadError message={inbox.error} onRetry={inbox.reload} /></div> : !data ? <Loading /> : data.items.length === 0 ? (
        <div className="empty">{status === "open" ? "Nothing to check." : "Nothing here."}</div>
      ) : (
        <>
          <div className="table-wrap"><table>
            <thead><tr>
              {data.can_edit && <th scope="col"><input type="checkbox" aria-label="Select every fill on this page" checked={pageIds.every((i) => chosen.includes(i))} onChange={(e) => { setAllMatching(false); setChosen(e.target.checked ? pageIds : []); }} /></th>}
              <th scope="col">Fill</th><th scope="col">Vehicle</th><th scope="col">Why it was flagged</th><th scope="col"><span className="sr-only">Actions</span></th>
            </tr></thead>
            <tbody>{data.items.map((f) => (
              <tr key={f.id}>
                {data.can_edit && <td><input type="checkbox" aria-label={`Select the fill of ${formatDate(f.local_date)} ${f.local_time}`} checked={chosen.includes(f.id)} onChange={(e) => { setAllMatching(false); setChosen(e.target.checked ? [...chosen, f.id] : chosen.filter((x) => x !== f.id)); }} /></td>}
                <td>{formatDate(f.local_date)} {f.local_time}<div className="small">{f.litres} L{f.card_number ? ` · card ${f.card_number}` : ""}</div>{f.simulated && <SimChip />}</td>
                <td>{f.asset ?? <span className="muted">Not matched</span>}{f.description && !f.asset && <div className="muted small">"{f.description}" in the fuel system</div>}</td>
                <td><Reasons reasons={f.reasons ?? []} /></td>
                <td className="row" style={{ gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
                  {data.can_edit && (f.reasons ?? []).some((x) => x.status === "open") && (
                    <>
                      {!f.asset && <button type="button" className="btn small" onClick={() => setAsking({ action: "match", ids: [f.id] })}>Match to a vehicle</button>}
                      <button type="button" className="btn small" onClick={() => setAsking({ action: "resolve", ids: [f.id] })}>Resolve</button>
                      <button type="button" className="btn small" onClick={() => setAsking({ action: "dismiss", ids: [f.id] })}>Dismiss</button>
                    </>
                  )}
                  {data.can_edit && (f.reasons ?? []).every((x) => x.status !== "open") && <button type="button" className="btn small" onClick={() => setAsking({ action: "reopen", ids: [f.id] })}>Reopen</button>}
                </td>
              </tr>))}</tbody>
          </table></div>
          <Pager page={page} pageSize={data.page_size} total={data.total} shown={data.items.length} onPage={(p) => { setPage(p); reset(); }} />
        </>
      )}
      {asking && <ActModal ask={asking} onClose={() => setAsking(null)} onDone={finished} />}
    </>
  );
}

function ActModal({ ask, onClose, onDone }: { ask: { action: "resolve" | "dismiss" | "reopen" | "match"; ids: string[] }; onClose: () => void; onDone: (c: Counts, verb: string) => void }) {
  const assets = useLoad(() => (ask.action === "match" ? api<{ items: { id: string; label: string }[] }>("/api/assets?kind=fleet&page_size=200") : Promise.resolve(null)), [ask.action]);
  const [note, setNote] = useState("");
  const [assetId, setAssetId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const n = ask.ids.length;
  const title = { resolve: "Resolve", dismiss: "Dismiss", reopen: "Reopen", match: "Match to a vehicle" }[ask.action];
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (ask.action === "match") onDone(await api<Counts>("/api/fleet/fuel/anomalies/bulk-attribute", { method: "POST", body: { txn_ids: ask.ids, asset_id: assetId, reason: note } }), "matched");
      else onDone(await api<Counts>(`/api/fleet/fuel/anomalies/bulk-${ask.action}`, { method: "POST", body: { txn_ids: ask.ids, note: note || null } }), { resolve: "resolved", dismiss: "dismissed", reopen: "reopened" }[ask.action]);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  return (
    <Modal title={`${title}: ${n} fill${n === 1 ? "" : "s"}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {ask.action === "resolve" && <p className="muted small">Resolve means it was looked into and is in order, or has been put right. The litres and cost are not changed.</p>}
        {ask.action === "dismiss" && <p className="muted small">Dismiss means the flag does not matter for this fill. The litres and cost are not changed.</p>}
        {ask.action === "match" && (
          <label className="field">Vehicle
            <select value={assetId} onChange={(e) => setAssetId(e.target.value)} required><option value="">Choose…</option>{(assets.data?.items ?? []).map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}</select>
          </label>
        )}
        <label className="field">{ask.action === "match" ? "How do you know?" : ask.action === "dismiss" ? "Why?" : "Note (optional)"}
          <input value={note} onChange={(e) => setNote(e.target.value)} required={ask.action === "match" || ask.action === "dismiss"} minLength={ask.action === "match" ? 3 : undefined} maxLength={400} autoFocus />
        </label>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{title}</button>
        </div>
      </form>
    </Modal>
  );
}

function Pager({ page, pageSize, total, shown, onPage }: { page: number; pageSize: number; total: number; shown: number; onPage: (p: number) => void }) {
  return (
    <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
      <span className="muted small" role="status">Showing {(page - 1) * pageSize + 1}–{(page - 1) * pageSize + shown} of {total}</span>
      <span className="spacer" />
      <button type="button" className="btn small" disabled={page === 1} onClick={() => onPage(page - 1)}>Previous</button>
      <button type="button" className="btn small" disabled={page * pageSize >= total} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  );
}

// ---------- fills ----------

function Fills({ onChanged }: { onChanged: () => void }) {
  const [page, setPage] = useState(1);
  const [waiting, setWaiting] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const list = useLoad(() => api<{ items: Fill[]; total: number; page_size: number }>(`/api/fleet/fuel/transactions?page=${page}${waiting ? "&pricing=pending" : ""}`), [page, waiting]);
  const data = list.data;
  return (
    <>
      <div className="toolbar">
        <label className="check"><input type="checkbox" checked={waiting} onChange={(e) => { setWaiting(e.target.checked); setPage(1); }} /> Only fills waiting for a weekly rate</label>
      </div>
      {list.error ? <div className="card-pad"><LoadError message={list.error} onRetry={list.reload} /></div> : !data ? <Loading /> : data.items.length === 0 ? <div className="empty">{waiting ? "No fills are waiting for a rate." : "No fuel yet. Bring it in on the Import tab."}</div> : (
        <>
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">When</th><th scope="col">Vehicle</th><th scope="col">Litres</th><th scope="col">Cost</th><th scope="col">Card</th></tr></thead>
            <tbody>{data.items.map((f) => (
              <tr key={f.id}>
                <td><button type="button" className="link-btn" onClick={() => setOpen(f.id)}>{formatDate(f.local_date)} {f.local_time}</button>{f.simulated && <> <SimChip /></>}</td>
                <td>{f.asset ?? <span className="muted">Not matched</span>}{f.asset && f.attribution !== "direct" && <div className="muted small">{f.attribution_label}</div>}</td>
                <td>{f.litres}{f.pricing === "not_diesel" && <span className="muted small"> {f.fuel_type}</span>}</td>
                <td>{f.report_cost !== null ? <>{money(f.report_cost)} <span className="muted small">at ${f.report_rate}</span></> : <span className="muted">{f.pricing_label}</span>}</td>
                <td>{f.card_number ?? "—"}</td>
              </tr>))}</tbody>
          </table></div>
          <Pager page={page} pageSize={data.page_size} total={data.total} shown={data.items.length} onPage={setPage} />
        </>
      )}
      {open && <FillModal id={open} onClose={() => setOpen(null)} onChanged={() => { list.reload(); onChanged(); }} />}
    </>
  );
}

function FillModal({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const fill = useLoad(() => api<Fill>(`/api/fleet/fuel/transactions/${id}`), [id]);
  const f = fill.data;
  const kv = (label: string, value: ReactNode) => <><dt>{label}</dt><dd>{value}</dd></>;
  return (
    <Modal title="Fill" onClose={() => { onChanged(); onClose(); }}>
      {fill.error ? <Notice tone="bad">{fill.error}</Notice> : !f ? <Loading /> : (
        <div className="stack">
          {f.simulated && <SimChip />}
          <dl className="kv">
            {kv("When", `${formatDate(f.local_date)} ${f.local_time}`)}
            {kv("Litres", `${f.litres}${f.fuel_type ? ` (${f.fuel_type})` : ""}`)}
            {kv("Vehicle", f.asset ? <>{f.asset}<div className="muted small">{f.attribution_label}{f.attributed_by ? ` (${f.attributed_by}: ${f.attribution_note})` : ""}</div></> : "Not matched")}
            {kv("Driver", f.worker ?? f.driver_name ?? "—")}
            {kv("Card", f.card_number ?? "—")}
            {kv("Where", f.location ?? "—")}
            {kv("Odometer", f.odometer_km === null ? "Not entered" : `${f.odometer_km.toLocaleString()} km`)}
            {kv("Reporting cost", f.report_cost !== null ? `${money(f.report_cost)} at $${f.report_rate} a litre${f.week ? ` (week of ${formatDate(f.week.week_start)})` : ""}` : f.pricing_label)}
            {f.source_total !== null && kv("Supplier's amount", `${money(f.source_total)} (for reference only)`)}
            {kv("Transaction number", f.source_id.startsWith("derived:") ? "None in the file" : f.source_id)}
          </dl>
          {(f.reasons ?? []).length > 0 && <><h3 style={{ margin: 0 }}>Flags</h3><Reasons reasons={f.reasons ?? []} /></>}
        </div>
      )}
    </Modal>
  );
}

// ---------- cards ----------

interface Card { card_number: string; label: string | null; simulated: boolean; asset_id: string | null; asset: string | null; worker: string | null; fills: number; last_used_at: string | null; unmatched_fills: number }

function Cards({ onChanged }: { onChanged: () => void }) {
  const { me } = useAuth();
  const cards = useLoad(() => api<{ items: Card[]; can_edit: boolean }>("/api/fleet/fuel/cards"), []);
  const [editing, setEditing] = useState<Card | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const data = cards.data;
  return (
    <>
      <p className="card-pad muted small" style={{ margin: 0 }}>A card's vehicle is used when a fill has no registration to go by. It is shown on the fill as coming from the card, not as a confirmed match.</p>
      {outcome && <div className="card-pad" style={{ paddingTop: 0 }}><Notice tone="ok">{outcome}</Notice></div>}
      {cards.error ? <div className="card-pad"><LoadError message={cards.error} onRetry={cards.reload} /></div> : !data ? <Loading /> : data.items.length === 0 ? <div className="empty">No cards yet. They appear here as fuel is imported.</div> : (
        <div className="table-wrap"><table>
          <thead><tr><th scope="col">Card</th><th scope="col">Assigned to</th><th scope="col">Fills</th><th scope="col">Last used</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{data.items.map((c) => (
            <tr key={c.card_number}>
              <td>{c.card_number}{c.label && <div className="muted small">"{c.label}" in the fuel system</div>}{c.simulated && <SimChip />}</td>
              <td>{c.asset ?? <span className="muted">Nothing yet</span>}{c.worker && <div className="muted small">{c.worker}</div>}</td>
              <td>{c.fills}{c.unmatched_fills > 0 && <> <Chip tone="warn">{c.unmatched_fills} not matched</Chip></>}</td>
              <td>{c.last_used_at ? formatDateTime(c.last_used_at, me?.org_timezone ?? "Australia/Hobart") : "—"}</td>
              <td style={{ textAlign: "right" }}>{data.can_edit && <button type="button" className="btn small" aria-label={`Assign card ${c.card_number}`} onClick={() => setEditing(c)}>Assign</button>}</td>
            </tr>))}</tbody>
        </table></div>
      )}
      {editing && <CardModal card={editing} onClose={() => setEditing(null)} onSaved={(n) => { setEditing(null); setOutcome(n ? `Saved. ${n} earlier fill${n === 1 ? "" : "s"} matched to it.` : "Saved."); cards.reload(); onChanged(); }} />}
    </>
  );
}

function CardModal({ card, onClose, onSaved }: { card: Card; onClose: () => void; onSaved: (matched: number) => void }) {
  const assets = useLoad(() => api<{ items: { id: string; label: string }[] }>("/api/assets?kind=fleet&page_size=200"), []);
  const [assetId, setAssetId] = useState(card.asset_id ?? "");
  const [apply, setApply] = useState(card.unmatched_fills > 0);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const r = await api<{ fills_matched: number }>(`/api/fleet/fuel/cards/${encodeURIComponent(card.card_number)}`, { method: "PATCH", body: { asset_id: assetId || null, apply_to_unmatched: apply } });
      onSaved(r.fills_matched);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Modal title={`Card ${card.card_number}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">Vehicle this card belongs to
          <select value={assetId} onChange={(e) => setAssetId(e.target.value)}><option value="">None</option>{(assets.data?.items ?? []).map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}</select>
        </label>
        {card.unmatched_fills > 0 && <label className="check"><input type="checkbox" checked={apply} onChange={(e) => setApply(e.target.checked)} /> Also match this card's {card.unmatched_fills} fill{card.unmatched_fills === 1 ? "" : "s"} that have no vehicle yet</label>}
        <p className="muted small">Fills a person has already matched, or that matched by registration, are left as they are.</p>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary">Save</button>
        </div>
      </form>
    </Modal>
  );
}

// ---------- weekly rate ----------

function Rate({ onChanged }: { onChanged: () => void }) {
  const { me } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const weekly = useLoad(() => api<Weekly>("/api/fleet/fuel/weekly-diesel"), []);
  const [result, setResult] = useState<{ ok: boolean; error: string | null; results: { week_start: string; outcome: string; rate: string | null; missing?: string[] }[]; warnings: string[]; priced: number } | null>(null);
  const [entering, setEntering] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const w = weekly.data;
  if (weekly.error) return <div className="card-pad"><LoadError message={weekly.error} onRetry={weekly.reload} /></div>;
  if (!w) return <Loading />;
  async function refresh() {
    setBusy(true);
    try {
      setResult(await api("/api/fleet/fuel/weekly-diesel/refresh", { method: "POST" }));
      weekly.reload();
      onChanged();
    } catch (e) {
      setResult({ ok: false, error: errorMessage(e), results: [], warnings: [], priced: 0 });
    } finally {
      setBusy(false);
    }
  }
  const week = w.this_week;
  return (
    <div className="card-pad stack">
      <p style={{ margin: 0 }}>{w.rule} A week runs Monday to Sunday. If any of the seven days is missing, the week has no rate and its fills wait; nothing is estimated.</p>
      <div className="rate-box">
        <div>
          <span className="tile-label">Week of {formatDate(week.week_start)} to {formatDate(week.week_end)}</span>
          <strong style={{ fontSize: 22, display: "block" }}>{week.price ? `$${week.price.rate} a litre` : "No rate yet"}</strong>
          <span className="muted small">From the daily prices of {formatDate(week.source_week_start)} to {formatDate(week.source_week_end)}{week.price ? `. ${week.price.source_label}, fixed ${formatDateTime(week.price.locked_at, tz)}` : ""}</span>
          {w.simulated && <div><SimChip /></div>}
        </div>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          {w.can_refresh && <button type="button" className="btn primary" disabled={busy} onClick={() => void refresh()}>{busy ? "Reading prices…" : "Get prices and fix the rate"}</button>}
          {w.can_enter && !week.price && !w.simulated && <button type="button" className="btn" onClick={() => setEntering(week.week_start)}>Enter the seven prices by hand</button>}
        </div>
      </div>
      {w.fuelcheck_mode === "off" && w.can_refresh && <p className="muted small" style={{ margin: 0 }}>FuelCheck is switched off under Settings, Connections, so prices cannot be read automatically. An admin can type them in from the <a href={w.source_page} target="_blank" rel="noreferrer">FuelCheck diesel page</a>.</p>}
      {result && !result.ok && <Notice tone="bad">{result.error} No rate was changed.</Notice>}
      {result?.ok && result.results.map((r) => (
        <Notice key={r.week_start} tone={r.outcome === "locked" ? "ok" : r.outcome === "missing_days" ? "warn" : "info"}>
          Week of {formatDate(r.week_start)}: {r.outcome === "locked" ? `rate fixed at $${r.rate} a litre.` : r.outcome === "already_locked" ? `already fixed at $${r.rate}. Not changed.` : `no rate. FuelCheck has no price for ${(r.missing ?? []).map((d) => formatDate(d)).join(", ")}.`}
        </Notice>
      ))}
      {result?.ok && result.priced > 0 && <Notice tone="ok">{result.priced} fill{result.priced === 1 ? "" : "s"} given a cost.</Notice>}
      {result?.warnings.map((x) => <Notice key={x} tone="warn">{x}</Notice>)}

      {w.pending_weeks.length > 0 && (
        <>
          <h3 style={{ margin: 0 }}>Weeks with fills waiting for a rate</h3>
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Week</th><th scope="col">Fills waiting</th><th scope="col">Litres</th><th scope="col">Needs prices for</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{w.pending_weeks.map((p) => (
              <tr key={p.week_start}>
                <td>{formatDate(p.week_start)} to {formatDate(p.week_end)}</td><td>{p.fills}</td><td>{p.litres}</td><td>{formatDate(p.source_week_start)} to {formatDate(p.source_week_end)}</td>
                <td style={{ textAlign: "right" }}>{w.can_enter && !w.simulated && p.week_start <= w.today && <button type="button" className="btn small" aria-label={`Enter prices for the week of ${formatDate(p.week_start)}`} onClick={() => setEntering(p.week_start)}>Enter prices</button>}</td>
              </tr>))}</tbody>
          </table></div>
        </>
      )}
      <h3 style={{ margin: 0 }}>Rates fixed so far</h3>
      {w.ledger.length === 0 ? <p className="muted">None yet.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th scope="col">Week</th><th scope="col">Rate</th><th scope="col">Daily prices used (cents)</th><th scope="col">Source</th></tr></thead>
          <tbody>{w.ledger.map((p) => (
            <tr key={p.id}>
              <td>{formatDate(p.week_start)} to {formatDate(p.week_end)}</td><td><strong>${p.rate}</strong></td>
              <td className="small">{p.daily_cents.map((d) => d.price).join(", ")}<div className="muted">{formatDate(p.source_week_start)} to {formatDate(p.source_week_end)}</div></td>
              <td>{p.source_label}{p.locked_by ? `, ${p.locked_by}` : ""}{p.source_note && <div className="muted small">{p.source_note}</div>}</td>
            </tr>))}</tbody>
        </table></div>
      )}
      <p className="muted small" style={{ margin: 0 }}>A fixed rate cannot be edited or removed, and later price changes at the source do not alter it.</p>
      {entering && <EnterWeek weekStart={entering} onClose={() => setEntering(null)} onSaved={() => { setEntering(null); setResult(null); weekly.reload(); onChanged(); }} />}
    </div>
  );
}

function EnterWeek({ weekStart, onClose, onSaved }: { weekStart: string; onClose: () => void; onSaved: () => void }) {
  const start = new Date(`${weekStart}T12:00:00Z`);
  const days = Array.from({ length: 7 }, (_, i) => new Date(start.getTime() - (7 - i) * 86400000).toISOString().slice(0, 10));
  const [cents, setCents] = useState<string[]>(Array(7).fill(""));
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filled = cents.every((c) => c !== "" && Number(c) > 50 && Number(c) < 500);
  // Shown for checking only; the server works the rate out again from the seven prices.
  const preview = filled ? (Math.round((cents.reduce((sum, c) => sum + Math.round(Number(c) * 10), 0) / 7000) * 10000 + Number.EPSILON) / 10000).toFixed(4) : null;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api("/api/fleet/fuel/weekly-diesel", { method: "PUT", body: { week_start: weekStart, note, days: days.map((d, i) => ({ date: d, cents: Number(cents[i]) })) } });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Modal title={`Diesel prices for the week of ${formatDate(weekStart)}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p className="muted small">Type the daily diesel price, in cents a litre, for each of the seven days before that week, as shown on the FuelCheck Tasmania diesel page.</p>
        <div className="grid-3">
          {days.map((d, i) => (
            <label key={d} className="field">{new Date(`${d}T12:00:00Z`).toLocaleDateString("en-AU", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" })}
              <input type="number" min={50} max={500} step="0.1" required value={cents[i]} onChange={(e) => { setConfirmed(false); setCents(cents.map((c, j) => (j === i ? e.target.value : c))); }} />
            </label>
          ))}
        </div>
        <label className="field">Where the prices came from<input value={note} onChange={(e) => setNote(e.target.value)} required minLength={5} maxLength={400} placeholder="For example: FuelCheck diesel trend page, read on 16/11/2026" /></label>
        {preview && <Notice tone="warn">This gives a rate of <strong>${preview} a litre</strong>. Once saved it cannot be changed or removed, so check each price first.</Notice>}
        {preview && <label className="check"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I have checked the seven prices</label>}
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!filled || !confirmed}>Fix this week's rate</button>
        </div>
      </form>
    </Modal>
  );
}

// ---------- import ----------

const ZONES = ["Australia/Hobart", "Australia/Melbourne", "Australia/Sydney", "Australia/Brisbane", "Australia/Adelaide", "Australia/Darwin", "Australia/Perth"];

function BatchResult({ b }: { b: Batch }) {
  const c = b.counts;
  if (b.status === "failed") return <Notice tone="bad">{b.error ?? b.errors[0]?.error ?? "The import failed."} Nothing was imported.</Notice>;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <Notice tone={b.dry_run ? "info" : "ok"}>
        {b.dry_run ? "Preview only, nothing has been saved. " : "Imported. "}
        {c.rows} row{c.rows === 1 ? "" : "s"} read: {c.new} new, {c.updated} with details added, {c.unchanged} already here{c.errors ? `, ${c.errors} could not be used` : ""}.
        {!b.dry_run && ` ${c.priced} given a cost, ${c.pending_price} waiting for a weekly rate${c.not_diesel ? `, ${c.not_diesel} not diesel` : ""}. ${c.new_flags} new flag${c.new_flags === 1 ? "" : "s"} to check.`}
      </Notice>
      {c.conflicts > 0 && <Notice tone="warn">{c.conflicts} fill{c.conflicts === 1 ? " has" : "s have"} a different value in this file from the one already saved. The saved value was kept.</Notice>}
      {b.warnings.map((x) => <Notice key={x} tone="warn">{x}</Notice>)}
      {b.errors.length > 0 && (
        <div className="table-wrap" style={{ maxHeight: 220, overflowY: "auto" }}><table>
          <thead><tr><th scope="col">Row in the file</th><th scope="col">Problem</th></tr></thead>
          <tbody>{b.errors.map((e, i) => <tr key={i}><td>{e.row ?? "—"}</td><td>{e.error}</td></tr>)}</tbody>
        </table></div>
      )}
    </div>
  );
}

function Import({ onChanged }: { onChanged: () => void }) {
  const { me } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const admin = me?.role_id === "admin";
  const [file, setFile] = useState<File | null>(null);
  const [zone, setZone] = useState(tz);
  const [result, setResult] = useState<Batch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const smartfill = useLoad(() => api<{ ready: boolean; message: string; simulated: boolean; last_synced_at: string | null; calls_left: Record<string, number>; schedule: string; can_sync: boolean }>("/api/fleet/fuel/smartfill-status"), []);
  const [days, setDays] = useState("7");
  const [sfResult, setSfResult] = useState<Batch | null>(null);
  const batches = useLoad(() => api<{ items: Batch[] }>("/api/fleet/fuel/batches"), []);

  async function send(dryRun: boolean) {
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    form.append("timezone", zone);
    form.append("dry_run", String(dryRun));
    try {
      setResult(await api<Batch>("/api/fleet/fuel/import-csv", { form }));
      if (!dryRun) { batches.reload(); onChanged(); }
    } catch (e) {
      setError(errorMessage(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }
  async function sync(dryRun: boolean) {
    setBusy(true);
    setError(null);
    try {
      setSfResult(await api<Batch>("/api/fleet/fuel/sync-smartfill", { method: "POST", body: { dry_run: dryRun, days: Number(days) } }));
      smartfill.reload();
      if (!dryRun) { batches.reload(); onChanged(); }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const sf = smartfill.data;
  return (
    <div className="card-pad stack">
      {!admin && <Notice tone="info">Importing fuel is done by an admin. You can see past imports below.</Notice>}
      {error && <Notice tone="bad">{error}</Notice>}
      {admin && (
        <section className="stack" aria-label="Import a spreadsheet file" style={{ gap: 10 }}>
          <h3 style={{ margin: 0 }}>From a spreadsheet file (CSV)</h3>
          <p className="muted small" style={{ margin: 0 }}>Export transactions from SmartFill as CSV. The file is checked first and nothing is saved until you choose Import. Importing the same fills again does not double them up.</p>
          <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
            <label className="field">File<input type="file" accept=".csv,text/csv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} /></label>
            <label className="field">Times in the file are in
              <select value={zone} onChange={(e) => { setZone(e.target.value); setResult(null); }}>{[...new Set([tz, ...ZONES])].map((z) => <option key={z} value={z}>{z.replace("Australia/", "")} time</option>)}</select>
            </label>
            <button type="button" className="btn" disabled={!file || busy} onClick={() => void send(true)}>Check the file</button>
            {result?.dry_run && result.counts.new + result.counts.updated > 0 && <button type="button" className="btn primary" disabled={busy} onClick={() => void send(false)}>Import</button>}
          </div>
          {result && <BatchResult b={result} />}
        </section>
      )}
      {sf && (
        <section className="stack" aria-label="Import from SmartFill" style={{ gap: 10 }}>
          <h3 style={{ margin: 0 }}>From SmartFill directly {sf.simulated && <SimChip />}</h3>
          <p className="muted small" style={{ margin: 0 }}>{sf.message} {sf.schedule} {sf.last_synced_at ? `Last imported ${formatDateTime(sf.last_synced_at, tz)}.` : "Never imported."}{sf.ready && !sf.simulated ? ` SmartFill limits calls: ${sf.calls_left.minute} left this minute, ${sf.calls_left.day} today.` : ""}</p>
          {sf.ready && sf.can_sync && (
            <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
              <label className="field">How far back
                <select value={days} onChange={(e) => { setDays(e.target.value); setSfResult(null); }}><option value="7">7 days</option><option value="14">14 days</option><option value="31">31 days</option><option value="62">62 days</option></select>
              </label>
              <button type="button" className="btn" disabled={busy} onClick={() => void sync(true)}>Check SmartFill</button>
              {sfResult?.dry_run && sfResult.status !== "failed" && sfResult.counts.new + sfResult.counts.updated > 0 && <button type="button" className="btn primary" disabled={busy} onClick={() => void sync(false)}>Import from SmartFill</button>}
            </div>
          )}
          {sfResult && <BatchResult b={sfResult} />}
        </section>
      )}
      <h3 style={{ margin: 0 }}>Past imports</h3>
      {!batches.data ? <Loading /> : batches.data.items.length === 0 ? <p className="muted">None yet.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th scope="col">When</th><th scope="col">From</th><th scope="col">Result</th><th scope="col">By</th></tr></thead>
          <tbody>{batches.data.items.map((b) => (
            <tr key={b.id}>
              <td>{formatDateTime(b.created_at, tz)}</td><td>{b.via_label}{b.filename ? `: ${b.filename}` : ""}</td>
              <td>{b.status === "failed" ? <Chip tone="bad">Failed</Chip> : `${b.counts.new ?? 0} new, ${b.counts.updated ?? 0} added to, ${b.counts.errors ?? 0} not usable`}</td><td>{b.created_by ?? "—"}</td>
            </tr>))}</tbody>
        </table></div>
      )}
    </div>
  );
}
