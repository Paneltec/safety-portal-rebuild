import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useLoad } from "../hooks";
import { Loading, Modal, Notice, errorMessage } from "./ui";

interface Scan {
  dry_run: boolean;
  due: number;
  counts: Record<string, number>;
  items: { label: string; outcome: string; recipient?: string }[];
}

const OUTCOME: Record<string, string> = {
  would_prepare: "Will be prepared", already_prepared: "Already prepared", no_email: "No email address on file", no_one_assigned: "No one is assigned to it",
  held: "Prepared, not sent", sent: "Sent", simulated: "Simulated", failed: "Sending failed",
};

/** Prepare expiry reminders: always shows who would get one before anything is prepared. */
export function ReminderButton({ url, what, label = "Expiry reminders" }: { url: string; what: string; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="btn" onClick={() => setOpen(true)}>{label}</button>
      {open && <ReminderModal url={url} what={what} label={label} onClose={() => setOpen(false)} />}
    </>
  );
}

function ReminderModal({ url, what, label, onClose }: { url: string; what: string; label: string; onClose: () => void }) {
  const service = what === "services";
  const preview = useLoad(() => api<Scan>(url, { method: "POST", body: { dry_run: true } }), [url]);
  const [result, setResult] = useState<Scan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const shown = result ?? preview.data;
  const toPrepare = preview.data?.counts.would_prepare ?? 0;

  async function prepare() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api<Scan>(url, { method: "POST", body: { dry_run: false } }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`${label}: ${what}`} onClose={onClose}>
      <div className="stack">
        {error && <Notice tone="bad">{error}</Notice>}
        {preview.error ? <Notice tone="bad">{preview.error}</Notice> : !shown ? <Loading /> : shown.due === 0 ? (
          <p>{service ? "Nothing is due or overdue, so there is nobody to remind." : "Nothing is expiring or recently expired, so there is nobody to remind."}</p>
        ) : (
          <>
            {result
              ? <Notice tone="ok">Done. Each reminder is in the <Link to="/app/settings/outbox">Outbox</Link>, which shows whether it was sent or held.</Notice>
              : <p>{service ? "One email per service that is due soon or overdue, to the person it is assigned to. Nothing is prepared until you press the button, and the same due service is never reminded twice." : "One email per item that is expiring or has recently expired. Nothing is prepared until you press the button, and an item is never reminded twice for the same expiry date."}</p>}
            <div className="table-wrap" style={{ maxHeight: 300, overflowY: "auto" }}>
              <table>
                <thead><tr><th scope="col">Item</th><th scope="col">To</th><th scope="col">{result ? "Result" : "What will happen"}</th></tr></thead>
                <tbody>
                  {shown.items.map((item) => (
                    <tr key={item.label}><td>{item.label}</td><td>{item.recipient ?? "—"}</td><td>{OUTCOME[item.outcome] ?? item.outcome}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>{result ? "Close" : "Cancel"}</button>
          {!result && toPrepare > 0 && <button type="button" className="btn primary" disabled={busy} onClick={() => void prepare()}>{busy ? "Preparing…" : `Prepare ${toPrepare} reminder${toPrepare === 1 ? "" : "s"}`}</button>}
        </div>
      </div>
    </Modal>
  );
}
