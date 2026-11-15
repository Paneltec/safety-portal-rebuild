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

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
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
  if (status >= 500) return "The server had a problem. Please try again.";
  return `Request failed (${status})`;
}

export async function api<T>(path: string, options: { method?: string; body?: unknown; anonymous?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token && !options.anonymous) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
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
    throw new ApiError(response.status, message);
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
