import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type FieldDef, type FormTemplate } from "../api";
import { AssignmentRules } from "../components/AssignmentRules";
import { FormRenderer } from "../components/FormRenderer";
import { Chip, LoadError, Loading, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

interface FieldType {
  type: string;
  label: string;
  needs_options: boolean;
  is_layout: boolean;
  config: string[];
}

interface Catalogue {
  field_types: FieldType[];
  categories: { key: string; label: string }[];
}

type Msg = { tone: "ok" | "bad" | "warn"; text: string } | null;
type Row = { id: string; label: string };

const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);
const newId = () => `f_${crypto.randomUUID().replace(/-/g, "").slice(0, 10)}`;

export function FormEditor() {
  const { templateId = "" } = useParams();
  const template = useLoad(() => api<FormTemplate>(`/api/forms/templates/${templateId}`), [templateId]);
  const catalogue = useLoad(() => api<Catalogue>("/api/forms/field-types"), []);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("general");
  const [description, setDescription] = useState("");
  const [fields, setFields] = useState<FieldDef[]>([]);
  const [addType, setAddType] = useState("text");
  const [message, setMessage] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (!template.data) return;
    setName(template.data.name);
    setCategory(template.data.category);
    setDescription(template.data.description);
    setFields(template.data.fields ?? []);
  }, [template.data]);

  if (template.error) return <LoadError message={template.error} onRetry={template.reload} />;
  if (!template.data || !catalogue.data) return <Loading />;
  const saved = template.data;
  const types = Object.fromEntries(catalogue.data.field_types.map((t) => [t.type, t]));
  const dirty = name !== saved.name || category !== saved.category || description !== saved.description || JSON.stringify(fields) !== JSON.stringify(saved.fields ?? []);

  const update = (index: number, patch: Partial<FieldDef>) => setFields(fields.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  const setConfig = (index: number, key: string, value: unknown) => {
    const config = { ...(fields[index].config ?? {}) };
    if (value === undefined || value === "" || value === false) delete config[key];
    else config[key] = value;
    update(index, { config: Object.keys(config).length ? config : undefined });
  };
  const move = (index: number, by: number) => {
    const next = [...fields];
    const [item] = next.splice(index, 1);
    next.splice(index + by, 0, item);
    setFields(next);
  };

  async function save(publish: boolean) {
    setBusy(true);
    setMessage(null);
    try {
      let result = saved;
      if (dirty) result = await api<FormTemplate>(`/api/forms/templates/${saved.id}`, { method: "PATCH", body: { name, category, description, fields } });
      if (publish) result = await api<FormTemplate>(`/api/forms/templates/${saved.id}/publish`, { method: "POST" });
      template.setData(result);
      setMessage({ tone: "ok", text: publish ? `Published as version ${result.published_version}. Earlier submissions keep the version they were filled in on.` : "Draft saved. People filling in forms still see the published version." });
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <Link to="/app/forms" className="small">← All forms</Link>
          <h1>Edit form</h1>
          <div className="row">
            {saved.status === "published" ? <Chip tone="ok">Published · version {saved.published_version}</Chip> : saved.status === "archived" ? <Chip tone="neutral" icon={false}>Archived</Chip> : <Chip tone="warn">Draft · not in use</Chip>}
            {dirty ? <Chip tone="warn">Unsaved changes</Chip> : saved.has_unpublished_changes && saved.published_version > 0 && <Chip tone="warn">Saved changes not published yet</Chip>}
          </div>
        </div>
        <div className="row">
          <button type="button" className="btn" disabled={busy || !dirty} onClick={() => void save(false)}>Save draft</button>
          <button type="button" className="btn primary" disabled={busy || (!dirty && !saved.has_unpublished_changes && saved.status === "published")} onClick={() => void save(true)}>
            {saved.published_version > 0 ? "Publish new version" : "Publish"}
          </button>
          {saved.status === "published" && (
            <button type="button" className="btn" disabled={busy} onClick={async () => {
              try {
                template.setData(await api<FormTemplate>(`/api/forms/templates/${saved.id}/archive`, { method: "POST" }));
                setMessage({ tone: "ok", text: "Archived. Nobody can start this form now; existing records are kept." });
              } catch (e) {
                setMessage({ tone: "bad", text: errorMessage(e) });
              }
            }}>
              Archive
            </button>
          )}
        </div>
      </div>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <section className="card">
        <div className="card-head"><h2>About this form</h2></div>
        <div className="card-pad fields" style={{ paddingTop: 4 }}>
          <label className="field">Form name<input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} /></label>
          <label className="field">
            Type
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {catalogue.data.categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </label>
          <label className="field">Description (optional)<input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        </div>
      </section>

      <section className="card">
        <div className="card-head"><h2>Questions</h2><span className="small muted">{fields.length} in order</span></div>
        <div className="card-pad stack" style={{ paddingTop: 4 }}>
          {fields.length === 0 && <p className="muted">No questions yet. Add the first one below.</p>}
          {fields.map((field, index) => {
            const meta = types[field.type];
            const config = field.config ?? {};
            return (
              <div key={field.id} className="stack" style={{ padding: 12, border: "1px solid var(--line)", borderRadius: 8, gap: 10 }}>
                <div className="row">
                  <Chip tone="info" icon={false}>{index + 1}. {meta?.label ?? field.type}</Chip>
                  <span className="spacer" />
                  <button type="button" className="btn small" disabled={index === 0} aria-label={`Move ${field.label || "question"} up`} onClick={() => move(index, -1)}>Up</button>
                  <button type="button" className="btn small" disabled={index === fields.length - 1} aria-label={`Move ${field.label || "question"} down`} onClick={() => move(index, 1)}>Down</button>
                  <button type="button" className="btn small danger" aria-label={`Remove ${field.label || "question"}`} onClick={() => setFields(fields.filter((_, i) => i !== index))}>Remove</button>
                </div>
                <div className="fields">
                  <label className="field">{meta?.is_layout ? "Heading text" : "Question"}<input value={field.label} maxLength={200} onChange={(e) => update(index, { label: e.target.value })} /></label>
                  {!meta?.is_layout && <label className="field">Help text (optional)<input value={field.help ?? ""} onChange={(e) => update(index, { help: e.target.value })} /></label>}
                </div>
                {meta?.needs_options && (
                  <label className="field">
                    Choices, one per line
                    <textarea value={(field.options ?? []).join("\n")} onChange={(e) => update(index, { options: e.target.value.split("\n") })} onBlur={(e) => update(index, { options: lines(e.target.value) })} />
                  </label>
                )}
                {field.type === "reference_matrix" && (
                  <div className="fields">
                    <label className="field">
                      Rows, one per line
                      <textarea defaultValue={((config.rows as Row[]) ?? []).map((r) => r.label).join("\n")}
                        onBlur={(e) => {
                          const rows = lines(e.target.value).map((label, i) => ({ id: label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 60) || `row_${i + 1}`, label }));
                          setConfig(index, "rows", rows.length ? rows : undefined);
                        }} />
                    </label>
                    <label className="field">
                      Columns, one per line
                      <textarea defaultValue={((config.columns as string[]) ?? []).join("\n")}
                        onBlur={(e) => setConfig(index, "columns", lines(e.target.value).length ? lines(e.target.value) : undefined)} />
                    </label>
                  </div>
                )}
                {field.type === "table" && (
                  <label className="field">
                    Columns, one per line
                    <textarea defaultValue={((config.table_columns as Row[]) ?? []).map((c) => c.label).join("\n")}
                      onBlur={(e) => {
                        const cols = lines(e.target.value).map((label, i) => ({ id: label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 60) || `col_${i + 1}`, label }));
                        setConfig(index, "table_columns", cols.length ? cols : undefined);
                      }} />
                  </label>
                )}
                <div className="row" style={{ alignItems: "flex-end" }}>
                  {!meta?.is_layout && (
                    <label className="check">
                      <input type="checkbox" checked={field.required} onChange={(e) => update(index, { required: e.target.checked })} />
                      Required
                    </label>
                  )}
                  {meta?.config.includes("multiple") && (
                    <label className="check"><input type="checkbox" checked={config.multiple === true} onChange={(e) => setConfig(index, "multiple", e.target.checked)} />Allow more than one</label>
                  )}
                  {meta?.config.includes("uppercase") && (
                    <label className="check"><input type="checkbox" checked={config.uppercase === true} onChange={(e) => setConfig(index, "uppercase", e.target.checked)} />Store in capitals</label>
                  )}
                  {(["max_length", "max_files", "min", "max"] as const).filter((k) => meta?.config.includes(k)).map((key) => (
                    <label className="field" key={key} style={{ width: 130 }}>
                      {{ max_length: "Longest length", max_files: "Most files", min: "Smallest", max: "Largest" }[key]}
                      <input type="number" value={config[key] === undefined ? "" : String(config[key])} onChange={(e) => setConfig(index, key, e.target.value === "" ? undefined : Number(e.target.value))} />
                    </label>
                  ))}
                  {meta?.config.includes("unit") && (
                    <label className="field" style={{ width: 130 }}>Unit<input value={(config.unit as string) ?? ""} onChange={(e) => setConfig(index, "unit", e.target.value)} /></label>
                  )}
                </div>
              </div>
            );
          })}
          <div className="row" style={{ alignItems: "flex-end" }}>
            <label className="field">
              Add a question
              <select value={addType} onChange={(e) => setAddType(e.target.value)}>
                {catalogue.data.field_types.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
              </select>
            </label>
            <button type="button" className="btn" onClick={() => {
              const meta = types[addType];
              setFields([...fields, { id: newId(), type: addType, label: "", required: false, ...(meta.needs_options ? { options: [] } : {}) }]);
            }}>
              Add
            </button>
          </div>
        </div>
      </section>

      <AssignmentRules
        url={`/api/forms/templates/${saved.id}/assignments`}
        title="Who can fill this in"
        everyoneLabel="Everyone who can fill in forms"
        note={`People who manage form templates can always open the form.${saved.status === "published" ? "" : " Applies once the form is published."}`}
        check={async (userId) => {
          const result = await api<{ forms: { template_id: string; visible: boolean; reasons: string[]; blocked_by: string | null }[] }>(`/api/forms/assignments/effective?user_id=${userId}`);
          const form = result.forms.find((f) => f.template_id === saved.id);
          return !form ? "This form is not published yet, so nobody sees it."
            : form.visible ? `Yes, they see this form. ${form.reasons.join("; ")}.`
            : `No, they do not see this form. ${form.blocked_by ?? "It is not assigned to them"}.`;
        }}
      />

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Preview</h2>
            <p className="small muted">How the form looks when filled in. Nothing typed here is saved.</p>
          </div>
        </div>
        <div className="card-pad" style={{ paddingTop: 4 }}>
          {fields.length === 0 ? <p className="muted">Add a question to see the preview.</p> : (
            <FormRenderer schema={fields.map((f) => ({ ...f, label: f.label || "(no question text yet)" }))} values={preview} onChange={(id, value) => setPreview((p) => ({ ...p, [id]: value }))} />
          )}
        </div>
      </section>
    </>
  );
}
