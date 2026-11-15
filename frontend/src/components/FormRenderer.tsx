import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, formatSize, type FieldDef, type SubmissionFile } from "../api";
import { Chip, Icon, Notice, errorMessage } from "./ui";

/**
 * Renders a form from a schema snapshot. Used by the office app and the phone app,
 * and (read-only) to show a submitted record exactly as it was filled in.
 */
interface Props {
  schema: FieldDef[];
  values: Record<string, unknown>;
  errors?: Record<string, string>;
  readOnly?: boolean;
  files?: SubmissionFile[];
  onChange?: (fieldId: string, value: unknown) => void;
  onUpload?: (fieldId: string, file: File) => Promise<SubmissionFile>;
  onRemoveFile?: (file: SubmissionFile) => Promise<void>;
  onOpenFile?: (file: SubmissionFile) => void;
}

type Ref = { id: string; label: string };
type Compliance = { result?: "pass" | "fail" | "na"; note?: string };
type Action = { description: string; responsible: string; due_date?: string; status: "open" | "done" };

export function FormRenderer(props: Props) {
  const { schema, values, errors = {}, readOnly = false } = props;
  return (
    <div className="stack" style={{ gap: 16 }}>
      {schema.map((field) =>
        field.type === "section_header" ? (
          <h3 key={field.id} style={{ borderBottom: "1px solid var(--line)", paddingBottom: 6, marginTop: 6 }}>{field.label}</h3>
        ) : (
          <div key={field.id} className="stack" style={{ gap: 6 }} data-field={field.id}>
            <div id={`label-${field.id}`} style={{ fontWeight: 600 }}>
              {field.label}
              {field.required && !readOnly && <span className="muted" style={{ fontWeight: 400 }}> (required)</span>}
            </div>
            {field.help && <div className="small muted">{field.help}</div>}
            {readOnly ? <Display field={field} value={values[field.id]} {...props} /> : <Input field={field} value={values[field.id]} {...props} />}
            {errors[field.id] && (
              <div className="small" style={{ color: "var(--bad)", fontWeight: 600 }} role="alert">
                <Icon name="bad" size={13} /> {errors[field.id]}
              </div>
            )}
          </div>
        ),
      )}
    </div>
  );
}

// ---------- read-only display ----------

function Display({ field, value, files = [], onOpenFile }: Props & { field: FieldDef; value: unknown }) {
  const mine = files.filter((f) => f.field_id === field.id);
  const empty = <span className="muted">Not answered</span>;
  if (value === undefined || value === null || value === "") return empty;
  switch (field.type) {
    case "compliance": {
      const v = value as Compliance;
      return (
        <div className="row">
          {v.result === "pass" ? <Chip tone="ok">Pass</Chip> : v.result === "fail" ? <Chip tone="bad">Fail</Chip> : <Chip tone="neutral" icon={false}>N/A</Chip>}
          {v.note && <span>{v.note}</span>}
        </div>
      );
    }
    case "select":
      return <span>{Array.isArray(value) ? (value as string[]).join(", ") : String(value)}</span>;
    case "date": {
      const [y, m, d] = String(value).split("-");
      return <span className="num">{`${d}/${m}/${y}`}</span>;
    }
    case "reference_matrix": {
      const rows = (field.config?.rows as Ref[]) ?? [];
      const v = value as Record<string, string>;
      return <span>{rows.filter((r) => v[r.id]).map((r) => `${r.label}: ${v[r.id]}`).join(" · ") || "—"}</span>;
    }
    case "photo":
    case "attachment":
      return <FileList files={mine.filter((f) => (value as string[]).includes(f.id))} onOpenFile={onOpenFile} />;
    case "signature": {
      const v = value as { file_id: string; signed_name: string };
      return (
        <div className="row">
          <span>Signed by {v.signed_name}</span>
          <FileList files={mine.filter((f) => f.id === v.file_id)} onOpenFile={onOpenFile} />
        </div>
      );
    }
    case "gps": {
      const v = value as { lat?: number; lng?: number; accuracy_m?: number; unavailable_reason?: string };
      return v.lat !== undefined ? (
        <span className="num">{v.lat.toFixed(5)}, {v.lng?.toFixed(5)}{v.accuracy_m !== undefined ? ` (within ${Math.round(v.accuracy_m)} m)` : ""}</span>
      ) : (
        <span>Location not available: {v.unavailable_reason}</span>
      );
    }
    case "navixy_vehicle": {
      const v = value as { label: string; source: string };
      return <span>{v.label} <span className="muted small">({v.source === "navixy" ? "from Navixy" : "entered by hand"})</span></span>;
    }
    case "worker_picker":
      return <span>{(Array.isArray(value) ? (value as Ref[]) : [value as Ref]).map((r) => r.label).join(", ")}</span>;
    case "job_picker":
    case "site_picker":
    case "asset_qr":
      return <span>{(value as Ref).label}</span>;
    case "table": {
      const columns = (field.config?.table_columns as Ref[]) ?? [];
      const rows = value as Record<string, string>[];
      if (rows.length === 0) return empty;
      return (
        <div className="table-wrap">
          <table>
            <thead><tr>{columns.map((c) => <th key={c.id} scope="col">{c.label}</th>)}</tr></thead>
            <tbody>{rows.map((row, i) => <tr key={i}>{columns.map((c) => <td key={c.id} style={{ whiteSpace: "pre-wrap" }}>{row[c.id] || "—"}</td>)}</tr>)}</tbody>
          </table>
        </div>
      );
    }
    case "corrective_actions":
      return (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {(value as Action[]).map((a, i) => (
            <li key={i}>
              {a.description} · {a.responsible}{a.due_date ? ` · due ${a.due_date.split("-").reverse().join("/")}` : ""} · {a.status === "done" ? "Done" : "Open"}
            </li>
          ))}
        </ul>
      );
    default:
      return <span style={{ whiteSpace: "pre-wrap" }}>{String(value)}{field.config?.unit ? ` ${String(field.config.unit)}` : ""}</span>;
  }
}

function FileList({ files, onOpenFile, onRemove }: { files: SubmissionFile[]; onOpenFile?: (f: SubmissionFile) => void; onRemove?: (f: SubmissionFile) => void }) {
  if (files.length === 0) return null;
  return (
    <div className="stack" style={{ gap: 6 }}>
      {files.map((file) => (
        <div className="row" key={file.id}>
          {file.missing ? (
            <Chip tone="warn">{file.name} · content missing</Chip>
          ) : (
            <button type="button" className="link-btn" onClick={() => onOpenFile?.(file)}>{file.name}</button>
          )}
          <span className="small muted">{formatSize(file.size_bytes)}</span>
          {onRemove && <button type="button" className="btn small" aria-label={`Remove ${file.name}`} onClick={() => onRemove(file)}>Remove</button>}
        </div>
      ))}
    </div>
  );
}

// ---------- inputs ----------

function Input(props: Props & { field: FieldDef; value: unknown }) {
  const { field, value, onChange } = props;
  const set = (v: unknown) => onChange?.(field.id, v);
  const labelled = { "aria-labelledby": `label-${field.id}` };
  const inputStyle = { minHeight: 44, padding: "0 10px", border: "1px solid var(--control)", borderRadius: 6, font: "inherit", width: "100%" } as const;
  const config = field.config ?? {};

  switch (field.type) {
    case "text":
      return <input {...labelled} style={inputStyle} value={(value as string) ?? ""} maxLength={(config.max_length as number) ?? undefined} onChange={(e) => set(e.target.value)} />;
    case "textarea":
      return <textarea {...labelled} style={{ ...inputStyle, minHeight: 88, padding: 10 }} value={(value as string) ?? ""} onChange={(e) => set(e.target.value)} />;
    case "number":
      return (
        <div className="row">
          <input {...labelled} type="number" inputMode="decimal" style={{ ...inputStyle, maxWidth: 200 }} value={value === undefined || value === null ? "" : String(value)}
            onChange={(e) => set(e.target.value === "" ? undefined : Number(e.target.value))} />
          {config.unit ? <span className="muted">{String(config.unit)}</span> : null}
        </div>
      );
    case "date":
      return <input {...labelled} type="date" style={{ ...inputStyle, maxWidth: 220 }} value={(value as string) ?? ""} onChange={(e) => set(e.target.value || undefined)} />;
    case "time":
      return <input {...labelled} type="time" style={{ ...inputStyle, maxWidth: 160 }} value={(value as string) ?? ""} onChange={(e) => set(e.target.value || undefined)} />;
    case "select":
      if (config.multiple) {
        const chosen = (value as string[]) ?? [];
        return (
          <div role="group" {...labelled} className="stack" style={{ gap: 2 }}>
            {field.options?.map((option) => (
              <label className="check" key={option}>
                <input type="checkbox" checked={chosen.includes(option)}
                  onChange={(e) => set(e.target.checked ? [...chosen, option] : chosen.filter((c) => c !== option))} />
                {option}
              </label>
            ))}
          </div>
        );
      }
      return (
        <select {...labelled} style={inputStyle} value={(value as string) ?? ""} onChange={(e) => set(e.target.value || undefined)}>
          <option value="">Choose…</option>
          {field.options?.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      );
    case "radio":
      return (
        <div role="radiogroup" {...labelled} className="stack" style={{ gap: 2 }}>
          {field.options?.map((option) => (
            <label className="check" key={option}>
              <input type="radio" name={field.id} checked={value === option} onChange={() => set(option)} />
              {option}
            </label>
          ))}
        </div>
      );
    case "compliance":
      return <ComplianceInput field={field} value={(value as Compliance) ?? {}} set={set} />;
    case "reference_matrix": {
      const rows = (config.rows as Ref[]) ?? [];
      const columns = (config.columns as string[]) ?? [];
      const v = (value as Record<string, string>) ?? {};
      return (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col"><span className="sr-only">Item</span></th>{columns.map((c) => <th key={c} scope="col" style={{ textAlign: "center" }}>{c}</th>)}</tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <th scope="row" style={{ fontSize: 14, color: "var(--text)" }}>{row.label}</th>
                  {columns.map((c) => (
                    <td key={c} style={{ textAlign: "center" }}>
                      <input type="radio" name={`${field.id}-${row.id}`} aria-label={`${row.label}: ${c}`} checked={v[row.id] === c}
                        onChange={() => set({ ...v, [row.id]: c })} style={{ width: 20, height: 20 }} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    case "photo":
    case "attachment":
      return <FilesInput {...props} />;
    case "signature":
      return <SignatureInput {...props} />;
    case "gps":
      return <GpsInput value={value as Record<string, unknown> | undefined} set={set} />;
    case "navixy_vehicle":
      return (
        <div className="stack" style={{ gap: 6 }}>
          <input {...labelled} style={inputStyle} placeholder="Vehicle or plant, for example PT-031 Tipper" value={(value as { label?: string })?.label ?? ""}
            onChange={(e) => set(e.target.value ? { label: e.target.value, source: "manual" } : undefined)} />
          <span className="small muted">Entered by hand. The Navixy vehicle list is not connected yet.</span>
        </div>
      );
    case "worker_picker":
      return <WorkerPicker field={field} value={value} set={set} />;
    case "site_picker":
      return <SitePicker field={field} value={value} set={set} />;
    case "job_picker":
      return <JobPicker field={field} value={value} set={set} />;
    case "asset_qr":
      return <Notice tone="info">Assets cannot be chosen yet. This part of the portal is built in a later stage.</Notice>;
    case "corrective_actions":
      return <ActionsInput value={(value as Action[]) ?? []} set={set} />;
    case "table":
      return <TableInput field={field} value={(value as Record<string, string>[]) ?? []} set={set} />;
    default:
      return <Notice tone="warn">This field type is not supported here.</Notice>;
  }
}

function ComplianceInput({ field, value, set }: { field: FieldDef; value: Compliance; set: (v: unknown) => void }) {
  const options: { key: "pass" | "fail" | "na"; label: string; color: string }[] = [
    { key: "pass", label: "Pass", color: "var(--ok)" },
    { key: "fail", label: "Fail", color: "var(--bad)" },
    { key: "na", label: "N/A", color: "var(--neutral)" },
  ];
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div role="group" aria-labelledby={`label-${field.id}`} style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8, maxWidth: 420 }}>
        {options.map((option) => {
          const on = value.result === option.key;
          return (
            <button key={option.key} type="button" aria-pressed={on} className="btn"
              style={{ minHeight: 44, ...(on ? { background: option.color, borderColor: option.color, color: "#fff" } : {}) }}
              onClick={() => set({ ...value, result: option.key })}>
              {option.key === "pass" && <Icon name="ok" size={15} />}
              {option.label}
            </button>
          );
        })}
      </div>
      {value.result && (
        <label className="field">
          {value.result === "fail" ? "What is wrong? (required)" : "Note (optional)"}
          <textarea value={value.note ?? ""} onChange={(e) => set({ ...value, note: e.target.value })} />
        </label>
      )}
    </div>
  );
}

function FilesInput({ field, value, files = [], onChange, onUpload, onRemoveFile, onOpenFile }: Props & { field: FieldDef; value: unknown }) {
  const ids = (value as string[]) ?? [];
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const mine = files.filter((f) => f.field_id === field.id && ids.includes(f.id));
  const photo = field.type === "photo";

  async function add(list: FileList) {
    if (!onUpload) return;
    setBusy(true);
    setError(null);
    let next = ids;
    try {
      for (const file of Array.from(list)) {
        const saved = await onUpload(field.id, file);
        next = [...next, saved.id];
        onChange?.(field.id, next);
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="stack" style={{ gap: 8 }}>
      <FileList files={mine} onOpenFile={onOpenFile}
        onRemove={(file) => void (async () => {
          try {
            await onRemoveFile?.(file);
            onChange?.(field.id, ids.filter((id) => id !== file.id));
          } catch (e) {
            setError(errorMessage(e));
          }
        })()} />
      {error && <div className="small" style={{ color: "var(--bad)", fontWeight: 600 }} role="alert">{error}</div>}
      {onUpload ? (
        <label className="btn" style={{ alignSelf: "flex-start", minHeight: 44, cursor: busy ? "wait" : "pointer" }}>
          {busy ? "Uploading…" : photo ? "Add a photo" : "Add a file"}
          {/* The camera or file chooser opens only when the person presses this. */}
          <input ref={input} type="file" className="sr-only" multiple disabled={busy} accept={photo ? "image/png,image/jpeg,image/webp" : "image/png,image/jpeg,image/webp,application/pdf"}
            aria-labelledby={`label-${field.id}`} onChange={(e) => e.target.files?.length && void add(e.target.files)} />
        </label>
      ) : (
        <span className="small muted">Files can be added when the form is being filled in.</span>
      )}
    </div>
  );
}

function SignatureInput({ field, value, files = [], onChange, onUpload, onRemoveFile }: Props & { field: FieldDef; value: unknown }) {
  const current = value as { file_id?: string; signed_name?: string } | undefined;
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const [dirty, setDirty] = useState(false);
  const [name, setName] = useState(current?.signed_name ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const saved = files.find((f) => f.id === current?.file_id);

  function point(event: React.PointerEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: ((event.clientX - rect.left) / rect.width) * event.currentTarget.width, y: ((event.clientY - rect.top) / rect.height) * event.currentTarget.height };
  }
  function start(event: React.PointerEvent<HTMLCanvasElement>) {
    const ctx = canvas.current?.getContext("2d");
    if (!ctx) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drawing.current = true;
    const { x, y } = point(event);
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#16222e";
    ctx.beginPath();
    ctx.moveTo(x, y);
  }
  function move(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawing.current) return;
    const ctx = canvas.current?.getContext("2d");
    const { x, y } = point(event);
    ctx?.lineTo(x, y);
    ctx?.stroke();
    setDirty(true);
  }
  function clear() {
    const c = canvas.current;
    c?.getContext("2d")?.clearRect(0, 0, c.width, c.height);
    setDirty(false);
  }

  async function save() {
    if (!onUpload || !canvas.current) return;
    if (!name.trim()) {
      setError("Enter the name of the person signing");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const blob = await new Promise<Blob | null>((resolve) => canvas.current!.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("Could not read the signature");
      const file = await onUpload(field.id, new File([blob], "signature.png", { type: "image/png" }));
      onChange?.(field.id, { file_id: file.id, signed_name: name.trim() });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (saved && current) {
    return (
      <div className="row">
        <Chip tone="ok">Signed by {current.signed_name}</Chip>
        <button type="button" className="btn small" onClick={() => void (async () => {
          try {
            await onRemoveFile?.(saved);
          } finally {
            onChange?.(field.id, undefined);
          }
        })()}>
          Sign again
        </button>
      </div>
    );
  }
  if (!onUpload) return <span className="small muted">A signature is drawn here when the form is being filled in.</span>;
  return (
    <div className="stack" style={{ gap: 8, maxWidth: 460 }}>
      <canvas ref={canvas} width={920} height={300} aria-label={`${field.label}: draw your signature`} role="img"
        style={{ width: "100%", height: 150, border: "1px dashed var(--control)", borderRadius: 6, background: "#fff", touchAction: "none" }}
        onPointerDown={start} onPointerMove={move} onPointerUp={() => (drawing.current = false)} onPointerLeave={() => (drawing.current = false)} />
      <label className="field">
        Name of the person signing
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {error && <div className="small" style={{ color: "var(--bad)", fontWeight: 600 }} role="alert">{error}</div>}
      <div className="row">
        <button type="button" className="btn" disabled={!dirty || busy} onClick={() => void save()}>{busy ? "Saving…" : "Save signature"}</button>
        <button type="button" className="btn" disabled={!dirty} onClick={clear}>Clear</button>
      </div>
    </div>
  );
}

function GpsInput({ value, set }: { value: Record<string, unknown> | undefined; set: (v: unknown) => void }) {
  const [state, setState] = useState<"idle" | "asking" | "failed">("idle");
  const [problem, setProblem] = useState("");
  const hasFix = typeof value?.lat === "number";

  // Location is requested once, only when the person presses the button.
  function locate() {
    if (!("geolocation" in navigator)) {
      setProblem("This device cannot give a location.");
      setState("failed");
      return;
    }
    setState("asking");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        set({ lat: position.coords.latitude, lng: position.coords.longitude, accuracy_m: position.coords.accuracy, captured_at: new Date(position.timestamp).toISOString() });
        setState("idle");
      },
      (error) => {
        setProblem(error.code === error.PERMISSION_DENIED ? "Location permission was not given." : "The location could not be found.");
        setState("failed");
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  }

  return (
    <div className="stack" style={{ gap: 8 }}>
      {hasFix && (
        <Chip tone="ok">
          {(value!.lat as number).toFixed(5)}, {(value!.lng as number).toFixed(5)}
        </Chip>
      )}
      <div className="row">
        <button type="button" className="btn" style={{ minHeight: 44 }} disabled={state === "asking"} onClick={locate}>
          {state === "asking" ? "Finding location…" : hasFix ? "Update location" : state === "failed" ? "Try again" : "Use my location"}
        </button>
      </div>
      {state === "failed" && <div className="small" style={{ color: "var(--warn)", fontWeight: 600 }}>{problem} You can try again, or say why below.</div>}
      {!hasFix && (
        <label className="field">
          If location is not available, say why
          <input value={(value?.unavailable_reason as string) ?? ""} onChange={(e) => set(e.target.value ? { unavailable_reason: e.target.value } : undefined)} />
        </label>
      )}
    </div>
  );
}

function WorkerPicker({ field, value, set }: { field: FieldDef; value: unknown; set: (v: unknown) => void }) {
  const [people, setPeople] = useState<Ref[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const multiple = field.config?.multiple === true;
  useEffect(() => {
    api<{ items: Ref[] }>("/api/forms/pickers/workers").then((r) => setPeople(r.items)).catch((e) => setError(errorMessage(e)));
  }, []);
  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!people) return <span className="muted">Loading people…</span>;
  if (multiple) {
    const chosen = (value as Ref[]) ?? [];
    return (
      <div role="group" aria-labelledby={`label-${field.id}`} className="stack" style={{ gap: 2, maxHeight: 220, overflowY: "auto" }}>
        {people.map((p) => (
          <label className="check" key={p.id}>
            <input type="checkbox" checked={chosen.some((c) => c.id === p.id)}
              onChange={(e) => {
                const next = e.target.checked ? [...chosen, p] : chosen.filter((c) => c.id !== p.id);
                set(next.length ? next : undefined);
              }} />
            {p.label}
          </label>
        ))}
      </div>
    );
  }
  return (
    <select aria-labelledby={`label-${field.id}`} style={{ minHeight: 44, padding: "0 10px", border: "1px solid var(--control)", borderRadius: 6, font: "inherit" }}
      value={(value as Ref)?.id ?? ""} onChange={(e) => set(people.find((p) => p.id === e.target.value))}>
      <option value="">Choose…</option>
      {people.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
    </select>
  );
}

function JobPicker({ field, value, set }: { field: FieldDef; value: unknown; set: (v: unknown) => void }) {
  const [jobs, setJobs] = useState<Ref[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<{ items: Ref[] }>("/api/forms/pickers/jobs").then((r) => setJobs(r.items)).catch((e) => setError(errorMessage(e)));
  }, []);
  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!jobs) return <span className="muted">Loading jobs…</span>;
  if (jobs.length === 0) return <Notice tone="info">You have no jobs from the last week to choose from. The office issues jobs under Daily jobs.</Notice>;
  return (
    <select aria-labelledby={`label-${field.id}`} style={{ minHeight: 44, padding: "0 10px", border: "1px solid var(--control)", borderRadius: 6, font: "inherit" }}
      value={(value as Ref)?.id ?? ""} onChange={(e) => set(jobs.find((j) => j.id === e.target.value))}>
      <option value="">Choose…</option>
      {jobs.map((j) => <option key={j.id} value={j.id}>{j.label}</option>)}
    </select>
  );
}

function SitePicker({ field, value, set }: { field: FieldDef; value: unknown; set: (v: unknown) => void }) {
  const [sites, setSites] = useState<(Ref & { address?: string | null })[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<{ items: (Ref & { address?: string | null })[] }>("/api/forms/pickers/sites").then((r) => setSites(r.items)).catch((e) => setError(errorMessage(e)));
  }, []);
  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!sites) return <span className="muted">Loading sites…</span>;
  if (sites.length === 0) return <Notice tone="info">No sites have been added yet. The office adds them under Sites.</Notice>;
  return (
    <select aria-labelledby={`label-${field.id}`} style={{ minHeight: 44, padding: "0 10px", border: "1px solid var(--control)", borderRadius: 6, font: "inherit" }}
      value={(value as Ref)?.id ?? ""}
      onChange={(e) => {
        const site = sites.find((s) => s.id === e.target.value);
        set(site ? { id: site.id, label: site.label } : undefined);
      }}>
      <option value="">Choose…</option>
      {sites.map((s) => <option key={s.id} value={s.id}>{s.label}{s.address ? ` · ${s.address}` : ""}</option>)}
    </select>
  );
}

function ActionsInput({ value, set }: { value: Action[]; set: (v: unknown) => void }) {
  const update = (index: number, patch: Partial<Action>) => set(value.map((a, i) => (i === index ? { ...a, ...patch } : a)));
  return (
    <div className="stack" style={{ gap: 10 }}>
      {value.map((action, index) => (
        <div key={index} className="fields" style={{ alignItems: "end", padding: 10, border: "1px solid var(--line)", borderRadius: 6 }}>
          <label className="field">What needs doing<input value={action.description} onChange={(e) => update(index, { description: e.target.value })} /></label>
          <label className="field">Who is responsible<input value={action.responsible} onChange={(e) => update(index, { responsible: e.target.value })} /></label>
          <label className="field">Due date<input type="date" value={action.due_date ?? ""} onChange={(e) => update(index, { due_date: e.target.value || undefined })} /></label>
          <button type="button" className="btn" onClick={() => set(value.filter((_, i) => i !== index).length ? value.filter((_, i) => i !== index) : undefined)}>Remove action</button>
        </div>
      ))}
      <div>
        <button type="button" className="btn" onClick={() => set([...value, { description: "", responsible: "", status: "open" }])}>Add an action</button>
      </div>
    </div>
  );
}

function TableInput({ field, value, set }: { field: FieldDef; value: Record<string, string>[]; set: (v: unknown) => void }) {
  const columns = (field.config?.table_columns as Ref[]) ?? [];
  const update = (index: number, column: string, text: string) => set(value.map((row, i) => (i === index ? { ...row, [column]: text } : row)));
  return (
    <div className="stack" style={{ gap: 8 }}>
      {value.map((row, index) => (
        <div key={index} className="fields" style={{ alignItems: "end", padding: 10, border: "1px solid var(--line)", borderRadius: 6 }}>
          {columns.map((c) => (
            <label className="field" key={c.id}>
              {c.label}
              <input value={row[c.id] ?? ""} onChange={(e) => update(index, c.id, e.target.value)} />
            </label>
          ))}
          <button type="button" className="btn" aria-label={`Remove row ${index + 1} from ${field.label}`}
            onClick={() => { const next = value.filter((_, i) => i !== index); set(next.length ? next : undefined); }}>
            Remove row
          </button>
        </div>
      ))}
      <div>
        <button type="button" className="btn" style={{ minHeight: 44 }} onClick={() => set([...value, Object.fromEntries(columns.map((c) => [c.id, ""]))])}>
          Add a row
        </button>
      </div>
    </div>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card">
      <div className="card-head"><h2>{title}</h2></div>
      <div className="card-pad" style={{ paddingTop: 4 }}>{children}</div>
    </section>
  );
}
