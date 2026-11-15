import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, formatDate, formatDateTime, openFile, type Cert, type CertKind, type Company, type Role, type User, type Worker } from "../api";
import { useAuth } from "../auth";
import { Avatar, CertChip, Chip, LoadError, Loading, Modal, Notice, StatusChip, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";
import { WorkerStatus } from "./Workers";

type Msg = { tone: "ok" | "bad"; text: string } | null;

const text = (value: unknown): string => (typeof value === "string" ? value : "");

const HISTORY_LABELS: Record<string, string> = {
  "worker.created": "Worker added",
  "worker.updated": "Details changed",
  "worker.self_updated": "Details changed by the worker",
  "worker.archived": "Archived",
  "worker.unarchived": "Returned from archive",
  "worker.deleted": "Deleted",
  "worker.restored": "Restored",
  "worker.user_linked": "Login linked",
  "worker.user_unlinked": "Login unlinked",
  "worker.user_provisioned": "Login created",
  "worker.photo_changed": "Photo changed",
  "worker.photo_removed": "Photo removed",
  "worker.protected_revealed": "Protected details viewed",
  "certification.added": "Certificate added",
  "certification.updated": "Certificate changed",
  "certification.renewed": "Certificate renewed",
  "certification.deleted": "Certificate deleted",
  "certification.archived": "Certificate archived",
  "certification.restored": "Certificate restored",
  "certification.evidence_uploaded": "Certificate file uploaded",
};

export function WorkerDetail() {
  const { workerId = "" } = useParams();
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const worker = useLoad(() => api<Worker>(`/api/workers/${workerId}`), [workerId]);
  const [message, setMessage] = useState<Msg>(null);
  const [editing, setEditing] = useState(false);
  const photoInput = useRef<HTMLInputElement>(null);
  const canEdit = can("workers", "edit");
  const tz = me?.org_timezone ?? "Australia/Hobart";

  if (worker.error) return <LoadError message={worker.error} onRetry={worker.reload} />;
  const w = worker.data;
  if (!w) return <Loading />;
  const mayEditPhoto = canEdit || w.is_me;

  async function act(path: string, method: string, done: string, after?: () => void) {
    setMessage(null);
    try {
      await api(path, { method });
      setMessage({ tone: "ok", text: done });
      if (after) after();
      else worker.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  async function uploadPhoto(file: File | undefined) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      worker.setData(await api<Worker>(`/api/workers/${workerId}/photo`, { form }));
      worker.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <>
      <p className="small"><Link to="/app/workers">← Workers</Link></p>
      <div className="page-head">
        <div className="person">
          <Avatar url={w.photo_url} name={w.name} size={64} />
          <div>
            <h1>{w.name}</h1>
            <p className="muted row" style={{ gap: 8 }}>
              <WorkerStatus status={w.status} />
              <span>{w.position ?? "No position set"}</span>
              <span>· {w.source ? `Imported from ${w.source.system}` : "Added by hand"}</span>
            </p>
          </div>
        </div>
        <div className="row">
          {mayEditPhoto && (
            <>
              <input ref={photoInput} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" aria-label="Choose a photo"
                onChange={(e) => { void uploadPhoto(e.target.files?.[0]); e.target.value = ""; }} />
              <button type="button" className="btn" onClick={() => photoInput.current?.click()}>{w.photo_url ? "Change photo" : "Add photo"}</button>
              {w.photo_url && <button type="button" className="btn" onClick={() => void act(`/api/workers/${workerId}/photo`, "DELETE", "Photo removed.")}>Remove photo</button>}
            </>
          )}
          {canEdit && <button type="button" className="btn primary" onClick={() => setEditing(true)}>Edit details</button>}
        </div>
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {w.possible_duplicates && w.possible_duplicates.length > 0 && (
        <Notice tone="warn">
          Another record looks like the same person:{" "}
          {w.possible_duplicates.map((d, i) => (
            <span key={d.id}>{i > 0 ? ", " : ""}<Link to={`/app/workers/${d.id}`}>{d.name}</Link> (same {d.match})</span>
          ))}
          . Records are never merged automatically. If one is a mistake, archive or delete it.
        </Notice>
      )}
      {w.missing_certifications && w.missing_certifications.length > 0 && (
        <Notice tone="bad">Missing expected certificates: {w.missing_certifications.map((m) => m.label).join(", ")}.</Notice>
      )}

      <div className="grid-2">
        <section className="card">
          <div className="card-head"><h2>Work details</h2></div>
          <dl className="kv card-pad" style={{ paddingTop: 4 }}>
            <dt>Preferred name</dt><dd>{w.preferred_name ?? "—"}</dd>
            <dt>Position</dt><dd>{w.position ?? "—"}</dd>
            <dt>Email</dt><dd>{w.email ?? "—"}</dd>
            <dt>Mobile</dt><dd>{w.mobile ?? "—"}</dd>
            <dt>Phone</dt><dd>{w.phone ?? "—"}</dd>
            {w.can_see_personal && (
              <>
                <dt>Employee number</dt><dd>{text(w.employee_id) || "—"}</dd>
                <dt>Start date</dt><dd>{formatDate(text(w.date_employee_added) || null)}</dd>
              </>
            )}
          </dl>
        </section>

        {w.can_see_personal ? (
          <section className="card">
            <div className="card-head"><h2>Personal details</h2></div>
            <dl className="kv card-pad" style={{ paddingTop: 4 }}>
              <dt>Address</dt>
              <dd>{[w.street_address, w.suburb, w.state, w.postal_code, w.country].map(text).filter(Boolean).join(", ") || "—"}</dd>
              <dt>Emergency contact</dt>
              <dd>
                {text(w.emergency_contact_name) || "—"}
                {text(w.emergency_contact_relationship) ? ` (${text(w.emergency_contact_relationship)})` : ""}
                {text(w.emergency_contact_phone) ? ` · ${text(w.emergency_contact_phone)}` : ""}
              </dd>
              <dt>Notes</dt><dd style={{ whiteSpace: "pre-wrap" }}>{text(w.additional_notes) || "—"}</dd>
            </dl>
          </section>
        ) : (
          <section className="card card-pad">
            <h2>Personal details</h2>
            <p className="muted">Home address, emergency contact and notes are only shown to the office and to the worker themself.</p>
          </section>
        )}
      </div>

      {w.protected && <ProtectedCard worker={w} onChanged={worker.reload} />}
      <LoginCard worker={w} onChanged={worker.reload} onMessage={setMessage} />
      <CertsCard workerId={w.id} workerName={w.name} />

      {(can("workers", "archive") || can("workers", "delete")) && (
        <section className="card card-pad stack">
          <h2>Roster status</h2>
          <p className="muted small">
            Inactive keeps the person on file but off pickers. Archive is for people who have left: the record and its history are kept.
            Delete is for records made by mistake.
          </p>
          <div className="row">
            {canEdit && w.status !== "archived" && (
              <button type="button" className="btn" onClick={async () => {
                try {
                  worker.setData({ ...w, ...(await api<Worker>(`/api/workers/${workerId}`, { method: "PATCH", body: { active: !w.active } })) });
                  worker.reload();
                } catch (e) { setMessage({ tone: "bad", text: errorMessage(e) }); }
              }}>
                {w.active ? "Mark inactive" : "Mark active"}
              </button>
            )}
            {can("workers", "archive") && (w.status === "archived"
              ? <button type="button" className="btn" onClick={() => void act(`/api/workers/${workerId}/unarchive`, "POST", "Returned from the archive.")}>Return from archive</button>
              : <button type="button" className="btn" onClick={() => void act(`/api/workers/${workerId}/archive`, "POST", "Archived.")}>Archive</button>)}
            {can("workers", "delete") && (
              <button type="button" className="btn danger" onClick={() => {
                if (window.confirm(`Delete the worker record for ${w.name}? Any login is unlinked but not removed.`)) {
                  void act(`/api/workers/${workerId}`, "DELETE", "Deleted.", () => navigate("/app/workers"));
                }
              }}>Delete</button>
            )}
          </div>
        </section>
      )}

      {can("workers", "audit_view") && <HistoryCard workerId={w.id} timezone={tz} stamp={w.updated_at as string} />}
      {editing && <EditWorker worker={w} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); setMessage({ tone: "ok", text: "Details saved." }); worker.reload(); }} />}
    </>
  );
}

// ---------- edit ----------

const EDIT_FIELDS: { key: string; label: string; type?: string; personal?: boolean }[] = [
  { key: "first_name", label: "First name" },
  { key: "last_name", label: "Last name" },
  { key: "preferred_name", label: "Preferred name" },
  { key: "position", label: "Position" },
  { key: "email", label: "Email", type: "email" },
  { key: "mobile", label: "Mobile", type: "tel" },
  { key: "phone", label: "Phone", type: "tel" },
  { key: "employee_id", label: "Employee number", personal: true },
  { key: "date_employee_added", label: "Start date", type: "date", personal: true },
  { key: "street_address", label: "Street address", personal: true },
  { key: "suburb", label: "Suburb", personal: true },
  { key: "state", label: "State", personal: true },
  { key: "postal_code", label: "Postcode", personal: true },
  { key: "country", label: "Country", personal: true },
  { key: "emergency_contact_name", label: "Emergency contact name", personal: true },
  { key: "emergency_contact_relationship", label: "Relationship", personal: true },
  { key: "emergency_contact_phone", label: "Emergency contact phone", type: "tel", personal: true },
];

function EditWorker({ worker, onClose, onSaved }: { worker: Worker; onClose: () => void; onSaved: () => void }) {
  const companies = useLoad(() => api<{ companies: Company[] }>("/api/org/companies"), []);
  const fields = EDIT_FIELDS.filter((f) => !f.personal || worker.can_see_personal);
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f.key, text(worker[f.key])])));
  const [notes, setNotes] = useState(text(worker.additional_notes));
  const [companyId, setCompanyId] = useState(worker.company_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    // Only what changed is sent, and an emptied box clears the value.
    const body: Record<string, unknown> = {};
    for (const f of fields) if (values[f.key] !== text(worker[f.key])) body[f.key] = values[f.key].trim() || null;
    if (worker.can_see_personal && notes !== text(worker.additional_notes)) body.additional_notes = notes.trim() || null;
    if (companyId !== (worker.company_id ?? "")) body.company_id = companyId || null;
    try {
      if (Object.keys(body).length > 0) await api(`/api/workers/${worker.id}`, { method: "PATCH", body });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={`Edit ${worker.name}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="grid-2">
          {fields.map((f) => (
            <label className="field" key={f.key}>
              {f.label}
              <input type={f.type ?? "text"} required={f.key === "first_name"} value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
            </label>
          ))}
          {companies.data && companies.data.companies.length > 0 && (
            <label className="field">
              Company
              <select value={companyId} onChange={(e) => setCompanyId(e.target.value)}>
                <option value="">Not set</option>
                {companies.data.companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
          )}
        </div>
        {worker.can_see_personal && (
          <label className="field">Notes<textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
        )}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

// ---------- protected details ----------

function ProtectedCard({ worker, onChanged }: { worker: Worker; onChanged: () => void }) {
  const { can } = useAuth();
  const [revealed, setRevealed] = useState<Record<string, string | null> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const canEdit = can("hr_employees", "edit");

  async function reveal(): Promise<Record<string, string | null> | null> {
    setError(null);
    try {
      const result = await api<{ fields: { field: string; value: string | null }[] }>(`/api/workers/${worker.id}/reveal`, { method: "POST" });
      const map = Object.fromEntries(result.fields.map((f) => [f.field, f.value]));
      setRevealed(map);
      return map;
    } catch (e) {
      setError(errorMessage(e));
      return null;
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const body: Record<string, string | null> = {};
    for (const hint of worker.protected ?? []) {
      const before = revealed?.[hint.field] ?? "";
      if ((draft[hint.field] ?? "") !== before) body[hint.field] = draft[hint.field]?.trim() || null;
    }
    try {
      if (Object.keys(body).length > 0) await api(`/api/workers/${worker.id}`, { method: "PATCH", body });
      setEditing(false);
      setRevealed(null);
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Protected details</h2>
        <div className="row">
          {worker.can_reveal_protected && !revealed && <button type="button" className="btn small" onClick={() => void reveal()}>Show full values</button>}
          {revealed && !editing && <button type="button" className="btn small" onClick={() => setRevealed(null)}>Hide</button>}
          {canEdit && worker.can_reveal_protected && !editing && (
            <button type="button" className="btn small" onClick={async () => {
              const map = revealed ?? (await reveal());
              if (map) { setDraft(Object.fromEntries(Object.entries(map).map(([k, v]) => [k, v ?? ""]))); setEditing(true); }
            }}>Edit</button>
          )}
        </div>
      </div>
      <div className="card-pad stack" style={{ paddingTop: 4 }}>
        <p className="small muted">
          Stored encrypted. Normally shown masked; each time the full values are shown, it is written to this worker's history with the name of the person who looked.
        </p>
        {error && <Notice tone="bad">{error}</Notice>}
        {editing ? (
          <form className="form" onSubmit={save}>
            <div className="grid-2">
              {(worker.protected ?? []).map((hint) => (
                <label className="field" key={hint.field}>
                  {hint.label}
                  <input type={hint.field === "birth_date" ? "date" : "text"} autoComplete="off" value={draft[hint.field] ?? ""}
                    onChange={(e) => setDraft({ ...draft, [hint.field]: e.target.value })} />
                </label>
              ))}
            </div>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn" onClick={() => { setEditing(false); setRevealed(null); }}>Cancel</button>
              <button type="submit" className="btn primary">Save</button>
            </div>
          </form>
        ) : (
          <dl className="kv">
            {(worker.protected ?? []).map((hint) => {
              const full = revealed?.[hint.field];
              return (
                <div key={hint.field} style={{ display: "contents" }}>
                  <dt>{hint.label}</dt>
                  <dd>
                    {!hint.recorded ? <span className="muted">Not recorded</span>
                      : revealed ? (hint.field === "birth_date" ? formatDate(full ?? null) : full)
                      : <span>{hint.masked}</span>}
                  </dd>
                </div>
              );
            })}
          </dl>
        )}
      </div>
    </section>
  );
}

// ---------- login ----------

function LoginCard({ worker, onChanged, onMessage }: { worker: Worker; onChanged: () => void; onMessage: (m: Msg) => void }) {
  const { can } = useAuth();
  const manage = can("users", "edit");
  const [mode, setMode] = useState<"create" | "link" | null>(null);
  const [invite, setInvite] = useState<{ link: string; delivery_note: string } | null>(null);
  const user = worker.linked_user;

  return (
    <section className="card card-pad stack">
      <h2>Login</h2>
      {user ? (
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="row" style={{ gap: 8 }}>
            {manage ? <Link to={`/app/settings/users/${user.id}`}>{user.name}</Link> : <span>{user.name}</span>}
            {user.email && <span className="muted">{user.email}</span>}
            <StatusChip status={user.status} />
          </span>
          {manage && (
            <button type="button" className="btn small" onClick={async () => {
              if (!window.confirm("Unlink this login from the worker record? The login itself is kept.")) return;
              try {
                await api(`/api/workers/${worker.id}/link-user`, { method: "DELETE" });
                onMessage({ tone: "ok", text: "Login unlinked." });
                onChanged();
              } catch (e) { onMessage({ tone: "bad", text: errorMessage(e) }); }
            }}>Unlink</button>
          )}
        </div>
      ) : (
        <>
          <p className="muted">
            {worker.is_me ? "This is your record." : "This worker cannot sign in yet."}
            {manage ? " Create a login for them, or link one that already exists." : ""}
          </p>
          {manage && (
            <div className="row">
              <button type="button" className="btn primary" onClick={() => setMode("create")}>Create login</button>
              <button type="button" className="btn" onClick={() => setMode("link")}>Link existing login</button>
            </div>
          )}
        </>
      )}
      {invite && (
        <Notice tone="info">
          <strong>Login created.</strong> {invite.delivery_note}
          <div className="code-box" style={{ marginTop: 8 }}>{invite.link}</div>
          <button type="button" className="btn small" style={{ marginTop: 8 }} onClick={() => void navigator.clipboard?.writeText(invite.link)}>Copy link</button>
        </Notice>
      )}
      {mode === "create" && <CreateLogin worker={worker} onClose={() => setMode(null)} onDone={(result) => { setMode(null); setInvite(result); onChanged(); }} />}
      {mode === "link" && <LinkLogin worker={worker} onClose={() => setMode(null)} onDone={() => { setMode(null); onMessage({ tone: "ok", text: "Login linked." }); onChanged(); }} />}
    </section>
  );
}

function CreateLogin({ worker, onClose, onDone }: { worker: Worker; onClose: () => void; onDone: (r: { link: string; delivery_note: string }) => void }) {
  const roles = useLoad(() => api<{ roles: Role[] }>("/api/admin/roles"), []);
  const [email, setEmail] = useState(worker.email ?? "");
  const [roleId, setRoleId] = useState("worker");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onDone(await api(`/api/workers/${worker.id}/provision-user`, { method: "POST", body: { email, role_id: roleId } }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={`Create a login for ${worker.name}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">Email (their sign-in name)<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="field">
          Role
          <select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
            {(roles.data?.roles ?? []).filter((r) => r.is_active).map((r) => <option key={r.role_id} value={r.role_id}>{r.name}</option>)}
          </select>
        </label>
        <p className="small muted">You will get a one-time invitation link to pass on. Nothing is emailed.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create login"}</button>
        </div>
      </form>
    </Modal>
  );
}

function LinkLogin({ worker, onClose, onDone }: { worker: Worker; onClose: () => void; onDone: () => void }) {
  const users = useLoad(() => api<{ items: User[] }>("/api/users?limit=200"), []);
  const [userId, setUserId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const free = (users.data?.items ?? []).filter((u) => !u.worker_id);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api(`/api/workers/${worker.id}/link-user`, { method: "POST", body: { user_id: userId } });
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Modal title={`Link a login to ${worker.name}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        {users.error ? <Notice tone="bad">{users.error}</Notice> : !users.data ? <Loading /> : free.length === 0 ? (
          <Notice tone="info">Every login is already linked to a worker. Create a new login instead.</Notice>
        ) : (
          <label className="field">
            Login
            <select required value={userId} onChange={(e) => setUserId(e.target.value)}>
              <option value="">Choose…</option>
              {free.map((u) => <option key={u.id} value={u.id}>{u.name} · {u.email}</option>)}
            </select>
          </label>
        )}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!userId}>Link login</button>
        </div>
      </form>
    </Modal>
  );
}

// ---------- certificates ----------

export function ExpiryCell({ cert }: { cert: Cert }) {
  if (cert.no_expiry) return <>Does not expire</>;
  if (!cert.expiry_date) return <span className="muted">Not recorded</span>;
  const days = cert.days_left ?? 0;
  const note = days < 0 ? `${-days} day${days === -1 ? "" : "s"} ago` : days === 0 ? "today" : days <= 60 ? `in ${days} day${days === 1 ? "" : "s"}` : "";
  return <>{formatDate(cert.expiry_date)}{note ? <span className="muted small"> · {note}</span> : null}</>;
}

export function CertsCard({ workerId, workerName, readOnly = false }: { workerId: string; workerName: string; readOnly?: boolean }) {
  const { can } = useAuth();
  const [history, setHistory] = useState(false);
  const certs = useLoad(() => api<{ items: Cert[]; missing: { slug: string; label: string }[] }>(`/api/workers/${workerId}/certifications?history=${history}`), [workerId, history]);
  const [form, setForm] = useState<{ mode: "add" | "renew" | "edit"; cert?: Cert } | null>(null);
  const [message, setMessage] = useState<Msg>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadFor, setUploadFor] = useState<string | null>(null);
  const canEdit = !readOnly && can("certifications", "edit");
  const canDelete = !readOnly && can("certifications", "delete");

  async function upload(file: File | undefined) {
    if (!file || !uploadFor) return;
    const data = new FormData();
    data.append("file", file);
    setMessage(null);
    try {
      await api(`/api/workers/${workerId}/certifications/${uploadFor}/upload`, { form: data });
      setMessage({ tone: "ok", text: "File attached." });
      certs.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  async function post(path: string, method: string, done: string) {
    setMessage(null);
    try {
      await api(path, { method });
      setMessage({ tone: "ok", text: done });
      certs.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Certificates</h2>
        <div className="row">
          <label className="check"><input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} /> Show earlier and archived</label>
          {canEdit && <button type="button" className="btn small primary" onClick={() => setForm({ mode: "add" })}>Add certificate</button>}
        </div>
      </div>
      <input ref={fileInput} type="file" accept="application/pdf,image/png,image/jpeg,image/webp" className="sr-only" aria-label="Choose a certificate file"
        onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
      {message && <div className="card-pad" style={{ paddingBottom: 0 }}><Notice tone={message.tone}>{message.text}</Notice></div>}
      {certs.error ? <div className="card-pad"><LoadError message={certs.error} onRetry={certs.reload} /></div> : !certs.data ? <Loading /> : certs.data.items.length === 0 ? (
        <div className="empty">No certificates recorded{readOnly ? " yet. The office adds them for you." : "."}</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Certificate</th>
                <th scope="col">Number</th>
                <th scope="col">Expires</th>
                <th scope="col">Status</th>
                <th scope="col">File</th>
                {(canEdit || canDelete) && <th scope="col"><span className="sr-only">Actions</span></th>}
              </tr>
            </thead>
            <tbody>
              {certs.data.items.map((c) => (
                <tr key={c.id}>
                  <td>{c.kind.label}{c.notes ? <div className="small muted">{c.notes}</div> : null}</td>
                  <td>{c.number ?? "—"}</td>
                  <td><ExpiryCell cert={c} /></td>
                  <td>
                    {!c.current ? <Chip tone="neutral" icon={false}>Replaced by a renewal</Chip>
                      : c.archived_at ? <Chip tone="dashed" icon={false}>Archived</Chip>
                      : <CertChip status={c.status} label={c.status_label} />}
                  </td>
                  <td>
                    {c.evidence && c.evidence_url
                      ? <button type="button" className="link-btn" onClick={() => void openFile(c.evidence_url!, c.evidence!.name, false).catch((e) => setMessage({ tone: "bad", text: errorMessage(e) }))}>{c.evidence.name}</button>
                      : <span className="muted">None</span>}
                  </td>
                  {(canEdit || canDelete) && (
                    <td>
                      <span className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                        {canEdit && c.current && !c.archived_at && <button type="button" className="btn small" onClick={() => setForm({ mode: "renew", cert: c })}>Renew</button>}
                        {canEdit && <button type="button" className="btn small" onClick={() => { setUploadFor(c.id); fileInput.current?.click(); }}>{c.evidence ? "Replace file" : "Attach file"}</button>}
                        {canEdit && c.current && <button type="button" className="btn small" onClick={() => setForm({ mode: "edit", cert: c })}>Edit</button>}
                        {canEdit && c.current && (c.archived_at
                          ? <button type="button" className="btn small" onClick={() => void post(`/api/workers/certifications/${c.id}/restore`, "POST", "Certificate restored.")}>Restore</button>
                          : <button type="button" className="btn small" onClick={() => void post(`/api/workers/certifications/${c.id}/archive`, "POST", "Certificate archived.")}>Archive</button>)}
                        {canDelete && c.current && (
                          <button type="button" className="btn small danger" onClick={() => {
                            if (window.confirm(`Delete this ${c.kind.label} certificate? Use this only for an entry made by mistake.`)) void post(`/api/workers/certifications/${c.id}`, "DELETE", "Certificate deleted.");
                          }}>Delete</button>
                        )}
                      </span>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {form && (
        <CertForm workerId={workerId} workerName={workerName} mode={form.mode} cert={form.cert} onClose={() => setForm(null)}
          onSaved={(text) => { setForm(null); setMessage({ tone: "ok", text }); certs.reload(); }} />
      )}
    </section>
  );
}

function CertForm({ workerId, workerName, mode, cert, onClose, onSaved }: {
  workerId: string; workerName: string; mode: "add" | "renew" | "edit"; cert?: Cert; onClose: () => void; onSaved: (text: string) => void;
}) {
  const kinds = useLoad(() => api<{ items: CertKind[] }>("/api/certification-kinds"), []);
  const [kind, setKind] = useState("");
  const [number, setNumber] = useState(cert?.number ?? "");
  const [issue, setIssue] = useState(mode === "edit" ? cert?.issue_date ?? "" : "");
  const [expiry, setExpiry] = useState(mode === "edit" ? cert?.expiry_date ?? "" : "");
  const [never, setNever] = useState(mode === "edit" ? Boolean(cert?.no_expiry) : false);
  const [notes, setNotes] = useState(mode === "edit" ? cert?.notes ?? "" : "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const title = mode === "add" ? `Add a certificate for ${workerName}` : mode === "renew" ? `Renew ${cert?.kind.label}` : `Edit ${cert?.kind.label}`;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body = { number: number || null, issue_date: issue || null, expiry_date: never ? null : expiry || null, no_expiry: never, notes: notes || null };
    try {
      if (mode === "add") await api(`/api/workers/${workerId}/certifications`, { method: "POST", body: { ...body, kind } });
      else if (mode === "renew") await api(`/api/workers/certifications/${cert!.id}/renew`, { method: "POST", body });
      else await api(`/api/workers/certifications/${cert!.id}`, { method: "PATCH", body });
      onSaved(mode === "add" ? "Certificate added." : mode === "renew" ? "Renewal recorded. The earlier certificate is kept under “Show earlier and archived”." : "Certificate saved.");
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        {mode === "add" && (
          <label className="field">
            Type
            <select required value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">Choose…</option>
              {(kinds.data?.items ?? []).map((k) => <option key={k.slug} value={k.slug}>{k.label}</option>)}
            </select>
          </label>
        )}
        <label className="field">Certificate or licence number (optional)<input value={number} onChange={(e) => setNumber(e.target.value)} /></label>
        <div className="grid-2">
          <label className="field">Issue date (optional)<input type="date" value={issue} onChange={(e) => setIssue(e.target.value)} /></label>
          <label className="field">Expiry date<input type="date" value={never ? "" : expiry} disabled={never} onChange={(e) => setExpiry(e.target.value)} /></label>
        </div>
        <label className="check"><input type="checkbox" checked={never} onChange={(e) => setNever(e.target.checked)} /> This certificate does not expire</label>
        {!never && !expiry && <p className="small muted">Without an expiry date the certificate shows as “Expiry not recorded” and does not count as valid.</p>}
        <label className="field">Notes (optional)<input value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : mode === "renew" ? "Record renewal" : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

// ---------- history ----------

function HistoryCard({ workerId, timezone, stamp }: { workerId: string; timezone: string; stamp: string }) {
  const [open, setOpen] = useState(false);
  const history = useLoad(
    () => (open ? api<{ items: { id: string; action: string; at: string; actor: string; detail: Record<string, unknown> }[] }>(`/api/workers/${workerId}/history`) : Promise.resolve({ items: [] })),
    [workerId, open, stamp],
  );
  return (
    <section className="card">
      <div className="card-head">
        <h2>History</h2>
        <button type="button" className="btn small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</button>
      </div>
      {open && (history.error ? <div className="card-pad"><LoadError message={history.error} onRetry={history.reload} /></div> : !history.data || history.loading ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">When</th><th scope="col">What</th><th scope="col">By</th></tr></thead>
            <tbody>
              {history.data.items.map((h) => (
                <tr key={h.id}>
                  <td className="num">{formatDateTime(h.at, timezone)}</td>
                  <td>
                    {HISTORY_LABELS[h.action] ?? h.action}
                    {Array.isArray(h.detail.fields) && h.detail.fields.length > 0 ? <span className="muted small"> · {(h.detail.fields as string[]).join(", ").replaceAll("_", " ")}</span> : null}
                  </td>
                  <td>{h.actor}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}

// ---------- the signed-in person's own record (shown on My profile) ----------

const SELF_FIELDS: { key: string; label: string; type?: string }[] = [
  { key: "preferred_name", label: "Preferred name" },
  { key: "mobile", label: "Mobile", type: "tel" },
  { key: "phone", label: "Phone", type: "tel" },
  { key: "email", label: "Contact email", type: "email" },
  { key: "street_address", label: "Street address" },
  { key: "suburb", label: "Suburb" },
  { key: "state", label: "State" },
  { key: "postal_code", label: "Postcode" },
  { key: "emergency_contact_name", label: "Emergency contact name" },
  { key: "emergency_contact_relationship", label: "Relationship" },
  { key: "emergency_contact_phone", label: "Emergency contact phone", type: "tel" },
];

export function MyWorkerRecord() {
  const record = useLoad(() => api<Worker>("/api/workers/me").catch((e) => { if ((e as { status?: number }).status === 404) return null; throw e; }), []);
  const [values, setValues] = useState<Record<string, string> | null>(null);
  const [message, setMessage] = useState<Msg>(null);
  if (record.error) return <LoadError message={record.error} onRetry={record.reload} />;
  if (record.loading && record.data === null) return null;
  const w = record.data;
  if (!w) {
    return (
      <section className="card card-pad">
        <h2>My worker record</h2>
        <p className="muted">Your login is not linked to a worker record yet. Once the office links it, your contact details and certificates appear here.</p>
      </section>
    );
  }
  const current = values ?? Object.fromEntries(SELF_FIELDS.map((f) => [f.key, text(w[f.key])]));

  async function save(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    const body: Record<string, string | null> = {};
    for (const f of SELF_FIELDS) if (current[f.key] !== text(w![f.key])) body[f.key] = current[f.key].trim() || null;
    try {
      if (Object.keys(body).length > 0) record.setData(await api<Worker>("/api/workers/me", { method: "PATCH", body }));
      setValues(null);
      setMessage({ tone: "ok", text: "Your details are saved." });
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <>
      <section className="card">
        <div className="card-head"><h2>My worker record</h2><span className="muted small">{w.position ?? ""}</span></div>
        <form className="form card-pad" style={{ paddingTop: 4 }} onSubmit={save}>
          {message && <Notice tone={message.tone}>{message.text}</Notice>}
          <p className="small muted">You can keep your own contact and emergency details up to date. Your name, position and employment details are changed by the office.</p>
          <div className="grid-2">
            {SELF_FIELDS.map((f) => (
              <label className="field" key={f.key}>
                {f.label}
                <input type={f.type ?? "text"} value={current[f.key]} onChange={(e) => setValues({ ...current, [f.key]: e.target.value })} />
              </label>
            ))}
          </div>
          <div><button type="submit" className="btn primary">Save my details</button></div>
        </form>
      </section>
      <CertsCard workerId={w.id} workerName={w.name} readOnly />
    </>
  );
}
