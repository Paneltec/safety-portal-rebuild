import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, formatDateTime, type TokenResponse } from "../api";
import { useAuth } from "../auth";
import { Loading, Notice, errorMessage } from "../components/ui";

interface Branding {
  name: string;
  logo_url: string | null;
}

function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  const [brand, setBrand] = useState<Branding>({ name: "Safety Portal", logo_url: null });
  useEffect(() => {
    api<Branding>("/api/public/branding", { anonymous: true }).then(setBrand).catch(() => undefined);
  }, []);
  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          {brand.logo_url ? <img src={brand.logo_url} alt="" className="brand-img" /> : <div className="brand-logo" aria-hidden="true">LOGO</div>}
          <div>
            <h1 style={{ fontSize: 26 }}>{brand.name}</h1>
            <div className="small muted">{brand.name === "Safety Portal" ? title : `Safety Portal · ${title}`}</div>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Login() {
  const { status, signIn, notice } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  useEffect(() => {
    api<{ needs_setup: boolean }>("/api/setup/status", { anonymous: true })
      .then((result) => setNeedsSetup(result.needs_setup))
      .catch(() => undefined);
  }, []);

  if (needsSetup) return <Navigate to="/setup" replace />;
  if (status === "signed-in") {
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from && from.startsWith("/app") ? from : "/app/home"} replace />;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password, remember);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Sign in">
      {notice && !error && <Notice tone="info">{notice}</Notice>}
      {error && <Notice tone="bad">{error}</Notice>}
      <form className="form" onSubmit={submit}>
        <label className="field">
          Email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          Password
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Keep me signed in on this device
        </label>
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <Link to="/forgot-password">Forgot your password?</Link>
    </AuthCard>
  );
}

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/forgot-password", { method: "POST", body: { email }, anonymous: true });
      setDone(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Reset your password">
      {done ? (
        <Notice tone="info">
          If that email is registered, a reset has been logged. Email delivery is not connected yet, so please ask your
          administrator to give you a reset link.
        </Notice>
      ) : (
        <form className="form" onSubmit={submit}>
          {error && <Notice tone="bad">{error}</Notice>}
          <label className="field">
            Email
            <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Sending…" : "Request reset"}
          </button>
        </form>
      )}
      <Link to="/login">Back to sign in</Link>
    </AuthCard>
  );
}

interface LinkInfo {
  user_email: string;
  user_name: string;
  org_name: string;
  expires_at: string;
}

/** Shared screen for accepting an invitation and for resetting a password. */
export function RedeemLink({ kind }: { kind: "invite" | "reset" }) {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const { adoptToken } = useAuth();
  const navigate = useNavigate();
  const [info, setInfo] = useState<LinkInfo | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!token) {
      setLinkError("This link is incomplete. Ask your administrator for a new one.");
      return;
    }
    api<LinkInfo>(`/api/auth/${kind}/validate`, { method: "POST", body: { token }, anonymous: true })
      .then(setInfo)
      .catch((e) => setLinkError(errorMessage(e)));
  }, [kind, token]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("The two passwords do not match");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api<TokenResponse>(`/api/auth/${kind}/redeem`, {
        method: "POST",
        body: { token, password, confirm_password: confirm },
        anonymous: true,
      });
      await adoptToken(result.access_token);
      navigate("/app/home", { replace: true });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const title = kind === "invite" ? "Set up your account" : "Choose a new password";
  return (
    <AuthCard title={title}>
      {linkError ? (
        <>
          <Notice tone="bad">{linkError}</Notice>
          <Link to="/login">Go to sign in</Link>
        </>
      ) : !info ? (
        <Loading label="Checking your link…" />
      ) : (
        <form className="form" onSubmit={submit}>
          <p>
            {info.user_name} · {info.user_email}
            <br />
            <span className="small muted">
              {info.org_name} · link valid until {formatDateTime(info.expires_at, "Australia/Hobart")}
            </span>
          </p>
          {error && <Notice tone="bad">{error}</Notice>}
          <label className="field">
            New password
            <input type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
            <span className="hint">At least 8 characters.</span>
          </label>
          <label className="field">
            Repeat new password
            <input type="password" autoComplete="new-password" required minLength={8} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </label>
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Saving…" : "Save password and sign in"}
          </button>
        </form>
      )}
    </AuthCard>
  );
}

/** First-run screen: creates the organisation and its account holder. Works once. */
export function Setup() {
  const { adoptToken } = useAuth();
  const navigate = useNavigate();
  const [state, setState] = useState<{ needs_setup: boolean; setup_key_configured: boolean } | null>(null);
  const [form, setForm] = useState({ setup_key: "", org_name: "", name: "", email: "", password: "", confirm_password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ needs_setup: boolean; setup_key_configured: boolean }>("/api/setup/status", { anonymous: true })
      .then(setState)
      .catch((e) => setError(errorMessage(e)));
  }, []);

  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api<TokenResponse>("/api/setup", { method: "POST", body: form, anonymous: true });
      await adoptToken(result.access_token);
      navigate("/app/home", { replace: true });
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  if (state && !state.needs_setup) return <Navigate to="/login" replace />;
  return (
    <AuthCard title="First-time setup">
      {error && <Notice tone="bad">{error}</Notice>}
      {!state ? (
        !error && <Loading label="Checking this portal…" />
      ) : !state.setup_key_configured ? (
        <Notice tone="warn">
          Setup is switched off. Add a SETUP_KEY line to the server's .env file, restart the portal, then reload this page.
        </Notice>
      ) : (
        <form className="form" onSubmit={submit}>
          <p className="small muted">
            This creates your organisation and makes you its account holder: the protected top-level admin and payroll owner.
          </p>
          <label className="field">
            Setup key
            <input type="password" autoComplete="off" required value={form.setup_key} onChange={set("setup_key")} />
            <span className="hint">The SETUP_KEY value from the server's .env file.</span>
          </label>
          <label className="field">
            Organisation name
            <input required value={form.org_name} onChange={set("org_name")} />
          </label>
          <label className="field">
            Your name
            <input required autoComplete="name" value={form.name} onChange={set("name")} />
          </label>
          <label className="field">
            Your email
            <input type="email" required autoComplete="username" value={form.email} onChange={set("email")} />
          </label>
          <label className="field">
            Password
            <input type="password" required minLength={8} autoComplete="new-password" value={form.password} onChange={set("password")} />
            <span className="hint">At least 8 characters.</span>
          </label>
          <label className="field">
            Repeat password
            <input type="password" required minLength={8} autoComplete="new-password" value={form.confirm_password} onChange={set("confirm_password")} />
          </label>
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "Setting up…" : "Create organisation and sign in"}
          </button>
        </form>
      )}
    </AuthCard>
  );
}
