import { useEffect, useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import { Chip, LoadError, Loading, Notice, errorMessage } from "./ui";

interface Rule {
  kind: "role" | "user" | "company";
  target_id: string;
}

type Msg = { tone: "ok" | "bad" | "warn"; text: string } | null;

interface Props {
  /** GET and PUT address of the rules, for example /api/forms/templates/{id}/assignments. */
  url: string;
  title: string;
  everyoneLabel: string;
  note?: string;
  readOnly?: boolean;
  /** Optional: explain what one person will see. */
  check?: (userId: string) => Promise<string>;
}

/** Choose the roles, companies and people something is assigned to. No rules means everyone. */
export function AssignmentRules({ url, title, everyoneLabel, note, readOnly = false, check }: Props) {
  const rules = useLoad(() => api<{ rules: Rule[] }>(url), [url]);
  const roles = useLoad(() => api<{ roles: { role_id: string; name: string; is_active: boolean }[] }>("/api/admin/roles").catch(() => ({ roles: [] })), []);
  const companies = useLoad(() => api<{ companies: { id: string; name: string }[] }>("/api/org/companies"), []);
  const people = useLoad(() => api<{ items: { id: string; label: string }[] }>("/api/forms/pickers/workers").catch(() => ({ items: [] })), []);
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
  const group = (heading: string, kind: Rule["kind"], items: { id: string; label: string }[]) => (
    <fieldset style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "8px 12px", margin: 0, minWidth: 0 }} disabled={readOnly}>
      <legend className="small" style={{ fontWeight: 600, color: "var(--muted)" }}>{heading}</legend>
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
          <h2>{title}</h2>
          {note && <p className="small muted">{note}</p>}
        </div>
        {dirty && <Chip tone="warn">Unsaved changes</Chip>}
      </div>
      <div className="card-pad stack" style={{ paddingTop: 4 }}>
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        <label className="check"><input type="radio" name={`mode-${url}`} checked={!restricted} disabled={readOnly} onChange={() => setRestricted(false)} />{everyoneLabel}</label>
        <label className="check"><input type="radio" name={`mode-${url}`} checked={restricted} disabled={readOnly} onChange={() => setRestricted(true)} />Only the roles, companies and people ticked below</label>
        {restricted && (
          <div className="fields">
            {group("Roles", "role", roles.data.roles.filter((r) => r.is_active).map((r) => ({ id: r.role_id, label: r.name })))}
            {group("Companies", "company", companies.data.companies.map((c) => ({ id: c.id, label: c.name })))}
            {group("People", "user", people.data.items)}
          </div>
        )}
        {restricted && chosen.size === 0 && <Notice tone="warn">Nothing is ticked, so saving would leave this open to everyone.</Notice>}
        <div className="row" style={{ alignItems: "flex-end" }}>
          {!readOnly && (
            <button type="button" className="btn primary" disabled={!dirty} onClick={async () => {
              setMessage(null);
              try {
                const body = { rules: restricted ? [...chosen].map((key) => ({ kind: key.slice(0, key.indexOf(":")), target_id: key.slice(key.indexOf(":") + 1) })) : [] };
                rules.setData(await api<{ rules: Rule[] }>(url, { method: "PUT", body }));
                setCheckResult(null);
                setMessage({ tone: "ok", text: "Saved who this is assigned to." });
              } catch (e) {
                setMessage({ tone: "bad", text: errorMessage(e) });
              }
            }}>
              Save assignment
            </button>
          )}
          {check && (
            <>
              <span className="spacer" />
              <label className="field">
                Check what one person sees
                <select value={checkUser} onChange={async (e) => {
                  const userId = e.target.value;
                  setCheckUser(userId);
                  setCheckResult(null);
                  if (!userId) return;
                  try {
                    setCheckResult(await check(userId));
                  } catch (err) {
                    setCheckResult(errorMessage(err));
                  }
                }}>
                  <option value="">Choose a person…</option>
                  {people.data.items.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </label>
            </>
          )}
        </div>
        {checkResult && <Notice tone="info">{checkResult}{dirty ? " (Based on the saved settings, not your unsaved changes.)" : ""}</Notice>}
      </div>
    </section>
  );
}
