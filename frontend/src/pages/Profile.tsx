import { useState, type FormEvent } from "react";
import { api, formatDateTime, setToken } from "../api";
import { useAuth } from "../auth";
import { Notice, errorMessage } from "../components/ui";
import { MyWorkerRecord } from "./WorkerDetail";

export function Profile() {
  const { me, reload } = useAuth();
  const [name, setName] = useState(me?.name ?? "");
  const [email, setEmail] = useState(me?.email ?? "");
  const [profileMsg, setProfileMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [passwordMsg, setPasswordMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  if (!me) return null;
  const forced = me.must_change_password;

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    setProfileMsg(null);
    try {
      await api("/api/auth/update-profile", { method: "POST", body: { name, email } });
      await reload();
      setProfileMsg({ tone: "ok", text: "Profile saved." });
    } catch (e) {
      setProfileMsg({ tone: "bad", text: errorMessage(e) });
    }
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    setPasswordMsg(null);
    if (next !== confirm) {
      setPasswordMsg({ tone: "bad", text: "The two new passwords do not match" });
      return;
    }
    try {
      const result = await api<{ access_token: string }>("/api/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      setToken(result.access_token);
      setCurrent("");
      setNext("");
      setConfirm("");
      await reload();
      setPasswordMsg({ tone: "ok", text: "Password changed. Other devices have been signed out." });
    } catch (e) {
      setPasswordMsg({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My profile</h1>
          <p className="muted">
            {me.role_name} · {me.org_name} · session ends {formatDateTime(me.session.expires_at, me.org_timezone)}
          </p>
        </div>
      </div>

      {forced && <Notice tone="warn">You need to choose a new password before you can use the rest of the portal.</Notice>}

      <div className="grid-2">
        {!forced && (
          <section className="card">
            <div className="card-head"><h2>Your details</h2></div>
            <form className="form card-pad" style={{ paddingTop: 4 }} onSubmit={saveProfile}>
              {profileMsg && <Notice tone={profileMsg.tone}>{profileMsg.text}</Notice>}
              <label className="field">
                Full name
                <input required value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="field">
                Email (used to sign in)
                <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
              </label>
              <div>
                <button type="submit" className="btn primary" disabled={name === me.name && email === me.email}>Save details</button>
              </div>
            </form>
          </section>
        )}

        <section className="card">
          <div className="card-head"><h2>Change password</h2></div>
          <form className="form card-pad" style={{ paddingTop: 4 }} onSubmit={changePassword}>
            {passwordMsg && <Notice tone={passwordMsg.tone}>{passwordMsg.text}</Notice>}
            <label className="field">
              Current password
              <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
            </label>
            <label className="field">
              New password
              <input type="password" autoComplete="new-password" required minLength={8} value={next} onChange={(e) => setNext(e.target.value)} />
              <span className="hint">At least 8 characters, different from the current one.</span>
            </label>
            <label className="field">
              Repeat new password
              <input type="password" autoComplete="new-password" required minLength={8} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </label>
            <div>
              <button type="submit" className="btn primary">Change password</button>
            </div>
          </form>
        </section>
      </div>
      {!forced && <MyWorkerRecord />}
    </>
  );
}
