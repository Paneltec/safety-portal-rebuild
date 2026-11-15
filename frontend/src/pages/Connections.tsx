import { useState, type FormEvent } from "react";
import { api, formatDateTime } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface FieldSpec {
  name: string;
  label: string;
  kind: "text" | "url" | "email" | "number" | "bool" | "list";
  secret: boolean;
  required: boolean;
  help: string;
  minimum: number | null;
  maximum: number | null;
  value?: unknown;
  is_set?: boolean;
}

interface Connection {
  kind: string;
  label: string;
  purpose: string;
  notes: string[];
  mode: "off" | "simulator" | "live";
  status: { state: string; label: string; detail: string };
  fields: FieldSpec[];
  last_test: { at: string; ok: boolean; simulated: boolean; message: string } | null;
  sync: string | null;
  can_edit: boolean;
}

export interface SafeMode {
  effective: "on" | "off";
  env_locked: boolean;
  org_value: "on" | "off";
  blocked_count: number;
  can_change: boolean;
}

const TONES: Record<string, "ok" | "warn" | "bad" | "info" | "dashed"> = {
  connected: "ok", simulator: "info", error: "bad", incomplete: "warn", untested: "warn", off: "dashed", not_configured: "dashed",
};

export function SafeModePanel({ onChanged }: { onChanged?: () => void }) {
  const status = useLoad(() => api<SafeMode>("/api/admin/comms-safe-mode/status"), []);
  const [error, setError] = useState<string | null>(null);
  const s = status.data;
  if (!s) return null;
  const on = s.effective === "on";

  async function set(mode: "on" | "off") {
    if (mode === "off" && !window.confirm("Turn safe mode off? From then on the portal sends real emails and texts when a connection is live and tested.")) return;
    setError(null);
    try {
      status.setData(await api<SafeMode>("/api/admin/comms-safe-mode", { method: "PATCH", body: { mode } }));
      onChanged?.();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <section className="card card-pad stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Safe mode</h2>
        {on ? <Chip tone="ok">On: nothing is sent</Chip> : <Chip tone="warn">Off: messages are sent</Chip>}
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      <p>
        {on
          ? "While safe mode is on, the portal prepares emails and texts but never sends them. They are kept in the Outbox, and invitation and document links are shown on screen for you to pass on."
          : "Emails and texts are sent when their connection is set to Live and its last test succeeded. Everything else is still held in the Outbox."}
        {s.blocked_count > 0 ? ` ${s.blocked_count} message${s.blocked_count === 1 ? " is" : "s are"} being held by safe mode.` : ""}
      </p>
      {s.env_locked ? (
        <p className="small muted">Safe mode is locked on by the server setting <code>COMMS_SAFE_MODE</code>. To allow sending, change it to <code>off</code> in the server's .env file and restart; this switch then decides.</p>
      ) : s.can_change ? (
        <div>{on ? <button type="button" className="btn" onClick={() => void set("off")}>Turn safe mode off…</button> : <button type="button" className="btn primary" onClick={() => void set("on")}>Turn safe mode on</button>}</div>
      ) : (
        <p className="small muted">Changing this needs the “Comms Safe Mode” permission, which is given to one person at a time under Settings → Users → their permissions. Being an admin is not enough on its own.</p>
      )}
    </section>
  );
}

export function Connections() {
  const { me } = useAuth();
  const list = useLoad(() => api<{ items: Connection[]; encryption: { configured: boolean; message: string | null } }>("/api/integrations"), []);
  const tz = me?.org_timezone ?? "Australia/Hobart";
  if (list.error) return <LoadError message={list.error} onRetry={list.reload} />;
  if (!list.data) return <Loading />;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Connections</h1>
          <p className="muted">The outside systems the portal talks to. “Connected” means the last test with the saved details succeeded; it does not mean data has been imported or a message delivered.</p>
        </div>
      </div>
      {!list.data.encryption.configured && <Notice tone="warn">Passwords and keys cannot be saved yet: {list.data.encryption.message}</Notice>}
      <SafeModePanel />
      {list.data.items.map((c) => <ConnectionCard key={c.kind} initial={c} timezone={tz} />)}
    </>
  );
}

function ConnectionCard({ initial, timezone }: { initial: Connection; timezone: string }) {
  const [c, setC] = useState(initial);
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string | boolean>>(() => fromConnection(initial));
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [clear, setClear] = useState<string[]>([]);
  const [mode, setMode] = useState(initial.mode);
  const [message, setMessage] = useState<{ tone: "ok" | "bad" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  function fromConnection(conn: Connection): Record<string, string | boolean> {
    return Object.fromEntries(conn.fields.filter((f) => !f.secret).map((f) => [f.name,
      f.kind === "bool" ? Boolean(f.value) : f.kind === "list" ? ((f.value as string[] | null) ?? []).join(", ") : f.value === null || f.value === undefined ? "" : String(f.value)]));
  }

  function adopt(next: Connection) {
    setC(next);
    setValues(fromConnection(next));
    setSecrets({});
    setClear([]);
    setMode(next.mode);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    const settings: Record<string, unknown> = {};
    for (const f of c.fields.filter((x) => !x.secret)) {
      const raw = values[f.name];
      settings[f.name] = f.kind === "bool" ? raw : f.kind === "number" ? (raw === "" ? null : Number(raw))
        : f.kind === "list" ? String(raw).split(",").map((x) => x.trim()).filter(Boolean) : raw === "" ? null : raw;
    }
    try {
      // Only secrets that were typed are sent. An empty box means "keep what is saved".
      adopt(await api<Connection>(`/api/integrations/${c.kind}`, { method: "PUT", body: { settings, secrets, clear_secrets: clear, mode } }));
      setMessage({ tone: "ok", text: "Saved." });
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api<{ ok: boolean; simulated: boolean; message: string; connection: Connection }>(`/api/integrations/${c.kind}/test-connection`, { method: "POST" });
      adopt(result.connection);
      setMessage({ tone: result.simulated ? "info" : result.ok ? "ok" : "bad", text: result.message });
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!window.confirm(`Remove the saved ${c.label} details? The connection is switched off.`)) return;
    try {
      adopt(await api<Connection>(`/api/integrations/${c.kind}`, { method: "DELETE" }));
      setMessage({ tone: "ok", text: "Saved details removed." });
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>{c.label}</h2>
          <span className="small muted">{c.purpose}</span>
        </div>
        <div className="row">
          <Chip tone={TONES[c.status.state] ?? "dashed"} icon={(TONES[c.status.state] ?? "dashed") !== "dashed"}>{c.status.label}</Chip>
          <button type="button" className="btn small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Close" : c.can_edit ? "Set up" : "View"}</button>
        </div>
      </div>
      <div className="card-pad stack" style={{ paddingTop: 0 }}>
        <span className="small">{c.status.detail}</span>
        {c.last_test && <span className="small muted">Last test {formatDateTime(c.last_test.at, timezone)}{c.last_test.simulated ? " (simulator)" : ""}. {c.sync ?? ""}</span>}
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        {open && (
          <form className="form" onSubmit={save}>
            <label className="field">
              This connection is
              <select value={mode} disabled={!c.can_edit} onChange={(e) => setMode(e.target.value as Connection["mode"])}>
                <option value="off">Switched off</option>
                <option value="simulator">Simulator (practice only: no real system is contacted)</option>
                <option value="live">Live (uses the details below)</option>
              </select>
            </label>
            <div className="grid-2">
              {c.fields.map((f) => f.secret ? (
                <div key={f.name} className="stack" style={{ gap: 4 }}>
                  <label className="field">
                    {f.label}{f.required ? "" : " (optional)"}
                    <input type="password" autoComplete="new-password" disabled={!c.can_edit || clear.includes(f.name)} value={secrets[f.name] ?? ""}
                      placeholder={f.is_set ? "Saved. Leave empty to keep it." : ""} onChange={(e) => setSecrets({ ...secrets, [f.name]: e.target.value })} />
                    <span className="hint">{f.is_set ? "A value is saved. It is never shown again." : "Nothing saved yet."} {f.help}</span>
                  </label>
                  {f.is_set && c.can_edit && (
                    <label className="check"><input type="checkbox" checked={clear.includes(f.name)} onChange={(e) => setClear(e.target.checked ? [...clear, f.name] : clear.filter((x) => x !== f.name))} /> Remove the saved value</label>
                  )}
                </div>
              ) : f.kind === "bool" ? (
                <label className="check" key={f.name} style={{ alignSelf: "end", minHeight: 44 }}>
                  <input type="checkbox" disabled={!c.can_edit} checked={Boolean(values[f.name])} onChange={(e) => setValues({ ...values, [f.name]: e.target.checked })} /> {f.label}
                </label>
              ) : (
                <label className="field" key={f.name}>
                  {f.label}{f.required || f.label.includes("optional") ? "" : " (optional)"}
                  <input type={f.kind === "number" ? "number" : f.kind === "email" ? "email" : f.kind === "url" ? "url" : "text"} disabled={!c.can_edit}
                    step={f.kind === "number" ? "any" : undefined} min={f.minimum ?? undefined} max={f.maximum ?? undefined}
                    value={String(values[f.name] ?? "")} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
                  {(f.help || f.kind === "list") && <span className="hint">{f.help}{f.kind === "list" ? " Separate with commas." : ""}</span>}
                </label>
              ))}
            </div>
            {c.notes.map((note) => <span key={note} className="small muted">{note}</span>)}
            {c.can_edit && (
              <div className="row">
                <button type="submit" className="btn primary" disabled={busy}>{busy ? "Working…" : "Save"}</button>
                <button type="button" className="btn" disabled={busy || c.mode === "off"} onClick={() => void test()}>Test connection</button>
                {c.status.state !== "not_configured" && <button type="button" className="btn danger" disabled={busy} onClick={() => void disconnect()}>Remove saved details</button>}
              </div>
            )}
            {c.can_edit && c.mode === "off" && <span className="small muted">Save it as Simulator or Live to test it.</span>}
          </form>
        )}
      </div>
    </section>
  );
}
