import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api, formatDateTime, formatDuration, type Attendance, type PublicSite } from "../api";
import { useAuth } from "../auth";
import { SignOnQuestions, toAnswerList, type Answers } from "../components/SignOnQuestions";
import { LoadError, Loading, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface VisitorState {
  visitor_id: string;
  name: string;
  site_name: string;
  signed_in_at: string;
  signed_out_at: string | null;
  on_site: boolean;
}

/** Best effort only: sign-in never waits on, or fails because of, a location reading. */
function readGps(): Promise<{ gps_lat?: number; gps_long?: number; gps_accuracy_m?: number }> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve({});
    const timer = setTimeout(() => resolve({}), 4000);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        clearTimeout(timer);
        resolve({ gps_lat: pos.coords.latitude, gps_long: pos.coords.longitude, gps_accuracy_m: Math.round(pos.coords.accuracy) });
      },
      () => {
        clearTimeout(timer);
        resolve({});
      },
      { maximumAge: 60000, timeout: 3500 },
    );
  });
}

const visitKey = (token: string) => `sp.visit.${token}`;

function GateHeader({ site }: { site: PublicSite }) {
  return (
    <div>
      <div className="small muted">{site.organisation} · site sign-in</div>
      <h1>{site.site_name}</h1>
      {site.address && <div className="muted">{site.address}</div>}
    </div>
  );
}

/** The page behind the gate QR code. No login is needed to see it. */
export function PublicSignOn() {
  const { token = "" } = useParams();
  const { status } = useAuth();
  const site = useLoad(() => api<PublicSite>(`/api/public/site/${token}/form`, { anonymous: true }), [token]);
  const [mode, setMode] = useState<"choose" | "visitor">("choose");
  const [visit, setVisit] = useState<VisitorState | null>(null);
  const [checked, setChecked] = useState(false);

  // If this phone already signed a visitor in here, show that visit so they can sign out.
  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(visitKey(token));
    } catch {
      stored = null;
    }
    if (!stored) {
      setChecked(true);
      return;
    }
    api<VisitorState>(`/api/public/visitor/${stored}?token=${encodeURIComponent(token)}`, { anonymous: true })
      .then((v) => setVisit(v.on_site ? v : null))
      .catch(() => undefined)
      .finally(() => setChecked(true));
  }, [token]);

  function remember(v: VisitorState | null) {
    try {
      if (v && v.on_site) localStorage.setItem(visitKey(token), v.visitor_id);
      else localStorage.removeItem(visitKey(token));
    } catch {
      /* private browsing: the visit still works, it just is not remembered */
    }
    setVisit(v);
  }

  return (
    <div className="auth-page" style={{ alignItems: "flex-start" }}>
      <div className="gate-card">
        {site.error ? (
          <>
            <h1>Sign-in code not recognised</h1>
            <Notice tone="bad">{site.error}</Notice>
          </>
        ) : !site.data || !checked ? (
          <Loading />
        ) : visit ? (
          <VisitorPass site={site.data} visit={visit} token={token} onChange={remember} />
        ) : mode === "visitor" ? (
          <VisitorForm site={site.data} token={token} onBack={() => setMode("choose")} onDone={remember} />
        ) : (
          <>
            <GateHeader site={site.data} />
            <p>Who is signing in?</p>
            <Link className="btn primary big-btn" to={`/app/site-sign-on/${token}`} style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
              I work for {site.data.organisation}
            </Link>
            <span className="small muted" style={{ marginTop: -8 }}>
              {status === "signed-in" ? "You are signed in to the portal on this phone." : "You will be asked for your portal login."}
            </span>
            <button type="button" className="btn big-btn" onClick={() => setMode("visitor")}>I am a visitor or contractor</button>
          </>
        )}
      </div>
    </div>
  );
}

function VisitorForm({ site, token, onBack, onDone }: { site: PublicSite; token: string; onBack: () => void; onDone: (v: VisitorState) => void }) {
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [phone, setPhone] = useState("");
  const [purpose, setPurpose] = useState("");
  const [host, setHost] = useState("");
  const [answers, setAnswers] = useState<Answers>({});
  const [agreed, setAgreed] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  // One reference per form, so a second tap or a retry on a weak signal is the same visit.
  const [ref] = useState(() => crypto.randomUUID());

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblems([]);
    try {
      const gps = await readGps();
      onDone(await api<VisitorState>(`/api/public/visitor/site/${token}/signin`, {
        method: "POST", anonymous: true,
        body: { name, company: company || null, phone: phone || null, purpose: purpose || null, host: host || null,
          induction_acknowledged: agreed, answers: toAnswerList(answers), client_ref: ref, ...gps },
      }));
    } catch (e) {
      const fieldErrors = e instanceof ApiError ? e.fieldErrors.map((f) => f.message) : [];
      setProblems(fieldErrors.length > 0 ? fieldErrors : [errorMessage(e)]);
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      <GateHeader site={site} />
      {problems.length > 0 && (
        <Notice tone="bad">{problems.length === 1 ? problems[0] : <ul style={{ margin: 0, paddingLeft: 18 }}>{problems.map((p) => <li key={p}>{p}</li>)}</ul>}</Notice>
      )}
      <label className="field">Your full name<input required minLength={2} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">Company (optional)<input autoComplete="organization" value={company} onChange={(e) => setCompany(e.target.value)} /></label>
      <label className="field">Mobile number (optional)<input type="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
      <label className="field">Reason for visit (optional)<input value={purpose} onChange={(e) => setPurpose(e.target.value)} /></label>
      <label className="field">Who are you here to see? (optional)<input value={host} onChange={(e) => setHost(e.target.value)} /></label>
      <SignOnQuestions questions={site.questions} answers={answers} onChange={setAnswers} />
      <div className="stack" style={{ gap: 8, padding: 12, border: "1px solid var(--line)", borderRadius: 6, background: "var(--ground)" }}>
        <strong>Site rules</strong>
        <span style={{ whiteSpace: "pre-wrap" }}>{site.visitor_declaration}</span>
        <label className="check"><input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} /> I have read and accept the site rules</label>
      </div>
      <button type="submit" className="btn primary big-btn" disabled={busy || !agreed}>{busy ? "Signing in…" : "Sign in"}</button>
      {!agreed && <span className="small muted">Tick the box above to sign in.</span>}
      <button type="button" className="btn" onClick={onBack}>Back</button>
    </form>
  );
}

function VisitorPass({ site, visit, token, onChange }: { site: PublicSite; visit: VisitorState; token: string; onChange: (v: VisitorState | null) => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signOut() {
    setBusy(true);
    setError(null);
    try {
      onChange(await api<VisitorState>(`/api/public/visitor/${visit.visitor_id}/sign-out?token=${encodeURIComponent(token)}`, { method: "POST", anonymous: true }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <>
      <GateHeader site={site} />
      {error && <Notice tone="bad">{error}</Notice>}
      {visit.on_site ? (
        <>
          <Notice tone="ok"><strong>{visit.name}</strong>, you are signed in. Signed in at {formatDateTime(visit.signed_in_at, site.timezone)}.</Notice>
          <p className="muted">Keep this page, or scan the code again when you leave, to sign out.</p>
          <button type="button" className="btn primary big-btn" disabled={busy} onClick={() => void signOut()}>{busy ? "Signing out…" : "Sign out"}</button>
        </>
      ) : (
        <>
          <Notice tone="ok">You are signed out. Thank you, {visit.name}.</Notice>
          <p className="muted">Signed out at {formatDateTime(visit.signed_out_at, site.timezone)}.</p>
          <button type="button" className="btn" onClick={() => onChange(null)}>Sign in again</button>
        </>
      )}
    </>
  );
}

/** Sign-on for someone with a portal login. Reached from the gate page after signing in. */
export function StaffSignOn() {
  const { token = "" } = useParams();
  const { me } = useAuth();
  const state = useLoad(() => api<PublicSite & { site_id: string; signed_on: Attendance | null }>(`/api/scan/site/${token}/me`), [token]);
  const [answers, setAnswers] = useState<Answers>({});
  const [problems, setProblems] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const tz = me?.org_timezone ?? "Australia/Hobart";

  if (state.error) return <LoadError message={state.error} onRetry={state.reload} />;
  const site = state.data;
  if (!site) return <Loading />;
  const signedOn = site.signed_on;

  async function signOn(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblems([]);
    setMessage(null);
    try {
      const gps = await readGps();
      await api(`/api/scan/site/${token}/sign-on`, { method: "POST", body: { answers: toAnswerList(answers), ...gps } });
      setMessage("You are signed on.");
      state.reload();
    } catch (e) {
      const fieldErrors = e instanceof ApiError ? e.fieldErrors.map((f) => f.message) : [];
      setProblems(fieldErrors.length > 0 ? fieldErrors : [errorMessage(e)]);
    } finally {
      setBusy(false);
    }
  }

  async function signOff() {
    setBusy(true);
    setProblems([]);
    try {
      await api(`/api/scan/site/${token}/sign-off`, { method: "POST" });
      setMessage("You are signed off. Thank you.");
      setAnswers({});
      state.reload();
    } catch (e) {
      setProblems([errorMessage(e)]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="small muted">Site sign-on</p>
          <h1>{site.site_name}</h1>
          {site.address && <p className="muted">{site.address}</p>}
        </div>
      </div>
      <section className="card card-pad stack" style={{ maxWidth: 560 }}>
        {message && <Notice tone="ok">{message}</Notice>}
        {problems.length > 0 && (
          <Notice tone="bad">{problems.length === 1 ? problems[0] : <ul style={{ margin: 0, paddingLeft: 18 }}>{problems.map((p) => <li key={p}>{p}</li>)}</ul>}</Notice>
        )}
        {signedOn ? (
          <>
            <p><strong>{signedOn.name}</strong>, you have been on site since {formatDateTime(signedOn.signed_in_at, tz)} ({formatDuration(signedOn.minutes)}).</p>
            <button type="button" className="btn primary big-btn" disabled={busy} onClick={() => void signOff()}>{busy ? "Signing off…" : "Sign off"}</button>
          </>
        ) : (
          <form className="form" onSubmit={signOn}>
            <p>Signing on as <strong>{me?.name}</strong>.</p>
            <SignOnQuestions questions={site.questions} answers={answers} onChange={setAnswers} />
            <button type="submit" className="btn primary big-btn" disabled={busy}>{busy ? "Signing on…" : "Sign on"}</button>
          </form>
        )}
      </section>
    </>
  );
}
