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
    return (detail as { message: string }).message;
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
