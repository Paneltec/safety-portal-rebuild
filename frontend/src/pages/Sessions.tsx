import { useState } from "react";
import { Link } from "react-router-dom";
import { api, formatDateTime } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface SessionRow {
  jti: string;
  user_id: string;
  user_name: string;
  user_email: string;
  ip: string | null;
  user_agent: string | null;
  remember_me: boolean;
  last_seen_at: string;
  expires_at: string;
  is_current: boolean;
}

export function Sessions() {
  const { me } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const sessions = useLoad(() => api<{ sessions: SessionRow[]; count: number }>("/api/admin/active-sessions"), []);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  if (me?.role_id !== "admin") return <Notice tone="warn">Only an admin can see active sessions.</Notice>;
  if (sessions.error) return <LoadError message={sessions.error} onRetry={sessions.reload} />;
  if (!sessions.data) return <Loading />;
  const rows = sessions.data.sessions;
  const others = rows.filter((s) => !s.is_current);

  function toggle(jti: string) {
    const next = new Set(picked);
    if (next.has(jti)) next.delete(jti);
    else next.add(jti);
    setPicked(next);
  }

  async function revoke(ids: string[]) {
    setMessage(null);
    try {
      const result = await api<{ revoked: number }>("/api/admin/active-sessions/bulk-revoke", { method: "POST", body: { ids } });
      setMessage({ tone: "ok", text: `${result.revoked} ${result.revoked === 1 ? "session" : "sessions"} ended.` });
      setPicked(new Set());
      sessions.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Active sessions</h1>
          <p className="muted">Everyone currently signed in to {me.org_name}. Your own session cannot be ended from here.</p>
        </div>
        <div className="row">
          <button type="button" className="btn" disabled={picked.size === 0} onClick={() => void revoke([...picked])}>
            End selected ({picked.size})
          </button>
          <button type="button" className="btn danger" disabled={others.length === 0} onClick={() => void revoke(others.map((s) => s.jti))}>
            Sign everyone else out
          </button>
        </div>
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <section className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col"><span className="sr-only">Select</span></th>
                <th scope="col">Person</th>
                <th scope="col">Last active</th>
                <th scope="col">Expires</th>
                <th scope="col">Address</th>
                <th scope="col">Device</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.jti}>
                  <td>
                    {!s.is_current && (
                      <input type="checkbox" aria-label={`Select session for ${s.user_name}`} checked={picked.has(s.jti)} onChange={() => toggle(s.jti)} style={{ width: 18, height: 18 }} />
                    )}
                  </td>
                  <td>
                    <Link to={`/app/settings/users/${s.user_id}`}>{s.user_name}</Link>{" "}
                    {s.is_current && <Chip tone="info" icon={false}>This session</Chip>}
                    <div className="small muted">{s.user_email}</div>
                  </td>
                  <td className="num">{formatDateTime(s.last_seen_at, tz)}</td>
                  <td className="num">
                    {formatDateTime(s.expires_at, tz)}
                    {s.remember_me && <div className="small muted">Kept signed in</div>}
                  </td>
                  <td className="num">{s.ip ?? "—"}</td>
                  <td className="small muted">{(s.user_agent ?? "—").slice(0, 60)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
