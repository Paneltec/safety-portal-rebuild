import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, formatDate, formatDateTime, openFile, type CertKind, type DocType, type Renewal, type RenewalUpload, type Supplier, type Worker } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

type Msg = { tone: "ok" | "bad"; text: string } | null;

const TABS: { key: string; label: string }[] = [
  { key: "submitted", label: "Ready to review" },
  { key: "pending", label: "Waiting for recipient" },
  { key: "expired", label: "Link expired" },
  { key: "partly_accepted", label: "Partly accepted" },
  { key: "rejected", label: "Rejected" },
  { key: "accepted", label: "Accepted" },
  { key: "revoked", label: "Cancelled" },
];

export function RenewalStatus({ renewal }: { renewal: Renewal }) {
  const tone = renewal.status === "submitted" ? "warn" : renewal.status === "accepted" ? "ok" : renewal.status === "pending" ? "info"
    : renewal.status === "rejected" || renewal.status === "partly_accepted" ? "bad" : "dashed";
  return <Chip tone={tone} icon={tone !== "dashed"}>{renewal.status_label}</Chip>;
}

const DELIVERY: Record<string, string> = {
  held: "Email not sent (delivery not connected)", none: "No email recorded", queued: "Email queued", sent: "Email sent", failed: "Email failed",
};

/** The one-time link panel shown after creating or reissuing a request. */
export function LinkBox({ renewal, onClose }: { renewal: Renewal; onClose: () => void }) {
  if (!renewal.link) return null;
  return (
    <Notice tone="info">
      <strong>Link for {renewal.entity_name}.</strong> {renewal.delivery_note} It is shown once: copy it now.
      <div className="code-box" style={{ marginTop: 8 }}>{renewal.link}</div>
      <div className="row" style={{ marginTop: 8 }}>
        <button type="button" className="btn small" onClick={() => void navigator.clipboard?.writeText(renewal.link ?? "")}>Copy link</button>
        <button type="button" className="btn small" onClick={onClose}>Done</button>
      </div>
    </Notice>
  );
}

export function Renewals() {
  const { me, can } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const [tab, setTab] = useState("submitted");
  const list = useLoad(() => api<{ items: Renewal[]; total: number; counts: Record<string, number> }>(`/api/renewals?status=${tab}&limit=200`), [tab]);
  const [message, setMessage] = useState<Msg>(null);
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState<Renewal | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const canEdit = can("renewals", "edit");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Renewals</h1>
          <p className="muted">Ask a supplier for insurance and licence documents, or a worker for certificates, through a link. Nothing they upload counts until you accept it here.</p>
        </div>
        {canEdit && <button type="button" className="btn primary" onClick={() => setCreating(true)}>New request</button>}
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {issued && <LinkBox renewal={issued} onClose={() => setIssued(null)} />}

      <section className="card">
        <div className="tabs" role="tablist" aria-label="Request status">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" className="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}>
              {t.label}{list.data ? ` (${list.data.counts[t.key] ?? 0})` : ""}
            </button>
          ))}
        </div>
        {list.error ? <div className="card-pad"><LoadError message={list.error} onRetry={list.reload} /></div> : !list.data ? <Loading /> : list.data.items.length === 0 ? (
          <div className="empty">{tab === "submitted" ? "Nothing is waiting for review." : "No requests in this group."}</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">For</th><th scope="col">Documents</th><th scope="col">Status</th><th scope="col">Link expires</th><th scope="col">Message to recipient</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {list.data.items.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={r.entity_type === "supplier" ? `/app/suppliers/${r.entity_id}` : `/app/workers/${r.entity_id}`}>{r.entity_name}</Link>
                      <div className="small muted">{r.entity_type === "supplier" ? "Supplier" : "Worker"}</div>
                    </td>
                    <td>{r.doc_types.map((d) => d.label).join(", ")}<div className="small muted">{r.uploaded} of {r.requested} uploaded</div></td>
                    <td><RenewalStatus renewal={r} /></td>
                    <td className="num">{formatDateTime(r.expires_at, tz)}</td>
                    <td className="small">{DELIVERY[r.delivery] ?? r.delivery}{r.recipient_email ? <div className="muted">{r.recipient_email}</div> : null}</td>
                    <td className="right"><button type="button" className="btn small" onClick={() => setOpen(r.id)}>{r.status === "submitted" && canEdit ? "Review" : "Open"}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {creating && <NewRequest onClose={() => setCreating(false)} onCreated={(r) => { setCreating(false); setIssued(r); setTab("pending"); list.reload(); }} />}
      {open && (
        <RenewalDetail id={open} timezone={tz} onClose={() => setOpen(null)}
          onChanged={(text, renewal) => { setOpen(null); setMessage({ tone: "ok", text }); if (renewal?.link) setIssued(renewal); list.reload(); }} />
      )}
    </>
  );
}

export function NewRequest({ supplier, worker, onClose, onCreated }: { supplier?: Supplier; worker?: Worker; onClose: () => void; onCreated: (r: Renewal) => void }) {
  const { can } = useAuth();
  const fixed = supplier ? "supplier" : worker ? "worker" : null;
  const [kind, setKind] = useState<"supplier" | "worker">(fixed ?? "supplier");
  const [entityId, setEntityId] = useState(supplier?.id ?? worker?.id ?? "");
  const [chosen, setChosen] = useState<string[]>([]);
  const [days, setDays] = useState(14);
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mayAskWorkers = can("certifications", "edit") && can("workers", "team_view");
  const suppliers = useLoad(() => (fixed ? Promise.resolve(null) : api<{ items: Supplier[] }>("/api/suppliers?limit=200")), []);
  const workers = useLoad(() => (fixed || !mayAskWorkers ? Promise.resolve(null) : api<{ items: Worker[] }>("/api/workers?limit=200")), []);
  const docTypes = useLoad(() => api<{ items: DocType[] }>("/api/renewals/doc-types"), []);
  const certKinds = useLoad(() => (mayAskWorkers ? api<{ items: CertKind[] }>("/api/certification-kinds") : Promise.resolve({ items: [] })), []);
  const options = kind === "supplier" ? (docTypes.data?.items ?? []).map((t) => ({ slug: t.slug, label: t.label })) : (certKinds.data?.items ?? []).map((k) => ({ slug: k.slug, label: k.label }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (chosen.length === 0) { setError("Tick at least one document to ask for."); return; }
    setBusy(true);
    setError(null);
    try {
      onCreated(await api<Renewal>("/api/renewals", {
        method: "POST",
        body: { entity_type: kind, entity_id: entityId, doc_types_requested: chosen, expires_in_days: days, recipient_email: email || null, message: message || null },
      }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={fixed ? `Ask ${supplier?.name ?? worker?.name} for documents` : "New request"} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        {!fixed && (
          <>
            {mayAskWorkers && (
              <label className="field">
                Ask
                <select value={kind} onChange={(e) => { setKind(e.target.value as "supplier" | "worker"); setEntityId(""); setChosen([]); }}>
                  <option value="supplier">A supplier or contractor (insurance, licences)</option>
                  <option value="worker">A worker (certificates)</option>
                </select>
              </label>
            )}
            <label className="field">
              {kind === "supplier" ? "Supplier" : "Worker"}
              <select required value={entityId} onChange={(e) => setEntityId(e.target.value)}>
                <option value="">Choose…</option>
                {(kind === "supplier" ? suppliers.data?.items ?? [] : workers.data?.items ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
            </label>
          </>
        )}
        <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend style={{ fontWeight: 600, padding: 0 }}>What to ask for</legend>
          <div className="grid-2" style={{ maxHeight: 220, overflowY: "auto" }}>
            {options.map((o) => (
              <label className="check" key={o.slug}>
                <input type="checkbox" checked={chosen.includes(o.slug)} onChange={(e) => setChosen(e.target.checked ? [...chosen, o.slug] : chosen.filter((x) => x !== o.slug))} /> {o.label}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid-2">
          <label className="field">Link works for (days)<input type="number" min={1} max={90} required value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
          <label className="field">Their email (optional)<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /><span className="hint">Left empty, the email on their record is used.</span></label>
        </div>
        <label className="field">Message shown on their page (optional)<textarea rows={2} value={message} onChange={(e) => setMessage(e.target.value)} /></label>
        <p className="small muted">You will get a link to pass on. Nothing is emailed until email delivery is connected.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create link"}</button>
        </div>
      </form>
    </Modal>
  );
}

interface Draft {
  decision: "accept" | "reject" | "";
  note: string;
  reference: string;
  expiry: string;
  never: boolean;
}

export function RenewalDetail({ id, timezone, onClose, onChanged }: { id: string; timezone: string; onClose: () => void; onChanged: (text: string, renewal?: Renewal) => void }) {
  const { can } = useAuth();
  const detail = useLoad(() => api<Renewal>(`/api/renewals/${id}`), [id]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = can("renewals", "edit");
  const r = detail.data;
  if (detail.error) return <Modal title="Request" onClose={onClose}><Notice tone="bad">{detail.error}</Notice></Modal>;
  if (!r) return <Modal title="Request" onClose={onClose}><Loading /></Modal>;
  const reviewing = r.status === "submitted" && canEdit;
  const draftOf = (u: RenewalUpload): Draft => drafts[u.id] ?? { decision: "", note: "", reference: u.reference ?? "", expiry: u.expiry_date ?? "", never: false };
  const setDraft = (u: RenewalUpload, patch: Partial<Draft>) => setDrafts({ ...drafts, [u.id]: { ...draftOf(u), ...patch } });

  async function run(action: () => Promise<Renewal | unknown>, done: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      onChanged(done, result && typeof result === "object" && "id" in result ? (result as Renewal) : undefined);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  function review() {
    const uploads = r!.uploads ?? [];
    if (uploads.some((u) => !draftOf(u).decision)) { setError("Choose Accept or Reject for every file."); return; }
    void run(() => api(`/api/renewals/${id}/review`, {
      method: "POST",
      body: { decisions: uploads.map((u) => {
        const d = draftOf(u);
        return { upload_id: u.id, decision: d.decision, note: d.note || null, reference: d.reference || null, expiry_date: d.never ? null : d.expiry || null, no_expiry: d.never };
      }) },
    }), "Review saved.");
  }

  return (
    <Modal title={`${r.entity_name}: ${r.doc_types.map((d) => d.label).join(", ")}`} onClose={onClose}>
      <div className="stack">
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8 }}>
          <RenewalStatus renewal={r} />
          <span className="small muted">Link expires {formatDateTime(r.expires_at, timezone)} · {DELIVERY[r.delivery] ?? r.delivery}</span>
        </div>
        {(r.uploads ?? []).length === 0 && <p className="muted">Nothing has been uploaded{r.status === "pending" ? " yet." : "."}</p>}
        {(r.uploads ?? []).map((u) => {
          const d = draftOf(u);
          return (
            <div key={u.id} className="stack" style={{ gap: 8, padding: 12, border: "1px solid var(--line)", borderRadius: 6 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{u.doc_type.label}</strong>
                {u.decision === "accepted" ? <Chip tone="ok">Accepted</Chip> : u.decision === "rejected" ? <Chip tone="bad">Rejected</Chip> : <Chip tone="warn">To be checked</Chip>}
              </div>
              <div className="row" style={{ gap: 8 }}>
                <button type="button" className="link-btn" onClick={() => void openFile(u.file_url, u.file?.name ?? "document", false).catch((e) => setError(errorMessage(e)))}>Open {u.file?.name ?? "file"}</button>
                {!reviewing && <span className="small muted">{u.reference ?? "No number"} · {u.expiry_date ? `expires ${formatDate(u.expiry_date)}` : "no expiry date given"}</span>}
              </div>
              {u.decision_note && <span className="small">Note: {u.decision_note}</span>}
              {reviewing && u.decision === "pending" && (
                <>
                  <div className="grid-2">
                    <label className="field">Number<input value={d.reference} onChange={(e) => setDraft(u, { reference: e.target.value })} /></label>
                    <label className="field">Expiry date<input type="date" disabled={d.never} value={d.never ? "" : d.expiry} onChange={(e) => setDraft(u, { expiry: e.target.value })} /></label>
                  </div>
                  <label className="check"><input type="checkbox" checked={d.never} onChange={(e) => setDraft(u, { never: e.target.checked })} /> Does not expire</label>
                  <div role="group" aria-label={`Decision for ${u.doc_type.label}`} className="row">
                    <button type="button" className={`btn${d.decision === "accept" ? " primary" : ""}`} aria-pressed={d.decision === "accept"} onClick={() => setDraft(u, { decision: "accept" })}>Accept</button>
                    <button type="button" className={`btn${d.decision === "reject" ? " danger" : ""}`} aria-pressed={d.decision === "reject"} onClick={() => setDraft(u, { decision: "reject" })}>Reject</button>
                  </div>
                  {d.decision === "reject" && <label className="field">Why is it rejected? (shown to them if you send a new link)<input value={d.note} onChange={(e) => setDraft(u, { note: e.target.value })} /></label>}
                </>
              )}
            </div>
          );
        })}
        {(r.earlier_uploads ?? []).length > 0 && (
          <p className="small muted">Earlier rounds: {(r.earlier_uploads ?? []).map((u) => `${u.doc_type.label} ${u.decision}`).join("; ")}.</p>
        )}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          {canEdit && ["pending", "expired", "rejected", "partly_accepted", "revoked"].includes(r.status) && (
            <button type="button" className="btn" disabled={busy} onClick={() => void run(() => api<Renewal>(`/api/renewals/${id}/reissue`, { method: "POST", body: { expires_in_days: 14 } }), "New link created. The previous link no longer works.")}>Send a new link</button>
          )}
          {canEdit && ["pending", "submitted"].includes(r.status) && (
            <button type="button" className="btn" disabled={busy} onClick={() => { if (window.confirm("Cancel this request? The link stops working.")) void run(() => api(`/api/renewals/${id}/revoke`, { method: "POST" }), "Request cancelled."); }}>Cancel request</button>
          )}
          <button type="button" className="btn" onClick={onClose}>Close</button>
          {reviewing && <button type="button" className="btn primary" disabled={busy} onClick={review}>{busy ? "Saving…" : "Save review"}</button>}
        </div>
      </div>
    </Modal>
  );
}
