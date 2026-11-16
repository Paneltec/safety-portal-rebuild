import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fetchBlobUrl, formatDate, formatDateTime, openFile } from "../api";
import { useAuth } from "../auth";
import { Chip, LoadError, Loading, Modal, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

export interface Asset {
  id: string; name: string; label: string; registration: string | null; kind: string; kind_label: string; source: string; source_label: string; simulated: boolean;
  sub_type: string | null; category_id: string | null; category: string | null; serial_number: string | null; make: string | null; model: string | null; year: number | null;
  site_id: string | null; site: string | null; location_note: string | null; status: string; status_label: string; fuel_type: string | null; tank_capacity_l: number | null;
  odometer_km: number | null; engine_hours: number | null; meter_at: string | null;
  meter_review: { readings: { meter: string; on_file: number; incoming: number }[]; source: string; reading_date: string } | null;
  last_inspection_date: string | null; next_inspection_date: string | null; inspection_status: string | null; tags: string[]; not_listed_by_provider: boolean;
  tracker: { lat: number | null; lng: number | null; speed_kmh: number | null; last_seen_at: string | null; connection: string | null; stale: boolean | null } | null;
  notes?: string | null; documents?: AssetDoc[]; can_edit?: boolean; can_delete?: boolean; can_reset_meter?: boolean; scan_url?: string | null;
}
interface AssetDoc { id: string; kind: string; title: string; issued_date: string | null; expiry_date: string | null; expiry_status: string | null; file: { original_name: string } | null }
interface AssetPage { items: Asset[]; total: number; page: number; page_size: number; can_edit: boolean; kinds: Record<string, string>; statuses: Record<string, string> }
interface Category { id: string; name: string; count: number }
interface NavixyStatus {
  state: string; ready: boolean; message: string; simulated: boolean; tags: { id: string; name: string }[]; selected_tags: string[]; grouping_note: string;
  last_synced_at: string | null; last_error: string | null; can_sync: boolean;
}
interface SyncResult {
  status: string; dry_run: boolean; simulated: boolean; counts: Record<string, number>; error: string | null;
  created: { name: string; tags: string[] }[]; updated: { name: string; changes: string[] }[]; not_listed: { name: string }[]; warnings: string[];
}

const PAGE_SIZE = 25;
const FLEET_KINDS: [string, string][] = [["vehicle", "Vehicle"], ["plant", "Plant"], ["trailer", "Trailer"], ["other", "Other"]];
export const num = (value: number | null | undefined, unit: string) => (value === null || value === undefined ? "Not recorded" : `${value.toLocaleString("en-AU", { maximumFractionDigits: 1 })} ${unit}`);

function StatusChip({ a }: { a: Asset }) {
  return <Chip tone={a.status === "active" ? "ok" : a.status === "out_of_service" ? "warn" : "neutral"}>{a.status_label}</Chip>;
}

export function SourceNote({ a }: { a: { source: string; source_label: string; simulated: boolean } }) {
  if (a.source === "manual") return null;
  return a.simulated ? <Chip tone="warn">Simulator, not real</Chip> : <span className="muted small"> · {a.source_label}</span>;
}

export function Fleet() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState<"fleet" | "equipment">("fleet");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const [managing, setManaging] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => { setQuery(search.trim()); setPage(1); }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const list = useLoad(() => {
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE), kind: tab === "equipment" ? "equipment" : kind || "fleet" });
    if (query) params.set("q", query);
    if (status) params.set("status", status);
    if (tab === "equipment" && category) params.set("category_id", category);
    return api<AssetPage>(`/api/assets?${params}`);
  }, [tab, query, kind, status, category, page]);
  const categories = useLoad(() => api<{ items: Category[]; can_edit: boolean }>("/api/equipment/categories"), []);
  const data = list.data;
  const equipment = tab === "equipment";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Fleet and equipment</h1>
          <p className="muted">Vehicles, plant, trailers and equipment, with their meters, documents and QR labels.</p>
        </div>
        {can("assets", "edit") && <button type="button" className="btn primary" onClick={() => setAdding(true)}>{equipment ? "Add equipment" : "Add vehicle or plant"}</button>}
      </div>

      {!equipment && <NavixyPanel onChanged={list.reload} />}

      <section className="card">
        <div className="tabs" role="tablist" aria-label="Register">
          <button type="button" role="tab" className="tab" aria-selected={!equipment} onClick={() => { setTab("fleet"); setPage(1); }}>Vehicles and plant</button>
          <button type="button" role="tab" className="tab" aria-selected={equipment} onClick={() => { setTab("equipment"); setPage(1); }}>Equipment</button>
        </div>
        <div className="toolbar">
          <label className="field" style={{ flex: "1 1 220px" }}>
            Search name, registration or serial
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
          {equipment ? (
            <label className="field">Category
              <select value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
                <option value="">All categories</option>
                {(categories.data?.items ?? []).map((c) => <option key={c.id} value={c.id}>{c.name} ({c.count})</option>)}
              </select>
            </label>
          ) : (
            <label className="field">Type
              <select value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}>
                <option value="">All types</option>
                {FLEET_KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select>
            </label>
          )}
          <label className="field">Status
            <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="">In use and out of service</option>
              <option value="active">In use</option>
              <option value="out_of_service">Out of service</option>
              <option value="archived">Archived</option>
            </select>
          </label>
          {equipment && categories.data?.can_edit && <button type="button" className="btn" onClick={() => setManaging(true)}>Categories</button>}
        </div>
        {list.error ? (
          <div className="card-pad"><LoadError message={list.error} onRetry={list.reload} /></div>
        ) : !data ? (
          <Loading />
        ) : data.items.length === 0 ? (
          <div className="empty">{query || kind || status || category ? "Nothing matches these filters." : equipment ? "No equipment yet." : "No vehicles or plant yet. Add one, or bring them in from Navixy above."}</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  {equipment
                    ? <tr><th scope="col">Equipment</th><th scope="col">Category</th><th scope="col">Serial</th><th scope="col">Next inspection</th><th scope="col">Status</th></tr>
                    : <tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Registration</th><th scope="col">Odometer</th><th scope="col">Hours</th><th scope="col">Status</th></tr>}
                </thead>
                <tbody>
                  {data.items.map((a) => (
                    <tr key={a.id}>
                      <td>
                        <Link to={`/app/fleet/${a.id}`}>{a.name}</Link> <SourceNote a={a} />
                        {a.not_listed_by_provider && <> <Chip tone="warn">No longer listed by Navixy</Chip></>}
                        {a.meter_review && <> <Chip tone="warn">Meter to check</Chip></>}
                      </td>
                      {equipment ? (
                        <>
                          <td>{a.category ?? "—"}</td>
                          <td>{a.serial_number ?? "—"}</td>
                          <td>{a.next_inspection_date ? <>{formatDate(a.next_inspection_date)} {a.inspection_status === "overdue" && <Chip tone="bad">Overdue</Chip>}{a.inspection_status === "due_soon" && <Chip tone="warn">Due soon</Chip>}</> : <span className="muted">Not set</span>}</td>
                        </>
                      ) : (
                        <>
                          <td>{a.sub_type ? `${a.kind_label}: ${a.sub_type}` : a.kind_label}</td>
                          <td>{a.registration ?? "—"}</td>
                          <td>{a.odometer_km === null ? <span className="muted">Not recorded</span> : num(a.odometer_km, "km")}</td>
                          <td>{a.engine_hours === null ? <span className="muted">Not recorded</span> : num(a.engine_hours, "h")}</td>
                        </>
                      )}
                      <td><StatusChip a={a} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ alignItems: "center", borderTop: "1px solid var(--line-soft)" }}>
              <span className="muted small" role="status">Showing {(data.page - 1) * data.page_size + 1}–{(data.page - 1) * data.page_size + data.items.length} of {data.total}</span>
              <span className="spacer" />
              <button type="button" className="btn small" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button>
              <button type="button" className="btn small" disabled={page * PAGE_SIZE >= data.total} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </>
        )}
      </section>
      {adding && <AssetForm equipment={equipment} categories={categories.data?.items ?? []} onClose={() => setAdding(false)} onSaved={(a) => navigate(`/app/fleet/${a.id}`)} />}
      {managing && <CategoriesModal onClose={() => { setManaging(false); categories.reload(); list.reload(); }} />}
    </>
  );
}

function NavixyPanel({ onChanged }: { onChanged: () => void }) {
  const status = useLoad(() => api<NavixyStatus>("/api/fleet/navixy/status"), []);
  const { me } = useAuth();
  const [result, setResult] = useState<SyncResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const s = status.data;
  if (!s) return null;

  async function run(dryRun: boolean) {
    setBusy(true);
    setError(null);
    try {
      const r = await api<SyncResult>("/api/fleet/navixy/sync", { method: "POST", body: { dry_run: dryRun } });
      setResult(r);
      if (!dryRun) { status.reload(); onChanged(); }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const c = result?.counts ?? {};
  return (
    <section className="card card-pad stack" style={{ gap: 10 }} aria-label="Navixy vehicle list">
      <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <strong>Navixy vehicle list</strong>
        {s.simulated ? <Chip tone="warn">Simulator, not real</Chip> : s.ready ? <Chip tone="ok">Connected</Chip> : <Chip tone="neutral">Not connected</Chip>}
        <span className="muted small">{s.last_synced_at ? `Last brought in ${formatDateTime(s.last_synced_at, me?.org_timezone ?? "Australia/Hobart")}` : "Never brought in"}</span>
        <span className="spacer" />
        {s.can_sync && s.ready && (
          <>
            {s.tags.length > 0 && <button type="button" className="btn small" onClick={() => setChoosing(true)}>Choose tags ({s.selected_tags.length || "all"})</button>}
            <button type="button" className="btn small" disabled={busy} onClick={() => run(true)}>Preview</button>
          </>
        )}
      </div>
      {!s.ready && <p className="muted small" style={{ margin: 0 }}>{s.message} Vehicles can still be added by hand, and forms still accept a typed vehicle.</p>}
      {s.last_error && !result && <Notice tone="warn">The last attempt failed: {s.last_error} Nothing on the register was changed.</Notice>}
      {error && <Notice tone="bad">{error}</Notice>}
      {result && (
        <div className="stack" style={{ gap: 8 }}>
          {result.status === "failed" ? (
            <Notice tone="bad">{result.error} Nothing on the register was changed.</Notice>
          ) : (
            <Notice tone={result.dry_run ? "info" : "ok"}>
              {result.dry_run ? "Preview only, nothing has been saved. " : "Done. "}
              {c.listed} tracker{c.listed === 1 ? "" : "s"} listed{c.in_scope !== c.listed ? `, ${c.in_scope} with the chosen tags` : ""}: {c.created} new, {c.updated} changed, {c.unchanged} unchanged
              {c.kept_local ? `, ${c.kept_local} name${c.kept_local === 1 ? "" : "s"} kept as edited here` : ""}{c.not_listed ? `, ${c.not_listed} no longer listed` : ""}{c.meter_review ? `, ${c.meter_review} meter reading${c.meter_review === 1 ? "" : "s"} to check` : ""}.
            </Notice>
          )}
          {result.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
          {result.dry_run && result.status !== "failed" && (
            <>
              {result.created.length > 0 && <p className="small" style={{ margin: 0 }}><strong>New:</strong> {result.created.slice(0, 12).map((x) => x.name).join(", ")}{result.created.length > 12 ? ` and ${result.created.length - 12} more` : ""}</p>}
              {result.not_listed.length > 0 && <p className="small" style={{ margin: 0 }}><strong>No longer listed (kept, flagged):</strong> {result.not_listed.map((x) => x.name).join(", ")}</p>}
              <div className="row" style={{ gap: 8 }}>
                <button type="button" className="btn primary" disabled={busy} onClick={() => run(false)}>Bring in now</button>
                <button type="button" className="btn" onClick={() => setResult(null)}>Cancel</button>
              </div>
            </>
          )}
        </div>
      )}
      {choosing && <TagsModal status={s} onClose={() => { setChoosing(false); status.reload(); }} />}
    </section>
  );
}

function TagsModal({ status, onClose }: { status: NavixyStatus; onClose: () => void }) {
  const [chosen, setChosen] = useState<string[]>(status.selected_tags);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    try {
      await api("/api/fleet/navixy/tags", { method: "PUT", body: { tag_ids: chosen } });
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Modal title="Navixy tags to include" onClose={onClose}>
      <div className="stack">
        <p className="muted small">{status.grouping_note} Tick the tags whose vehicles belong on this register. Leave all unticked to include every tracker.</p>
        {status.tags.map((t) => (
          <label key={t.id} className="check">
            <input type="checkbox" checked={chosen.includes(t.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, t.id] : chosen.filter((x) => x !== t.id))} /> {t.name}
          </label>
        ))}
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" onClick={save}>Save</button>
        </div>
      </div>
    </Modal>
  );
}

function CategoriesModal({ onClose }: { onClose: () => void }) {
  const cats = useLoad(() => api<{ items: Category[] }>("/api/equipment/categories"), []);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); cats.reload(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <Modal title="Equipment categories" onClose={onClose}>
      <div className="stack">
        {(cats.data?.items ?? []).map((c) => (
          <div key={c.id} className="row" style={{ gap: 8, alignItems: "center" }}>
            <span style={{ flex: 1 }}>{c.name} <span className="muted small">({c.count})</span></span>
            <button type="button" className="btn small" aria-label={`Rename ${c.name}`} onClick={() => { const n = window.prompt("New name", c.name); if (n) void act(() => api(`/api/equipment/categories/${c.id}`, { method: "PATCH", body: { name: n } })); }}>Rename</button>
            <button type="button" className="btn small" aria-label={`Remove ${c.name}`} disabled={c.count > 0} title={c.count > 0 ? "Move its equipment first" : undefined} onClick={() => void act(() => api(`/api/equipment/categories/${c.id}`, { method: "DELETE" }))}>Remove</button>
          </div>
        ))}
        <form className="row" style={{ gap: 8, alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); void act(async () => { await api("/api/equipment/categories", { method: "POST", body: { name } }); setName(""); }); }}>
          <label className="field" style={{ flex: 1 }}>New category<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} /></label>
          <button type="submit" className="btn primary">Add</button>
        </form>
        {error && <Notice tone="bad">{error}</Notice>}
      </div>
    </Modal>
  );
}

function AssetForm({ asset, equipment, categories, onClose, onSaved }: { asset?: Asset; equipment: boolean; categories: Category[]; onClose: () => void; onSaved: (a: Asset) => void }) {
  const [v, setV] = useState({
    name: asset?.name ?? "", kind: asset?.kind ?? (equipment ? "equipment" : "vehicle"), sub_type: asset?.sub_type ?? "", category_id: asset?.category_id ?? "",
    registration: asset?.registration ?? "", serial_number: asset?.serial_number ?? "", make: asset?.make ?? "", model: asset?.model ?? "", year: asset?.year ? String(asset.year) : "",
    status: asset?.status ?? "active", tank_capacity_l: asset?.tank_capacity_l != null ? String(asset.tank_capacity_l) : "", location_note: asset?.location_note ?? "",
    last_inspection_date: asset?.last_inspection_date ?? "", next_inspection_date: asset?.next_inspection_date ?? "", notes: asset?.notes ?? "", odometer_km: "", engine_hours: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });
  const isEquipment = v.kind === "equipment";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const text = (x: string) => x.trim() || null;
    const number = (x: string) => (x.trim() === "" ? null : Number(x));
    const body: Record<string, unknown> = {
      name: v.name, kind: v.kind, sub_type: text(v.sub_type), category_id: isEquipment ? v.category_id || null : null, registration: text(v.registration), serial_number: text(v.serial_number),
      make: text(v.make), model: text(v.model), year: number(v.year), status: v.status, tank_capacity_l: number(v.tank_capacity_l), location_note: text(v.location_note),
      last_inspection_date: v.last_inspection_date || null, next_inspection_date: v.next_inspection_date || null, notes: text(v.notes),
    };
    if (!asset) { body.odometer_km = number(v.odometer_km); body.engine_hours = number(v.engine_hours); }
    try {
      onSaved(await api<Asset>(asset ? `/api/assets/${asset.id}` : "/api/assets", { method: asset ? "PUT" : "POST", body }));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  return (
    <Modal title={asset ? `Edit ${asset.name}` : isEquipment ? "Add equipment" : "Add vehicle or plant"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">Name<input value={v.name} onChange={set("name")} required maxLength={200} autoFocus /></label>
        <div className="grid-2">
          <label className="field">Type
            <select value={v.kind} onChange={set("kind")}>
              {FLEET_KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              <option value="equipment">Equipment</option>
            </select>
          </label>
          {isEquipment ? (
            <label className="field">Category
              <select value={v.category_id} onChange={set("category_id")}><option value="">No category</option>{categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
            </label>
          ) : (
            <label className="field">Kind of {v.kind} (optional)<input value={v.sub_type} onChange={set("sub_type")} placeholder="For example Ute, Tipper, Excavator" maxLength={80} /></label>
          )}
          {!isEquipment && <label className="field">Registration<input value={v.registration} onChange={set("registration")} maxLength={40} /></label>}
          <label className="field">Serial or VIN<input value={v.serial_number} onChange={set("serial_number")} maxLength={80} /></label>
          <label className="field">Make<input value={v.make} onChange={set("make")} maxLength={80} /></label>
          <label className="field">Model<input value={v.model} onChange={set("model")} maxLength={80} /></label>
          {!isEquipment && <label className="field">Year<input type="number" value={v.year} onChange={set("year")} min={1950} max={2100} /></label>}
          {!isEquipment && <label className="field">Fuel tank size (litres)<input type="number" value={v.tank_capacity_l} onChange={set("tank_capacity_l")} min={0} step="any" /></label>}
          {!asset && !isEquipment && <label className="field">Odometer now (km)<input type="number" value={v.odometer_km} onChange={set("odometer_km")} min={0} step="any" /></label>}
          {!asset && !isEquipment && <label className="field">Hour meter now<input type="number" value={v.engine_hours} onChange={set("engine_hours")} min={0} step="any" /></label>}
          <label className="field">Last inspected<input type="date" value={v.last_inspection_date} onChange={set("last_inspection_date")} /></label>
          <label className="field">Next inspection due<input type="date" value={v.next_inspection_date} onChange={set("next_inspection_date")} /></label>
          <label className="field">Status
            <select value={v.status} onChange={set("status")}><option value="active">In use</option><option value="out_of_service">Out of service</option><option value="archived">Archived</option></select>
          </label>
          <label className="field">Where it is kept<input value={v.location_note} onChange={set("location_note")} maxLength={200} /></label>
        </div>
        <label className="field">Notes<textarea rows={3} value={v.notes} onChange={set("notes")} maxLength={4000} /></label>
        {!asset && !isEquipment && <p className="muted small">Leave a meter empty if it is not known. Empty means not recorded; it is not treated as zero.</p>}
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{asset ? "Save" : "Add"}</button>
        </div>
      </form>
    </Modal>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <><dt>{label}</dt><dd>{children}</dd></>;
}

export function AssetDetail({ extra }: { extra?: (asset: Asset, reload: () => void) => ReactNode }) {
  const { assetId = "" } = useParams();
  const navigate = useNavigate();
  const { me } = useAuth();
  const tz = me?.org_timezone ?? "Australia/Hobart";
  const load = useLoad(() => api<Asset>(`/api/assets/${assetId}`), [assetId]);
  const categories = useLoad(() => api<{ items: Category[] }>("/api/equipment/categories"), []);
  const [editing, setEditing] = useState(false);
  const [meter, setMeter] = useState<"update" | "reset" | null>(null);
  const [showLabel, setShowLabel] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const a = load.data;
  if (load.error) return <LoadError message={load.error} onRetry={load.reload} />;
  if (!a) return <Loading />;
  const equipment = a.kind === "equipment";

  async function act(fn: () => Promise<unknown>, done: string) {
    setMsg(null);
    try { await fn(); setMsg({ tone: "ok", text: done }); load.reload(); } catch (e) { setMsg({ tone: "bad", text: errorMessage(e) }); }
  }
  return (
    <>
      <p className="small"><Link to="/app/fleet">Fleet and equipment</Link> / {a.name}</p>
      <div className="page-head">
        <div>
          <h1>{a.name}</h1>
          <p className="muted">{a.sub_type ? `${a.kind_label}: ${a.sub_type}` : a.kind_label}{a.registration ? ` · ${a.registration}` : ""} <StatusChip a={a} /> <SourceNote a={a} /></p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button type="button" className="btn" onClick={() => setShowLabel(true)}>QR label</button>
          {a.can_edit && <button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>}
          {a.can_delete && <button type="button" className="btn danger" onClick={() => { if (window.confirm(`Remove ${a.name} from the register? Its history is kept.`)) void act(async () => { await api(`/api/assets/${a.id}`, { method: "DELETE" }); navigate("/app/fleet"); }, "Removed"); }}>Remove</button>}
        </div>
      </div>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {a.not_listed_by_provider && <Notice tone="warn">Navixy no longer lists this tracker (or it no longer has a chosen tag). Nothing was deleted. Archive it here if it has left the fleet.</Notice>}
      {a.meter_review && (
        <Notice tone="warn">
          {a.source_label} reported a lower reading on {formatDate(a.meter_review.reading_date)} than the one on file ({a.meter_review.readings.map((r) => `${r.meter === "odometer_km" ? "odometer" : "hour meter"} ${r.incoming.toLocaleString()} against ${r.on_file.toLocaleString()}`).join("; ")}). It was not used.
          {a.can_edit && <> <button type="button" className="btn small" onClick={() => void act(() => api(`/api/assets/${a.id}/meter/dismiss-review`, { method: "POST" }), "Kept the reading on file")}>Keep the reading on file</button></>}
          {a.can_reset_meter && <> <button type="button" className="btn small" onClick={() => setMeter("reset")}>Reset the meter</button></>}
        </Notice>
      )}

      <div className="grid-2" style={{ alignItems: "start" }}>
        <section className="card card-pad">
          <h2>Details</h2>
          <dl className="kv">
            {!equipment && <Row label="Registration">{a.registration ?? "—"}</Row>}
            {equipment && <Row label="Category">{a.category ?? "—"}</Row>}
            <Row label="Serial or VIN">{a.serial_number ?? "—"}</Row>
            <Row label="Make and model">{[a.make, a.model, a.year].filter(Boolean).join(" ") || "—"}</Row>
            <Row label="Kept at">{a.location_note ?? a.site ?? "—"}</Row>
            <Row label="Last inspected">{a.last_inspection_date ? formatDate(a.last_inspection_date) : "—"}</Row>
            <Row label="Next inspection">{a.next_inspection_date ? <>{formatDate(a.next_inspection_date)} {a.inspection_status === "overdue" && <Chip tone="bad">Overdue</Chip>}{a.inspection_status === "due_soon" && <Chip tone="warn">Due soon</Chip>}</> : "Not set"}</Row>
            {!equipment && <Row label="Fuel tank">{a.tank_capacity_l === null ? "Not recorded" : `${a.tank_capacity_l} litres`}</Row>}
            {a.tags.length > 0 && <Row label="Navixy tags">{a.tags.join(", ")}</Row>}
            {a.notes && <Row label="Notes">{a.notes}</Row>}
          </dl>
        </section>
        {!equipment && (
          <section className="card card-pad">
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
              <h2>Meters</h2>
              {a.can_edit && <button type="button" className="btn small" onClick={() => setMeter("update")}>Update meter</button>}
            </div>
            <dl className="kv">
              <Row label="Odometer">{num(a.odometer_km, "km")}</Row>
              <Row label="Hour meter">{num(a.engine_hours, "hours")}</Row>
              <Row label="Last reading">{a.meter_at ? formatDateTime(a.meter_at, tz) : "—"}</Row>
              {a.tracker && <Row label="Tracker last heard">{a.tracker.last_seen_at ? <>{formatDateTime(a.tracker.last_seen_at, tz)} {a.tracker.stale && <Chip tone="warn">More than a day ago</Chip>}</> : "Not reported"}</Row>}
              {a.tracker && <Row label="Last position">{a.tracker.lat !== null && a.tracker.lng !== null ? `${a.tracker.lat.toFixed(5)}, ${a.tracker.lng.toFixed(5)}` : "Not reported"}</Row>}
            </dl>
            <MeterHistory assetId={a.id} stamp={a.meter_at ?? ""} />
          </section>
        )}
      </div>
      {extra?.(a, load.reload)}
      <Documents asset={a} reload={load.reload} />
      {editing && <AssetForm asset={a} equipment={equipment} categories={categories.data?.items ?? []} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); load.reload(); }} />}
      {meter && <MeterModal asset={a} mode={meter} onClose={() => setMeter(null)} onSaved={() => { setMeter(null); load.reload(); }} />}
      {showLabel && <LabelModal asset={a} onClose={() => setShowLabel(false)} onRotated={load.reload} />}
    </>
  );
}

function MeterHistory({ assetId, stamp }: { assetId: string; stamp: string }) {
  const [open, setOpen] = useState(false);
  const rows = useLoad(() => (open ? api<{ items: { id: string; reading_date: string; odometer_km: number | null; engine_hours: number | null; source_label: string; applied: boolean; note: string | null; recorded_by: string | null }[] }>(`/api/assets/${assetId}/meter-history`) : Promise.resolve(null)), [open, assetId, stamp]);
  return (
    <div className="stack" style={{ gap: 6, marginTop: 8 }}>
      <button type="button" className="btn small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide reading history" : "Show reading history"}</button>
      {open && rows.data && (rows.data.items.length === 0 ? <p className="muted small">No readings yet.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th scope="col">Date</th><th scope="col">Odometer</th><th scope="col">Hours</th><th scope="col">From</th></tr></thead>
          <tbody>{rows.data.items.map((r) => (
            <tr key={r.id}>
              <td>{formatDate(r.reading_date)}</td><td>{r.odometer_km ?? "—"}</td><td>{r.engine_hours ?? "—"}</td>
              <td>{r.source_label}{r.recorded_by ? `, ${r.recorded_by}` : ""}{!r.applied && <> <Chip tone="warn">Not used</Chip></>}{r.note && <div className="muted small">{r.note}</div>}</td>
            </tr>))}</tbody>
        </table></div>
      ))}
    </div>
  );
}

function MeterModal({ asset, mode, onClose, onSaved }: { asset: Asset; mode: "update" | "reset"; onClose: () => void; onSaved: () => void }) {
  const [odo, setOdo] = useState("");
  const [hours, setHours] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const body: Record<string, unknown> = { odometer_km: odo === "" ? null : Number(odo), engine_hours: hours === "" ? null : Number(hours) };
    if (mode === "reset") body.reason = note; else body.note = note || null;
    try {
      await api(`/api/assets/${asset.id}/meter${mode === "reset" ? "/reset" : ""}`, { method: "POST", body });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <Modal title={mode === "reset" ? "Reset a meter" : "Update meter"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {mode === "reset" && <Notice tone="warn">Use this only when a meter was replaced or an earlier reading was wrong. The old figure, the new one and your reason are kept in the history.</Notice>}
        <label className="field">Odometer (km){asset.odometer_km !== null && <span className="muted small"> now {asset.odometer_km.toLocaleString()}</span>}<input type="number" min={0} step="any" value={odo} onChange={(e) => setOdo(e.target.value)} autoFocus /></label>
        <label className="field">Hour meter{asset.engine_hours !== null && <span className="muted small"> now {asset.engine_hours.toLocaleString()}</span>}<input type="number" min={0} step="any" value={hours} onChange={(e) => setHours(e.target.value)} /></label>
        <label className="field">{mode === "reset" ? "Reason" : "Note (optional)"}<input value={note} onChange={(e) => setNote(e.target.value)} required={mode === "reset"} minLength={mode === "reset" ? 5 : undefined} maxLength={400} /></label>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary">{mode === "reset" ? "Reset meter" : "Save reading"}</button>
        </div>
      </form>
    </Modal>
  );
}

function LabelModal({ asset, onClose, onRotated }: { asset: Asset; onClose: () => void; onRotated: () => void }) {
  const qr = useLoad(() => fetchBlobUrl(`/api/assets/${asset.id}/qr.png`), [asset.id, asset.scan_url]);
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal title={`QR label for ${asset.name}`} onClose={onClose}>
      <div className="stack" style={{ alignItems: "center" }}>
        {qr.data ? <img src={qr.data} alt={`QR code for ${asset.name}`} width={220} height={220} /> : <Loading />}
        <p className="muted small" style={{ textAlign: "center" }}>Scanning the label with a phone that is signed in to the portal opens this asset. People outside your organisation see nothing.</p>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
          <button type="button" className="btn primary" onClick={() => void openFile(`/api/assets/${asset.id}/label.pdf`, `label-${asset.name}.pdf`, false).catch((e) => setError(errorMessage(e)))}>Print label (PDF)</button>
          {asset.can_edit && <button type="button" className="btn" onClick={() => { if (window.confirm("Replace the QR code? Labels already printed for this asset will stop working.")) void api(`/api/assets/${asset.id}/rotate-qr`, { method: "POST" }).then(() => { onRotated(); qr.reload(); }).catch((e) => setError(errorMessage(e))); }}>Replace QR code</button>}
        </div>
      </div>
    </Modal>
  );
}

function Documents({ asset, reload }: { asset: Asset; reload: () => void }) {
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const docs = asset.documents ?? [];
  return (
    <section className="card">
      <div className="card-pad row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0 }}>Documents and certificates</h2>
        {asset.can_edit && <button type="button" className="btn small" onClick={() => setAdding(true)}>Add document</button>}
      </div>
      {error && <div className="card-pad"><Notice tone="bad">{error}</Notice></div>}
      {docs.length === 0 ? <div className="empty">Nothing attached yet. Registration papers, manuals and test certificates go here.</div> : (
        <div className="table-wrap"><table>
          <thead><tr><th scope="col">Title</th><th scope="col">Kind</th><th scope="col">Expires</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{docs.map((d) => (
            <tr key={d.id}>
              <td>{d.title}</td>
              <td>{d.kind === "certificate" ? "Certificate" : d.kind === "photo" ? "Photo" : "Document"}</td>
              <td>{d.expiry_date ? <>{formatDate(d.expiry_date)} {d.expiry_status === "expired" && <Chip tone="bad">Expired</Chip>}{d.expiry_status === "expiring" && <Chip tone="warn">Expiring</Chip>}</> : "—"}</td>
              <td className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                <button type="button" className="btn small" aria-label={`Open ${d.title}`} onClick={() => void openFile(`/api/assets/${asset.id}/documents/${d.id}/download?inline=true`, d.file?.original_name ?? d.title, false).catch((e) => setError(errorMessage(e)))}>Open</button>
                {asset.can_edit && <button type="button" className="btn small" aria-label={`Remove ${d.title}`} onClick={() => { if (window.confirm(`Remove ${d.title}?`)) void api(`/api/assets/${asset.id}/documents/${d.id}`, { method: "DELETE" }).then(reload).catch((e) => setError(errorMessage(e))); }}>Remove</button>}
              </td>
            </tr>))}</tbody>
        </table></div>
      )}
      {adding && <DocumentModal assetId={asset.id} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
    </section>
  );
}

function DocumentModal({ assetId, onClose, onSaved }: { assetId: string; onClose: () => void; onSaved: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("document");
  const [expiry, setExpiry] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    form.append("kind", kind);
    form.append("expiry_date", expiry);
    try {
      await api(`/api/assets/${assetId}/documents`, { form });
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  return (
    <Modal title="Add document" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">File<input type="file" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        <label className="field">Title (optional)<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="For example Registration papers" /></label>
        <label className="field">Kind<select value={kind} onChange={(e) => setKind(e.target.value)}><option value="document">Document</option><option value="certificate">Certificate (test, calibration, registration)</option><option value="photo">Photo</option></select></label>
        <label className="field">Expires (optional)<input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} /></label>
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !file}>Add</button>
        </div>
      </form>
    </Modal>
  );
}

/** Where a scanned asset label lands. */
export function AssetScan() {
  const { token = "" } = useParams();
  const scan = useLoad(() => api<{ id: string; label: string; kind_label: string; status_label: string; can_open: boolean }>(`/api/assets/scan/${encodeURIComponent(token)}`), [token]);
  if (scan.error) return <Notice tone="bad">{scan.error}</Notice>;
  if (!scan.data) return <Loading />;
  const a = scan.data;
  return (
    <section className="card card-pad stack">
      <h1>{a.label}</h1>
      <p className="muted">{a.kind_label} · {a.status_label}</p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        {a.can_open && <Link className="btn primary" to={`/app/fleet/${a.id}`}>Open on the register</Link>}
        <Link className="btn" to="/app/forms">Fill in a form for it</Link>
      </div>
    </section>
  );
}
