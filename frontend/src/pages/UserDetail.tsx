import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, formatDateTime, type Matrix, type Role, type Schema, type User } from "../api";
import { useAuth } from "../auth";
import { PermissionMatrix } from "../components/PermissionMatrix";
import { Chip, LoadError, Loading, Modal, Notice, StatusChip, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface PermissionsPayload {
  role: string;
  role_defaults: Matrix;
  overrides: Matrix;
  reasons: Record<string, string>;
  applied_preset_key: string | null;
  payroll_access: { is_owner: boolean };
}

interface AccessStatus {
  state: string;
  last_login_at: string | null;
  last_password_change_at: string | null;
  invite_expires_at: string | null;
  locked_until: string | null;
  failed_login_attempts: number;
  must_change_password: boolean;
}

interface LinkResult {
  link: string;
  expires_at: string;
  delivery_note: string;
}

interface Presets {
  built_in: { key: string; label: string }[];
  custom: { id: string; label: string }[];
}

interface SessionRow {
  jti: string;
  state: string;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
  last_seen_at: string;
  revoke_reason: string | null;
}

const STATE_LABELS: Record<string, string> = {
  active: "Active",
  not_invited: "Not activated · no invitation yet",
  invite_pending: "Not activated · invitation waiting",
  locked: "Locked after wrong passwords",
  disabled: "Disabled",
  deleted: "Deleted",
};

export function UserDetail() {
  const { userId = "" } = useParams();
  const { me, can, reload: reloadMe } = useAuth();
  const navigate = useNavigate();
  const canEdit = can("users", "edit");
  const isAdmin = me?.role_id === "admin";
  const tz = me?.org_timezone ?? "Australia/Hobart";

  const user = useLoad(() => api<User>(`/api/users/${userId}`), [userId]);
  const roles = useLoad(() => api<{ roles: Role[] }>("/api/admin/roles"), []);
  const schema = useLoad(() => api<Schema>("/api/permissions/schema"), []);
  const perms = useLoad(() => api<PermissionsPayload>(`/api/users/${userId}/permissions`), [userId]);
  const presets = useLoad(() => api<Presets>("/api/permission-presets"), []);
  const access = useLoad(() => (isAdmin ? api<AccessStatus>(`/api/users/${userId}/access-status`) : Promise.resolve(null)), [userId, isAdmin]);
  const history = useLoad(
    () => (isAdmin ? api<{ history: SessionRow[] }>(`/api/admin/users/${userId}/session-history?limit=10`) : Promise.resolve(null)),
    [userId, isAdmin],
  );

  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [link, setLink] = useState<LinkResult | null>(null);
  const [settingPassword, setSettingPassword] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Details form
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [roleId, setRoleId] = useState("");
  const [status, setStatus] = useState("");
  const [roleLocked, setRoleLocked] = useState(false);
  useEffect(() => {
    if (user.data) {
      setName(user.data.name);
      setEmail(user.data.email);
      setRoleId(user.data.role_id);
      setStatus(user.data.status);
      setRoleLocked(user.data.role_locked);
    }
  }, [user.data]);

  // Permission overrides draft
  const [draft, setDraft] = useState<Matrix>({});
  const [presetChoice, setPresetChoice] = useState("");
  useEffect(() => {
    if (perms.data) setDraft(perms.data.overrides);
  }, [perms.data]);
  const dirty = perms.data ? JSON.stringify(draft) !== JSON.stringify(perms.data.overrides) : false;

  function refreshAll() {
    user.reload();
    perms.reload();
    access.reload();
    history.reload();
    if (userId === me?.id) void reloadMe();
  }

  async function run(action: () => Promise<unknown>, success: string) {
    setMessage(null);
    try {
      await action();
      setMessage({ tone: "ok", text: success });
      refreshAll();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  if (user.error) return <LoadError message={user.error} onRetry={user.reload} />;
  if (!user.data || !schema.data || !roles.data) return <Loading />;
  const u = user.data;
  const deleted = u.deleted_at !== null;
  const detailsDirty = name !== u.name || email !== u.email || roleId !== u.role_id || status !== u.status || roleLocked !== u.role_locked;

  async function saveDetails(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = {};
    if (name !== u.name) body.name = name;
    if (email !== u.email) body.email = email;
    if (roleId !== u.role_id) body.role_id = roleId;
    if (status !== u.status) body.status = status;
    if (roleLocked !== u.role_locked) body.role_locked = roleLocked;
    await run(() => api(`/api/users/${u.id}`, { method: "PATCH", body }), "Details saved.");
  }

  async function createLink(kind: "invite" | "reset-password") {
    setMessage(null);
    setLink(null);
    try {
      setLink(await api<LinkResult>(`/api/users/${u.id}/${kind}`, { method: "POST" }));
      access.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <Link to="/app/settings/users" className="small">← All users</Link>
          <h1>{u.name}</h1>
          <div className="row">
            <StatusChip status={deleted ? "disabled" : u.status} />
            {deleted && <Chip tone="neutral" icon={false}>Deleted</Chip>}
            <span className="muted">{u.email} · {u.role_name}</span>
          </div>
        </div>
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <div className="grid-2">
        <section className="card">
          <div className="card-head"><h2>Details</h2></div>
          <form className="form card-pad" style={{ paddingTop: 4 }} onSubmit={saveDetails}>
            <label className="field">
              Full name
              <input required value={name} disabled={!canEdit || deleted} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="field">
              Email
              <input type="email" required value={email} disabled={!canEdit || deleted} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="field">
              Role
              <select value={roleId} disabled={!canEdit || deleted} onChange={(e) => setRoleId(e.target.value)}>
                {roles.data.roles
                  .filter((r) => r.is_active || r.role_id === u.role_id)
                  .map((r) => (
                    <option key={r.role_id} value={r.role_id}>
                      {r.name}{r.is_system ? "" : " (custom)"}
                    </option>
                  ))}
              </select>
            </label>
            <label className="field">
              Account status
              <select value={status} disabled={!canEdit || deleted} onChange={(e) => setStatus(e.target.value)}>
                {u.status === "invited" && <option value="invited">Not activated</option>}
                <option value="active">Active</option>
                <option value="disabled">Disabled</option>
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={roleLocked} disabled={!canEdit || deleted} onChange={(e) => setRoleLocked(e.target.checked)} />
              Lock role (skip this person in bulk role changes)
            </label>
            {canEdit && !deleted && (
              <div>
                <button type="submit" className="btn primary" disabled={!detailsDirty}>Save details</button>
              </div>
            )}
          </form>
        </section>

        <section className="card">
          <div className="card-head"><h2>Sign-in and access</h2></div>
          <div className="card-pad stack" style={{ paddingTop: 4 }}>
            {isAdmin && access.data ? (
              <dl className="kv">
                <dt>State</dt>
                <dd>{STATE_LABELS[access.data.state] ?? access.data.state}</dd>
                <dt>Last sign-in</dt>
                <dd className="num">{formatDateTime(access.data.last_login_at, tz)}</dd>
                <dt>Password changed</dt>
                <dd className="num">{formatDateTime(access.data.last_password_change_at, tz)}</dd>
                {access.data.invite_expires_at && (
                  <>
                    <dt>Invitation valid until</dt>
                    <dd className="num">{formatDateTime(access.data.invite_expires_at, tz)}</dd>
                  </>
                )}
                {access.data.must_change_password && (
                  <>
                    <dt>Password</dt>
                    <dd>Must be changed at next sign-in</dd>
                  </>
                )}
              </dl>
            ) : (
              !isAdmin && <p className="muted">Only an admin can create links or set passwords.</p>
            )}

            {link && (
              <div className="stack">
                <Notice tone="warn">{link.delivery_note} Valid until {formatDateTime(link.expires_at, tz)}. It is shown only once.</Notice>
                <div className="code-box" data-testid="one-time-link">{link.link}</div>
                <div>
                  <button type="button" className="btn small" onClick={() => void navigator.clipboard?.writeText(link.link)}>Copy link</button>
                </div>
              </div>
            )}

            {!deleted && (
              <div className="row">
                {isAdmin && u.status === "invited" && (
                  <button type="button" className="btn" onClick={() => void createLink("invite")}>Create invitation link</button>
                )}
                {isAdmin && u.status === "active" && (
                  <button type="button" className="btn" onClick={() => void createLink("reset-password")}>Create password reset link</button>
                )}
                {isAdmin && u.status !== "disabled" && (
                  <button type="button" className="btn" onClick={() => setSettingPassword(true)}>Set temporary password</button>
                )}
                {isAdmin && access.data?.state === "locked" && (
                  <button type="button" className="btn" onClick={() => void run(() => api(`/api/users/${u.id}/unlock`, { method: "POST" }), "Account unlocked.")}>
                    Unlock account
                  </button>
                )}
                {canEdit && (
                  <button type="button" className="btn" onClick={() => void run(() => api(`/api/users/${u.id}/force-signout`, { method: "POST" }), "Signed out of all devices.")}>
                    Sign out everywhere
                  </button>
                )}
                {can("users", "delete") && u.id !== me?.id && (
                  <button type="button" className="btn danger" onClick={() => setConfirmDelete(true)}>Delete user</button>
                )}
              </div>
            )}
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Permissions for this person</h2>
            <p className="small muted">
              Starts from the {u.role_name} role. Allow or Deny here wins over the role. Click a cell to change it.
            </p>
          </div>
          {dirty && <Chip tone="warn">Unsaved changes</Chip>}
        </div>
        {perms.error ? (
          <div className="card-pad"><LoadError message={perms.error} onRetry={perms.reload} /></div>
        ) : !perms.data ? (
          <Loading />
        ) : (
          <>
            {perms.data.payroll_access.is_owner && (
              <div className="card-pad" style={{ paddingTop: 0 }}>
                <Notice tone="info">This person is the configured payroll owner.</Notice>
              </div>
            )}
            <PermissionMatrix
              schema={schema.data}
              mode="overrides"
              base={perms.data.role_defaults}
              overrides={draft}
              readOnly={!canEdit || deleted}
              canEditSpecial={isAdmin}
              onChange={setDraft}
            />
            {canEdit && !deleted && (
              <div className="toolbar" style={{ borderTop: "1px solid var(--line-soft)" }}>
                <button type="button" className="btn primary" disabled={!dirty}
                  onClick={() => void run(() => api(`/api/users/${u.id}/permissions`, { method: "PUT", body: { overrides: draft } }), "Permissions saved.")}>
                  Save permissions
                </button>
                <button type="button" className="btn" disabled={!dirty} onClick={() => setDraft(perms.data!.overrides)}>Discard changes</button>
                <button type="button" className="btn" disabled={Object.keys(perms.data.overrides).length === 0}
                  onClick={() => void run(() => api(`/api/users/${u.id}/permissions/reset`, { method: "POST" }), "Back to role defaults.")}>
                  Reset to role defaults
                </button>
                <span className="spacer" />
                <label className="field">
                  Match another role or preset
                  <select value={presetChoice} onChange={(e) => setPresetChoice(e.target.value)}>
                    <option value="">Choose…</option>
                    {presets.data?.built_in.map((p) => <option key={p.key} value={`key:${p.key}`}>{p.label}</option>)}
                    {presets.data?.custom.map((p) => <option key={p.id} value={`id:${p.id}`}>{p.label} (preset)</option>)}
                  </select>
                </label>
                <button type="button" className="btn" disabled={!presetChoice}
                  onClick={() => {
                    const [kind, value] = presetChoice.split(/:(.*)/s);
                    const body = kind === "key" ? { preset_key: value } : { preset_id: value };
                    void run(() => api(`/api/users/${u.id}/permissions/apply-preset`, { method: "POST", body }), "Preset applied. The role itself is unchanged.");
                    setPresetChoice("");
                  }}>
                  Apply
                </button>
              </div>
            )}
          </>
        )}
      </section>

      {isAdmin && history.data && (
        <section className="card">
          <div className="card-head"><h2>Recent sessions</h2></div>
          {history.data.history.length === 0 ? (
            <div className="empty">This person has not signed in yet.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th scope="col">Signed in</th><th scope="col">Last active</th><th scope="col">State</th><th scope="col">Address</th><th scope="col">Device</th></tr>
                </thead>
                <tbody>
                  {history.data.history.map((s) => (
                    <tr key={s.jti}>
                      <td className="num">{formatDateTime(s.created_at, tz)}</td>
                      <td className="num">{formatDateTime(s.last_seen_at, tz)}</td>
                      <td>
                        {s.state === "active" ? <Chip tone="ok">Active</Chip> : <Chip tone="neutral" icon={false}>{s.state === "revoked" ? `Ended${s.revoke_reason ? ` · ${s.revoke_reason.replaceAll("_", " ")}` : ""}` : "Expired"}</Chip>}
                      </td>
                      <td className="num">{s.ip ?? "—"}</td>
                      <td className="small muted">{(s.user_agent ?? "—").slice(0, 60)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {settingPassword && (
        <SetPassword userId={u.id} onClose={() => setSettingPassword(false)}
          onDone={() => { setSettingPassword(false); setMessage({ tone: "ok", text: "Temporary password set. They must change it at next sign-in." }); refreshAll(); }} />
      )}

      {confirmDelete && (
        <Modal title="Delete this user?" onClose={() => setConfirmDelete(false)}>
          <p>{u.name} will be signed out and will no longer be able to sign in. Their history is kept.</p>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={() => setConfirmDelete(false)}>Cancel</button>
            <button type="button" className="btn danger"
              onClick={async () => {
                try {
                  await api(`/api/users/${u.id}`, { method: "DELETE" });
                  navigate("/app/settings/users");
                } catch (e) {
                  setConfirmDelete(false);
                  setMessage({ tone: "bad", text: errorMessage(e) });
                }
              }}>
              Delete user
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function SetPassword({ userId, onClose, onDone }: { userId: string; onClose: () => void; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/api/users/${userId}/set-password`, { method: "POST", body: { password, must_change_password: true } });
      onDone();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title="Set a temporary password" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">
          Temporary password
          <input type="text" autoComplete="off" required minLength={8} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} />
          <span className="hint">At least 8 characters. The person will be asked to change it when they sign in, and is signed out of other devices.</span>
        </label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Set password"}</button>
        </div>
      </form>
    </Modal>
  );
}
