import { useEffect, useState } from "react";
import { api, formatDateTime } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface Check { check: string; ok: boolean; detail: string }
interface Outcome { state: string; at: string | null; checks?: Check[]; reason?: string | null; notes?: string[]; rows?: number; files?: number }
interface Snapshot {
  id: string; status: "creating" | "created" | "failed"; filename: string | null; size_bytes: number | null; sha256: string | null; error: string | null; in_folder: boolean;
  summary: { tables?: number; rows?: number; files?: number; files_missing?: number; files_damaged?: number }; verify: Outcome; rehearsal: Outcome; created_by: string; created_at: string;
}
interface Summary { state: "none" | "fresh" | "ageing" | "old"; newest: string | null; newest_verified: string | null; newest_is_verified: boolean; folder: string; folder_free_bytes: number; keep: number; rehearsal_available: boolean; schedule: string; key_note: string; not_built: string }

function size(n: number | null | undefined): string {
  if (n == null) return "";
  return n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : n < 1024 ** 3 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${(n / 1024 ** 3).toFixed(1)} GB`;
}

const VERIFY: Record<string, { tone: "ok" | "bad" | "warn" | "neutral"; label: string }> = {
  not_checked: { tone: "neutral", label: "Not checked yet" }, verified: { tone: "ok", label: "Verified" }, failed: { tone: "bad", label: "Failed its checks" }, pending: { tone: "warn", label: "Could not be checked" },
};
const REHEARSAL: Record<string, { tone: "ok" | "bad" | "warn" | "neutral"; label: string }> = {
  not_run: { tone: "neutral", label: "Not rehearsed" }, passed: { tone: "ok", label: "Restore rehearsed" }, failed: { tone: "bad", label: "Rehearsal failed" }, unavailable: { tone: "warn", label: "Rehearsal not available" },
};

/** Backups of the whole installation: make one, check it, and rehearse a restore without touching live records. */
export function Backups() {
  const { me } = useAuth();
  const summary = useLoad(() => api<Summary>("/api/backup/summary"), []);
  const list = useLoad(() => api<{ items: Snapshot[] }>("/api/backup/snapshots"), []);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<{ title: string; outcome: Outcome } | null>(null);
  const [tidy, setTidy] = useState<{ remove: { id: string; filename: string; size_bytes: number }[]; frees_bytes: number; rule: string } | null>(null);
  const zone = me?.org_timezone ?? "Australia/Hobart";
  const making = list.data?.items.some((s) => s.status === "creating") ?? false;
  const reload = () => { summary.reload(); list.reload(); };
  useEffect(() => {
    if (!making) return;
    const timer = window.setInterval(reload, 3000);  // only while an archive is being written
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [making]);

  async function act(key: string, fn: () => Promise<unknown>, done: (result: unknown) => string) {
    setBusy(key);
    setMsg(null);
    try { setMsg({ tone: "ok", text: done(await fn()) }); } catch (e) { setMsg({ tone: "bad", text: errorMessage(e) }); }
    setBusy(null);
    reload();
  }
  if (summary.error) return <LoadError message={summary.error} onRetry={summary.reload} />;
  if (!summary.data) return <Loading />;
  const s = summary.data;
  const headline = { none: { tone: "bad", text: "No backup has been made on this server yet." }, fresh: { tone: "ok", text: "A backup was made in the last day." },
    ageing: { tone: "warn", text: "The newest backup is more than a day old." }, old: { tone: "bad", text: "The newest backup is more than a week old." } }[s.state] as { tone: "ok" | "warn" | "bad"; text: string };
  return (
    <>
      <div className="page-head">
        <div><h1>Backups</h1><p className="muted">A backup is one file holding every record and every uploaded file. Making one, checking it, and rehearsing a restore are three separate steps.</p></div>
        <div className="row">
          <button type="button" className="btn" onClick={() => void api<typeof tidy>("/api/backup/retention/preview").then(setTidy).catch((e) => setMsg({ tone: "bad", text: errorMessage(e) }))}>Tidy up old backups</button>
          <button type="button" className="btn primary" disabled={making || busy !== null} onClick={() => void act("make", () => api("/api/backup/snapshots", { method: "POST" }), () => "Backup started. It appears below as Created when it is finished.")}>{making ? "Making a backup…" : "Make a backup now"}</button>
        </div>
      </div>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <Notice tone={headline.tone}>{headline.text}{s.newest && !s.newest_is_verified ? " It has not been verified yet." : ""}{s.newest_verified ? ` Newest verified backup: ${formatDateTime(s.newest_verified, zone)}.` : s.newest ? " No backup has been verified." : ""}</Notice>
      <section className="card card-pad stack">
        <h2 style={{ margin: 0 }}>Where backups go and what to keep safe</h2>
        <dl className="definitions" style={{ margin: 0 }}>
          <div><dt>Folder on the server</dt><dd>{s.folder} ({size(s.folder_free_bytes)} free). Backups cannot be downloaded from this screen: collect them from that folder.</dd></div>
          <div><dt>How often</dt><dd>{s.schedule}</dd></div>
          <div><dt>The encryption key</dt><dd>{s.key_note}</dd></div>
          <div><dt>Not built</dt><dd>{s.not_built}</dd></div>
        </dl>
      </section>
      <section className="card">
        {list.error ? <LoadError message={list.error} onRetry={list.reload} /> : !list.data ? <Loading /> : list.data.items.length === 0 ? <p className="muted card-pad" style={{ margin: 0 }}>No backups yet.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Made</th><th scope="col">What it holds</th><th scope="col">Checked?</th><th scope="col">Restore rehearsed?</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{list.data.items.map((b) => {
              const when = formatDateTime(b.created_at, zone);
              return (
                <tr key={b.id}>
                  <td>{when}<div className="small muted">{b.created_by}</div></td>
                  <td>{b.status === "creating" ? <Chip tone="info">Being made…</Chip> : b.status === "failed" ? <><Chip tone="bad">Failed</Chip><div className="small muted">{b.error}</div></> : (
                    <>{b.summary.rows?.toLocaleString()} records, {b.summary.files} files, {size(b.size_bytes)}
                      <div className="small muted" style={{ overflowWrap: "anywhere" }}>{b.filename}</div>
                      {!b.in_folder && <Chip tone="warn">Not in the backup folder</Chip>}
                      {(b.summary.files_missing ?? 0) > 0 && <div><Chip tone="warn">{b.summary.files_missing} file{b.summary.files_missing === 1 ? " was" : "s were"} already missing</Chip></div>}</>)}</td>
                  <td>{b.status === "created" && <><Chip tone={VERIFY[b.verify.state].tone}>{VERIFY[b.verify.state].label}</Chip>
                    {b.verify.at && <div><button type="button" className="btn small" aria-label={`Check results for the backup made ${when}`} onClick={() => setOpen({ title: "Check results", outcome: b.verify })}>See results</button></div>}</>}</td>
                  <td>{b.status === "created" && <><Chip tone={REHEARSAL[b.rehearsal.state].tone}>{REHEARSAL[b.rehearsal.state].label}</Chip>
                    {b.rehearsal.at && <div><button type="button" className="btn small" aria-label={`Rehearsal results for the backup made ${when}`} onClick={() => setOpen({ title: "Rehearsal results", outcome: b.rehearsal })}>See results</button></div>}</>}</td>
                  <td style={{ textAlign: "right" }}>{b.status === "created" && <span className="row" style={{ justifyContent: "flex-end" }}>
                    <button type="button" className="btn small" disabled={busy !== null} aria-label={`Verify the backup made ${when}`}
                      onClick={() => void act(`v${b.id}`, () => api<Snapshot>(`/api/backup/snapshots/${b.id}/verify`, { method: "POST" }), (r) => `Checked: ${VERIFY[(r as Snapshot).verify.state].label.toLowerCase()}.`)}>{busy === `v${b.id}` ? "Checking…" : "Verify"}</button>
                    <button type="button" className="btn small" disabled={busy !== null || b.verify.state !== "verified" || !s.rehearsal_available} aria-label={`Rehearse a restore of the backup made ${when}`}
                      title={!s.rehearsal_available ? "No scratch database is set up for rehearsals" : b.verify.state !== "verified" ? "Verify this backup first" : undefined}
                      onClick={() => void act(`r${b.id}`, () => api<Snapshot>(`/api/backup/snapshots/${b.id}/rehearse`, { method: "POST" }), (r) => `Rehearsal: ${REHEARSAL[(r as Snapshot).rehearsal.state].label.toLowerCase()}. Your live records were not touched.`)}>{busy === `r${b.id}` ? "Rehearsing…" : "Rehearse a restore"}</button>
                  </span>}</td>
                </tr>);
            })}</tbody>
          </table></div>
        )}
      </section>
      <Notice tone="info">There is no button to restore over the running portal, because that would overwrite current records. A real restore goes into a new, empty database and is done on the server.{!s.rehearsal_available ? " Rehearsals are switched off until a scratch database is set up on the server." : ""}</Notice>
      {open && (
        <Modal title={open.title} onClose={() => setOpen(null)}>
          <div className="stack">
            {open.outcome.at && <p className="muted small" style={{ margin: 0 }}>Run {formatDateTime(open.outcome.at, zone)}</p>}
            {open.outcome.reason && <Notice tone={open.outcome.state === "pending" || open.outcome.state === "unavailable" ? "warn" : "bad"}>{open.outcome.reason}</Notice>}
            <ul className="results">{(open.outcome.checks ?? []).map((c) => <li key={c.check} style={{ padding: "8px 0" }}><Chip tone={c.ok ? "ok" : "bad"}>{c.ok ? "Passed" : "Did not pass"}</Chip> {c.check}{c.detail && <div className="small muted">{c.detail}</div>}</li>)}</ul>
            {(open.outcome.notes ?? []).map((n) => <Notice key={n} tone="warn">{n}</Notice>)}
          </div>
        </Modal>
      )}
      {tidy && (
        <Modal title="Tidy up old backups" onClose={() => setTidy(null)}>
          <div className="stack">
            <p style={{ margin: 0 }}>{tidy.rule}</p>
            {tidy.remove.length === 0 ? <Notice tone="ok">Nothing to remove.</Notice> : (
              <><p style={{ margin: 0 }}>This would delete {tidy.remove.length} backup file{tidy.remove.length === 1 ? "" : "s"} from the server folder and free {size(tidy.frees_bytes)}. Copies you have taken elsewhere are not affected.</p>
                <ul className="small muted" style={{ margin: 0 }}>{tidy.remove.map((r) => <li key={r.id}>{r.filename}</li>)}</ul></>
            )}
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn" onClick={() => setTidy(null)}>Cancel</button>
              {tidy.remove.length > 0 && <button type="button" className="btn primary" onClick={() => { setTidy(null); void act("tidy", () => api<{ removed: number }>("/api/backup/retention/run", { method: "POST" }), (r) => `${(r as { removed: number }).removed} old backup file(s) deleted.`); }}>Delete these files</button>}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

interface Status {
  overall: "ok" | "warn" | "bad"; checks: { label: string; state: "ok" | "warn" | "bad" | "unknown"; detail: string }[];
  connections: { kind: string; mode: string; last_test_at: string | null; last_test_ok: boolean | null }[];
  version: { portal: string; build: string | null; environment: string; python: string }; counts: { organisations: number; logins: number }; not_built: string;
}
const STATE: Record<string, { tone: "ok" | "warn" | "bad" | "neutral"; label: string }> = { ok: { tone: "ok", label: "Fine" }, warn: { tone: "warn", label: "Needs a look" }, bad: { tone: "bad", label: "Problem" }, unknown: { tone: "neutral", label: "Could not be checked" } };

/** A plain health check of the installation. Read-only. */
export function SystemCheck() {
  const { me } = useAuth();
  const load = useLoad(() => api<Status>("/api/system/status"), []);
  const files = useLoad(() => api<{ checked: number; missing: number; items: { id: string; name: string; used_for: string }[]; note: string }>("/api/system/missing-files"), []);
  if (load.error) return <LoadError message={load.error} onRetry={load.reload} />;
  if (!load.data) return <Loading label="Checking…" />;
  const d = load.data;
  return (
    <>
      <div className="page-head"><div><h1>System check</h1><p className="muted">Whether the parts the portal depends on are working. Each line is checked on its own. Nothing here changes anything.</p></div>
        <button type="button" className="btn" onClick={() => { load.reload(); files.reload(); }}>Check again</button></div>
      <Notice tone={d.overall === "ok" ? "ok" : d.overall === "warn" ? "warn" : "bad"}>{d.overall === "ok" ? "Everything checked is fine." : d.overall === "warn" ? "Working, with something to look at below." : "Something below needs fixing."}</Notice>
      <section className="card"><div className="table-wrap"><table>
        <thead><tr><th scope="col">What</th><th scope="col">State</th><th scope="col">Detail</th></tr></thead>
        <tbody>{d.checks.map((c) => <tr key={c.label}><th scope="row" style={{ fontWeight: 600, color: "inherit", fontSize: "inherit" }}>{c.label}</th><td><Chip tone={STATE[c.state].tone}>{STATE[c.state].label}</Chip></td><td>{c.detail}</td></tr>)}
          <tr><th scope="row" style={{ fontWeight: 600, color: "inherit", fontSize: "inherit" }}>Uploaded files</th>
            <td>{files.error ? <Chip tone="neutral">Could not be checked</Chip> : !files.data ? "…" : <Chip tone={files.data.missing ? "bad" : "ok"}>{files.data.missing ? "Problem" : "Fine"}</Chip>}</td>
            <td>{files.data ? (files.data.missing ? `${files.data.missing} of ${files.data.checked} uploaded files in your organisation cannot be found in file storage: ${files.data.items.slice(0, 5).map((i) => i.name).join(", ")}. ${files.data.note}` : `All ${files.data.checked} uploaded files in your organisation are in file storage.`) : files.error}</td></tr>
        </tbody>
      </table></div></section>
      <section className="card card-pad stack">
        <h2 style={{ margin: 0 }}>This installation</h2>
        <dl className="definitions" style={{ margin: 0 }}>
          <div><dt>Portal version</dt><dd>{d.version.portal}{d.version.build ? ` (build ${d.version.build})` : " (build not recorded)"} · {d.version.environment}</dd></div>
          <div><dt>Organisations and logins on this server</dt><dd>{d.counts.organisations} and {d.counts.logins}</dd></div>
          <div><dt>Connections ({me?.org_name})</dt><dd>{d.connections.length === 0 ? "None set up." : d.connections.map((c) => `${c.kind}: ${c.mode}${c.last_test_at ? (c.last_test_ok ? ", last test passed" : ", last test failed") : ", never tested"}`).join(" · ")}</dd></div>
          <div><dt>Not built</dt><dd>{d.not_built}</dd></div>
        </dl>
      </section>
    </>
  );
}
