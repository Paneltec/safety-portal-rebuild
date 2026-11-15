import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatDateTime, type Role, type User } from "../api";
import { useAuth } from "../auth";
import { LoadError, Loading, Modal, Notice, StatusChip, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface UserPage {
  items: User[];
  total: number;
  limit: number;
  offset: number;
}

const PAGE_SIZE = 25;

export function Users() {
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [roleId, setRoleId] = useState("");
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);

  // Search runs shortly after typing stops.
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const roles = useLoad(() => api<{ roles: Role[] }>("/api/admin/roles"), []);
  const users = useLoad(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (query) params.set("q", query);
    if (status) params.set("status", status);
    if (roleId) params.set("role_id", roleId);
    return api<UserPage>(`/api/users?${params}`);
  }, [query, status, roleId, offset]);

  const tz = me?.org_timezone ?? "Australia/Hobart";
  const page = users.data;
  const filtered = Boolean(query || status || roleId);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Users</h1>
          <p className="muted">People who can sign in. A user is separate from a worker record.</p>
        </div>
        {can("users", "edit") && (
          <button type="button" className="btn primary" onClick={() => setAdding(true)}>
            Add user
          </button>
        )}
      </div>

      <section className="card">
        <div className="toolbar">
          <label className="field" style={{ flex: "1 1 220px" }}>
            Search name or email
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
          <label className="field">
            Status
            <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="invited">Not activated</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
          <label className="field">
            Role
            <select value={roleId} onChange={(e) => { setRoleId(e.target.value); setOffset(0); }}>
              <option value="">All</option>
              {roles.data?.roles.map((r) => (
                <option key={r.role_id} value={r.role_id}>{r.name}</option>
              ))}
            </select>
          </label>
          {filtered && (
            <button type="button" className="btn" onClick={() => { setSearch(""); setStatus(""); setRoleId(""); setOffset(0); }}>
              Clear filters
            </button>
          )}
        </div>

        {users.error ? (
          <div className="card-pad"><LoadError message={users.error} onRetry={users.reload} /></div>
        ) : !page ? (
          <Loading />
        ) : page.items.length === 0 ? (
          <div className="empty">{filtered ? "No users match these filters." : "No users yet."}</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Email</th>
                    <th scope="col">Role</th>
                    <th scope="col">Status</th>
                    <th scope="col">Last sign-in</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((u) => (
                    <tr key={u.id} className="clickable">
                      <td>
                        <button type="button" className="link-btn" onClick={() => navigate(`/app/settings/users/${u.id}`)}>
                          {u.name}
                        </button>
                      </td>
                      <td>{u.email}</td>
                      <td>{u.role_name}</td>
                      <td><StatusChip status={u.status} /></td>
                      <td className="num">{formatDateTime(u.last_login_at, tz)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
              <span className="muted small" role="status">
                Showing {page.offset + 1}–{page.offset + page.items.length} of {page.total}
                {filtered ? " matching" : ""} {users.loading ? "· updating…" : ""}
              </span>
              <span className="spacer" />
              <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Previous
              </button>
              <button type="button" className="btn small" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Next
              </button>
            </div>
          </>
        )}
      </section>

      {adding && roles.data && (
        <AddUser
          roles={roles.data.roles.filter((r) => r.is_active)}
          onClose={() => setAdding(false)}
          onCreated={(u) => navigate(`/app/settings/users/${u.id}`)}
        />
      )}
    </>
  );
}

function AddUser({ roles, onClose, onCreated }: { roles: Role[]; onClose: () => void; onCreated: (u: User) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [roleId, setRoleId] = useState("worker");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await api<User>("/api/users", { method: "POST", body: { name, email, role_id: roleId } }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title="Add user" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">
          Full name
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          Email
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          Role
          <select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
            {roles.map((r) => (
              <option key={r.role_id} value={r.role_id}>{r.name}</option>
            ))}
          </select>
        </label>
        <p className="small muted">The account starts as "Not activated". Create an invitation link on the next screen.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Adding…" : "Add user"}</button>
        </div>
      </form>
    </Modal>
  );
}
