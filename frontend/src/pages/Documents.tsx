import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, formatDate, formatDateTime, formatSize, openFile } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface Folder {
  id: string;
  name: string;
  parent_folder_id: string | null;
  is_system: boolean;
  file_count: number;
  subfolder_count: number;
  path: { id: string; name: string }[];
}

interface Doc {
  id: string;
  folder_id: string;
  filename: string;
  size_bytes: number | null;
  revision: number;
  expiry_date: string | null;
  expired: boolean;
  tags: string[];
  uploaded_by_name: string | null;
  created_at: string;
  archived_at: string | null;
  missing_binary: boolean;
  gone_at: string | null;
  previewable: boolean;
  folder_path?: string;
}

interface Counts {
  folders_active: number;
  files_active: number;
  files_archived: number;
  files_missing_binary: number;
}

type Msg = { tone: "ok" | "bad" | "warn"; text: string } | null;
const BASE = "/api/document-library";
const PAGE = 50;

export function Documents() {
  const { folderId } = useParams();
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const canEdit = can("documents", "edit");
  const canDelete = can("documents", "delete");
  const tz = me?.org_timezone ?? "Australia/Hobart";

  const [showArchived, setShowArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState<Msg>(null);
  const [rejected, setRejected] = useState<{ filename: string; reason: string }[]>([]);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Doc | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setOffset(0);
    setMessage(null);
    setRejected([]);
  }, [folderId]);
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const folder = useLoad(() => (folderId ? api<Folder>(`${BASE}/folders/${folderId}`) : Promise.resolve(null)), [folderId]);
  const folders = useLoad(
    () => api<Folder[]>(folderId ? `${BASE}/folders/${folderId}/subfolders` : `${BASE}/folders`),
    [folderId],
  );
  const files = useLoad(
    () =>
      folderId
        ? api<{ items: Doc[]; total: number }>(`${BASE}/folders/${folderId}/files?limit=${PAGE}&offset=${offset}&include_archived=${showArchived}`)
        : Promise.resolve(null),
    [folderId, offset, showArchived],
  );
  const counts = useLoad(() => api<Counts>(`${BASE}/counts`), [folderId]);
  const results = useLoad(
    () =>
      query
        ? api<{ results: Doc[]; count: number }>(
            `${BASE}/search?q=${encodeURIComponent(query)}${folderId ? `&folder_id=${folderId}` : ""}&include_archived=${showArchived}`,
          )
        : Promise.resolve(null),
    [query, folderId, showArchived],
  );

  function refresh() {
    folders.reload();
    files.reload();
    counts.reload();
    folder.reload();
    if (query) results.reload();
  }

  async function run(action: () => Promise<unknown>, success: string) {
    setMessage(null);
    try {
      await action();
      setMessage({ tone: "ok", text: success });
      refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  async function upload(list: FileList) {
    if (!folderId) return;
    setBusy(true);
    setMessage(null);
    setRejected([]);
    try {
      const form = new FormData();
      for (const file of Array.from(list)) form.append("files", file);
      const result = await api<{ saved: Doc[]; rejected: { filename: string; reason: string }[] }>(`${BASE}/folders/${folderId}/files`, { form });
      setRejected(result.rejected);
      const saved = result.saved.length;
      setMessage(
        saved > 0
          ? { tone: result.rejected.length ? "warn" : "ok", text: `${saved} ${saved === 1 ? "file" : "files"} uploaded${result.rejected.length ? `, ${result.rejected.length} not accepted` : ""}.` }
          : { tone: "bad", text: "No files were uploaded." },
      );
      refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function open(doc: Doc, download: boolean) {
    setMessage(null);
    try {
      await openFile(`${BASE}/files/${doc.id}/download?download=${download ? 1 : 0}`, doc.filename, download || !doc.previewable);
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  if (folder.error) {
    return (
      <div className="stack">
        <LoadError message={folder.error} onRetry={folder.reload} />
        <Link to="/app/documents">Back to the document library</Link>
      </div>
    );
  }
  if (folders.error) return <LoadError message={folders.error} onRetry={folders.reload} />;
  if (!folders.data || (folderId && !folder.data)) return <Loading />;
  const current = folder.data;
  const rows = query ? results.data?.results ?? null : files.data?.items ?? null;
  const total = files.data?.total ?? 0;

  return (
    <>
      <div className="page-head">
        <div>
          <nav aria-label="Folder path" className="small">
            {current ? (
              <>
                <Link to="/app/documents">Document library</Link>
                {current.path.map((p) => (
                  <span key={p.id}> / <Link to={`/app/documents/${p.id}`}>{p.name}</Link></span>
                ))}
              </>
            ) : (
              <span className="muted">Compliance</span>
            )}
          </nav>
          <h1>{current ? current.name : "Document library"}</h1>
          {counts.data && (
            <div className="row small muted" role="status">
              <span>
                {counts.data.folders_active} {counts.data.folders_active === 1 ? "folder" : "folders"} · {counts.data.files_active}{" "}
                {counts.data.files_active === 1 ? "file" : "files"} · {counts.data.files_archived} archived (whole library)
              </span>
              {counts.data.files_missing_binary > 0 && <Chip tone="warn">{counts.data.files_missing_binary} with missing content</Chip>}
            </div>
          )}
        </div>
        {canEdit && (
          <div className="row">
            <button type="button" className="btn" onClick={() => setCreating(true)}>New folder</button>
            {current && !current.is_system && <button type="button" className="btn" onClick={() => setRenaming(true)}>Rename folder</button>}
            {current && canDelete && !current.is_system && (
              <button type="button" className="btn danger"
                onClick={() => void (async () => {
                  try {
                    await api(`${BASE}/folders/${current.id}`, { method: "DELETE" });
                    navigate(current.parent_folder_id ? `/app/documents/${current.parent_folder_id}` : "/app/documents");
                  } catch (e) {
                    setMessage({ tone: "bad", text: errorMessage(e) });
                  }
                })()}>
                Delete folder
              </button>
            )}
            {current && (
              <label className="btn primary" style={{ cursor: busy ? "wait" : "pointer" }}>
                {busy ? "Uploading…" : "Upload files"}
                <input ref={fileInput} type="file" multiple className="sr-only" disabled={busy} onChange={(e) => e.target.files?.length && void upload(e.target.files)} />
              </label>
            )}
          </div>
        )}
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {rejected.length > 0 && (
        <Notice tone="warn">
          Not accepted: {rejected.map((r) => `${r.filename} (${r.reason})`).join("; ")}
        </Notice>
      )}

      <section className="card">
        <div className="toolbar">
          <label className="field" style={{ flex: "1 1 240px" }}>
            {current ? "Search this folder and its subfolders" : "Search all documents"}
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
          <label className="check">
            <input type="checkbox" checked={showArchived} onChange={(e) => { setShowArchived(e.target.checked); setOffset(0); }} />
            Show archived
          </label>
        </div>
      </section>

      {!query && (
        <section className="card">
          <div className="card-head"><h2>Folders</h2></div>
          {folders.data.length === 0 ? (
            <div className="empty">{current ? "No subfolders." : "No folders yet. Create one to start storing documents."}</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr><th scope="col">Folder</th><th scope="col" className="right">Subfolders</th><th scope="col" className="right">Files</th></tr></thead>
                <tbody>
                  {folders.data.map((f) => (
                    <tr key={f.id} className="clickable">
                      <td><Link to={`/app/documents/${f.id}`} style={{ fontWeight: 600 }}>{f.name}</Link></td>
                      <td className="right num">{f.subfolder_count}</td>
                      <td className="right num">{f.file_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {(current || query) && (
        <section className="card">
          <div className="card-head">
            <h2>{query ? `Search results for "${query}"` : "Files"}</h2>
            {query && results.data && <span className="small muted" role="status">{results.data.count} found</span>}
          </div>
          {(query ? results.error : files.error) ? (
            <div className="card-pad"><LoadError message={(query ? results.error : files.error) ?? ""} onRetry={query ? results.reload : files.reload} /></div>
          ) : rows === null ? (
            <Loading />
          ) : rows.length === 0 ? (
            <div className="empty">{query ? "Nothing matches that search." : showArchived ? "No files in this folder." : "No files in this folder yet."}</div>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      {query && <th scope="col">Folder</th>}
                      <th scope="col">Status</th>
                      <th scope="col">Expires</th>
                      <th scope="col" className="right">Size</th>
                      <th scope="col">Uploaded</th>
                      <th scope="col"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((doc) => (
                      <tr key={doc.id}>
                        <td>
                          <button type="button" className="link-btn" disabled={doc.missing_binary || doc.gone_at !== null} onClick={() => void open(doc, false)}>
                            {doc.filename}
                          </button>
                          {doc.revision > 1 && <span className="small muted"> · version {doc.revision}</span>}
                          {doc.tags.length > 0 && <div className="small muted">{doc.tags.join(", ")}</div>}
                        </td>
                        {query && <td className="small">{doc.folder_path}</td>}
                        <td>
                          <span className="row">
                            {doc.gone_at ? <Chip tone="bad">Content lost</Chip> : doc.missing_binary ? <Chip tone="warn">Content missing</Chip> : doc.archived_at ? <Chip tone="neutral" icon={false}>Archived</Chip> : <Chip tone="ok">Available</Chip>}
                            {doc.expired && <Chip tone="bad">Expired</Chip>}
                          </span>
                        </td>
                        <td className="num">{formatDate(doc.expiry_date)}</td>
                        <td className="right num">{formatSize(doc.size_bytes)}</td>
                        <td className="small">
                          <span className="num">{formatDateTime(doc.created_at, tz)}</span>
                          <div className="muted">{doc.uploaded_by_name ?? "—"}</div>
                        </td>
                        <td>
                          <span className="row" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                            <button type="button" className="btn small" disabled={doc.missing_binary || doc.gone_at !== null} onClick={() => void open(doc, true)}>Download</button>
                            {canEdit && <button type="button" className="btn small" onClick={() => setEditing(doc)}>Manage</button>}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!query && (
                <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
                  <span className="muted small" role="status">Showing {offset + 1}–{offset + rows.length} of {total}</span>
                  <span className="spacer" />
                  <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
                  <button type="button" className="btn small" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
                </div>
              )}
            </>
          )}
        </section>
      )}

      {creating && (
        <FolderForm title={current ? `New folder in ${current.name}` : "New folder"} initial="" onClose={() => setCreating(false)}
          onSubmit={async (name) => {
            await api(`${BASE}/folders`, { method: "POST", body: { name, parent_folder_id: folderId ?? null } });
            setCreating(false);
            setMessage({ tone: "ok", text: "Folder created." });
            refresh();
          }} />
      )}
      {renaming && current && (
        <FolderForm title="Rename folder" initial={current.name} onClose={() => setRenaming(false)}
          onSubmit={async (name) => {
            await api(`${BASE}/folders/${current.id}`, { method: "PATCH", body: { name } });
            setRenaming(false);
            setMessage({ tone: "ok", text: "Folder renamed." });
            refresh();
          }} />
      )}
      {editing && (
        <ManageFile doc={editing} canDelete={canDelete} onClose={() => setEditing(null)}
          onDone={(text) => { setEditing(null); setMessage({ tone: "ok", text }); refresh(); }}
          run={run} />
      )}
    </>
  );
}

function FolderForm({ title, initial, onClose, onSubmit }: { title: string; initial: string; onClose: () => void; onSubmit: (name: string) => Promise<void> }) {
  const [name, setName] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit(name);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  return (
    <Modal title={title} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {error && <Notice tone="bad">{error}</Notice>}
        <label className="field">
          Folder name
          <input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

function ManageFile({ doc, canDelete, onClose, onDone, run }: {
  doc: Doc;
  canDelete: boolean;
  onClose: () => void;
  onDone: (text: string) => void;
  run: (action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const allFolders = useLoad(() => api<{ id: string; full_path: string }[]>(`${BASE}/folders/all`), []);
  const [filename, setFilename] = useState(doc.filename);
  const [expiry, setExpiry] = useState(doc.expiry_date ?? "");
  const [tags, setTags] = useState(doc.tags.join(", "));
  const [folderId, setFolderId] = useState(doc.folder_id);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  const lost = doc.missing_binary || doc.gone_at !== null;

  async function save(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const body: Record<string, unknown> = {};
    if (filename !== doc.filename) body.filename = filename;
    if (folderId !== doc.folder_id) body.folder_id = folderId;
    if (expiry !== (doc.expiry_date ?? "")) {
      if (expiry) body.expiry_date = expiry;
      else body.clear_expiry = true;
    }
    const nextTags = tags.split(",").map((t) => t.trim()).filter(Boolean);
    if (nextTags.join("|") !== doc.tags.join("|")) body.tags = nextTags;
    if (Object.keys(body).length === 0) {
      onClose();
      return;
    }
    try {
      await api(`${BASE}/files/${doc.id}`, { method: "PATCH", body });
      onDone("File details saved.");
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const act = (action: () => Promise<unknown>, text: string) => { onClose(); void run(action, text); };

  return (
    <Modal title={`Manage ${doc.filename}`} onClose={onClose}>
      <form className="form" onSubmit={save}>
        {error && <Notice tone="bad">{error}</Notice>}
        {doc.missing_binary && <Notice tone="warn">This file's content is missing from storage. Upload it again below, or record it as lost.</Notice>}
        <label className="field">
          File name
          <input required maxLength={200} value={filename} onChange={(e) => setFilename(e.target.value)} />
          <span className="hint">The ending (for example .pdf) cannot change.</span>
        </label>
        <label className="field">
          Folder
          <select value={folderId} onChange={(e) => setFolderId(e.target.value)}>
            {(allFolders.data ?? []).map((f) => <option key={f.id} value={f.id}>{f.full_path}</option>)}
          </select>
        </label>
        <label className="field">
          Expiry date (optional)
          <input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
        </label>
        <label className="field">
          Tags, separated by commas
          <input value={tags} onChange={(e) => setTags(e.target.value)} />
        </label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary">Save details</button>
        </div>
      </form>
      <div className="stack" style={{ borderTop: "1px solid var(--line-soft)", paddingTop: 14 }}>
        <div className="row">
          <label className="btn" style={{ cursor: "pointer" }}>
            {lost ? "Upload the file again" : "Upload a new version"}
            <input ref={replaceInput} type="file" className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                const form = new FormData();
                form.append("file", file);
                act(() => api(`${BASE}/files/${doc.id}/replace-binary`, { form }), "New version saved. Earlier versions are kept.");
              }} />
          </label>
          {doc.archived_at ? (
            <button type="button" className="btn" onClick={() => act(() => api(`${BASE}/files/${doc.id}/restore`, { method: "POST" }), "File restored.")}>Restore from archive</button>
          ) : (
            <button type="button" className="btn" onClick={() => act(() => api(`${BASE}/files/${doc.id}/archive`, { method: "POST" }), "File archived. Tick \"Show archived\" to see it.")}>Archive</button>
          )}
          {canDelete && (
            <button type="button" className="btn danger" onClick={() => act(() => api(`${BASE}/files/${doc.id}`, { method: "DELETE" }), "File deleted.")}>Delete</button>
          )}
        </div>
        {doc.missing_binary && (
          <div className="row" style={{ alignItems: "flex-end" }}>
            <label className="field" style={{ flex: 1 }}>
              If it cannot be recovered, say why
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
            <button type="button" className="btn" disabled={reason.trim().length < 3}
              onClick={() => act(() => api(`${BASE}/files/${doc.id}/mark-gone`, { method: "POST", body: { reason } }), "Recorded as lost.")}>
              Record as lost
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
}
