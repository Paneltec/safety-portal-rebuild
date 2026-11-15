import { useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, api, formatDate, formatDateTime } from "../api";
import { Chip, Loading, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface PublicDoc {
  slug: string;
  label: string;
  description: string | null;
  already_accepted: boolean;
  rejected_note: string | null;
  uploaded: { name: string; reference: string | null; expiry_date: string | null } | null;
}

interface PublicRenewal {
  organisation: string;
  timezone: string;
  for_name: string;
  kind: "supplier" | "worker";
  message: string | null;
  status: string;
  expires_at: string;
  submitted_at: string | null;
  documents: PublicDoc[];
  can_upload: boolean;
}

/** The page behind a renewal link. No login: the link is the key, and it opens only this request. */
export function Renew() {
  const { token = "" } = useParams();
  const request = useLoad(() => api<PublicRenewal>(`/api/public/renewals/${token}`, { anonymous: true }), [token]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const data = request.data;

  async function send() {
    setBusy(true);
    setError(null);
    try {
      request.setData(await api<PublicRenewal>(`/api/public/renewals/${token}/submit`, { method: "POST", anonymous: true }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page" style={{ alignItems: "flex-start" }}>
      <div className="gate-card" style={{ maxWidth: 620 }}>
        {request.error ? (
          <>
            <h1>This link cannot be used</h1>
            <Notice tone="bad">{request.error}</Notice>
          </>
        ) : !data ? (
          <Loading />
        ) : (
          <>
            <div>
              <div className="small muted">{data.organisation} · documents requested</div>
              <h1>{data.for_name}</h1>
            </div>
            {data.message && <p style={{ whiteSpace: "pre-wrap" }}>{data.message}</p>}
            {error && <Notice tone="bad">{error}</Notice>}
            {data.status === "submitted" ? (
              <Notice tone="ok">Thank you. Your documents were sent on {formatDateTime(data.submitted_at, data.timezone)} and are waiting to be checked by {data.organisation}. You can close this page.</Notice>
            ) : data.status === "expired" ? (
              <Notice tone="warn">This link expired on {formatDateTime(data.expires_at, data.timezone)}. Ask {data.organisation} for a new one.</Notice>
            ) : data.status !== "pending" ? (
              <Notice tone="info">This request is finished. Nothing more is needed with this link.</Notice>
            ) : (
              <p className="muted">Add a PDF or a clear photo for each item, then press “Send documents”. This link works until {formatDateTime(data.expires_at, data.timezone)}.</p>
            )}
            <div className="stack">
              {data.documents.map((doc) => (
                <DocRow key={doc.slug} doc={doc} token={token} canUpload={data.can_upload} onChange={(next) => request.setData(next)} />
              ))}
            </div>
            {data.can_upload && (
              <button type="button" className="btn primary big-btn" disabled={busy} onClick={() => void send()}>{busy ? "Sending…" : "Send documents"}</button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function DocRow({ doc, token, canUpload, onChange }: { doc: PublicDoc; token: string; canUpload: boolean; onChange: (r: PublicRenewal) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [reference, setReference] = useState("");
  const [expiry, setExpiry] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("doc_type", doc.slug);
    form.append("file", file);
    if (reference.trim()) form.append("reference", reference.trim());
    if (expiry) form.append("expiry_date", expiry);
    try {
      onChange(await api<PublicRenewal>(`/api/public/renewals/${token}/upload`, { form, anonymous: true }));
    } catch (e) {
      setError(e instanceof ApiError && e.status === 415 ? "That file type is not accepted. Use a PDF, JPG or PNG." : errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" style={{ gap: 8, padding: 12, border: "1px solid var(--line)", borderRadius: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{doc.label}</strong>
        {doc.already_accepted ? <Chip tone="ok">Already accepted</Chip> : doc.uploaded ? <Chip tone="info">Added</Chip> : <Chip tone="dashed" icon={false}>Still needed</Chip>}
      </div>
      {doc.description && <span className="small muted">{doc.description}</span>}
      {doc.rejected_note && !doc.uploaded && <Notice tone="warn">The last file was not accepted: {doc.rejected_note}</Notice>}
      {error && <Notice tone="bad">{error}</Notice>}
      {doc.uploaded && (
        <span className="small">
          {doc.uploaded.name}{doc.uploaded.reference ? ` · ${doc.uploaded.reference}` : ""}{doc.uploaded.expiry_date ? ` · expires ${formatDate(doc.uploaded.expiry_date)}` : ""}
        </span>
      )}
      {canUpload && !doc.already_accepted && (
        <>
          <div className="grid-2">
            <label className="field">Policy, licence or certificate number (optional)<input value={reference} onChange={(e) => setReference(e.target.value)} /></label>
            <label className="field">Expiry date (optional)<input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} /></label>
          </div>
          <input ref={input} type="file" accept="application/pdf,image/png,image/jpeg,image/webp" className="sr-only" aria-label={`Choose a file for ${doc.label}`}
            onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
          <div><button type="button" className="btn" disabled={busy} onClick={() => input.current?.click()}>{busy ? "Uploading…" : doc.uploaded ? "Replace file" : "Choose file"}</button></div>
        </>
      )}
    </div>
  );
}
