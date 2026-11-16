import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, NavLink, Navigate, Outlet, useNavigate } from "react-router-dom";
import { api, formatDateTime } from "../api";
import { useAuth } from "../auth";
import { Bell } from "../components/Bell";
import { Chip, LoadError, Loading, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";
import { discard, flush, queued, useQueue } from "../offline";
import { SignedOnNow } from "./Home";

interface Module { key: string; label: string; path: string; allocated: boolean; from: string; available: boolean; permitted: boolean; shown: boolean; why_not: string | null }
interface Modules { bucket: string; bucket_label: string; modules: Module[]; shown: string[] }
const LAYOUT_KEY = "sp:layout";

/**
 * Phone layout when the person chose it (opening /m, or the app installed to the home screen), or on
 * a narrow screen unless they chose the office layout. Decided once when the app opens: turning the
 * phone or resizing the window never swaps the layout, because that would throw away a half-filled form.
 */
let decided: boolean | null = null;
export function usePhoneLayout(): boolean {
  if (decided === null) {
    let chosen: string | null = null;
    try { chosen = localStorage.getItem(LAYOUT_KEY); } catch { chosen = null; }
    decided = chosen === "phone" || (chosen !== "office" && window.matchMedia("(max-width: 720px)").matches);
  }
  return decided;
}

export function setLayout(value: "phone" | "office") {
  decided = value === "phone";
  try { localStorage.setItem(LAYOUT_KEY, value); } catch { /* a private window: the choice lasts for this visit only */ }
}

/** /m is the address to install or bookmark: it switches to the phone layout and goes home. */
export function PhoneEntry() {
  setLayout("phone");
  return <Navigate to="/app/home" replace />;
}

export function PhoneShell() {
  const { me } = useAuth();
  const modules = useLoad(() => api<Modules>("/api/me/mobile-modules"), []);
  const waiting = useQueue(me?.id);
  const shown = new Set(modules.data?.shown ?? []);
  const tabs = [
    { to: "/app/home", label: "Home", show: true },
    { to: "/app/my-work", label: "My work", show: shown.has("daily_jobs") },
    { to: "/app/forms", label: "Forms", show: shown.has("forms") },
    { to: "/app/scan", label: "Scan", show: shown.has("sign_on") || shown.has("plant_vehicles") },
    { to: "/app/more", label: "More", show: true },
  ].filter((t) => t.show);
  return (
    <div className="phone">
      <header className="phone-top">
        <div><strong>{me?.org_name}</strong><div className="small" style={{ opacity: 0.8 }}>{me?.name}</div></div>
        <div className="row" style={{ gap: 8, alignItems: "center" }}>
          {waiting > 0 && <Link to="/app/waiting" className="chip warn" aria-label={`${waiting} waiting to send`}>{waiting} waiting to send</Link>}
          <Bell />
        </div>
      </header>
      {!navigator.onLine && <div className="phone-offline" role="status">No connection. What you save is kept on this phone and sent when you are back online.</div>}
      <main className="phone-main"><Outlet /></main>
      <nav className="phone-tabs" aria-label="Phone menu">
        {tabs.map((t) => <NavLink key={t.to} to={t.to} className={({ isActive }) => `phone-tab${isActive ? " active" : ""}`}>{t.label}</NavLink>)}
      </nav>
    </div>
  );
}

interface HomeData {
  user: { first_name: string; linked_to_worker: boolean }; org: string; today: string; modules: Module[]; todo: { text: string; path: string }[];
  today_job?: { site: string | null; status: string; start_time: string | null } | null; signed_on?: { site: string; since: string }[]; leave_waiting?: number;
}

export function PhoneHome() {
  const { me } = useAuth();
  const home = useLoad(() => api<HomeData>("/api/mobile/home"), []);
  const h = home.data;
  if (home.error) return <LoadError message={home.error} onRetry={home.reload} />;
  if (!h) return <Loading />;
  const quick = h.modules.filter((m) => ["sign_on", "forms", "timesheets", "leave", "payslips", "plant_vehicles"].includes(m.key));
  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>Hello {h.user.first_name}</h1>
      {!h.user.linked_to_worker && <Notice tone="info">Your login is not linked to a worker record yet, so jobs, timesheets and leave are not available. Ask the office to link it.</Notice>}
      {h.todo.map((t) => <Link key={t.text} to={t.path} className="phone-card todo">{t.text}<span aria-hidden="true">›</span></Link>)}
      {h.today_job !== undefined && (
        <Link to="/app/my-work" className="phone-card">
          <span><span className="tile-label">Today's job</span><br />{h.today_job ? <><strong>{h.today_job.site ?? "Site to be confirmed"}</strong>{h.today_job.start_time ? ` · start ${h.today_job.start_time}` : ""}</> : "Nothing has been given to you for today"}</span><span aria-hidden="true">›</span>
        </Link>
      )}
      <SignedOnNow timezone={me?.org_timezone ?? "Australia/Hobart"} />
      <div className="phone-grid">
        {quick.map((m) => <Link key={m.key} to={m.path} className="phone-tile">{m.label}</Link>)}
      </div>
    </div>
  );
}

export function PhoneMore() {
  const { signOut, me } = useAuth();
  const navigate = useNavigate();
  const modules = useLoad(() => api<Modules>("/api/me/mobile-modules"), []);
  const waiting = useQueue(me?.id);
  const [installed] = useState(() => window.matchMedia("(display-mode: standalone)").matches);
  if (modules.error) return <LoadError message={modules.error} onRetry={modules.reload} />;
  if (!modules.data) return <Loading />;
  const seen = new Set<string>();
  const items = modules.data.modules.filter((m) => m.shown && m.path && !seen.has(m.path) && Boolean(seen.add(m.path)));
  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>More</h1>
      {waiting > 0 && <Notice tone="warn">{waiting} item{waiting === 1 ? " is" : "s are"} saved on this phone and waiting to send. They go by themselves when you are back online. <Link to="/app/waiting">See them</Link></Notice>}
      <div className="phone-list">{items.map((m) => <Link key={m.key} to={m.path} className="phone-row">{m.label}<span aria-hidden="true">›</span></Link>)}</div>
      <div className="phone-list">
        <button type="button" className="phone-row" onClick={() => { setLayout("office"); navigate("/app/home"); window.location.reload(); }}>Use the office layout<span aria-hidden="true">›</span></button>
        <button type="button" className="phone-row" onClick={() => void signOut()}>Sign out<span aria-hidden="true">›</span></button>
      </div>
      <p className="muted small">{installed ? "Installed on this phone." : "To keep this on your phone: in your browser's menu choose \"Add to Home Screen\" or \"Install app\"."} There is no Android or iPhone app to download from a store.</p>
    </div>
  );
}

/** What is saved on this phone and not yet with the office. */
export function Waiting() {
  const { me } = useAuth();
  useQueue(me?.id);
  const [msg, setMsg] = useState<string | null>(null);
  const items = queued(me?.id);
  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>Waiting to send</h1>
      <p className="muted" style={{ margin: 0 }}>Work saved on this phone while there was no connection. It is sent once, by itself, when the connection is back.</p>
      {msg && <Notice tone="info">{msg}</Notice>}
      {items.length === 0 ? <Notice tone="ok">Nothing is waiting. Everything has been sent.</Notice> : (
        <>
          <button type="button" className="btn primary" onClick={() => void flush(me?.id ?? "").then((r) => setMsg(r.sent || r.refused ? `${r.sent} sent${r.refused ? `, ${r.refused} not accepted` : ""}.` : "Still no connection. Nothing was lost."))}>Try to send now</button>
          <div className="phone-list">{items.map((i) => (
            <div key={i.id} className="phone-row" style={{ alignItems: "flex-start" }}>
              <span><strong>{i.label}</strong><br /><span className="small muted">Saved {formatDateTime(i.queuedAt, me?.org_timezone ?? "Australia/Hobart")}</span>
                {i.error ? <><br /><span className="small" style={{ color: "var(--bad)" }}>Not accepted: {i.error} Open it again to fix it, then remove this copy.</span></> : <><br /><Chip tone="warn">Waiting to send</Chip></>}</span>
              {i.error && <button type="button" className="btn small" aria-label={`Remove ${i.label}`} onClick={() => discard(i.id)}>Remove</button>}
            </div>))}</div>
        </>
      )}
    </div>
  );
}

type Detector = { detect: (source: HTMLVideoElement) => Promise<{ rawValue: string }[]> };

/** Scan a site sign or an asset label, or type in what is printed under the code. */
export function Scan() {
  const navigate = useNavigate();
  const video = useRef<HTMLVideoElement>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [camera, setCamera] = useState<"idle" | "on" | "unavailable">("idle");
  const supported = "BarcodeDetector" in window && Boolean(navigator.mediaDevices?.getUserMedia);

  async function open(raw: string) {
    setError(null);
    const text = raw.trim();
    const site = text.match(/\/sign-on\/([A-Za-z0-9_-]{8,})/);
    const asset = text.match(/\/scan\/asset\/([A-Za-z0-9_-]{8,})/);
    if (site) return navigate(`/app/site-sign-on/${site[1]}`);
    if (asset) return navigate(`/app/scan/asset/${asset[1]}`);
    if (!/^[A-Za-z0-9_-]{8,}$/.test(text)) return setError("That does not look like a portal code. Scan the QR code, or type the letters and numbers printed under it.");
    try {  // a bare code: see which kind it is
      await api(`/api/assets/scan/${text}`);
      navigate(`/app/scan/asset/${text}`);
    } catch {
      try { await api(`/api/scan/site/${text}`, { anonymous: true }); navigate(`/app/site-sign-on/${text}`); } catch { setError("This code is not recognised. It may have been replaced by a newer label or sign."); }
    }
  }

  useEffect(() => {
    if (camera !== "on") return;
    let stream: MediaStream | null = null;
    let stop = false;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (!video.current) return;
        video.current.srcObject = stream;
        await video.current.play();
        const Ctor = (window as unknown as { BarcodeDetector: new (o: { formats: string[] }) => Detector }).BarcodeDetector;
        const detector = new Ctor({ formats: ["qr_code"] });
        while (!stop) {
          const found = video.current ? await detector.detect(video.current).catch(() => []) : [];
          if (found.length) { stop = true; void open(found[0].rawValue); break; }
          await new Promise((r) => setTimeout(r, 350));
        }
      } catch {
        setCamera("unavailable");
      }
    })();
    return () => { stop = true; stream?.getTracks().forEach((t) => t.stop()); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camera]);

  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>Scan</h1>
      <p className="muted" style={{ margin: 0 }}>Scan the QR code on a site sign to sign on or off, or on a vehicle or plant label to open it.</p>
      {supported && camera !== "unavailable" ? (
        camera === "on" ? <video ref={video} className="phone-video" muted playsInline aria-label="Camera view" /> : <button type="button" className="btn primary" onClick={() => setCamera("on")}>Use the camera</button>
      ) : (
        <Notice tone="info">{camera === "unavailable" ? "The camera could not be used (permission was not given, or it is in use)." : "This browser cannot read QR codes inside the app."} Use your phone's own camera app to scan the code and it will open here, or type the code below.</Notice>
      )}
      <form className="stack" onSubmit={(e: FormEvent) => { e.preventDefault(); void open(typed); }}>
        <label className="field">Or type the code printed under the QR code<input value={typed} onChange={(e) => setTyped(e.target.value)} autoCapitalize="none" autoCorrect="off" spellCheck={false} /></label>
        <button type="submit" className="btn" disabled={!typed.trim()}>Open</button>
      </form>
      {error && <Notice tone="bad">{error}</Notice>}
    </div>
  );
}

// ---------- the office screen that decides what phones show ----------

interface Settings {
  modules: { key: string; label: string; available: boolean }[]; buckets: { key: string; label: string }[]; bucket_settings: Record<string, Record<string, boolean>>;
  role_overrides: { role_id: string; role: string; modules: Record<string, boolean> }[]; user_overrides: { user_id: string; user: string; modules: Record<string, boolean> }[]; note: string;
}

export function PhoneModules() {
  const load = useLoad(() => api<Settings>("/api/settings/mobile-modules"), []);
  const users = useLoad(() => api<{ items: { id: string; name: string }[] }>("/api/users?limit=200").catch(() => ({ items: [] })), []);
  const [draft, setDraft] = useState<Record<string, Record<string, boolean>> | null>(null);
  const [person, setPerson] = useState("");
  const preview = useLoad(() => (person ? api<Modules & { user: { name: string } }>(`/api/mobile/preview-user?user_id=${person}`) : Promise.resolve(null)), [person, load.data]);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const s = load.data;
  if (load.error) return <LoadError message={load.error} onRetry={load.reload} />;
  if (!s) return <Loading />;
  const grid = draft ?? s.bucket_settings;
  const toggle = (bucket: string, key: string, value: boolean) => setDraft({ ...grid, [bucket]: { ...grid[bucket], [key]: value } });
  async function act(fn: () => Promise<unknown>, done: string) {
    setMsg(null);
    try { await fn(); setDraft(null); setMsg({ tone: "ok", text: done }); load.reload(); } catch (e) { setMsg({ tone: "bad", text: errorMessage(e) }); }
  }
  const exception = (key: string, value: string) => act(() => api("/api/settings/mobile-modules/overrides", { method: "PATCH", body: { user_id: person, module_key: key, value: value === "" ? null : value === "on" } }), "Saved for this person.");
  return (
    <>
      <div className="page-head"><div><h1>Phone modules</h1><p className="muted">What each person sees in the phone app.</p></div>
        {draft && <button type="button" className="btn primary" onClick={() => void act(() => api("/api/settings/mobile-modules", { method: "PUT", body: { buckets: draft } }), "Saved. Exceptions for a role or a person were left as they are.")}>Save defaults</button>}</div>
      <Notice tone="info">{s.note}</Notice>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <section className="card">
        <div className="table-wrap"><table>
          <thead><tr><th scope="col">Module</th>{s.buckets.map((b) => <th scope="col" key={b.key}>{b.label}</th>)}</tr></thead>
          <tbody>{s.modules.map((m) => (
            <tr key={m.key}>
              <th scope="row" style={{ fontWeight: 500 }}>{m.label}{!m.available && <> <Chip tone="neutral">Not built yet</Chip></>}</th>
              {s.buckets.map((b) => <td key={b.key}><input type="checkbox" aria-label={`${m.label} for ${b.label}`} checked={Boolean(grid[b.key]?.[m.key])} disabled={!m.available} onChange={(e) => toggle(b.key, m.key, e.target.checked)} /></td>)}
            </tr>))}</tbody>
        </table></div>
      </section>
      <section className="card card-pad stack" aria-label="Check one person">
        <h2 style={{ margin: 0 }}>Check or change one person</h2>
        <label className="field">Person<select value={person} onChange={(e) => setPerson(e.target.value)}><option value="">Choose…</option>{(users.data?.items ?? []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
        {preview.data && (
          <>
            <p className="muted small" style={{ margin: 0 }}>{preview.data.user.name} follows the defaults for {preview.data.bucket_label.toLowerCase()}, except where set below.</p>
            <div className="table-wrap"><table>
              <thead><tr><th scope="col">Module</th><th scope="col">On their phone?</th><th scope="col">For this person</th></tr></thead>
              <tbody>{preview.data.modules.filter((m) => m.available).map((m) => (
                <tr key={m.key}>
                  <td>{m.label}</td>
                  <td>{m.shown ? <Chip tone="ok">Shown</Chip> : <><Chip tone="neutral">Not shown</Chip><div className="muted small">{m.why_not}</div></>}</td>
                  <td><select aria-label={`${m.label} for ${preview.data?.user.name}`} value={m.from === "person" ? (m.allocated ? "on" : "off") : ""} onChange={(e) => void exception(m.key, e.target.value)}>
                    <option value="">Follow the defaults</option><option value="on">Always on</option><option value="off">Always off</option></select></td>
                </tr>))}</tbody>
            </table></div>
          </>
        )}
      </section>
      {(s.role_overrides.length > 0 || s.user_overrides.length > 0) && (
        <section className="card card-pad stack">
          <h2 style={{ margin: 0 }}>Exceptions in place</h2>
          {[...s.role_overrides.map((o) => ({ scope: "role", id: o.role_id, name: `Role: ${o.role}`, modules: o.modules })), ...s.user_overrides.map((o) => ({ scope: "user", id: o.user_id, name: o.user, modules: o.modules }))].map((o) => (
            <div key={o.scope + o.id} className="row" style={{ gap: 12, alignItems: "center" }}>
              <span style={{ flex: 1 }}><strong>{o.name}</strong>: {Object.entries(o.modules).map(([k, v]) => `${s.modules.find((m) => m.key === k)?.label ?? k} ${v ? "on" : "off"}`).join(", ")}</span>
              <button type="button" className="btn small" aria-label={`Remove exceptions for ${o.name}`} onClick={() => void act(() => api(`/api/settings/mobile-modules/overrides/${o.scope}/${o.id}`, { method: "DELETE" }), "Exceptions removed.")}>Remove</button>
            </div>))}
        </section>
      )}
    </>
  );
}
