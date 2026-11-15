import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Company, type Org, type User } from "../api";
import { useAuth } from "../auth";
import { LoadError, Loading, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";

type Msg = { tone: "ok" | "bad"; text: string } | null;

const TEXT_FIELDS: { key: string; label: string; type?: string }[] = [
  { key: "trading_name", label: "Trading name" },
  { key: "abn", label: "ABN" },
  { key: "address_line1", label: "Address line 1" },
  { key: "address_line2", label: "Address line 2" },
  { key: "suburb", label: "Suburb" },
  { key: "state", label: "State" },
  { key: "postcode", label: "Postcode" },
  { key: "country", label: "Country" },
  { key: "contact_name", label: "Main contact" },
  { key: "contact_email", label: "Contact email", type: "email" },
  { key: "contact_phone", label: "Contact phone", type: "tel" },
  { key: "emergency_contact_phone", label: "Emergency phone", type: "tel" },
  { key: "after_hours_contact_name", label: "After-hours contact" },
  { key: "after_hours_contact_phone", label: "After-hours phone", type: "tel" },
  { key: "website_url", label: "Website", type: "url" },
];

const TIME_ZONES = [
  "Australia/Hobart", "Australia/Melbourne", "Australia/Sydney", "Australia/Brisbane",
  "Australia/Adelaide", "Australia/Darwin", "Australia/Perth",
];

export function OrganisationSettings() {
  const { me, reload: reloadMe, setLogoUrl } = useAuth();
  const isAdmin = me?.role_id === "admin";
  const org = useLoad(() => api<Org>("/api/org"), []);
  const [form, setForm] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<Msg>(null);

  useEffect(() => {
    if (!org.data) return;
    const next: Record<string, string> = {
      name: org.data.name,
      timezone: org.data.timezone,
      public_portal_url: org.data.public_portal_url ?? "",
      staff_login_url: org.data.staff_login_url ?? "",
    };
    for (const field of TEXT_FIELDS) next[field.key] = (org.data[field.key] as string | null) ?? "";
    setForm(next);
  }, [org.data]);

  if (org.error) return <LoadError message={org.error} onRetry={org.reload} />;
  if (!org.data || !me) return <Loading />;
  const current = org.data;
  const here = window.location.origin;
  const configured = current.public_portal_url;
  const set = (key: string) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  async function save(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    try {
      // An emptied box means "clear this value", sent as null.
      const body = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value.trim() === "" ? null : value]));
      org.setData(await api<Org>("/api/org", { method: "PATCH", body }));
      setMessage({ tone: "ok", text: "Organisation settings saved." });
      void reloadMe();
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Organisation</h1>
          <p className="muted">Name, logo and contact details used on screens, reports and messages.</p>
        </div>
      </div>
      {!isAdmin && <Notice tone="info">Only an admin can change these settings.</Notice>}
      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <form className="card" onSubmit={save}>
        <div className="card-head"><h2>Details</h2></div>
        <fieldset className="card-pad stack" style={{ border: 0, margin: 0, paddingTop: 4 }} disabled={!isAdmin}>
          <div className="fields">
            <label className="field">
              Organisation name
              <input required value={form.name ?? ""} onChange={set("name")} />
            </label>
            <label className="field">
              Time zone
              <select value={form.timezone ?? ""} onChange={set("timezone")}>
                {[...new Set([current.timezone, ...TIME_ZONES])].map((tz) => <option key={tz} value={tz}>{tz}</option>)}
              </select>
            </label>
          </div>
          <div className="fields">
            <label className="field">
              Public portal address
              <input type="url" placeholder="https://" value={form.public_portal_url ?? ""} onChange={set("public_portal_url")} />
              <span className="hint">Used in links sent to people. You are using {here} right now.</span>
            </label>
            <label className="field">
              Staff login address
              <input type="url" placeholder="https://" value={form.staff_login_url ?? ""} onChange={set("staff_login_url")} />
              <span className="hint">Printed on staff material. Can differ from the public address.</span>
            </label>
          </div>
          {configured && configured !== here && (
            <Notice tone="warn">
              The saved public address ({configured}) is different from the address you are using now ({here}). Links in
              invitations will use the saved address.
            </Notice>
          )}
          <div className="fields">
            {TEXT_FIELDS.map((field) => (
              <label className="field" key={field.key}>
                {field.label}
                <input type={field.type ?? "text"} value={form[field.key] ?? ""} onChange={set(field.key)} />
              </label>
            ))}
          </div>
          {isAdmin && (
            <div>
              <button type="submit" className="btn primary">Save details</button>
            </div>
          )}
        </fieldset>
      </form>

      <div className="grid-2">
        <LogoCard org={current} isAdmin={isAdmin} onChange={(url) => { org.reload(); setLogoUrl(url); }} />
        <AccountHolderCard org={current} meId={me.id} onChange={() => { org.reload(); void reloadMe(); }} />
      </div>
      <CompaniesCard isAdmin={isAdmin} />
    </>
  );
}

function LogoCard({ org, isAdmin, onChange }: { org: Org; isAdmin: boolean; onChange: (url: string | null) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);

  async function upload(file: File) {
    setBusy(true);
    setMessage(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await api<{ logo_url: string }>("/api/org/logo/upload", { form });
      setMessage({ tone: "ok", text: "Logo saved." });
      onChange(result.logo_url);
    } catch (e) {
      setMessage({ tone: "bad", text: errorMessage(e) });
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <section className="card">
      <div className="card-head"><h2>Logo</h2></div>
      <div className="card-pad stack" style={{ paddingTop: 4 }}>
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        <div className="logo-preview">
          {org.logo_url ? <img src={org.logo_url} alt={`${org.name} logo`} /> : "No logo yet"}
        </div>
        <p className="small muted">PNG, JPEG or WebP, up to 2 MB. Shown at its own proportions.</p>
        {isAdmin && (
          <div className="row">
            <label className="btn" style={{ cursor: busy ? "wait" : "pointer" }}>
              {busy ? "Uploading…" : org.logo_url ? "Replace logo" : "Upload logo"}
              <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" disabled={busy}
                onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
            </label>
            {org.logo_url && (
              <button type="button" className="btn" onClick={async () => {
                try {
                  await api("/api/org/logo", { method: "DELETE" });
                  setMessage({ tone: "ok", text: "Logo removed." });
                  onChange(null);
                } catch (e) {
                  setMessage({ tone: "bad", text: errorMessage(e) });
                }
              }}>
                Remove logo
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function AccountHolderCard({ org, meId, onChange }: { org: Org; meId: string; onChange: () => void }) {
  const isHolder = org.owner_user_id === meId;
  const admins = useLoad(
    () => (isHolder ? api<{ items: User[] }>("/api/users?role_id=admin&status=active&limit=200") : Promise.resolve(null)),
    [isHolder],
  );
  const [target, setTarget] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<Msg>(null);
  const others = admins.data?.items.filter((u) => u.id !== meId) ?? [];

  return (
    <section className="card">
      <div className="card-head"><h2>Account holder</h2></div>
      <div className="card-pad stack" style={{ paddingTop: 4 }}>
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        <p>
          <strong>{org.account_holder?.name ?? "Not set"}</strong>
          {isHolder && " (you)"}
        </p>
        <p className="small muted">
          The account holder is the top-level admin. Other admins cannot disable, delete, demote or reset this account.
        </p>
        {isHolder && (
          others.length === 0 ? (
            <p className="small muted">To hand the account to someone else, first make them an active admin.</p>
          ) : (
            <div className="row" style={{ alignItems: "flex-end" }}>
              <label className="field">
                Transfer to another admin
                <select value={target} onChange={(e) => { setTarget(e.target.value); setConfirming(false); }}>
                  <option value="">Choose…</option>
                  {others.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </label>
              {!confirming ? (
                <button type="button" className="btn" disabled={!target} onClick={() => setConfirming(true)}>Transfer…</button>
              ) : (
                <button type="button" className="btn danger" onClick={async () => {
                  try {
                    await api("/api/org/account-holder", { method: "POST", body: { user_id: target } });
                    setMessage({ tone: "ok", text: "Account transferred. Payroll ownership has not moved." });
                    setConfirming(false);
                    setTarget("");
                    onChange();
                  } catch (e) {
                    setMessage({ tone: "bad", text: errorMessage(e) });
                  }
                }}>
                  Confirm transfer
                </button>
              )}
            </div>
          )
        )}
      </div>
    </section>
  );
}

function CompaniesCard({ isAdmin }: { isAdmin: boolean }) {
  const companies = useLoad(() => api<{ companies: Company[] }>("/api/org/companies"), []);
  const [rows, setRows] = useState<Company[]>([]);
  const [message, setMessage] = useState<Msg>(null);
  useEffect(() => {
    if (companies.data) setRows(companies.data.companies);
  }, [companies.data]);
  if (companies.error) return <LoadError message={companies.error} onRetry={companies.reload} />;
  if (!companies.data) return <Loading />;
  const dirty = JSON.stringify(rows) !== JSON.stringify(companies.data.companies);
  const update = (index: number, patch: Partial<Company>) => setRows(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Companies and divisions</h2>
          <p className="small muted">The business names people and records belong to, for example each trading company.</p>
        </div>
      </div>
      <div className="card-pad stack" style={{ paddingTop: 4 }}>
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        {rows.length === 0 && <p className="muted">No companies yet.</p>}
        {rows.map((row, index) => (
          <div className="row" key={row.id ?? `new-${index}`} style={{ alignItems: "flex-end" }}>
            <label className="field" style={{ flex: "2 1 220px" }}>
              Name
              <input value={row.name} disabled={!isAdmin} onChange={(e) => update(index, { name: e.target.value })} />
            </label>
            <label className="field" style={{ flex: "1 1 120px" }}>
              Short code
              <input value={row.code} disabled={!isAdmin} onChange={(e) => update(index, { code: e.target.value })} />
            </label>
            {isAdmin && (
              <button type="button" className="btn" aria-label={`Remove ${row.name || "company"}`} onClick={() => setRows(rows.filter((_, i) => i !== index))}>
                Remove
              </button>
            )}
          </div>
        ))}
        {isAdmin && (
          <div className="row">
            <button type="button" className="btn" onClick={() => setRows([...rows, { name: "", code: "" }])}>Add company</button>
            <button type="button" className="btn primary" disabled={!dirty} onClick={async () => {
              setMessage(null);
              try {
                companies.setData(await api<{ companies: Company[] }>("/api/org/companies", { method: "PUT", body: { companies: rows } }));
                setMessage({ tone: "ok", text: "Companies saved." });
              } catch (e) {
                setMessage({ tone: "bad", text: errorMessage(e) });
              }
            }}>
              Save companies
            </button>
            {dirty && <button type="button" className="btn" onClick={() => setRows(companies.data!.companies)}>Discard changes</button>}
          </div>
        )}
      </div>
    </section>
  );
}
