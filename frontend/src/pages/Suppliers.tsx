import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, formatDate, formatDateTime, openFile, type DocType, type Renewal, type Supplier, type SupplierDoc, type User } from "../api";
import { useAuth } from "../auth";
import { ReminderButton } from "../components/Reminders";
import { CertChip, Chip, LoadError, Loading, Modal, Notice, StatusChip, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";
import { LinkBox, NewRequest, RenewalDetail, RenewalStatus } from "./Renewals";

type Msg = { tone: "ok" | "bad"; text: string } | null;

interface SupplierPage {
  items: Supplier[];
  total: number;
  limit: number;
  offset: number;
  counts: { active: number; inactive: number; archived: number };
}

const PAGE_SIZE = 25;

function Compliance({ supplier }: { supplier: Supplier }) {
  const c = supplier.compliance;
  const parts: { status: string; label: string }[] = [];
  if (c.expired) parts.push({ status: "expired", label: `${c.expired} expired` });
  if (c.missing.length) parts.push({ status: "missing", label: `${c.missing.length} missing` });
  if (c.expiring) parts.push({ status: "expiring", label: `${c.expiring} expiring` });
  if (c.unknown) parts.push({ status: "unknown", label: `${c.unknown} no expiry date` });
  if (parts.length === 0) return c.valid ? <CertChip status="valid" label={`${c.valid} current`} /> : <span className="muted">{supplier.is_contractor ? "None held" : "—"}</span>;
  return <span className="chips">{parts.map((p) => <CertChip key={p.status} status={p.status} label={p.label} />)}</span>;
}

export function Suppliers() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"active" | "inactive" | "archived">("active");
  const [kind, setKind] = useState("all");
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => { setQuery(search.trim()); setOffset(0); }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const list = useLoad(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), status, kind });
    if (query) params.set("q", query);
    return api<SupplierPage>(`/api/suppliers?${params}`);
  }, [query, status, kind, offset]);
  const badges = useLoad(() => api<{ counts: Record<string, { open_tasks: number; notes: number; members: number }> }>("/api/suppliers/panel-counts"), []);
  const page = list.data;
  const canAdd = can("suppliers", "edit");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Suppliers and contractors</h1>
          <p className="muted">One directory. A contractor is a supplier who does work for you, so their insurance and licences are tracked.</p>
        </div>
        {canAdd && (
          <div className="row">
            <ReminderButton url="/api/suppliers/documents/scan-reminders" what="supplier documents" />
            <button type="button" className="btn primary" onClick={() => setAdding(true)}>Add supplier</button>
          </div>
        )}
      </div>
      <section className="card">
        <div className="tabs" role="tablist" aria-label="Supplier status">
          {(["active", "inactive", "archived"] as const).map((key) => (
            <button key={key} type="button" role="tab" className="tab" aria-selected={status === key} onClick={() => { setStatus(key); setOffset(0); }}>
              {key[0].toUpperCase() + key.slice(1)}{page ? ` (${page.counts[key]})` : ""}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <label className="field" style={{ flex: "1 1 240px" }}>Search name, trade, contact or ABN<input type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          <label className="field">
            Show
            <select value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0); }}>
              <option value="all">Everyone</option>
              <option value="contractor">Contractors only</option>
            </select>
          </label>
          {(query || kind !== "all") && <button type="button" className="btn" onClick={() => { setSearch(""); setKind("all"); }}>Clear filters</button>}
        </div>
        {list.error ? <div className="card-pad"><LoadError message={list.error} onRetry={list.reload} /></div> : !page ? <Loading /> : page.items.length === 0 ? (
          <div className="empty">{query || kind !== "all" ? "No suppliers match these filters." : status === "active" ? "No suppliers yet." : `No ${status} suppliers.`}</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead><tr><th scope="col">Name</th><th scope="col">Trade</th><th scope="col">Contact</th><th scope="col">Compliance documents</th><th scope="col">Open tasks</th></tr></thead>
                <tbody>
                  {page.items.map((s) => (
                    <tr key={s.id}>
                      <td>
                        <Link to={`/app/suppliers/${s.id}`}>{s.name}</Link>
                        {s.is_contractor && <> <Chip tone="info" icon={false}>Contractor</Chip></>}
                        {s.source ? <span className="muted small"> · {s.source.system}</span> : null}
                      </td>
                      <td>{s.trade ?? "—"}</td>
                      <td className="small">{s.contact_name ?? "—"}{s.contact_phone ? <div className="muted">{s.contact_phone}</div> : null}</td>
                      <td><Compliance supplier={s} /></td>
                      <td className="num">{badges.data?.counts[s.id]?.open_tasks ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
              <span className="muted small" role="status">Showing {page.offset + 1}–{page.offset + page.items.length} of {page.total}</span>
              <span className="spacer" />
              <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
              <button type="button" className="btn small" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
            </div>
          </>
        )}
      </section>
      {adding && <SupplierForm onClose={() => setAdding(false)} onSaved={(s) => navigate(`/app/suppliers/${s.id}`)} />}
    </>
  );
}

function SupplierForm({ supplier, onClose, onSaved }: { supplier?: Supplier; onClose: () => void; onSaved: (s: Supplier) => void }) {
  const { can } = useAuth();
  const [v, setV] = useState({
    name: supplier?.name ?? "", abn: supplier?.abn ?? "", trade: supplier?.trade ?? "", contact_name: supplier?.contact_name ?? "",
    contact_email: supplier?.contact_email ?? "", contact_phone: supplier?.contact_phone ?? "", address: supplier?.address ?? "",
    suburb: supplier?.suburb ?? "", state: supplier?.state ?? "", postcode: supplier?.postcode ?? "",
  });
  const [contractor, setContractor] = useState(supplier?.is_contractor ?? false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = Object.fromEntries(Object.entries(v).map(([k, value]) => [k, value.trim() || null]));
    if (can("suppliers", "edit")) body.is_contractor = contractor;
    try {
      onSaved(await api<Supplier>(supplier ? `/api/suppliers/${supplier.id}` : "/api/suppliers", { method: supplier ? "PATCH" : "POST", body }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={supplier ? `Edit ${supplier.name}` : "Add supplier"} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">Business name<input required minLength={2} value={v.name} onChange={set("name")} /></label>
        <div className="grid-2">
          <label className="field">ABN (optional)<input inputMode="numeric" value={v.abn} onChange={set("abn")} /></label>
          <label className="field">Trade or service<input value={v.trade} onChange={set("trade")} /></label>
          <label className="field">Contact name<input value={v.contact_name} onChange={set("contact_name")} /></label>
          <label className="field">Contact phone<input type="tel" value={v.contact_phone} onChange={set("contact_phone")} /></label>
        </div>
        <label className="field">Contact email<input type="email" value={v.contact_email} onChange={set("contact_email")} /></label>
        <label className="field">Street address<input value={v.address} onChange={set("address")} /></label>
        <div className="grid-2">
          <label className="field">Suburb<input value={v.suburb} onChange={set("suburb")} /></label>
          <label className="field">State<input value={v.state} onChange={set("state")} /></label>
          <label className="field">Postcode<input value={v.postcode} onChange={set("postcode")} /></label>
        </div>
        {can("suppliers", "edit") && (
          <label className="check"><input type="checkbox" checked={contractor} onChange={(e) => setContractor(e.target.checked)} /> This is a contractor (does work for us; track their insurance and licences)</label>
        )}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : supplier ? "Save" : "Add supplier"}</button>
        </div>
      </form>
    </Modal>
  );
}

export function SupplierDetail() {
  const { supplierId = "" } = useParams();
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const supplier = useLoad(() => api<Supplier>(`/api/suppliers/${supplierId}`), [supplierId]);
  const [message, setMessage] = useState<Msg>(null);
  const [editing, setEditing] = useState(false);
  const tz = me?.org_timezone ?? "Australia/Hobart";

  if (supplier.error) return <LoadError message={supplier.error} onRetry={supplier.reload} />;
  const s = supplier.data;
  if (!s) return <Loading />;

  async function act(path: string, method: string, done: string, after?: () => void) {
    setMessage(null);
    try {
      await api(path, { method });
      setMessage({ tone: "ok", text: done });
      if (after) after(); else supplier.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <>
      <p className="small"><Link to="/app/suppliers">← Suppliers and contractors</Link></p>
      <div className="page-head">
        <div>
          <h1>{s.name}</h1>
          <p className="muted row" style={{ gap: 8 }}>
            {s.status === "active" ? <Chip tone="ok">Active</Chip> : s.status === "archived" ? <Chip tone="dashed" icon={false}>Archived</Chip> : <Chip tone="neutral" icon={false}>Inactive</Chip>}
            {s.is_contractor && <Chip tone="info" icon={false}>Contractor</Chip>}
            <span>{s.trade ?? "No trade set"} · {s.source ? `Imported from ${s.source.system}` : "Added by hand"}</span>
          </p>
        </div>
        {s.can_edit && <button type="button" className="btn primary" onClick={() => setEditing(true)}>Edit details</button>}
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {s.compliance.missing.length > 0 && <Notice tone="bad">Missing required documents: {s.compliance.missing.map((m) => m.label).join(", ")}.</Notice>}

      <section className="card">
        <div className="card-head"><h2>Details</h2></div>
        <dl className="kv card-pad" style={{ paddingTop: 4 }}>
          <dt>ABN</dt><dd>{s.abn ? s.abn.replace(/(\d{2})(\d{3})(\d{3})(\d{3})/, "$1 $2 $3 $4") : "—"}</dd>
          <dt>Contact</dt><dd>{[s.contact_name, s.contact_phone, s.contact_email].filter(Boolean).join(" · ") || "—"}</dd>
          <dt>Address</dt><dd>{s.address_full || "—"}</dd>
          {s.source && s.locally_edited && s.locally_edited.length > 0 && (
            <><dt>Changed here</dt><dd className="small">{s.locally_edited.join(", ").replaceAll("_", " ")} (kept when the next import runs)</dd></>
          )}
        </dl>
      </section>

      <DocumentsCard supplier={s} onChanged={supplier.reload} />
      {can("renewals", "view") && <RequestsCard supplier={s} timezone={tz} onChanged={supplier.reload} />}
      <PeopleCard supplier={s} />
      <TasksCard supplier={s} />
      <NotesCard supplier={s} timezone={tz} />
      {s.logins && <LoginsCard supplier={s} onChanged={supplier.reload} />}

      {(can("suppliers", "archive") || can("suppliers", "delete")) && (
        <section className="card card-pad stack">
          <h2>Directory status</h2>
          <div className="row">
            {can("suppliers", "edit") && s.status !== "archived" && (
              <button type="button" className="btn" onClick={async () => {
                try { supplier.setData(await api<Supplier>(`/api/suppliers/${s.id}`, { method: "PATCH", body: { active: !s.active } })); } catch (e) { setMessage({ tone: "bad", text: errorMessage(e) }); }
              }}>{s.active ? "Mark inactive" : "Mark active"}</button>
            )}
            {can("suppliers", "archive") && (s.status === "archived"
              ? <button type="button" className="btn" onClick={() => void act(`/api/suppliers/${s.id}/unarchive`, "POST", "Returned from the archive.")}>Return from archive</button>
              : <button type="button" className="btn" onClick={() => void act(`/api/suppliers/${s.id}/archive`, "POST", "Archived.")}>Archive</button>)}
            {can("suppliers", "delete") && (
              <button type="button" className="btn danger" onClick={() => { if (window.confirm(`Delete ${s.name}? Use this for a record made by mistake.`)) void act(`/api/suppliers/${s.id}`, "DELETE", "Deleted.", () => navigate("/app/suppliers")); }}>Delete</button>
            )}
          </div>
        </section>
      )}
      {editing && <SupplierForm supplier={s} onClose={() => setEditing(false)} onSaved={(next) => { setEditing(false); supplier.setData(next); setMessage({ tone: "ok", text: "Details saved." }); }} />}
    </>
  );
}

function DocumentsCard({ supplier, onChanged }: { supplier: Supplier; onChanged: () => void }) {
  const [history, setHistory] = useState(false);
  const docs = useLoad(() => api<{ items: SupplierDoc[] }>(`/api/suppliers/${supplier.id}/documents?history=${history}`), [supplier.id, history]);
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState<Msg>(null);
  return (
    <section className="card">
      <div className="card-head">
        <h2>Compliance documents</h2>
        <div className="row">
          <label className="check"><input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} /> Show earlier versions</label>
          {supplier.can_edit && <button type="button" className="btn small primary" onClick={() => setAdding(true)}>Add document</button>}
        </div>
      </div>
      {message && <div className="card-pad" style={{ paddingBottom: 0 }}><Notice tone={message.tone}>{message.text}</Notice></div>}
      {docs.error ? <div className="card-pad"><LoadError message={docs.error} onRetry={docs.reload} /></div> : !docs.data ? <Loading /> : docs.data.items.length === 0 ? (
        <div className="empty">No documents held. Add one here, or ask the supplier to upload through a renewal link.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Document</th><th scope="col">Number</th><th scope="col">Expires</th><th scope="col">Status</th><th scope="col">File</th>{supplier.can_edit && <th scope="col"><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {docs.data.items.map((d) => (
                <tr key={d.id}>
                  <td>{d.doc_type.label}{d.source === "renewal" ? <span className="muted small"> · via renewal link</span> : null}</td>
                  <td>{d.reference ?? "—"}</td>
                  <td>{d.no_expiry ? "Does not expire" : d.expiry_date ? formatDate(d.expiry_date) : <span className="muted">Not recorded</span>}</td>
                  <td>{d.current ? <CertChip status={d.status} label={d.status_label} /> : <Chip tone="neutral" icon={false}>Replaced</Chip>}</td>
                  <td><button type="button" className="link-btn" onClick={() => void openFile(d.file_url, d.file?.name ?? "document", false).catch((e) => setMessage({ tone: "bad", text: errorMessage(e) }))}>{d.file?.name ?? "Open"}</button></td>
                  {supplier.can_edit && (
                    <td className="right">
                      {d.current && <button type="button" className="btn small danger" onClick={async () => {
                        if (!window.confirm(`Delete this ${d.doc_type.label} document? The one it replaced becomes current again.`)) return;
                        try { await api(`/api/suppliers/${supplier.id}/documents/${d.id}`, { method: "DELETE" }); docs.reload(); onChanged(); } catch (e) { setMessage({ tone: "bad", text: errorMessage(e) }); }
                      }}>Delete</button>}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && <AddDocument supplier={supplier} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); setMessage({ tone: "ok", text: "Document added." }); docs.reload(); onChanged(); }} />}
    </section>
  );
}

function AddDocument({ supplier, onClose, onSaved }: { supplier: Supplier; onClose: () => void; onSaved: () => void }) {
  const types = useLoad(() => api<{ items: DocType[] }>("/api/renewals/doc-types"), []);
  const fileInput = useRef<HTMLInputElement>(null);
  const [type, setType] = useState("");
  const [reference, setReference] = useState("");
  const [expiry, setExpiry] = useState("");
  const [never, setNever] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) { setError("Choose the document file."); return; }
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("doc_type", type);
    form.append("file", file);
    if (reference.trim()) form.append("reference", reference.trim());
    if (!never && expiry) form.append("expiry_date", expiry);
    if (never) form.append("no_expiry", "true");
    try {
      await api(`/api/suppliers/${supplier.id}/documents`, { form });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={`Add a document for ${supplier.name}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">
          Type
          <select required value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">Choose…</option>
            {(types.data?.items ?? []).map((t) => <option key={t.slug} value={t.slug}>{t.label}</option>)}
          </select>
        </label>
        <label className="field">File (PDF or photo)<input ref={fileInput} type="file" accept="application/pdf,image/png,image/jpeg,image/webp" /></label>
        <div className="grid-2">
          <label className="field">Policy or licence number (optional)<input value={reference} onChange={(e) => setReference(e.target.value)} /></label>
          <label className="field">Expiry date<input type="date" disabled={never} value={never ? "" : expiry} onChange={(e) => setExpiry(e.target.value)} /></label>
        </div>
        <label className="check"><input type="checkbox" checked={never} onChange={(e) => setNever(e.target.checked)} /> This document does not expire</label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Uploading…" : "Add document"}</button>
        </div>
      </form>
    </Modal>
  );
}

function RequestsCard({ supplier, timezone, onChanged }: { supplier: Supplier; timezone: string; onChanged: () => void }) {
  const { can } = useAuth();
  const list = useLoad(() => api<{ items: Renewal[] }>(`/api/renewals?entity_type=supplier&entity_id=${supplier.id}&limit=50`), [supplier.id]);
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState<Renewal | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  return (
    <section className="card">
      <div className="card-head">
        <h2>Document requests</h2>
        {can("renewals", "edit") && <button type="button" className="btn small primary" onClick={() => setCreating(true)}>Ask for documents</button>}
      </div>
      {(message || issued) && (
        <div className="card-pad stack" style={{ paddingBottom: 0 }}>
          {message && <Notice tone="ok">{message}</Notice>}
          {issued && <LinkBox renewal={issued} onClose={() => setIssued(null)} />}
        </div>
      )}
      {list.error ? <div className="card-pad"><LoadError message={list.error} onRetry={list.reload} /></div> : !list.data ? <Loading /> : list.data.items.length === 0 ? (
        <div className="empty">No requests yet.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Asked for</th><th scope="col">Status</th><th scope="col">Created</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {list.data.items.map((r) => (
                <tr key={r.id}>
                  <td>{r.doc_types.map((d) => d.label).join(", ")}</td>
                  <td><RenewalStatus renewal={r} /></td>
                  <td className="num">{formatDateTime(r.created_at, timezone)}</td>
                  <td className="right"><button type="button" className="btn small" onClick={() => setOpen(r.id)}>{r.status === "submitted" ? "Review" : "Open"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && <NewRequest supplier={supplier} onClose={() => setCreating(false)} onCreated={(r) => { setCreating(false); setIssued(r); list.reload(); }} />}
      {open && <RenewalDetail id={open} timezone={timezone} onClose={() => setOpen(null)} onChanged={(text, renewal) => { setOpen(null); setMessage(text); if (renewal?.link) setIssued(renewal); list.reload(); onChanged(); }} />}
    </section>
  );
}

interface Member { id: string; name: string; role: string | null; email: string | null; phone: string | null; is_primary_contact: boolean }
interface Task { id: string; title: string; due_date: string | null; status: string; priority: string; assigned_name: string | null; overdue: boolean }
interface Note { id: string; body: string; author: string; created_at: string; edited: boolean }

function PeopleCard({ supplier }: { supplier: Supplier }) {
  const members = useLoad(() => api<{ items: Member[] }>(`/api/suppliers/${supplier.id}/members`), [supplier.id]);
  const [v, setV] = useState({ name: "", role: "", phone: "", email: "" });
  const [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<unknown>) {
    setError(null);
    try { await action(); members.reload(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <section className="card">
      <div className="card-head"><h2>People</h2></div>
      {error && <div className="card-pad" style={{ paddingBottom: 0 }}><Notice tone="bad">{error}</Notice></div>}
      {members.data && members.data.items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Name</th><th scope="col">Role</th><th scope="col">Contact</th>{supplier.can_edit && <th scope="col"><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {members.data.items.map((m) => (
                <tr key={m.id}>
                  <td>{m.name} {m.is_primary_contact && <Chip tone="info" icon={false}>Main contact</Chip>}</td>
                  <td>{m.role ?? "—"}</td>
                  <td className="small">{[m.phone, m.email].filter(Boolean).join(" · ") || "—"}</td>
                  {supplier.can_edit && (
                    <td><span className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                      {!m.is_primary_contact && <button type="button" className="btn small" onClick={() => void run(() => api(`/api/suppliers/members/${m.id}`, { method: "PATCH", body: { is_primary_contact: true } }))}>Make main contact</button>}
                      <button type="button" className="btn small" onClick={() => void run(() => api(`/api/suppliers/members/${m.id}`, { method: "DELETE" }))}>Remove</button>
                    </span></td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {members.data && members.data.items.length === 0 && !supplier.can_edit && <div className="empty">No people listed.</div>}
      {supplier.can_edit && (
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api(`/api/suppliers/${supplier.id}/members`, { method: "POST", body: { name: v.name, role: v.role || null, phone: v.phone || null, email: v.email || null } }); setV({ name: "", role: "", phone: "", email: "" }); }); }}>
          <label className="field">Add a person<input required minLength={2} placeholder="Name" value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} /></label>
          <label className="field">Role<input value={v.role} onChange={(e) => setV({ ...v, role: e.target.value })} /></label>
          <label className="field">Phone<input type="tel" value={v.phone} onChange={(e) => setV({ ...v, phone: e.target.value })} /></label>
          <label className="field">Email<input type="email" value={v.email} onChange={(e) => setV({ ...v, email: e.target.value })} /></label>
          <button type="submit" className="btn">Add</button>
        </form>
      )}
    </section>
  );
}

function TasksCard({ supplier }: { supplier: Supplier }) {
  const tasks = useLoad(() => api<{ items: Task[] }>(`/api/suppliers/${supplier.id}/tasks`), [supplier.id]);
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<unknown>) {
    setError(null);
    try { await action(); tasks.reload(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <section className="card">
      <div className="card-head"><h2>Tasks</h2></div>
      {error && <div className="card-pad" style={{ paddingBottom: 0 }}><Notice tone="bad">{error}</Notice></div>}
      {tasks.data && tasks.data.items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Task</th><th scope="col">Due</th><th scope="col">Status</th>{supplier.can_edit && <th scope="col"><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {tasks.data.items.map((t) => (
                <tr key={t.id}>
                  <td style={t.status === "done" ? { textDecoration: "line-through", color: "var(--muted)" } : undefined}>{t.title}{t.assigned_name ? <span className="muted small"> · {t.assigned_name}</span> : null}</td>
                  <td>{t.due_date ? formatDate(t.due_date) : "—"} {t.overdue && <Chip tone="bad">Overdue</Chip>}</td>
                  <td>{t.status === "done" ? <Chip tone="ok">Done</Chip> : <Chip tone="neutral" icon={false}>Open</Chip>}</td>
                  {supplier.can_edit && (
                    <td><span className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                      <button type="button" className="btn small" onClick={() => void run(() => api(`/api/suppliers/tasks/${t.id}`, { method: "PATCH", body: { status: t.status === "done" ? "open" : "done" } }))}>{t.status === "done" ? "Reopen" : "Mark done"}</button>
                      <button type="button" className="btn small" onClick={() => void run(() => api(`/api/suppliers/tasks/${t.id}`, { method: "DELETE" }))}>Remove</button>
                    </span></td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {tasks.data && tasks.data.items.length === 0 && !supplier.can_edit && <div className="empty">No tasks.</div>}
      {supplier.can_edit && (
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api(`/api/suppliers/${supplier.id}/tasks`, { method: "POST", body: { title, due_date: due || null } }); setTitle(""); setDue(""); }); }}>
          <label className="field" style={{ flex: "1 1 240px" }}>Add a task<input required minLength={2} placeholder="What needs doing?" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
          <label className="field">Due<input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
          <button type="submit" className="btn">Add</button>
        </form>
      )}
    </section>
  );
}

function NotesCard({ supplier, timezone }: { supplier: Supplier; timezone: string }) {
  const notes = useLoad(() => api<{ items: Note[] }>(`/api/suppliers/${supplier.id}/notes`), [supplier.id]);
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<unknown>) {
    setError(null);
    try { await action(); notes.reload(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <section className="card">
      <div className="card-head"><h2>Notes</h2></div>
      <div className="card-pad stack" style={{ paddingTop: 4 }}>
        {error && <Notice tone="bad">{error}</Notice>}
        {notes.data?.items.map((n) => (
          <div key={n.id} style={{ borderLeft: "3px solid var(--line-strong)", paddingLeft: 10 }}>
            <div style={{ whiteSpace: "pre-wrap" }}>{n.body}</div>
            <div className="small muted row" style={{ gap: 8 }}>
              <span>{n.author} · {formatDateTime(n.created_at, timezone)}{n.edited ? " · edited" : ""}</span>
              {supplier.can_edit && <button type="button" className="link-btn" onClick={() => void run(() => api(`/api/suppliers/notes/${n.id}`, { method: "DELETE" }))}>Remove</button>}
            </div>
          </div>
        ))}
        {notes.data && notes.data.items.length === 0 && <span className="muted">No notes yet.</span>}
        {supplier.can_edit && (
          <form className="form" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api(`/api/suppliers/${supplier.id}/notes`, { method: "POST", body: { body } }); setBody(""); }); }}>
            <label className="field">Add a note<textarea rows={2} required value={body} onChange={(e) => setBody(e.target.value)} /></label>
            <div><button type="submit" className="btn">Save note</button></div>
          </form>
        )}
      </div>
    </section>
  );
}

function LoginsCard({ supplier, onChanged }: { supplier: Supplier; onChanged: () => void }) {
  const { can } = useAuth();
  const manage = can("users", "edit") && can("suppliers", "edit");
  const users = useLoad(() => (manage ? api<{ items: User[] }>("/api/users?limit=200") : Promise.resolve({ items: [] })), [manage]);
  const [userId, setUserId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const reps = (users.data?.items ?? []).filter((u) => u.role_id.startsWith("contractor_rep") && !(supplier.logins ?? []).some((l) => l.id === u.id));
  async function run(action: () => Promise<unknown>) {
    setError(null);
    try { await action(); setUserId(""); onChanged(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <section className="card card-pad stack">
      <h2>Portal logins for this supplier</h2>
      <p className="small muted">A person with a “Contractor rep” role whose login is linked here can sign in and see only this supplier: its details and compliance documents.</p>
      {error && <Notice tone="bad">{error}</Notice>}
      {(supplier.logins ?? []).length === 0 ? <span className="muted">No logins are linked.</span> : (supplier.logins ?? []).map((l) => (
        <div key={l.id} className="row" style={{ justifyContent: "space-between" }}>
          <span className="row" style={{ gap: 8 }}><Link to={`/app/settings/users/${l.id}`}>{l.name}</Link><span className="muted">{l.email}</span><StatusChip status={l.status} /></span>
          {manage && <button type="button" className="btn small" onClick={() => void run(() => api(`/api/suppliers/${supplier.id}/link-user/${l.id}`, { method: "DELETE" }))}>Unlink</button>}
        </div>
      ))}
      {manage && reps.length > 0 && (
        <form className="toolbar" style={{ padding: 0 }} onSubmit={(e) => { e.preventDefault(); void run(() => api(`/api/suppliers/${supplier.id}/link-user`, { method: "POST", body: { user_id: userId } })); }}>
          <label className="field">
            Link a contractor login
            <select required value={userId} onChange={(e) => setUserId(e.target.value)}>
              <option value="">Choose…</option>
              {reps.map((u) => <option key={u.id} value={u.id}>{u.name} · {u.email}</option>)}
            </select>
          </label>
          <button type="submit" className="btn">Link</button>
        </form>
      )}
      {manage && reps.length === 0 && (supplier.logins ?? []).length === 0 && <span className="small muted">To add one, create a user with the “Contractor rep” role under Settings → Users, then link them here.</span>}
    </section>
  );
}
