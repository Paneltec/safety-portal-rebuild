// Thin client for the Safety Portal API. Every call carries the bearer token;
// a 401 on an authenticated call signs the browser out.

const KEY = "sp.token";

export function getToken(): string | null {
  return sessionStorage.getItem(KEY) ?? localStorage.getItem(KEY);
}

/** Remember-me keeps the token after the browser closes; otherwise it lasts for the tab only. */
export function setToken(token: string, remember?: boolean): void {
  const keep = remember ?? localStorage.getItem(KEY) !== null;
  clearToken();
  (keep ? localStorage : sessionStorage).setItem(KEY, token);
}

export function clearToken(): void {
  sessionStorage.removeItem(KEY);
  localStorage.removeItem(KEY);
}

export interface FieldError {
  field_id: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  /** Per-field problems, when the server reports them (form validation). */
  fieldErrors: FieldError[];
  constructor(status: number, message: string, fieldErrors: FieldError[] = []) {
    super(message);
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

function describe(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI validation errors: [{loc: [...], msg: "..."}]
    return detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(item.loc) ? item.loc.filter((p) => p !== "body").join(".") : "";
        return field ? `${field}: ${item.msg ?? "invalid"}` : (item.msg ?? "invalid");
      })
      .join("; ");
  }
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
    const gate = (detail as { certification_gate?: { message: string }[] }).certification_gate;
    const base = (detail as { message: string }).message;
    // The certification gate lists exactly what is missing or out of date.
    return Array.isArray(gate) && gate.length > 0 ? `${base} ${gate.map((g) => g.message).join("; ")}.` : base;
  }
  if (status >= 500) return "The server had a problem. Please try again.";
  return `Request failed (${status})`;
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; form?: FormData; anonymous?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token && !options.anonymous) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? (options.form ? "POST" : "GET"),
      headers,
      // A FormData body (file upload) sets its own multipart content type.
      body: options.form ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  if (response.status === 204) return undefined as T;
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    const message = describe(detail, response.status);
    if (response.status === 401 && token && !options.anonymous) {
      clearToken();
      window.dispatchEvent(new CustomEvent("sp:signed-out", { detail: message }));
    }
    const fieldErrors = (detail as { errors?: FieldError[] } | null)?.errors;
    throw new ApiError(response.status, message, Array.isArray(fieldErrors) ? fieldErrors : []);
  }
  return payload as T;
}

// ---------- shared types ----------

export type Matrix = Record<string, Record<string, boolean>>;

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  role_id: string;
  role_name: string;
  role_locked: boolean;
  org_id: string;
  company_id: string | null;
  status: "active" | "invited" | "disabled";
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
  deleted_at: string | null;
  is_account_holder: boolean;
  worker_id?: string | null;
}

export interface Me extends User {
  org_name: string;
  org_timezone: string;
  effective_permissions: Matrix;
  session: { jti: string; expires_at: string; remember_me: boolean };
}

export interface ResourceMeta {
  label: string;
  supported_actions: string[];
  managed_by: "roles" | "payroll_grants" | "admin_override";
}

export interface Schema {
  actions: string[];
  resources: Record<string, ResourceMeta>;
}

export interface Role {
  role_id: string;
  name: string;
  description: string;
  is_system: boolean;
  is_active: boolean;
  legacy_role: string;
  permissions: Matrix;
  permission_tokens: string[];
  user_count: number;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export function formatDateTime(iso: string | null, timeZone: string): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-AU", {
    timeZone,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(new Date(iso));
}

export interface Org {
  id: string;
  name: string;
  timezone: string;
  staff_login_url: string | null;
  public_portal_url: string | null;
  logo_url: string | null;
  owner_user_id: string | null;
  account_holder: { id: string; name: string } | null;
  [detail: string]: unknown;
}

export interface Company {
  id?: string;
  name: string;
  code: string;
}

/** Fetch a protected file with the session token, then open or save it. */
export async function openFile(path: string, filename: string, download: boolean): Promise<void> {
  const token = getToken();
  let response: Response;
  try {
    response = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail;
    } catch {
      detail = null;
    }
    throw new ApiError(response.status, describe(detail, response.status));
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  if (download) link.download = filename;
  else link.target = "_blank";
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function formatDate(isoDate: string | null): string {
  if (!isoDate) return "—";
  const [y, m, d] = isoDate.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

export function formatSize(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ---------- forms ----------

export interface FieldDef {
  id: string;
  type: string;
  label: string;
  help?: string;
  required: boolean;
  options?: string[];
  config?: Record<string, unknown>;
}

export interface SubmissionFile {
  id: string;
  field_id: string;
  kind: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  missing?: boolean;
}

export interface FormTemplate {
  id: string;
  name: string;
  category: string;
  category_label: string;
  description: string;
  status: "draft" | "published" | "archived";
  published_version: number;
  has_unpublished_changes: boolean;
  field_count: number;
  updated_at: string;
  fields?: FieldDef[];
  published_fields?: FieldDef[] | null;
  certification_gate_enabled?: boolean;
  required_certifications?: string[];
}

export interface Submission {
  id: string;
  template_id: string;
  template_name: string;
  template_version: number | null;
  status: "draft" | "submitted";
  revision: number;
  submitted_by: string;
  submitted_by_name: string | null;
  created_at: string;
  updated_at: string;
  submitted_at: string | null;
  schema?: FieldDef[];
  values?: Record<string, unknown>;
  files?: SubmissionFile[];
}

// ---------- workers and certifications ----------

export interface CertCounts {
  valid: number;
  expiring: number;
  expired: number;
  unknown: number;
  missing?: number;
}

export interface ProtectedHint {
  field: string;
  label: string;
  recorded: boolean;
  masked: string | null;
}

export interface Worker {
  id: string;
  name: string;
  first_name: string;
  last_name: string;
  preferred_name: string | null;
  position: string | null;
  company_id: string | null;
  active: boolean;
  status: "active" | "inactive" | "archived" | "deleted";
  is_me: boolean;
  photo_url: string | null;
  source: { system: string; company: string | null; id: string } | null;
  email?: string | null;
  phone?: string | null;
  mobile?: string | null;
  has_login?: boolean;
  certifications?: CertCounts;
  linked_user?: { id: string; name: string; email: string | null; status: string; role_id: string } | null;
  can_see_personal?: boolean;
  can_reveal_protected?: boolean;
  protected?: ProtectedHint[];
  missing_certifications?: { slug: string; label: string }[];
  possible_duplicates?: { id: string; name: string; email: string | null; status: string; match: string }[];
  [key: string]: unknown;
}

export interface Cert {
  id: string;
  worker: { id: string; name: string; status: string };
  kind: { slug: string; label: string };
  number: string | null;
  issue_date: string | null;
  expiry_date: string | null;
  no_expiry: boolean;
  status: "valid" | "expiring" | "expired" | "unknown";
  status_label: string;
  days_left: number | null;
  notes: string | null;
  evidence: { id: string; name: string; size_bytes: number } | null;
  evidence_url: string | null;
  current: boolean;
  supersedes_id: string | null;
  superseded_by_id: string | null;
  archived_at: string | null;
}

export interface CertKind {
  id: string;
  slug: string;
  label: string;
  aliases: string[];
  is_system: boolean;
  active: boolean;
}

/** Fetch a protected image and return a temporary address for an <img>. */
export async function fetchBlobUrl(path: string): Promise<string> {
  const token = getToken();
  const response = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new ApiError(response.status, "Could not load the image");
  return URL.createObjectURL(await response.blob());
}

// ---------- sites and attendance ----------

export interface SignOnQuestion {
  id: string;
  type: "yesno" | "text" | "choice";
  label: string;
  required: boolean;
  choices?: string[];
}

export interface Site {
  id: string;
  name: string;
  address: string | null;
  suburb: string | null;
  state: string | null;
  postcode: string | null;
  address_full: string;
  kind: "project" | "depot" | "other";
  active: boolean;
  company_id: string | null;
  source: { system: string; company: string | null; id: string } | null;
  on_site: { workers: number; visitors: number };
  deleted_at: string | null;
  description?: string | null;
  signon_questions?: SignOnQuestion[];
  visitor_declaration?: string | null;
  default_visitor_declaration?: string;
  sign_on_url?: string;
  sign_on_address_configured?: boolean;
}

export interface Attendance {
  id: string;
  kind: "worker" | "visitor";
  site_id: string;
  site_name: string | null;
  name: string;
  company: string | null;
  phone: string | null;
  purpose: string | null;
  host: string | null;
  answers: { question_id: string; label: string; value: string | boolean }[];
  signed_in_at: string;
  signed_out_at: string | null;
  sign_out_source: string | null;
  on_site: boolean;
  minutes: number;
  stale: boolean;
  archived_at: string | null;
  deleted_at: string | null;
}

export interface PublicSite {
  site_name: string;
  address: string;
  organisation: string;
  timezone: string;
  questions: SignOnQuestion[];
  visitor_declaration: string;
}

export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}

/** Fetch a protected file and save it (used for CSV exports that are POST or need the session). */
export async function downloadFile(path: string, filename: string, method = "GET"): Promise<void> {
  const token = getToken();
  const response = await fetch(path, { method, headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new ApiError(response.status, "The export could not be created");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// ---------- suppliers and renewals ----------

export interface Supplier {
  id: string;
  name: string;
  abn: string | null;
  trade: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  address: string | null;
  suburb: string | null;
  state: string | null;
  postcode: string | null;
  address_full: string;
  is_contractor: boolean;
  active: boolean;
  status: "active" | "inactive" | "archived" | "deleted";
  source: { system: string; company: string | null; id: string } | null;
  compliance: { valid: number; expiring: number; expired: number; unknown: number; missing: { slug: string; label: string }[] };
  can_edit: boolean;
  locally_edited?: string[];
  logins?: { id: string; name: string; email: string; status: string; role_id: string }[];
  possible_duplicates?: { id: string; name: string; abn: string | null }[];
}

export interface SupplierDoc {
  id: string;
  doc_type: { slug: string; label: string };
  reference: string | null;
  issue_date: string | null;
  expiry_date: string | null;
  no_expiry: boolean;
  status: "valid" | "expiring" | "expired" | "unknown";
  status_label: string;
  days_left: number | null;
  source: string;
  current: boolean;
  file: { name: string } | null;
  file_url: string;
}

export interface DocType {
  id: string;
  slug: string;
  label: string;
  description: string | null;
  required_for_contractors: boolean;
  active: boolean;
}

export interface RenewalUpload {
  id: string;
  doc_type: { slug: string; label: string };
  reference: string | null;
  issue_date: string | null;
  expiry_date: string | null;
  decision: "pending" | "accepted" | "rejected";
  decision_note: string | null;
  file: { name: string } | null;
  file_url: string;
}

export interface Renewal {
  id: string;
  entity_type: "supplier" | "worker";
  entity_id: string;
  entity_name: string;
  doc_types: { slug: string; label: string }[];
  status: string;
  status_label: string;
  recipient_email: string | null;
  message: string | null;
  expires_at: string;
  created_at: string;
  submitted_at: string | null;
  uploaded: number;
  requested: number;
  delivery: string;
  uploads?: RenewalUpload[];
  earlier_uploads?: RenewalUpload[];
  link?: string;
  delivery_note?: string;
}

// ---------- inductions, training and daily jobs ----------

export interface MatrixCell {
  status: "valid" | "expiring" | "expired" | "unknown" | "not_applicable" | "blank";
  expiry_date?: string | null;
  issue_date?: string | null;
  no_expiry?: boolean;
  days_left?: number | null;
  certification_id?: string;
  has_evidence?: boolean;
}

export interface Matrix2 {
  columns: { column_key: string; name: string }[];
  rows: { worker: { id: string; name: string; position: string | null; active: boolean }; cells: Record<string, MatrixCell> }[];
  summary: Record<string, number>;
  window_days: number;
  can_edit: boolean;
  truncated: boolean;
}

export interface Training {
  id: string;
  worker: { id: string; name: string };
  course: string;
  provider: string | null;
  completed_date: string | null;
  notes: string | null;
  evidence: { name: string } | null;
  evidence_url: string | null;
}

export interface Job {
  id: string;
  date: string;
  worker_id: string;
  worker_name: string;
  site_id: string | null;
  site_name: string | null;
  address: string | null;
  customer: string | null;
  truck: string | null;
  start_time: string | null;
  staff: string[];
  notes: string | null;
  status: "issued" | "accepted" | "declined" | "completed" | "cancelled";
  status_label: string;
  decline_reason: string | null;
  issued_by: string | null;
  issued_at: string;
  delivery: string;
  forms?: { id: string; name: string; available: boolean }[];
}
