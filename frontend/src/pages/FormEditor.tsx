import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type FieldDef, type FormTemplate } from "../api";
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

      <Assignments templateId={saved.id} published={saved.status === "published"} />

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

interface Rule {
  kind: "role" | "user" | "company";
  target_id: string;
  label?: string;
}

interface Effective {
  can_fill_in_forms: boolean;
  forms: { template_id: string; visible: boolean; reasons: string[]; blocked_by: string | null }[];
}

/** Who may fill in the form, with a check that explains what one person will see. */
function Assignments({ templateId, published }: { templateId: string; published: boolean }) {
  const rules = useLoad(() => api<{ rules: Rule[] }>(`/api/forms/templates/${templateId}/assignments`), [templateId]);
  const roles = useLoad(() => api<{ roles: { role_id: string; name: string; is_active: boolean }[] }>("/api/admin/roles").catch(() => ({ roles: [] })), []);
  const companies = useLoad(() => api<{ companies: { id: string; name: string }[] }>("/api/org/companies"), []);
  const people = useLoad(() => api<{ items: { id: string; label: string }[] }>("/api/forms/pickers/workers"), []);
  const [restricted, setRestricted] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<Msg>(null);
  const [checkUser, setCheckUser] = useState("");
  const [checkResult, setCheckResult] = useState<string | null>(null);

  useEffect(() => {
    if (!rules.data) return;
    setRestricted(rules.data.rules.length > 0);
    setChosen(new Set(rules.data.rules.map((r) => `${r.kind}:${r.target_id}`)));
  }, [rules.data]);

  if (rules.error) return <LoadError message={rules.error} onRetry={rules.reload} />;
  if (!rules.data || !roles.data || !companies.data || !people.data) return <Loading />;
  const savedKeys = rules.data.rules.map((r) => `${r.kind}:${r.target_id}`).sort().join("|");
  const currentKeys = restricted ? [...chosen].sort().join("|") : "";
  const dirty = savedKeys !== currentKeys;
  const toggle = (key: string) => {
    const next = new Set(chosen);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setChosen(next);
  };
  const group = (title: string, kind: Rule["kind"], items: { id: string; label: string }[]) => (
    <fieldset style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "8px 12px", margin: 0, minWidth: 0 }}>
      <legend className="small" style={{ fontWeight: 600, color: "var(--muted)" }}>{title}</legend>
      <div style={{ maxHeight: 190, overflowY: "auto" }}>
        {items.length === 0 ? <span className="small muted">None yet.</span> : items.map((item) => (
          <label className="check" key={item.id}>
            <input type="checkbox" checked={chosen.has(`${kind}:${item.id}`)} onChange={() => toggle(`${kind}:${item.id}`)} />
            {item.label}
          </label>
        ))}
      </div>
    </fieldset>
  );

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Who can fill this in</h2>
          <p className="small muted">People who manage form templates can always open the form.{!published && " Applies once the form is published."}</p>
        </div>
        {dirty && <Chip tone="warn">Unsaved changes</Chip>}
      </div>
      <div className="card-pad stack" style={{ paddingTop: 4 }}>
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        <label className="check"><input type="radio" name="assign-mode" checked={!restricted} onChange={() => setRestricted(false)} />Everyone who can fill in forms</label>
        <label className="check"><input type="radio" name="assign-mode" checked={restricted} onChange={() => setRestricted(true)} />Only the roles, companies and people ticked below</label>
        {restricted && (
          <div className="fields">
            {group("Roles", "role", roles.data.roles.filter((r) => r.is_active).map((r) => ({ id: r.role_id, label: r.name })))}
            {group("Companies", "company", companies.data.companies.map((c) => ({ id: c.id, label: c.name })))}
            {group("People", "user", people.data.items)}
          </div>
        )}
        {restricted && chosen.size === 0 && <Notice tone="warn">Nothing is ticked, so saving would leave the form open to everyone.</Notice>}
        <div className="row" style={{ alignItems: "flex-end" }}>
          <button type="button" className="btn primary" disabled={!dirty} onClick={async () => {
            setMessage(null);
            try {
              const body = { rules: restricted ? [...chosen].map((key) => ({ kind: key.slice(0, key.indexOf(":")), target_id: key.slice(key.indexOf(":") + 1) })) : [] };
              rules.setData(await api<{ rules: Rule[] }>(`/api/forms/templates/${templateId}/assignments`, { method: "PUT", body }));
              setCheckResult(null);
              setMessage({ tone: "ok", text: "Saved who can fill this in." });
            } catch (e) {
              setMessage({ tone: "bad", text: errorMessage(e) });
            }
          }}>
            Save who can fill this in
          </button>
          <span className="spacer" />
          <label className="field">
            Check what one person sees
            <select value={checkUser} onChange={async (e) => {
              const userId = e.target.value;
              setCheckUser(userId);
              setCheckResult(null);
              if (!userId) return;
              try {
                const result = await api<Effective>(`/api/forms/assignments/effective?user_id=${userId}`);
                const form = result.forms.find((f) => f.template_id === templateId);
                setCheckResult(
                  !form ? "This form is not published yet, so nobody sees it."
                    : form.visible ? `Yes, they see this form. ${form.reasons.join("; ")}.`
                    : `No, they do not see this form. ${form.blocked_by ?? "It is not assigned to them"}.`,
                );
              } catch (err) {
                setCheckResult(errorMessage(err));
              }
            }}>
              <option value="">Choose a person…</option>
              {people.data.items.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </label>
        </div>
        {checkResult && <Notice tone="info">{checkResult}{dirty ? " (Based on the saved settings, not your unsaved changes.)" : ""}</Notice>}
      </div>
    </section>
  );
}
