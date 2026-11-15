import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api, formatDate, formatDateTime, openFile, type FieldDef, type SubmissionFile } from "../api";
import { useAuth } from "../auth";
import { AssignmentRules } from "../components/AssignmentRules";
import { FormRenderer } from "../components/FormRenderer";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

/** Screens shared by every safety register: SWMS, pre-starts, site diary, incidents, hazards, inspections, risk, SSRA. */

export const REGISTERS: Record<string, { title: string; intro: string; resource: string; newLabel: string }> = {
  swms: { title: "SWMS", intro: "Safe work method statements: drafted, reviewed, assigned and acknowledged.", resource: "swms", newLabel: "New SWMS" },
  "pre-starts": { title: "Pre-starts", intro: "Daily site and plant pre-start checks.", resource: "pre_starts", newLabel: "New pre-start" },
  "site-diary": { title: "Site diary", intro: "Daily site diary entries.", resource: "site_diary", newLabel: "New diary entry" },
  incidents: { title: "Incidents", intro: "Incident reports, follow-up actions and status.", resource: "incidents", newLabel: "Report an incident" },
  hazards: { title: "Hazards and near misses", intro: "Hazard and near-miss reports, tracked until closed.", resource: "hazards", newLabel: "Report a hazard" },
  inspections: { title: "Inspections", intro: "Site and plant inspections with corrective actions.", resource: "inspections", newLabel: "New inspection" },
  "risk-assessments": { title: "Risk assessments", intro: "Risk assessments with scored hazards and controls.", resource: "risk_assessments", newLabel: "New risk assessment" },
  ssra: { title: "Site-specific risk assessments", intro: "SSRA records, kept separately from general risk assessments.", resource: "risk_assessments", newLabel: "New SSRA" },
};

interface RegisterSchema {
  kind: string;
  label: string;
  workflow: "review" | "tracking";
  statuses: { key: string; label: string }[];
  schema: FieldDef[];
  risk_rule: string | null;
  can_create: boolean;
  scope: "own" | "team";
}

interface Rec {
  id: string;
  title: string;
  status: string;
  status_label: string;
  record_date: string | null;
  revision: number;
  source: string;
  version_no: number;
  supersedes_id: string | null;
  superseded_by_id: string | null;
  created_by_name: string | null;
  created_at: string;
  archived_at: string | null;
  category: string | null;
  severity: string | null;
  failed_check_count: number;
  schema?: FieldDef[];
  values?: Record<string, unknown>;
  files?: SubmissionFile[];
  failed_checks?: { field_id: string; label: string; note: string }[];
  risk?: { rule: string; rows: { hazard: string; initial: { score: number; rating: string } | null; residual: { score: number; rating: string } | null }[] } | null;
  can_edit?: boolean;
  can_review?: boolean;
  can_archive?: boolean;
  can_delete?: boolean;
  acknowledged_by_me_at?: string | null;
  acknowledgement_count?: number;
}

type Msg = { tone: "ok" | "bad" | "warn"; text: string } | null;

export function StatusChip({ status, label }: { status: string; label: string }) {
  const tone = ({ approved: "ok", closed: "ok", rejected: "bad", submitted: "info", in_progress: "info" } as Record<string, "ok" | "bad" | "info">)[status] ?? "warn";
  return <Chip tone={tone} icon={tone !== "info"}>{label}</Chip>;
}

const PAGE = 50;

export function RegisterList() {
  const { register = "" } = useParams();
  const config = REGISTERS[register];
  const { me } = useAuth();
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [archived, setArchived] = useState(false);
  const [superseded, setSuperseded] = useState(false);
  const [offset, setOffset] = useState(0);
  const [message, setMessage] = useState<Msg>(null);

  useEffect(() => {
    setStatus(""); setSearch(""); setQuery(""); setFrom(""); setTo(""); setArchived(false); setSuperseded(false); setOffset(0); setMessage(null);
  }, [register]);
  useEffect(() => {
    const timer = setTimeout(() => { setQuery(search.trim()); setOffset(0); }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const meta = useLoad(() => api<RegisterSchema>(`/api/${register}/schema`), [register]);
  const list = useLoad(() => {
    const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset), include_archived: String(archived), include_superseded: String(superseded) });
    if (status) params.set("status", status);
    if (query) params.set("q", query);
    if (from) params.set("date_from", from);
    if (to) params.set("date_to", to);
    return api<{ items: Rec[]; total: number; scope: string }>(`/api/${register}?${params}`);
  }, [register, status, query, from, to, archived, superseded, offset]);

  if (!config) return <Notice tone="bad">This register does not exist.</Notice>;
  if (meta.error) return <LoadError message={meta.error} onRetry={meta.reload} />;
  if (!meta.data) return <Loading />;
  const tracking = meta.data.workflow === "tracking";
  const filtered = Boolean(status || query || from || to);
  const tz = me?.org_timezone ?? "Australia/Hobart";

  async function create() {
    if (tracking) {
      navigate(`/app/r/${register}/new`);
      return;
    }
    try {
      // Review registers start as an empty draft, so photos and signatures can be added before submitting.
      const draft = await api<Rec>(`/api/${register}`, { method: "POST", body: { fields: [], status: "draft" } });
      navigate(`/app/r/${register}/${draft.id}`);
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{config.title}</h1>
          <p className="muted">{config.intro}{meta.data.scope === "own" ? " You see your own records only." : ""}</p>
        </div>
        {meta.data.can_create && <button type="button" className="btn primary" onClick={() => void create()}>{config.newLabel}</button>}
      </div>

      {(register === "incidents" || register === "hazards") && (
        <div role="group" aria-label="Report type" className="row" style={{ gap: 4, padding: 4, background: "#e3e8ed", borderRadius: 8, alignSelf: "flex-start" }}>
          {(["incidents", "hazards"] as const).map((key) => (
            <Link key={key} to={`/app/r/${key}`} aria-current={register === key ? "page" : undefined} className="btn small"
              style={{ border: 0, background: register === key ? "#fff" : "transparent", minHeight: 36 }}>
              {key === "incidents" ? "Incidents" : "Hazards and near misses"}
            </Link>
          ))}
        </div>
      )}

      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <section className="card">
        <div className="toolbar">
          <label className="field" style={{ flex: "1 1 200px" }}>Search titles<input type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          <label className="field">
            Status
            <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
              <option value="">All</option>
              {meta.data.statuses.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
          <label className="field">From<input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setOffset(0); }} /></label>
          <label className="field">To<input type="date" value={to} onChange={(e) => { setTo(e.target.value); setOffset(0); }} /></label>
          <label className="check"><input type="checkbox" checked={archived} onChange={(e) => { setArchived(e.target.checked); setOffset(0); }} />Show archived</label>
          {register === "swms" && (
            <label className="check"><input type="checkbox" checked={superseded} onChange={(e) => { setSuperseded(e.target.checked); setOffset(0); }} />Show old versions</label>
          )}
          {filtered && <button type="button" className="btn" onClick={() => { setStatus(""); setSearch(""); setFrom(""); setTo(""); setOffset(0); }}>Clear filters</button>}
        </div>
        {list.error ? (
          <div className="card-pad"><LoadError message={list.error} onRetry={list.reload} /></div>
        ) : !list.data ? (
          <Loading />
        ) : list.data.items.length === 0 ? (
          <div className="empty">{filtered ? "Nothing matches these filters." : `No ${config.title.toLowerCase()} recorded yet.`}</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Title</th><th scope="col">Date</th><th scope="col">Status</th>
                    {tracking && <th scope="col">Category</th>}
                    {tracking && <th scope="col">Severity</th>}
                    <th scope="col">Recorded by</th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((r) => (
                    <tr key={r.id} className="clickable">
                      <td>
                        <Link to={`/app/r/${register}/${r.id}`} style={{ fontWeight: 600 }}>{r.title}</Link>
                        {register === "swms" && <span className="small muted"> · version {r.version_no}</span>}
                        {r.source === "form" && <span className="small muted"> · from a form</span>}
                      </td>
                      <td className="num">{formatDate(r.record_date)}</td>
                      <td>
                        <span className="row">
                          <StatusChip status={r.status} label={r.status_label} />
                          {r.failed_check_count > 0 && <Chip tone="bad">{r.failed_check_count} failed</Chip>}
                          {r.archived_at && <Chip tone="neutral" icon={false}>Archived</Chip>}
                          {r.superseded_by_id && <Chip tone="neutral" icon={false}>Superseded</Chip>}
                        </span>
                      </td>
                      {tracking && <td>{r.category ?? "—"}</td>}
                      {tracking && <td>{r.severity ?? "—"}</td>}
                      <td className="small">{r.created_by_name ?? "—"}<div className="muted num">{formatDateTime(r.created_at, tz)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
              <span className="muted small" role="status">Showing {offset + 1}–{offset + list.data.items.length} of {list.data.total}{filtered ? " matching" : ""}</span>
              <span className="spacer" />
              <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
              <button type="button" className="btn small" disabled={offset + PAGE >= list.data.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
            </div>
          </>
        )}
      </section>
    </>
  );
}

function fieldErrors(e: unknown): Record<string, string> {
  return Object.fromEntries((e instanceof ApiError ? e.fieldErrors : []).map((f) => [f.field_id, f.message]));
}

/** First report of a hazard or incident. Photos are added on the next screen, once the report exists. */
export function RegisterNew() {
  const { register = "" } = useParams();
  const config = REGISTERS[register];
  const navigate = useNavigate();
  const meta = useLoad(() => api<RegisterSchema>(`/api/${register}/schema`), [register]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  if (!config) return <Notice tone="bad">This register does not exist.</Notice>;
  if (meta.error) return <LoadError message={meta.error} onRetry={meta.reload} />;
  if (!meta.data) return <Loading />;

  async function save() {
    setBusy(true);
    setMessage(null);
    setErrors({});
    try {
      const fields = Object.entries(values).filter(([, v]) => v !== undefined).map(([id, value]) => ({ id, value }));
      const created = await api<Rec>(`/api/${register}`, { method: "POST", body: { fields } });
      navigate(`/app/r/${register}/${created.id}`, { replace: true });
    } catch (e) {
      const found = fieldErrors(e);
      setErrors(found);
      const count = Object.keys(found).length;
      setMessage({ tone: "bad", text: count ? `${count} ${count === 1 ? "answer needs" : "answers need"} attention. They are marked below.` : errorMessage(e) });
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <Link to={`/app/r/${register}`} className="small">← {config.title}</Link>
          <h1>{config.newLabel}</h1>
        </div>
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <section className="card card-pad">
        <FormRenderer schema={meta.data.schema} values={values} errors={errors} onChange={(id, value) => setValues((v) => ({ ...v, [id]: value }))} />
      </section>
      <div className="row">
        <button type="button" className="btn primary" style={{ minHeight: 44 }} disabled={busy} onClick={() => void save()}>{busy ? "Saving…" : "Save report"}</button>
        <Link to={`/app/r/${register}`} className="btn" style={{ minHeight: 44 }}>Cancel</Link>
      </div>
    </>
  );
}

interface HistoryEvent {
  id: string;
  action: string;
  actor_name: string | null;
  note: string | null;
  at: string;
}

const EVENT_LABELS: Record<string, string> = {
  created: "Created", edited: "Edited", submitted: "Submitted for review", review_approve: "Approved",
  review_request_changes: "Changes requested", review_reject: "Rejected", status_changed: "Status changed",
  archived: "Archived", unarchived: "Restored from archive", superseded: "Superseded by a newer version",
  assignments_changed: "Assignment changed", acknowledged: "Acknowledged", deleted: "Deleted",
};

export function RegisterRecord() {
  const { register = "", recordId = "" } = useParams();
  const config = REGISTERS[register];
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const base = `/api/${register}/${recordId}`;
  const loaded = useLoad(() => api<Rec>(base), [base]);
  const meta = useLoad(() => api<RegisterSchema>(`/api/${register}/schema`), [register]);
  const history = useLoad(() => api<{ events: HistoryEvent[] }>(`${base}/history`), [base, loaded.data?.revision, loaded.data?.status, loaded.data?.archived_at]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [files, setFiles] = useState<SubmissionFile[]>([]);
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<null | { title: string; label: string; required: boolean; confirm: string; danger?: boolean; run: (note: string) => Promise<unknown>; done: string }>(null);
  const [note, setNote] = useState("");
  const [ackName, setAckName] = useState(me?.name ?? "");

  useEffect(() => {
    if (!loaded.data) return;
    setValues(loaded.data.values ?? {});
    setFiles(loaded.data.files ?? []);
    setDirty(false);
    setErrors({});
  }, [loaded.data]);

  if (!config) return <Notice tone="bad">This register does not exist.</Notice>;
  if (loaded.error) {
    return (
      <div className="stack">
        <LoadError message={loaded.error} onRetry={loaded.reload} />
        <Link to={`/app/r/${register}`}>Back to {config.title}</Link>
      </div>
    );
  }
  if (!loaded.data || !meta.data) return <Loading />;
  const rec = loaded.data;
  const review = meta.data.workflow === "review";
  const editable = rec.can_edit === true;
  const isSwms = register === "swms";
  const answers = () => Object.entries(values).filter(([, v]) => v !== undefined).map(([id, value]) => ({ id, value }));

  function fail(e: unknown) {
    const found = fieldErrors(e);
    setErrors(found);
    const count = Object.keys(found).length;
    setMessage({ tone: "bad", text: count ? `${count} ${count === 1 ? "answer needs" : "answers need"} attention. They are marked below.` : errorMessage(e) });
    const first = Object.keys(found)[0];
    if (first) document.querySelector(`[data-field="${first}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function act(run: () => Promise<unknown>, done: string) {
    setBusy(true);
    setMessage(null);
    setErrors({});
    try {
      const result = await run();
      if (result && typeof result === "object" && "id" in (result as object)) loaded.setData(result as Rec);
      else loaded.reload();
      setMessage({ tone: "ok", text: done });
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  const ask = (d: NonNullable<typeof dialog>) => { setNote(""); setDialog(d); };

  return (
    <>
      <div className="page-head">
        <div>
          <Link to={`/app/r/${register}`} className="small">← {config.title}</Link>
          <h1>{rec.title}</h1>
          <div className="row">
            <StatusChip status={rec.status} label={rec.status_label} />
            {rec.archived_at && <Chip tone="neutral" icon={false}>Archived</Chip>}
            {rec.superseded_by_id && <Chip tone="neutral" icon={false}>Superseded</Chip>}
            {editable && dirty && <Chip tone="warn">Unsaved changes</Chip>}
            <span className="muted">
              {rec.created_by_name} · {formatDate(rec.record_date)}{isSwms ? ` · version ${rec.version_no}` : ""}{rec.source === "form" ? " · captured on a form" : ""}
            </span>
          </div>
        </div>
        <div className="row">
          <button type="button" className="btn" disabled={dirty} title={dirty ? "Save your changes first" : undefined}
            onClick={() => void openFile(`${base}/pdf`, `${rec.title}.pdf`, false).catch((e) => setMessage({ tone: "bad", text: errorMessage(e) }))}>
            Open PDF
          </button>
          {rec.can_archive && (rec.archived_at
            ? <button type="button" className="btn" disabled={busy} onClick={() => void act(() => api(`${base}/unarchive`, { method: "POST" }), "Restored from the archive.")}>Restore</button>
            : <button type="button" className="btn" disabled={busy} onClick={() => void act(() => api(`${base}/archive`, { method: "POST" }), "Archived. It stays readable and can be restored.")}>Archive</button>)}
          {(rec.can_delete || (rec.status === "draft" && editable)) && (
            <button type="button" className="btn danger" onClick={() => ask({
              title: rec.status === "draft" ? "Discard this draft?" : "Delete this record?", label: "", required: false, danger: true,
              confirm: rec.status === "draft" ? "Discard draft" : "Delete record", done: "",
              run: async () => { await api(base, { method: "DELETE" }); navigate(`/app/r/${register}`); },
            })}>
              {rec.status === "draft" ? "Discard draft" : "Delete"}
            </button>
          )}
        </div>
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {rec.superseded_by_id && (
        <Notice tone="warn">A newer version of this SWMS is in force. <Link to={`/app/r/swms/${rec.superseded_by_id}`}>Open the current version</Link></Notice>
      )}
      {rec.status === "changes_requested" && history.data && (
        <Notice tone="warn">Changes requested: {history.data.events.find((e) => e.action === "review_request_changes")?.note ?? ""}</Notice>
      )}
      {(rec.failed_checks?.length ?? 0) > 0 && (
        <Notice tone="bad">Failed checks: {rec.failed_checks!.map((f) => `${f.label} (${f.note})`).join("; ")}</Notice>
      )}

      {isSwms && rec.status === "approved" && !rec.superseded_by_id && !can("swms", "edit") && (
        <section className="card card-pad stack">
          <h2>Acknowledge this SWMS</h2>
          {rec.acknowledged_by_me_at ? (
            <Notice tone="ok">You acknowledged version {rec.version_no} on {formatDateTime(rec.acknowledged_by_me_at, tz)}.</Notice>
          ) : (
            <div className="row" style={{ alignItems: "flex-end" }}>
              <label className="field" style={{ flex: "1 1 220px" }}>
                Your name
                <input value={ackName} onChange={(e) => setAckName(e.target.value)} />
                <span className="hint">By signing you confirm you have read and understood version {rec.version_no}.</span>
              </label>
              <button type="button" className="btn primary" style={{ minHeight: 44 }} disabled={busy || !ackName.trim()}
                onClick={() => void act(() => api(`${base}/acknowledge`, { method: "POST", body: { signed_name: ackName } }), "Acknowledged.")}>
                I have read and understood this SWMS
              </button>
            </div>
          )}
        </section>
      )}

      <section className="card card-pad">
        <FormRenderer
          schema={rec.schema ?? []}
          values={values}
          errors={errors}
          readOnly={!editable}
          files={files}
          onChange={(id, value) => { setValues((v) => ({ ...v, [id]: value })); setDirty(true); }}
          onUpload={async (fieldId, file) => {
            const form = new FormData();
            form.append("field_id", fieldId);
            form.append("file", file);
            const saved = await api<SubmissionFile>(`${base}/files`, { form });
            setFiles((f) => [...f, saved]);
            return saved;
          }}
          onRemoveFile={async (file) => {
            await api(`${base}/files/${file.id}`, { method: "DELETE" });
            setFiles((f) => f.filter((x) => x.id !== file.id));
          }}
          onOpenFile={(file) => void openFile(`${base}/files/${file.id}`, file.name, false).catch((e) => setMessage({ tone: "bad", text: errorMessage(e) }))}
        />
      </section>

      {rec.risk && rec.risk.rows.some((r) => r.initial) && (
        <section className="card">
          <div className="card-head"><div><h2>Risk scores</h2><p className="small muted">{rec.risk.rule}</p></div></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Hazard</th><th scope="col">Before controls</th><th scope="col">After controls</th></tr></thead>
              <tbody>
                {rec.risk.rows.map((row, i) => (
                  <tr key={i}>
                    <td>{row.hazard || "—"}</td>
                    <td>{row.initial ? `${row.initial.score} · ${row.initial.rating}` : <span className="muted">Not scored (needs numbers 1 to 5)</span>}</td>
                    <td>{row.residual ? `${row.residual.score} · ${row.residual.rating}` : <span className="muted">Not scored</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="row">
        {editable && review && (
          <>
            <button type="button" className="btn primary" style={{ minHeight: 44 }} disabled={busy}
              onClick={() => void act(() => api(`${base}/submit`, { method: "POST", body: { fields: answers() } }), "Submitted for review.")}>
              Submit for review
            </button>
            <button type="button" className="btn" style={{ minHeight: 44 }} disabled={busy || !dirty}
              onClick={() => void act(() => api(base, { method: "PATCH", body: { fields: answers(), expected_revision: rec.revision } }), "Draft saved.")}>
              Save draft
            </button>
          </>
        )}
        {editable && !review && (
          <button type="button" className="btn primary" style={{ minHeight: 44 }} disabled={busy || !dirty}
            onClick={() => void act(() => api(base, { method: "PATCH", body: { fields: answers(), expected_revision: rec.revision } }), "Changes saved. The earlier answers are kept in the history.")}>
            Save changes
          </button>
        )}
        {rec.can_review && (
          <>
            <button type="button" className="btn primary" disabled={busy} onClick={() => void act(() => api(`${base}/review`, { method: "POST", body: { action: "approve" } }), "Approved.")}>Approve</button>
            <button type="button" className="btn" disabled={busy} onClick={() => ask({ title: "Request changes", label: "What needs to change?", required: true, confirm: "Send back for changes", done: "Sent back for changes.",
              run: (text) => api(`${base}/review`, { method: "POST", body: { action: "request_changes", note: text } }) })}>Request changes</button>
            <button type="button" className="btn danger" disabled={busy} onClick={() => ask({ title: "Reject this record", label: "Why is it rejected?", required: true, danger: true, confirm: "Reject", done: "Rejected.",
              run: (text) => api(`${base}/review`, { method: "POST", body: { action: "reject", note: text } }) })}>Reject</button>
          </>
        )}
        {!review && can(config.resource, "edit") && !rec.archived_at && (
          <>
            {rec.status === "open" && <button type="button" className="btn" disabled={busy || dirty} onClick={() => void act(() => api(`${base}/status`, { method: "POST", body: { status: "in_progress" } }), "Marked as in progress.")}>Mark in progress</button>}
            {rec.status !== "closed" && <button type="button" className="btn" disabled={busy || dirty} onClick={() => ask({ title: "Close this record", label: "How was it resolved?", required: true, confirm: "Close", done: "Closed. The original report is unchanged.",
              run: (text) => api(`${base}/status`, { method: "POST", body: { status: "closed", note: text } }) })}>Close</button>}
            {rec.status === "closed" && <button type="button" className="btn" disabled={busy} onClick={() => void act(() => api(`${base}/status`, { method: "POST", body: { status: "open" } }), "Reopened.")}>Reopen</button>}
          </>
        )}
        {isSwms && can("swms", "edit") && rec.status === "approved" && !rec.superseded_by_id && !rec.archived_at && (
          <button type="button" className="btn" disabled={busy} onClick={async () => {
            try {
              const draft = await api<Rec>(`${base}/new-version`, { method: "POST" });
              navigate(`/app/r/swms/${draft.id}`);
            } catch (e) {
              setMessage({ tone: "bad", text: errorMessage(e) });
            }
          }}>
            Start a new version
          </button>
        )}
      </div>

      {isSwms && can("swms", "edit") && (
        <>
          <AssignmentRules url={`${base}/assignments`} title="Who this SWMS is assigned to" everyoneLabel="Everyone who can view SWMS"
            note="People without edit rights only see an approved SWMS that is assigned to them." />
          <SwmsPeople base={base} versionNo={rec.version_no} count={rec.acknowledgement_count ?? 0} tz={tz} />
        </>
      )}

      <section className="card">
        <div className="card-head"><h2>History</h2></div>
        {!history.data ? <Loading /> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">When</th><th scope="col">What</th><th scope="col">Who</th><th scope="col">Note</th></tr></thead>
              <tbody>
                {history.data.events.map((e) => (
                  <tr key={e.id}><td className="num">{formatDateTime(e.at, tz)}</td><td>{EVENT_LABELS[e.action] ?? e.action}</td><td>{e.actor_name ?? "—"}</td><td>{e.note ?? ""}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {dialog && (
        <Modal title={dialog.title} onClose={() => setDialog(null)}>
          {dialog.label ? (
            <label className="field">{dialog.label}<textarea value={note} onChange={(e) => setNote(e.target.value)} /></label>
          ) : (
            <p>This removes it from the register. The removal is kept in the audit log.</p>
          )}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={() => setDialog(null)}>Cancel</button>
            <button type="button" className={`btn ${dialog.danger ? "danger" : "primary"}`} disabled={dialog.required && !note.trim()}
              onClick={() => { const d = dialog; setDialog(null); if (d.done) void act(() => d.run(note), d.done); else void d.run(note).catch((e) => setMessage({ tone: "bad", text: errorMessage(e) })); }}>
              {dialog.confirm}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function SwmsPeople({ base, versionNo, count, tz }: { base: string; versionNo: number; count: number; tz: string }) {
  const acks = useLoad(() => api<{ acknowledgements: { user_id: string; user_name: string | null; signed_name: string; at: string }[] }>(`${base}/acknowledgements`), [base, count]);
  const versions = useLoad(() => api<{ versions: Rec[] }>(`${base}/versions`), [base]);
  return (
    <div className="grid-2">
      <section className="card">
        <div className="card-head"><h2>Acknowledgements of version {versionNo}</h2></div>
        {!acks.data ? <Loading /> : acks.data.acknowledgements.length === 0 ? <div className="empty">Nobody has acknowledged this version yet.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Person</th><th scope="col">Signed as</th><th scope="col">When</th></tr></thead>
              <tbody>{acks.data.acknowledgements.map((a) => <tr key={a.user_id}><td>{a.user_name ?? "—"}</td><td>{a.signed_name}</td><td className="num">{formatDateTime(a.at, tz)}</td></tr>)}</tbody>
            </table>
          </div>
        )}
      </section>
      <section className="card">
        <div className="card-head"><h2>Versions</h2></div>
        {!versions.data ? <Loading /> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Version</th><th scope="col">Status</th><th scope="col">Date</th></tr></thead>
              <tbody>
                {versions.data.versions.map((v) => (
                  <tr key={v.id}>
                    <td><Link to={`/app/r/swms/${v.id}`}>Version {v.version_no}</Link></td>
                    <td><span className="row"><StatusChip status={v.status} label={v.status_label} />{v.superseded_by_id && <Chip tone="neutral" icon={false}>Superseded</Chip>}</span></td>
                    <td className="num">{formatDate(v.record_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
