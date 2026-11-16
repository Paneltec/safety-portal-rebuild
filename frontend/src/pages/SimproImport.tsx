import { useState } from "react";
import { Link } from "react-router-dom";
import { api, formatDateTime } from "../api";
import { useAuth } from "../auth";
import { Chip, Loading, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface Run {
  id: string;
  resource: "workers" | "sites" | "suppliers";
  resource_label: string;
  simulated: boolean;
  dry_run: boolean;
  status: "previewed" | "committed" | "partial" | "failed";
  counts: { created?: number; updated?: number; unchanged?: number; kept_local?: number; needs_review?: number; skipped?: number };
  companies: { company_id: string; ok: boolean; fetched: number; error: string | null }[];
  error: string | null;
  finished_at: string | null;
  created?: { name: string }[];
  updated?: { name: string; fields: string[] }[];
  review?: { name: string; match_on: string }[];
  skipped?: { name?: string; reason: string }[];
}

interface ReviewItem {
  id: string;
  resource: "workers" | "sites" | "suppliers";
  resource_label: string;
  simulated: boolean;
  match_on: "email" | "name" | "abn";
  incoming: Record<string, string | null>;
  candidate: (Record<string, string | boolean | null> & { id: string; name: string; available: boolean }) | null;
}

const RESOURCES: { key: Run["resource"]; label: string; needs: string; path: string }[] = [
  { key: "workers", label: "Workers", needs: "workers", path: "/app/workers" },
  { key: "sites", label: "Sites", needs: "sites", path: "/app/sites" },
  { key: "suppliers", label: "Suppliers", needs: "suppliers", path: "/app/suppliers" },
];
const MATCH: Record<string, string> = { email: "the same email address", name: "the same name", abn: "the same ABN" };
const FIELD_LABELS: Record<string, string> = {
  first_name: "First name", last_name: "Last name", name: "Name", email: "Email", phone: "Phone", mobile: "Mobile", position: "Position", abn: "ABN",
  contact_email: "Email", contact_phone: "Phone", address: "Address", suburb: "Suburb", state: "State", postcode: "Postcode",
};

function summary(counts: Run["counts"]): string {
  const parts = [`${counts.created ?? 0} new`, `${counts.updated ?? 0} updated`, `${counts.unchanged ?? 0} unchanged`];
  if (counts.needs_review) parts.push(`${counts.needs_review} to check`);
  if (counts.kept_local) parts.push(`${counts.kept_local} local change${counts.kept_local === 1 ? "" : "s"} kept`);
  if (counts.skipped) parts.push(`${counts.skipped} skipped`);
  return parts.join(", ");
}

/** Import from Simpro: preview first, then import, then answer anything the import would not decide alone. */
export function SimproImport({ simulator }: { simulator: boolean }) {
  const { me, can } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const state = useLoad(() => api<{ resources: Record<string, Run | null>; open_review: number }>("/api/integrations/simpro/last-synced"), []);
  const review = useLoad(() => api<{ items: ReviewItem[] }>("/api/integrations/simpro/review"), []);
  const [preview, setPreview] = useState<Run | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "bad" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const mayRun = can("integrations", "edit");

  async function run(resource: Run["resource"], dryRun: boolean) {
    setBusy(resource);
    setMessage(null);
    try {
      const result = await api<Run>("/api/integrations/simpro/sync", { method: "POST", body: { resource, dry_run: dryRun } });
      if (dryRun) setPreview(result);
      else {
        setPreview(null);
        setMessage(result.status === "failed" ? { tone: "bad", text: `Nothing was imported. ${result.error ?? ""}` }
          : { tone: result.status === "partial" ? "info" : "ok", text: `${result.resource_label} imported: ${summary(result.counts)}.${result.status === "partial" ? " One company could not be read; see below." : ""}` });
        state.reload();
        review.reload();
      }
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    } finally {
      setBusy(null);
    }
  }

  async function resolve(item: ReviewItem, action: "link" | "create" | "ignore") {
    setMessage(null);
    try {
      await api(`/api/integrations/simpro/review/${item.id}/resolve`, { method: "POST", body: { action } });
      setMessage({ tone: "ok", text: action === "link" ? "Linked. The record on file keeps its own details." : action === "create" ? "Added as a separate record." : "Left out. It will not be asked again." });
      review.reload();
      state.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <div className="stack" style={{ borderTop: "1px solid var(--line-soft)", paddingTop: 12 }}>
      <h3 style={{ margin: 0 }}>Import from Simpro</h3>
      {simulator && <Notice tone="info">Simulator: these are invented practice records, marked “simulator” wherever they appear. They are not your Simpro data.</Notice>}
      <span className="small muted">Records are matched by their Simpro company and number, never by name. Details you change in the portal are kept when you import again. Importing a worker does not create a login.</span>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {!state.data ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">What</th><th scope="col">Last import</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {RESOURCES.map((r) => {
                const last = state.data!.resources[r.key];
                const allowed = mayRun && can(r.needs, "edit");
                return (
                  <tr key={r.key}>
                    <td><Link to={r.path}>{r.label}</Link></td>
                    <td className="small">
                      {last ? (
                        <>
                          {formatDateTime(last.finished_at, tz)} · {summary(last.counts)}{last.simulated ? " · simulator" : ""}
                          {last.status === "partial" && <> <Chip tone="warn">One company failed</Chip></>}
                          {last.companies.filter((c) => !c.ok).map((c) => <div key={c.company_id}>Company {c.company_id}: {c.error}</div>)}
                        </>
                      ) : <span className="muted">Never imported</span>}
                    </td>
                    <td className="right">
                      {allowed ? <button type="button" className="btn small" disabled={busy !== null} onClick={() => void run(r.key, true)}>{busy === r.key ? "Checking…" : "Preview import"}</button>
                        : <span className="small muted">Needs edit access to {r.label.toLowerCase()}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {preview && (
        <div className="stack" style={{ padding: 12, border: "1px solid var(--line-strong)", borderRadius: 6 }}>
          <strong>Preview: {preview.resource_label}{preview.simulated ? " (simulator)" : ""}. Nothing has been saved yet.</strong>
          {preview.status === "failed" ? <Notice tone="bad">{preview.error}</Notice> : <span>{summary(preview.counts)}.</span>}
          {preview.companies.filter((c) => !c.ok).map((c) => <Notice key={c.company_id} tone="warn">Company {c.company_id} could not be read: {c.error}</Notice>)}
          {(preview.created ?? []).length > 0 && <span className="small"><strong>New:</strong> {preview.created!.slice(0, 30).map((c) => c.name).join(", ")}{preview.created!.length > 30 ? "…" : ""}</span>}
          {(preview.updated ?? []).length > 0 && <span className="small"><strong>Updated:</strong> {preview.updated!.slice(0, 30).map((u) => `${u.name} (${u.fields.map((f) => FIELD_LABELS[f] ?? f).join(", ").toLowerCase()})`).join("; ")}</span>}
          {(preview.review ?? []).length > 0 && <span className="small"><strong>To check after importing:</strong> {preview.review!.map((r) => `${r.name} (${MATCH[r.match_on]} as a record on file)`).join("; ")}</span>}
          {(preview.skipped ?? []).length > 0 && <span className="small"><strong>Skipped:</strong> {preview.skipped!.slice(0, 30).map((s) => `${s.name ?? "A record"}: ${s.reason}`).join("; ")}</span>}
          <div className="row">
            {preview.status !== "failed" && <button type="button" className="btn primary" disabled={busy !== null} onClick={() => void run(preview.resource, false)}>{busy ? "Importing…" : "Import now"}</button>}
            <button type="button" className="btn" onClick={() => setPreview(null)}>Close preview</button>
          </div>
        </div>
      )}
      {review.data && review.data.items.length > 0 && (
        <div className="stack">
          <h3 style={{ margin: 0 }}>To check ({review.data.items.length})</h3>
          <span className="small muted">Each of these from Simpro looks like a record already on file. The import will not decide for you.</span>
          {review.data.items.map((item) => {
            const fields = Object.keys(item.incoming).filter((k) => k !== "name" && (item.incoming[k] || (item.candidate && item.candidate[k])));
            return (
              <div key={item.id} className="stack" style={{ gap: 8, padding: 12, border: "1px solid var(--line)", borderRadius: 6 }}>
                <span><strong>{item.incoming.name}</strong> from Simpro{item.simulated ? " (simulator)" : ""} has {MATCH[item.match_on]} as <strong>{item.candidate?.name ?? "a record that no longer exists"}</strong> on file ({item.resource_label.toLowerCase()}).</span>
                {item.candidate && fields.length > 0 && (
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th scope="col">Detail</th><th scope="col">From Simpro</th><th scope="col">On file</th></tr></thead>
                      <tbody>
                        {fields.map((f) => (
                          <tr key={f}><td>{FIELD_LABELS[f] ?? f}</td><td>{item.incoming[f] ?? "—"}</td><td>{(item.candidate![f] as string | null) ?? "—"}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {mayRun && can(item.resource, "edit") ? (
                  <div className="row">
                    {item.candidate?.available && <button type="button" className="btn primary" onClick={() => void resolve(item, "link")}>Same {item.resource === "workers" ? "person" : "record"}: link them</button>}
                    <button type="button" className="btn" onClick={() => void resolve(item, "create")}>Different: add separately</button>
                    <button type="button" className="btn" onClick={() => void resolve(item, "ignore")}>Leave out</button>
                  </div>
                ) : <span className="small muted">Answering needs edit access to connections and to {item.resource_label.toLowerCase()}.</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
