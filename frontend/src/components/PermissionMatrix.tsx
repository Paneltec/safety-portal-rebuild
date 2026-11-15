import type { Matrix, Schema } from "../api";

const ACTION_LABELS: Record<string, string> = {
  open: "Open",
  view: "View",
  edit: "Edit",
  delete: "Delete",
  email: "Email",
  team_view: "Team view",
  use: "Use",
  approve: "Approve",
  reveal_pii: "Reveal PII",
  archive: "Archive",
  reimport: "Re-import",
  audit_view: "Audit view",
};

interface Props {
  schema: Schema;
  /** "role": edit a role's own on/off matrix. "overrides": per-user allow/deny on top of role defaults. */
  mode: "role" | "overrides";
  /** The role matrix (role mode) or the user's role defaults (overrides mode). */
  base: Matrix;
  overrides?: Matrix;
  readOnly?: boolean;
  canEditSpecial?: boolean;
  onChange?: (next: Matrix) => void;
}

function setCell(matrix: Matrix, resource: string, action: string, value: boolean | undefined): Matrix {
  const next: Matrix = { ...matrix, [resource]: { ...(matrix[resource] ?? {}) } };
  if (value === undefined) delete next[resource][action];
  else next[resource][action] = value;
  if (Object.keys(next[resource]).length === 0) delete next[resource];
  return next;
}

export function PermissionMatrix({ schema, mode, base, overrides = {}, readOnly = false, canEditSpecial = false, onChange }: Props) {
  return (
    <div>
      <div className="legend" aria-hidden="true">
        {mode === "overrides" ? (
          <>
            <span className="row"><span className="cell yes">✓ Role</span> allowed by role</span>
            <span className="row"><span className="cell no">– Role</span> not in role</span>
            <span className="row"><span className="cell allow">Allow</span> allowed for this person</span>
            <span className="row"><span className="cell deny">Deny</span> blocked for this person</span>
          </>
        ) : (
          <>
            <span className="row"><span className="cell allow">On</span> granted</span>
            <span className="row"><span className="cell no">Off</span> not granted</span>
          </>
        )}
        <span className="row"><span className="cell na">n/a</span> not available</span>
      </div>
      <div className="table-wrap">
        <table className="matrix">
          <thead>
            <tr>
              <th scope="col">Area</th>
              {schema.actions.map((action) => (
                <th key={action} scope="col">{ACTION_LABELS[action] ?? action}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(schema.resources).map(([resource, meta]) => {
              const special = meta.managed_by !== "roles";
              if (meta.managed_by === "payroll_grants" || (special && mode === "role")) {
                return (
                  <tr key={resource}>
                    <td>{meta.label}</td>
                    <td colSpan={schema.actions.length} className="muted small" style={{ textAlign: "left" }}>
                      {meta.managed_by === "payroll_grants"
                        ? "Managed by named payroll grants, not by roles or overrides."
                        : "Granted to individual people by an admin, not by roles."}
                    </td>
                  </tr>
                );
              }
              const locked = readOnly || (special && !canEditSpecial);
              return (
                <tr key={resource}>
                  <td>{meta.label}</td>
                  {schema.actions.map((action) => {
                    const label = `${meta.label}, ${ACTION_LABELS[action] ?? action}`;
                    if (!meta.supported_actions.includes(action)) {
                      return (
                        <td key={action}>
                          <span className="cell na" aria-label={`${label}: not available`}>n/a</span>
                        </td>
                      );
                    }
                    const roleValue = base[resource]?.[action] === true;
                    if (mode === "role") {
                      return (
                        <td key={action}>
                          <button
                            type="button"
                            className={`cell ${roleValue ? "allow" : "no"}`}
                            aria-pressed={roleValue}
                            aria-label={`${label}: ${roleValue ? "on" : "off"}`}
                            disabled={locked}
                            onClick={() => onChange?.(setCell(base, resource, action, !roleValue))}
                          >
                            {roleValue ? "On" : "Off"}
                          </button>
                        </td>
                      );
                    }
                    const override = overrides[resource]?.[action];
                    const state = override === true ? "allow" : override === false ? "deny" : roleValue ? "yes" : "no";
                    const text = state === "allow" ? "Allow" : state === "deny" ? "Deny" : roleValue ? "✓ Role" : "– Role";
                    const spoken =
                      state === "allow" ? "allowed for this person" : state === "deny" ? "blocked for this person" : roleValue ? "allowed by role" : "not in role";
                    // Click order: role default → allow → deny → role default.
                    const nextValue = override === undefined ? true : override === true ? false : undefined;
                    return (
                      <td key={action}>
                        <button
                          type="button"
                          className={`cell ${state}`}
                          aria-label={`${label}: ${spoken}. Activate to change.`}
                          disabled={locked}
                          onClick={() => onChange?.(setCell(overrides, resource, action, nextValue))}
                        >
                          {text}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
