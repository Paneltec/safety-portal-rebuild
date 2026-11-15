import { useState } from "react";
import { api, formatDateTime } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";
import { SafeModePanel } from "./Connections";

interface Message {
  id: string;
  channel: "email" | "sms";
  purpose_label: string;
  recipient: string;
  subject: string;
  status: "held" | "queued" | "sent" | "simulated" | "failed" | "cancelled";
  status_label: string;
  reason: string | null;
  attempts: number;
  can_retry: boolean;
  created_at: string;
  sent_at: string | null;
  body_text?: string | null;
  body_stored?: boolean;
  history?: { at: string; outcome: string; detail: string | null }[];
}

const TABS: { key: string; label: string }[] = [
  { key: "held", label: "Not sent" }, { key: "failed", label: "Failed" }, { key: "sent", label: "Sent" },
  { key: "simulated", label: "Simulated" }, { key: "cancelled", label: "Cancelled" },
];
const TONE: Record<string, "ok" | "warn" | "bad" | "info" | "dashed"> = { held: "warn", queued: "info", sent: "ok", simulated: "info", failed: "bad", cancelled: "dashed" };
const OUTCOME: Record<string, string> = { held: "Not sent", sent: "Sent", simulated: "Simulated", failed: "Failed" };

export function Outbox() {
  const { me } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const [tab, setTab] = useState("held");
  const list = useLoad(() => api<{ items: Message[]; counts: Record<string, number>; sees_all: boolean }>(`/api/email/outbox?status=${tab}&limit=200`), [tab]);
  const [open, setOpen] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Outbox</h1>
          <p className="muted">
            Every email and text the portal has prepared, and what happened to each. {list.data && !list.data.sees_all ? "You see the messages you created." : ""}
          </p>
        </div>
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <SafeModePanel onChanged={list.reload} />
      <section className="card">
        <div className="tabs" role="tablist" aria-label="Message status">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" className="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}>
              {t.label}{list.data ? ` (${list.data.counts[t.key] ?? 0})` : ""}
            </button>
          ))}
        </div>
        {list.error ? <div className="card-pad"><LoadError message={list.error} onRetry={list.reload} /></div> : !list.data ? <Loading /> : list.data.items.length === 0 ? (
          <div className="empty">No messages in this group.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Prepared</th><th scope="col">Type</th><th scope="col">To</th><th scope="col">About</th><th scope="col">What happened</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {list.data.items.map((m) => (
                  <tr key={m.id}>
                    <td className="num">{formatDateTime(m.created_at, tz)}</td>
                    <td>{m.channel === "sms" ? "Text" : "Email"}</td>
                    <td>{m.recipient}</td>
                    <td>{m.purpose_label}<div className="small muted">{m.subject}</div></td>
                    <td><Chip tone={TONE[m.status]} icon={TONE[m.status] !== "dashed"}>{m.status_label}</Chip>{m.reason && <div className="small">{m.reason}</div>}</td>
                    <td className="right"><button type="button" className="btn small" onClick={() => setOpen(m.id)}>Open</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {open && <Detail id={open} timezone={tz} onClose={() => setOpen(null)} onChanged={(text) => { setOpen(null); setMessage({ tone: "ok", text }); list.reload(); }} />}
    </>
  );
}

function Detail({ id, timezone, onClose, onChanged }: { id: string; timezone: string; onClose: () => void; onChanged: (text: string) => void }) {
  const detail = useLoad(() => api<Message>(`/api/email/outbox/${id}`), [id]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const m = detail.data;
  if (detail.error) return <Modal title="Message" onClose={onClose}><Notice tone="bad">{detail.error}</Notice></Modal>;
  if (!m) return <Modal title="Message" onClose={onClose}><Loading /></Modal>;

  async function run(path: string, method: string, done: (result: Message | null) => string) {
    setBusy(true);
    setError(null);
    try {
      const result = await api<Message | { ok: boolean }>(path, { method });
      onChanged(done("status" in result ? result : null));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={`${m.purpose_label} to ${m.recipient}`} onClose={onClose}>
      <div className="stack">
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8 }}><Chip tone={TONE[m.status]} icon={TONE[m.status] !== "dashed"}>{m.status_label}</Chip><span className="small muted">{m.channel === "sms" ? "Text message" : "Email"} · {m.subject}</span></div>
        {m.reason && <Notice tone={m.status === "failed" ? "bad" : "info"}>{m.reason}</Notice>}
        {m.body_stored ? <div className="code-box" style={{ whiteSpace: "pre-wrap", fontFamily: "inherit" }}>{m.body_text}</div>
          : <p className="small muted">The text of this message is not kept, because it contained a one-time link. To send it again, create a new link where it came from.</p>}
        <div>
          <strong>History</strong>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {(m.history ?? []).map((h, i) => <li key={i} className="small">{formatDateTime(h.at, timezone)} · {OUTCOME[h.outcome] ?? h.outcome}{h.detail ? `: ${h.detail}` : ""}</li>)}
          </ul>
        </div>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          {["held", "failed"].includes(m.status) && <button type="button" className="btn" disabled={busy} onClick={() => void run(`/api/email/outbox/${m.id}/cancel`, "POST", () => "Message cancelled.")}>Cancel message</button>}
          <button type="button" className="btn" disabled={busy} onClick={() => { if (window.confirm("Remove this message from the list?")) void run(`/api/email/outbox/${m.id}`, "DELETE", () => "Removed from the list."); }}>Remove from list</button>
          <button type="button" className="btn" onClick={onClose}>Close</button>
          {m.can_retry && (
            <button type="button" className="btn primary" disabled={busy} onClick={() => void run(`/api/email/outbox/${m.id}/retry`, "POST",
              (result) => result?.status === "sent" ? "Sent." : result?.status === "simulated" ? "Handled by the simulator. Nothing was really sent." : `Still not sent. ${result?.reason ?? ""}`)}>
              {busy ? "Sending…" : "Send now"}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
