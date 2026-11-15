import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, formatDateTime, type FormTemplate, type Submission } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

type Msg = { tone: "ok" | "bad" | "warn"; text: string } | null;

function TemplateStatus({ template }: { template: FormTemplate }) {
  if (template.status === "archived") return <Chip tone="neutral" icon={false}>Archived</Chip>;
  if (template.status === "draft") return <Chip tone="warn">Draft · not in use</Chip>;
  return (
    <span className="row">
      <Chip tone="ok">Published · version {template.published_version}</Chip>
      {template.has_unpublished_changes && <Chip tone="warn">Unpublished changes</Chip>}
    </span>
  );
}

/** Start a new draft and go to it. A fresh client id makes a double-click harmless. */
export async function startSubmission(templateId: string): Promise<string> {
  const draft = await api<Submission>(`/api/forms/templates/${templateId}/submissions`, {
    method: "POST",
    body: { status: "draft", fields: [], client_submission_id: crypto.randomUUID(), launched_via: "office" },
  });
  return draft.id;
}

export function Forms() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const manager = can("forms", "use");
  const canFill = can("forms", "edit");
  const [showArchived, setShowArchived] = useState(false);
  const templates = useLoad(
    () => api<{ templates: FormTemplate[] }>(`/api/forms/templates?include_archived=${showArchived}`),
    [showArchived],
  );
  const [message, setMessage] = useState<Msg>(null);
  const importInput = useRef<HTMLInputElement>(null);

  async function exportAll() {
    try {
      const data = await api<unknown>("/api/forms/templates/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "form-templates.json";
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  async function importFile(file: File) {
    setMessage(null);
    try {
      const payload = JSON.parse(await file.text()) as unknown;
      const result = await api<{ created: unknown[]; errors: { name: string; error: string }[] }>("/api/forms/templates/import", { method: "POST", body: payload });
      setMessage({
        tone: result.errors.length ? "warn" : "ok",
        text: `${result.created.length} imported as drafts.${result.errors.length ? ` ${result.errors.length} skipped: ${result.errors.map((e) => e.name || "unnamed").join(", ")}.` : ""}`,
      });
      templates.reload();
    } catch (e) {
      setMessage({ tone: "bad", text: e instanceof SyntaxError ? "That file is not a form export." : errorMessage(e) });
    } finally {
      if (importInput.current) importInput.current.value = "";
    }
  }

  if (templates.error) return <LoadError message={templates.error} onRetry={templates.reload} />;
  if (!templates.data) return <Loading />;
  const rows = templates.data.templates;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Forms</h1>
          <p className="muted">{manager ? "Build forms, publish them, and review what has been submitted." : "Forms you can fill in."}</p>
        </div>
        {manager && (
          <div className="row">
            <button type="button" className="btn" onClick={() => void exportAll()}>Export forms</button>
            <label className="btn" style={{ cursor: "pointer" }}>
              Import forms
              <input ref={importInput} type="file" accept="application/json,.json" className="sr-only" onChange={(e) => e.target.files?.[0] && void importFile(e.target.files[0])} />
            </label>
            <button type="button" className="btn primary" onClick={async () => {
              try {
                const created = await api<FormTemplate>("/api/forms/templates", { method: "POST", body: { name: "Untitled form", category: "general", fields: [] } });
                navigate(`/app/forms/templates/${created.id}/edit`);
              } catch (e) {
                setMessage({ tone: "bad", text: errorMessage(e) });
              }
            }}>
              New form
            </button>
          </div>
        )}
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <section className="card">
        {manager && (
          <div className="toolbar">
            <label className="check">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
              Show archived forms
            </label>
          </div>
        )}
        {rows.length === 0 ? (
          <div className="empty">{manager ? "No forms yet. Choose \"New form\" to build one." : "No forms have been published for you yet."}</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th scope="col">Form</th><th scope="col">Type</th><th scope="col">Status</th><th scope="col" className="right">Questions</th><th scope="col"><span className="sr-only">Actions</span></th></tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <span style={{ fontWeight: 600 }}>{t.name}</span>
                      {t.description && <div className="small muted">{t.description}</div>}
                    </td>
                    <td>{t.category_label}</td>
                    <td><TemplateStatus template={t} /></td>
                    <td className="right num">{t.field_count}</td>
                    <td>
                      <span className="row" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                        {canFill && t.status === "published" && (
                          <button type="button" className="btn small primary" onClick={async () => {
                            try {
                              navigate(`/app/forms/submissions/${await startSubmission(t.id)}`);
                            } catch (e) {
                              setMessage({ tone: "bad", text: errorMessage(e) });
                            }
                          }}>
                            Fill in
                          </button>
                        )}
                        <Link className="btn small" to={`/app/forms/templates/${t.id}/submissions`}>Submissions</Link>
                        {manager && <Link className="btn small" to={`/app/forms/templates/${t.id}/edit`}>Edit</Link>}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

const PAGE = 50;

export function FormSubmissions() {
  const { templateId = "" } = useParams();
  const { me } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const template = useLoad(() => api<FormTemplate>(`/api/forms/templates/${templateId}`).catch(() => null), [templateId]);
  const list = useLoad(
    () => api<{ items: Submission[]; total: number; scope: string }>(`/api/forms/templates/${templateId}/submissions?limit=${PAGE}&offset=${offset}${status ? `&status=${status}` : ""}`),
    [templateId, status, offset],
  );
  if (list.error) return <LoadError message={list.error} onRetry={list.reload} />;
  if (!list.data) return <Loading />;
  const { items, total, scope } = list.data;
  return (
    <>
      <div className="page-head">
        <div>
          <Link to="/app/forms" className="small">← All forms</Link>
          <h1>{template.data?.name ?? "Submissions"}</h1>
          <p className="muted">{scope === "team" ? "Everyone's submitted forms, plus your own drafts." : "Your own forms only."}</p>
        </div>
      </div>
      <section className="card">
        <div className="toolbar">
          <label className="field">
            Status
            <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
              <option value="">All</option>
              <option value="submitted">Submitted</option>
              <option value="draft">Draft</option>
            </select>
          </label>
        </div>
        {items.length === 0 ? (
          <div className="empty">{status ? "Nothing with this status." : "Nothing has been submitted yet."}</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead><tr><th scope="col">Filled in by</th><th scope="col">Status</th><th scope="col">Submitted</th><th scope="col">Started</th><th scope="col" className="right">Form version</th></tr></thead>
                <tbody>
                  {items.map((s) => (
                    <tr key={s.id} className="clickable">
                      <td><Link to={`/app/forms/submissions/${s.id}`} style={{ fontWeight: 600 }}>{s.submitted_by_name ?? "Unknown"}</Link></td>
                      <td>{s.status === "submitted" ? <Chip tone="ok">Submitted</Chip> : <Chip tone="warn">Draft</Chip>}</td>
                      <td className="num">{formatDateTime(s.submitted_at, tz)}</td>
                      <td className="num">{formatDateTime(s.created_at, tz)}</td>
                      <td className="right num">{s.template_version}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
              <span className="muted small" role="status">Showing {offset + 1}–{offset + items.length} of {total}</span>
              <span className="spacer" />
              <button type="button" className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
              <button type="button" className="btn small" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
            </div>
          </>
        )}
      </section>
    </>
  );
}
