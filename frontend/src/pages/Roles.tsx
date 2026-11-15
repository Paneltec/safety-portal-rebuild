import { useEffect, useState, type FormEvent } from "react";
import { api, formatDateTime, type Matrix, type Role, type Schema } from "../api";
import { useAuth } from "../auth";
import { PermissionMatrix } from "../components/PermissionMatrix";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface AuditEntry {
  id: string;
  action: string;
  detail: Record<string, unknown>;
  at: string;
}

function tokensOf(matrix: Matrix): string[] {
  return Object.entries(matrix).flatMap(([resource, cells]) =>
    Object.entries(cells).filter(([, value]) => value === true).map(([action]) => `${resource}.${action}`),
  );
}

export function Roles() {
  const { me, can } = useAuth();
  const canEdit = can("users", "edit");
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const roles = useLoad(() => api<{ roles: Role[] }>("/api/admin/roles"), []);
  const schema = useLoad(() => api<Schema>("/api/permissions/schema"), []);
  const [selectedId, setSelectedId] = useState("admin");
  const [draft, setDraft] = useState<Matrix>({});
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [creating, setCreating] = useState(false);

  const selected = roles.data?.roles.find((r) => r.role_id === selectedId) ?? roles.data?.roles[0] ?? null;
  const audit = useLoad(
    () => (selected && canEdit ? api<{ entries: AuditEntry[] }>(`/api/admin/roles/${selected.role_id}/audit?limit=10`) : Promise.resolve(null)),
    [selected?.role_id, canEdit, roles.data],
  );
  useEffect(() => {
    if (selected) setDraft(selected.permissions);
  }, [selected]);

  if (roles.error) return <LoadError message={roles.error} onRetry={roles.reload} />;
  if (!roles.data || !schema.data || !selected) return <Loading />;
  const editable = canEdit && !selected.is_system;
  const dirty = JSON.stringify(tokensOf(draft).sort()) !== JSON.stringify([...selected.permission_tokens].sort());

  async function run(action: () => Promise<unknown>, success: string) {
    setMessage(null);
    try {
      await action();
      setMessage({ tone: "ok", text: success });
      roles.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Roles &amp; permissions</h1>
          <p className="muted">Built-in roles are fixed. Create a custom role, or adjust one person on their user page.</p>
        </div>
        {canEdit && (
          <button type="button" className="btn primary" onClick={() => setCreating(true)}>New custom role</button>
        )}
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <section className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th scope="col">Role</th><th scope="col">Type</th><th scope="col">State</th><th scope="col" className="right">People</th></tr>
            </thead>
            <tbody>
              {roles.data.roles.map((r) => (
                <tr key={r.role_id} className="clickable">
                  <td>
                    <button type="button" className="link-btn" aria-current={r.role_id === selected.role_id} onClick={() => { setSelectedId(r.role_id); setMessage(null); }}>
                      {r.name}
                    </button>
                    {r.role_id === selected.role_id && <span className="small muted"> · shown below</span>}
                  </td>
                  <td>{r.is_system ? <Chip tone="neutral" icon={false}>Built-in</Chip> : <Chip tone="info" icon={false}>Custom</Chip>}</td>
                  <td>{r.is_active ? <Chip tone="ok">Active</Chip> : <Chip tone="warn">Inactive</Chip>}</td>
                  <td className="right num">{r.user_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>{selected.name}</h2>
            <p className="small muted">
              {selected.is_system ? "Built-in role · read-only" : "Custom role"} · {selected.user_count} {selected.user_count === 1 ? "person" : "people"}
              {selected.description && !selected.is_system ? ` · ${selected.description}` : ""}
            </p>
          </div>
          {dirty && editable && <Chip tone="warn">Unsaved changes</Chip>}
        </div>
        <PermissionMatrix schema={schema.data} mode="role" base={draft} readOnly={!editable} onChange={setDraft} />
        {editable && (
          <div className="toolbar" style={{ borderTop: "1px solid var(--line-soft)" }}>
            <button type="button" className="btn primary" disabled={!dirty}
              onClick={() => void run(() => api(`/api/admin/roles/${selected.role_id}`, { method: "PATCH", body: { permission_tokens: tokensOf(draft) } }),
                `Saved. The change applies now to ${selected.user_count} ${selected.user_count === 1 ? "person" : "people"}; individual overrides are kept.`)}>
              Save role
            </button>
            <button type="button" className="btn" disabled={!dirty} onClick={() => setDraft(selected.permissions)}>Discard changes</button>
            <span className="spacer" />
            <button type="button" className="btn"
              onClick={() => void run(() => api(`/api/admin/roles/${selected.role_id}`, { method: "PATCH", body: { is_active: !selected.is_active } }),
                selected.is_active ? "Role deactivated. People holding it keep their accounts but lose its access." : "Role activated.")}>
              {selected.is_active ? "Deactivate" : "Activate"}
            </button>
            <button type="button" className="btn danger" disabled={selected.user_count > 0}
              title={selected.user_count > 0 ? "Move people to another role first" : undefined}
              onClick={() => void run(async () => { await api(`/api/admin/roles/${selected.role_id}`, { method: "DELETE" }); setSelectedId("admin"); }, "Role deleted.")}>
              Delete role
            </button>
          </div>
        )}
      </section>

      {canEdit && audit.data && audit.data.entries.length > 0 && (
        <section className="card">
          <div className="card-head"><h2>Recent changes to {selected.name}</h2></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">When</th><th scope="col">Change</th><th scope="col">Detail</th></tr></thead>
              <tbody>
                {audit.data.entries.map((entry) => (
                  <tr key={entry.id}>
                    <td className="num">{formatDateTime(entry.at, tz)}</td>
                    <td>{entry.action.replace("role.", "").replace("_", " ")}</td>
                    <td className="small muted">{JSON.stringify(entry.detail).slice(0, 160)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {creating && (
        <CreateRole onClose={() => setCreating(false)}
          onCreated={(role) => { setCreating(false); setSelectedId(role.role_id); setMessage({ tone: "ok", text: `${role.name} created. Turn on what this role may do, then save.` }); roles.reload(); }} />
      )}
    </>
  );
}

function CreateRole({ onClose, onCreated }: { onClose: () => void; onCreated: (role: Role) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [legacy, setLegacy] = useState("worker");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await api<Role>("/api/admin/roles", { method: "POST", body: { name, description, legacy_role: legacy, permission_tokens: [] } }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title="New custom role" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">
          Role name
          <input required minLength={2} maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          Description (optional)
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="field">
          Phone app behaves like
          <select value={legacy} onChange={(e) => setLegacy(e.target.value)}>
            <option value="worker">Worker</option>
            <option value="supervisor">Supervisor</option>
            <option value="contractor">Contractor</option>
          </select>
        </label>
        <p className="small muted">The role starts with no access. You choose what it can do on the next screen.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create role"}</button>
        </div>
      </form>
    </Modal>
  );
}
