import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api, formatDateTime, openFile, type Submission, type SubmissionFile } from "../api";
import { useAuth } from "../auth";
import { FormRenderer } from "../components/FormRenderer";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

type Msg = { tone: "ok" | "bad" | "warn"; text: string } | null;

/** Fill in a draft, or read a submitted form exactly as it was completed. */
export function SubmissionPage() {
  const { submissionId = "" } = useParams();
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const loaded = useLoad(() => api<Submission>(`/api/forms/submissions/${submissionId}`), [submissionId]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [files, setFiles] = useState<SubmissionFile[]>([]);
  const [revision, setRevision] = useState(1);
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  useEffect(() => {
    if (!loaded.data) return;
    setValues(loaded.data.values ?? {});
    setFiles(loaded.data.files ?? []);
    setRevision(loaded.data.revision);
    setDirty(false);
    setErrors({});
  }, [loaded.data]);

  // Warn before leaving the page with unsaved answers.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (loaded.error) {
    return (
      <div className="stack">
        <LoadError message={loaded.error} onRetry={loaded.reload} />
        <Link to="/app/forms">Back to forms</Link>
      </div>
    );
  }
  if (!loaded.data || !me) return <Loading />;
  const sub = loaded.data;
  const editable = sub.status === "draft" && sub.submitted_by === me.id;
  const base = `/api/forms/submissions/${sub.id}`;
  const answers = () => Object.entries(values).filter(([, v]) => v !== undefined).map(([id, value]) => ({ id, value }));

  function fail(e: unknown) {
    const fieldErrors = e instanceof ApiError ? e.fieldErrors : [];
    setErrors(Object.fromEntries(fieldErrors.map((f) => [f.field_id, f.message])));
    setMessage({
      tone: "bad",
      text: fieldErrors.length ? `${fieldErrors.length} ${fieldErrors.length === 1 ? "answer needs" : "answers need"} attention. They are marked below.` : errorMessage(e),
    });
    if (fieldErrors[0]) document.querySelector(`[data-field="${fieldErrors[0].field_id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function save(submit: boolean) {
    setBusy(true);
    setMessage(null);
    setErrors({});
    try {
      if (submit) {
        const done = await api<Submission>(`${base}/submit`, { method: "POST", body: { fields: answers() } });
        loaded.setData(done);
        setMessage({ tone: "ok", text: "Submitted. This record can no longer be changed." });
        window.scrollTo({ top: 0, behavior: "smooth" });
      } else {
        const saved = await api<Submission>(base, { method: "PATCH", body: { fields: answers(), expected_revision: revision } });
        setRevision(saved.revision);
        setDirty(false);
        setMessage({ tone: "ok", text: "Draft saved." });
      }
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <Link to={`/app/forms/templates/${sub.template_id}/submissions`} className="small">← {sub.template_name} submissions</Link>
          <h1>{sub.template_name}</h1>
          <div className="row">
            {sub.status === "submitted" ? <Chip tone="ok">Submitted</Chip> : <Chip tone="warn">Draft</Chip>}
            {editable && dirty && <Chip tone="warn">Unsaved changes</Chip>}
            <span className="muted">
              {sub.submitted_by_name} · {sub.status === "submitted" ? `submitted ${formatDateTime(sub.submitted_at, tz)}` : `started ${formatDateTime(sub.created_at, tz)}`} · form version {sub.template_version}
            </span>
          </div>
        </div>
        <div className="row">
          <button type="button" className="btn" disabled={dirty}
            title={dirty ? "Save the draft first" : undefined}
            onClick={() => void openFile(`${base}/pdf`, `${sub.template_name}.pdf`, false).catch((e) => setMessage({ tone: "bad", text: errorMessage(e) }))}>
            Open PDF
          </button>
          {!editable && can("forms", "delete") && sub.status === "submitted" && (
            <button type="button" className="btn danger" onClick={() => setConfirmDiscard(true)}>Delete record</button>
          )}
        </div>
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {sub.status === "draft" && !editable && <Notice tone="info">This is someone else's unfinished draft, shown read-only.</Notice>}

      <section className="card card-pad">
        <FormRenderer
          schema={sub.schema ?? []}
          values={values}
          errors={errors}
          readOnly={!editable}
          files={files}
          onChange={(id, value) => { setValues((v) => ({ ...v, [id]: value })); setDirty(true); }}
          onUpload={async (fieldId, file) => {
            const form = new FormData();
            form.append("field_id", fieldId);
            form.append("file", file);
            const saved = await api<SubmissionFile>(`${base}/files`, { form });
            setFiles((f) => [...f, saved]);
            return saved;
          }}
          onRemoveFile={async (file) => {
            await api(`${base}/files/${file.id}`, { method: "DELETE" });
            setFiles((f) => f.filter((x) => x.id !== file.id));
          }}
          onOpenFile={(file) => void openFile(`${base}/files/${file.id}`, file.name, false).catch((e) => setMessage({ tone: "bad", text: errorMessage(e) }))}
        />
      </section>

      {editable && (
        <div className="row">
          <button type="button" className="btn primary" style={{ minHeight: 44 }} disabled={busy} onClick={() => void save(true)}>{busy ? "Working…" : "Submit"}</button>
          <button type="button" className="btn" style={{ minHeight: 44 }} disabled={busy || !dirty} onClick={() => void save(false)}>Save draft</button>
          <span className="spacer" />
          <button type="button" className="btn danger" onClick={() => setConfirmDiscard(true)}>Discard draft</button>
        </div>
      )}

      {confirmDiscard && (
        <Modal title={editable ? "Discard this draft?" : "Delete this record?"} onClose={() => setConfirmDiscard(false)}>
          <p>{editable ? "Your answers and any files added to this draft will be removed." : "The record is removed from lists. The deletion is kept in the audit log."}</p>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={() => setConfirmDiscard(false)}>Cancel</button>
            <button type="button" className="btn danger" onClick={async () => {
              try {
                await api(base, { method: "DELETE" });
                setDirty(false);
                navigate(`/app/forms/templates/${sub.template_id}/submissions`);
              } catch (e) {
                setConfirmDiscard(false);
                setMessage({ tone: "bad", text: errorMessage(e) });
              }
            }}>
              {editable ? "Discard draft" : "Delete record"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
